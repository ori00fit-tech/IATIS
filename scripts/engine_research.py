#!/usr/bin/env python3
"""
scripts/engine_research.py
------------------------------
Three research experiments in one script:

  H014 — Orthogonality Test: correlation between engine votes
  H015 — Ablation Study: minimum engine set for same/better PF
  H016 — Engine Pair Analysis: which 2-engine combos add most value

All three use the full pipeline backtest data to answer:
  "Are our 9 engines truly independent, or are some redundant?"

Usage:
    python3 scripts/engine_research.py --symbols XAUUSD BTCUSD SPX500
    python3 scripts/engine_research.py --all
    python3 scripts/engine_research.py --experiment H014  # only correlation
"""
from __future__ import annotations
import argparse, json, sys, time, itertools
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT
from scripts.full_pipeline_backtest import build_config, simulate_trade, calc_pnl
from utils.logger import get_logger

logger = get_logger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

PROFITABLE_SYMBOLS = ["XAUUSD", "BTCUSD", "SPX500", "USDCAD", "AUDUSD", "ETHUSD", "GBPUSD"]

ALL_ENGINES = [
    "smc", "ict", "nnfx", "price_action", "quant",
    "wyckoff", "divergence", "market_structure", "sentiment",
]


def _collect_engine_votes(symbols: list[str], step: int = 8, warmup: int = 220):
    """Run backtest and collect per-trade engine vote data."""
    from core.data_loader import load_from_csv
    from main import run_pipeline

    all_records = []

    for sym in symbols:
        csv = next((DATA_DIR / f"{sym}_H1_{s}.csv"
                    for s in ["2y", "5y"]
                    if (DATA_DIR / f"{sym}_H1_{s}.csv").exists()), None)
        if not csv:
            print(f"  {sym}: no CSV — skipping")
            continue

        df = load_from_csv(str(csv))
        if len(df) < warmup + 50:
            continue

        pip = PIP_SIZE.get(sym, 0.0001)
        ac = ASSET_CLASS.get(sym, "forex")
        dpp = DOLLAR_PER_POINT.get(sym, 1.0)
        cfg = build_config(sym)
        balance = 10_000.0
        open_until = -1
        n = len(df)
        count = 0

        print(f"  {sym}: processing... ", end="", flush=True)

        for i in range(warmup, n - 2, step):
            if i <= open_until:
                continue

            cfg["data"]["source"] = "injected"
            cfg["data"]["_injected_df"] = df.iloc[:i + 1].copy()
            cfg["data"]["symbol"] = sym

            try:
                report = run_pipeline(cfg)
            except Exception:
                continue

            if report.get("final_verdict") != "EXECUTE":
                continue

            entry = report.get("entry_price")
            sl = report.get("stop_loss")
            tp = report.get("take_profit")
            if not all([entry, sl, tp]):
                continue

            entry, sl, tp = float(entry), float(sl), float(tp)
            direction = 1 if sl < entry else -1
            sl_dist = abs(entry - sl)
            if sl_dist <= 0 or sl_dist > abs(tp - entry):
                continue

            sim = simulate_trade(entry, sl, tp, direction, df, i + 1)
            pnl = calc_pnl(entry, sim["exit"], direction, sl_dist,
                           balance, sym, ac, pip, dpp)
            balance += pnl
            open_until = i + sim["bars"]
            count += 1

            # Record engine votes
            votes = {}
            for eng in report.get("engine_outputs", []):
                name = eng.get("engine", "?")
                bias = eng.get("bias", "NEUTRAL")
                score = eng.get("score", 0)
                # Convert to numeric: BULLISH=+1, BEARISH=-1, NEUTRAL=0
                numeric = 1 if bias == "BULLISH" else (-1 if bias == "BEARISH" else 0)
                votes[name] = {"bias": bias, "score": score, "numeric": numeric}

            all_records.append({
                "symbol": sym,
                "bar": i,
                "outcome": sim["outcome"],
                "pnl": pnl,
                "direction": direction,
                "votes": votes,
                "score": report.get("confluence", {}).get("score", 0),
            })

        print(f"{count} trades")

    return all_records


# ═══════════════════════════════════════════════════════════
# H014: ORTHOGONALITY TEST
# ═══════════════════════════════════════════════════════════

def run_h014(records: list[dict]) -> dict:
    """Compute pairwise correlation between engine vote directions."""
    import numpy as np

    engine_names = sorted({name for r in records for name in r["votes"]})
    n = len(records)

    if n < 20:
        return {"error": "Insufficient trades for correlation analysis"}

    # Build vote matrix: rows=trades, cols=engines
    matrix = []
    for r in records:
        row = []
        for eng in engine_names:
            v = r["votes"].get(eng, {})
            row.append(v.get("numeric", 0))
        matrix.append(row)

    mat = np.array(matrix, dtype=float)

    # Pairwise Pearson correlation
    correlations = {}
    high_corr_pairs = []

    for i, e1 in enumerate(engine_names):
        for j, e2 in enumerate(engine_names):
            if i >= j:
                continue
            col1 = mat[:, i]
            col2 = mat[:, j]
            # Skip if either column is constant
            if np.std(col1) == 0 or np.std(col2) == 0:
                correlations[f"{e1} ↔ {e2}"] = 0.0
                continue
            corr = float(np.corrcoef(col1, col2)[0, 1])
            correlations[f"{e1} ↔ {e2}"] = round(corr, 3)
            if abs(corr) > 0.7:
                high_corr_pairs.append({
                    "pair": f"{e1} ↔ {e2}",
                    "correlation": round(corr, 3),
                    "verdict": "REDUNDANT" if corr > 0.85 else "HIGH_OVERLAP",
                })

    # Sort by absolute correlation
    sorted_corr = dict(sorted(correlations.items(),
                               key=lambda x: abs(x[1]), reverse=True))

    return {
        "experiment": "H014_Orthogonality",
        "trades_analyzed": n,
        "correlations": sorted_corr,
        "high_correlation_pairs": high_corr_pairs,
        "recommendation": (
            "Remove one engine from each REDUNDANT pair"
            if high_corr_pairs else
            "All engines appear sufficiently independent"
        ),
    }


# ═══════════════════════════════════════════════════════════
# H015: ABLATION STUDY
# ═══════════════════════════════════════════════════════════

def run_h015(symbols: list[str], step: int = 12) -> dict:
    """Test system with progressively fewer engines to find minimum set."""
    from core.data_loader import load_from_csv
    from main import run_pipeline

    # Test these subsets (from 9 engines down to 3)
    subsets = {
        "ALL_9": ALL_ENGINES,
        "TOP_7 (-ict,-sentiment)": [e for e in ALL_ENGINES if e not in ("ict", "sentiment")],
        "TOP_6 (-ict,-sentiment,-quant)": [e for e in ALL_ENGINES if e not in ("ict", "sentiment", "quant")],
        "TOP_5 (-ict,-sent,-quant,-macro)": [e for e in ALL_ENGINES if e not in ("ict", "sentiment", "quant", "macro")],
        "CORE_4 (smc+nnfx+pa+wyckoff)": ["smc", "nnfx", "price_action", "wyckoff"],
        "CORE_3 (smc+nnfx+pa)": ["smc", "nnfx", "price_action"],
        "TREND_ONLY (smc+nnfx+pa+mss)": ["smc", "nnfx", "price_action", "market_structure"],
        "REVERSAL_HEAVY (wyckoff+div+sent+nnfx)": ["wyckoff", "divergence", "sentiment", "nnfx"],
    }

    results = {}

    for label, engines in subsets.items():
        print(f"\n  Testing: {label} ({len(engines)} engines)...")
        total_trades = 0
        total_wins = 0
        total_pnl = 0.0

        for sym in symbols:
            csv = next((DATA_DIR / f"{sym}_H1_{s}.csv"
                        for s in ["2y", "5y"]
                        if (DATA_DIR / f"{sym}_H1_{s}.csv").exists()), None)
            if not csv:
                continue

            df = load_from_csv(str(csv))
            if len(df) < 250:
                continue

            cfg = build_config(sym)
            # Disable engines not in this subset
            for eng_key in ALL_ENGINES:
                cfg["engines"]["enabled"][eng_key] = (eng_key in engines)
            # Adjust min_engines_agreeing
            cfg["confluence"]["min_engines_agreeing"] = min(2, len(engines))

            pip = PIP_SIZE.get(sym, 0.0001)
            ac = ASSET_CLASS.get(sym, "forex")
            dpp = DOLLAR_PER_POINT.get(sym, 1.0)
            balance = 10_000.0
            open_until = -1

            for i in range(220, len(df) - 2, step):
                if i <= open_until:
                    continue

                cfg["data"]["source"] = "injected"
                cfg["data"]["_injected_df"] = df.iloc[:i + 1].copy()
                cfg["data"]["symbol"] = sym

                try:
                    report = run_pipeline(cfg)
                except Exception:
                    continue

                if report.get("final_verdict") != "EXECUTE":
                    continue

                entry = report.get("entry_price")
                sl = report.get("stop_loss")
                tp = report.get("take_profit")
                if not all([entry, sl, tp]):
                    continue

                entry, sl, tp = float(entry), float(sl), float(tp)
                direction = 1 if sl < entry else -1
                sl_dist = abs(entry - sl)
                if sl_dist <= 0 or sl_dist > abs(tp - entry):
                    continue

                sim = simulate_trade(entry, sl, tp, direction, df, i + 1)
                pnl = calc_pnl(entry, sim["exit"], direction, sl_dist,
                               balance, sym, ac, pip, dpp)
                balance += pnl
                open_until = i + sim["bars"]
                total_trades += 1
                if sim["outcome"] == "win":
                    total_wins += 1
                total_pnl += pnl

        wr = total_wins / max(total_trades, 1) * 100
        results[label] = {
            "engines": len(engines),
            "trades": total_trades,
            "wins": total_wins,
            "win_rate": round(wr, 1),
            "pnl": round(total_pnl, 0),
        }
        print(f"    → {total_trades} trades, WR={wr:.1f}%, PnL=${total_pnl:+,.0f}")

    return {
        "experiment": "H015_Ablation",
        "subsets": results,
    }


# ═══════════════════════════════════════════════════════════
# H016: ENGINE PAIR ANALYSIS
# ═══════════════════════════════════════════════════════════

def run_h016(records: list[dict]) -> dict:
    """Analyze which engine PAIRS produce the best win rate when they agree."""
    engine_names = sorted({name for r in records for name in r["votes"]})

    pair_stats = {}

    for e1, e2 in itertools.combinations(engine_names, 2):
        agreed_wins = 0
        agreed_total = 0
        disagreed_wins = 0
        disagreed_total = 0

        for r in records:
            v1 = r["votes"].get(e1, {}).get("numeric", 0)
            v2 = r["votes"].get(e2, {}).get("numeric", 0)

            if v1 == 0 or v2 == 0:
                continue  # skip if either is NEUTRAL

            if v1 == v2:  # they agree
                agreed_total += 1
                if r["outcome"] == "win":
                    agreed_wins += 1
            else:  # they disagree
                disagreed_total += 1
                if r["outcome"] == "win":
                    disagreed_wins += 1

        if agreed_total >= 10:
            pair_stats[f"{e1} + {e2}"] = {
                "agreed_trades": agreed_total,
                "agreed_wr": round(agreed_wins / agreed_total * 100, 1),
                "disagreed_trades": disagreed_total,
                "disagreed_wr": round(disagreed_wins / max(disagreed_total, 1) * 100, 1),
                "agreement_lift": round(
                    (agreed_wins / agreed_total - disagreed_wins / max(disagreed_total, 1)) * 100, 1
                ) if disagreed_total > 0 else 0,
            }

    # Sort by agreed WR
    sorted_pairs = dict(sorted(pair_stats.items(),
                                key=lambda x: x[1]["agreed_wr"], reverse=True))

    # Find best complementary pairs (high lift = agreement matters)
    best_complementary = sorted(
        [(k, v) for k, v in pair_stats.items() if v["agreement_lift"] > 5],
        key=lambda x: x[1]["agreement_lift"], reverse=True
    )[:10]

    return {
        "experiment": "H016_EnginePairs",
        "trades_analyzed": len(records),
        "all_pairs": sorted_pairs,
        "best_complementary_pairs": [
            {"pair": k, **v} for k, v in best_complementary
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="IATIS Engine Research (H014+H015+H016)")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--experiment", choices=["H014", "H015", "H016", "ALL"], default="ALL")
    parser.add_argument("--step", type=int, default=12, help="Bar step for backtests")
    args = parser.parse_args()

    symbols = args.symbols or PROFITABLE_SYMBOLS
    if args.all:
        symbols = PROFITABLE_SYMBOLS

    print(f"\n{'='*70}")
    print(f"IATIS ENGINE RESEARCH — H014 + H015 + H016")
    print(f"{'='*70}")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Step: {args.step}")
    print(f"{'='*70}\n")

    results = {}
    t0 = time.monotonic()

    # Collect vote data (needed for H014 and H016)
    if args.experiment in ("H014", "H016", "ALL"):
        print("Phase 1: Collecting engine vote data...")
        records = _collect_engine_votes(symbols, step=args.step)
        print(f"  → {len(records)} trades collected\n")
    else:
        records = []

    # H014: Orthogonality
    if args.experiment in ("H014", "ALL") and records:
        print(f"\n{'─'*50}")
        print("H014: ORTHOGONALITY TEST")
        print(f"{'─'*50}")
        h014 = run_h014(records)
        results["H014"] = h014

        print(f"\nEngine Correlations (sorted by |correlation|):")
        print(f"{'Pair':<35} {'Corr':>8} {'Status'}")
        print("-" * 55)
        for pair, corr in list(h014["correlations"].items())[:15]:
            status = "🔴 REDUNDANT" if corr > 0.85 else "🟡 OVERLAP" if corr > 0.7 else "🟢 OK"
            bar = "█" * int(abs(corr) * 20)
            print(f"  {pair:<33} {corr:>+7.3f}  {status}  {bar}")

        if h014["high_correlation_pairs"]:
            print(f"\n⚠️  {len(h014['high_correlation_pairs'])} high-correlation pairs found")
        else:
            print(f"\n✅ All engine pairs show acceptable independence")

    # H015: Ablation
    if args.experiment in ("H015", "ALL"):
        print(f"\n{'─'*50}")
        print("H015: ABLATION STUDY")
        print(f"{'─'*50}")
        h015 = run_h015(symbols, step=args.step)
        results["H015"] = h015

        print(f"\n{'Subset':<40} {'Eng':>4} {'Trades':>7} {'WR%':>7} {'PnL':>10}")
        print("-" * 72)
        for label, data in h015["subsets"].items():
            print(f"  {label:<38} {data['engines']:>4} {data['trades']:>7} "
                  f"{data['win_rate']:>6.1f}% ${data['pnl']:>+9,.0f}")

    # H016: Engine Pairs
    if args.experiment in ("H016", "ALL") and records:
        print(f"\n{'─'*50}")
        print("H016: ENGINE PAIR ANALYSIS")
        print(f"{'─'*50}")
        h016 = run_h016(records)
        results["H016"] = h016

        print(f"\nTop Engine Pairs (by WR when agreeing):")
        print(f"{'Pair':<35} {'Agree':>6} {'WR%':>7} {'Disagree':>9} {'WR%':>7} {'Lift':>7}")
        print("-" * 75)
        for pair, data in list(h016["all_pairs"].items())[:12]:
            print(f"  {pair:<33} {data['agreed_trades']:>6} {data['agreed_wr']:>6.1f}% "
                  f"{data['disagreed_trades']:>9} {data['disagreed_wr']:>6.1f}% "
                  f"{data['agreement_lift']:>+6.1f}%")

        if h016["best_complementary_pairs"]:
            print(f"\n🏆 Best Complementary Pairs (highest lift when agreeing):")
            for p in h016["best_complementary_pairs"][:5]:
                print(f"  {p['pair']:<33} Lift: {p['agreement_lift']:>+.1f}% "
                      f"(agree={p['agreed_trades']}t/{p['agreed_wr']:.0f}%)")

    duration = time.monotonic() - t0
    print(f"\n{'='*70}")
    print(f"Research complete in {duration/60:.0f} min")
    print(f"{'='*70}")

    # Save
    out = Path("storage") / f"engine_research_{datetime.now().strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"Results saved: {out}")


if __name__ == "__main__":
    main()
