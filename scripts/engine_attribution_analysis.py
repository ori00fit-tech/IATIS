#!/usr/bin/env python3
"""
scripts/engine_attribution_analysis.py
-----------------------------------------
Engine Attribution Analysis — answers the most important question:
"Which engines actually contribute to WINNING trades?"

Runs the full pipeline backtest but records per-trade engine votes,
then analyzes which engine combinations lead to wins vs losses.

Output:
  1. Per-engine hit rate (when this engine agrees with majority, WR=?)
  2. Engine pairs — which 2-engine combos have highest WR
  3. Reversal veto impact — how many losses would H013 have prevented
  4. Regime × Engine matrix — which engines work in which regimes
  5. Score calibration — what WR does each score range actually produce

Usage:
    python3 scripts/engine_attribution_analysis.py --symbols EURUSD BTCUSD
    python3 scripts/engine_attribution_analysis.py --all
"""
from __future__ import annotations
import argparse, json, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT
from scripts.full_pipeline_backtest import (
    ACTIVE_SYMBOLS, build_config, simulate_trade, calc_pnl,
)


def run_attribution(symbol: str, df, step: int = 8, warmup: int = 220):
    """Run backtest with full engine attribution tracking."""
    from main import run_pipeline

    pip = PIP_SIZE.get(symbol, 0.0001)
    ac = ASSET_CLASS.get(symbol, "forex")
    dpp = DOLLAR_PER_POINT.get(symbol, 1.0)
    cfg = build_config(symbol)

    balance = 10_000.0
    trades = []
    open_until = -1
    n = len(df)

    for i in range(warmup, n - 2, step):
        if i <= open_until:
            continue

        cfg["data"]["source"] = "injected"
        cfg.setdefault("system", {})["backtest_mode"] = True  # offline backtest: skip live persistence (D1)
        cfg["data"]["_injected_df"] = df.iloc[:i+1].copy()
        cfg["data"]["symbol"] = symbol

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
                       balance, symbol, ac, pip, dpp)
        balance += pnl
        open_until = i + sim["bars"]

        # Record engine votes for this trade.
        # The current report exposes per-engine effect via
        # confluence.contributions = {engine_name: signed_weighted_value},
        # where sign is the effective bias (positive→BULLISH, negative→BEARISH,
        # 0→NEUTRAL) and magnitude is the engine's weighted push. There is no
        # top-level "engines" list, so the previous loop always yielded {}.
        conf = report.get("confluence", {})
        contributions = conf.get("contributions", {}) or {}
        engine_votes = {}
        for name, contrib in contributions.items():
            c = float(contrib or 0.0)
            if c > 0:
                bias = "BULLISH"
            elif c < 0:
                bias = "BEARISH"
            else:
                bias = "NEUTRAL"
            engine_votes[name] = {"bias": bias, "score": abs(c)}

        # Record reversal veto info
        rv = conf.get("reversal_veto", {})

        trades.append({
            "bar": i,
            "symbol": symbol,
            "outcome": sim["outcome"],
            "pnl": pnl,
            "direction": "BULLISH" if direction == 1 else "BEARISH",
            "score": conf.get("score", conf.get("raw_score", 0)),
            "regime": report.get("regime", {}).get("state", "UNKNOWN"),
            "engine_votes": engine_votes,
            "reversal_veto": rv,
            "winning_bias": conf.get("vote", {}).get("winning_bias", "?"),
        })

    return trades


def analyze_attribution(all_trades: list[dict]) -> dict:
    """Analyze engine attribution across all trades."""

    if not all_trades:
        return {"error": "No trades to analyze"}

    # 1. Per-engine analysis
    engine_stats = defaultdict(lambda: {
        "voted_with_majority": 0, "voted_against": 0, "neutral": 0,
        "wins_when_agreed": 0, "losses_when_agreed": 0,
        "wins_when_opposed": 0, "losses_when_opposed": 0,
    })

    # 2. Score calibration
    score_buckets = defaultdict(lambda: {"wins": 0, "losses": 0})

    # 3. Regime analysis
    regime_stats = defaultdict(lambda: {"wins": 0, "losses": 0})

    # 4. Reversal veto analysis
    veto_stats = {"would_have_blocked": 0, "blocked_wins": 0, "blocked_losses": 0}

    # 5. Engine pair analysis
    pair_stats = defaultdict(lambda: {"wins": 0, "losses": 0})

    for t in all_trades:
        outcome = t["outcome"]
        winning_bias = t["winning_bias"]
        is_win = outcome == "win"

        # Score calibration
        score = t.get("score", 0)
        bucket = f"{int(score // 5) * 5}-{int(score // 5) * 5 + 5}"
        if is_win:
            score_buckets[bucket]["wins"] += 1
        else:
            score_buckets[bucket]["losses"] += 1

        # Regime
        regime = t.get("regime", "UNKNOWN")
        if is_win:
            regime_stats[regime]["wins"] += 1
        else:
            regime_stats[regime]["losses"] += 1

        # Reversal veto impact
        rv = t.get("reversal_veto", {})
        rev_count = rv.get("reversal_count", 0)
        if rev_count >= 2:
            veto_stats["would_have_blocked"] += 1
            if is_win:
                veto_stats["blocked_wins"] += 1
            else:
                veto_stats["blocked_losses"] += 1

        # Per-engine attribution
        agreeing_engines = []
        for name, vote in t.get("engine_votes", {}).items():
            bias = vote.get("bias", "NEUTRAL")
            stats = engine_stats[name]

            if bias == "NEUTRAL":
                stats["neutral"] += 1
            elif bias == winning_bias:
                stats["voted_with_majority"] += 1
                if is_win:
                    stats["wins_when_agreed"] += 1
                else:
                    stats["losses_when_agreed"] += 1
                agreeing_engines.append(name)
            else:
                stats["voted_against"] += 1
                if is_win:
                    stats["wins_when_opposed"] += 1
                else:
                    stats["losses_when_opposed"] += 1

        # Engine pair analysis
        for i_idx, e1 in enumerate(agreeing_engines):
            for e2 in agreeing_engines[i_idx + 1:]:
                pair_key = tuple(sorted([e1, e2]))
                if is_win:
                    pair_stats[pair_key]["wins"] += 1
                else:
                    pair_stats[pair_key]["losses"] += 1

    # Format results
    engine_report = {}
    for name, stats in sorted(engine_stats.items()):
        total_agreed = stats["wins_when_agreed"] + stats["losses_when_agreed"]
        total_opposed = stats["wins_when_opposed"] + stats["losses_when_opposed"]
        engine_report[name] = {
            "agreement_rate": round(
                stats["voted_with_majority"] /
                max(stats["voted_with_majority"] + stats["voted_against"], 1) * 100, 1
            ),
            "neutral_rate": round(
                stats["neutral"] /
                max(stats["voted_with_majority"] + stats["voted_against"] + stats["neutral"], 1) * 100, 1
            ),
            "wr_when_agreed": round(
                stats["wins_when_agreed"] / max(total_agreed, 1) * 100, 1
            ),
            "wr_when_opposed": round(
                stats["wins_when_opposed"] / max(total_opposed, 1) * 100, 1
            ),
            "total_agreed": total_agreed,
            "total_opposed": total_opposed,
            "contribution_score": round(
                (stats["wins_when_agreed"] / max(total_agreed, 1)) *
                (stats["voted_with_majority"] /
                 max(stats["voted_with_majority"] + stats["voted_against"] + stats["neutral"], 1)),
                4
            ),
        }

    # Top engine pairs
    pair_report = []
    for pair, stats in sorted(pair_stats.items(),
                              key=lambda x: x[1]["wins"] / max(x[1]["wins"] + x[1]["losses"], 1),
                              reverse=True):
        total = stats["wins"] + stats["losses"]
        if total >= 5:  # minimum sample
            pair_report.append({
                "pair": f"{pair[0]} + {pair[1]}",
                "trades": total,
                "win_rate": round(stats["wins"] / total * 100, 1),
                "wins": stats["wins"],
                "losses": stats["losses"],
            })

    # Score calibration
    calibration = []
    for bucket in sorted(score_buckets.keys()):
        stats = score_buckets[bucket]
        total = stats["wins"] + stats["losses"]
        if total >= 3:
            calibration.append({
                "score_range": bucket,
                "trades": total,
                "win_rate": round(stats["wins"] / total * 100, 1),
            })

    # Regime
    regime_report = []
    for regime, stats in sorted(regime_stats.items()):
        total = stats["wins"] + stats["losses"]
        if total >= 3:
            regime_report.append({
                "regime": regime,
                "trades": total,
                "win_rate": round(stats["wins"] / total * 100, 1),
            })

    # Reversal veto
    rv_total = veto_stats["would_have_blocked"]
    if rv_total > 0:
        veto_report = {
            "trades_that_would_be_vetoed": rv_total,
            "of_which_were_losses": veto_stats["blocked_losses"],
            "of_which_were_wins": veto_stats["blocked_wins"],
            "veto_accuracy": round(
                veto_stats["blocked_losses"] / rv_total * 100, 1
            ),
            "net_impact": f"Would prevent {veto_stats['blocked_losses']} losses "
                          f"but also block {veto_stats['blocked_wins']} wins",
        }
    else:
        veto_report = {"message": "No trades triggered reversal veto conditions"}

    total_trades = len(all_trades)
    total_wins = sum(1 for t in all_trades if t["outcome"] == "win")

    return {
        "total_trades": total_trades,
        "overall_win_rate": round(total_wins / max(total_trades, 1) * 100, 1),
        "engine_attribution": engine_report,
        "top_engine_pairs": pair_report[:10],
        "score_calibration": calibration,
        "regime_performance": regime_report,
        "reversal_veto_analysis": veto_report,
    }


def main():
    parser = argparse.ArgumentParser(description="Engine Attribution Analysis")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--step", type=int, default=8)
    args = parser.parse_args()

    symbols = ACTIVE_SYMBOLS if (args.all or not args.symbols) else args.symbols

    print(f"\n{'=' * 70}")
    print(f"IATIS Engine Attribution Analysis")
    print(f"{'=' * 70}")
    print(f"Symbols: {len(symbols)} | Step: {args.step}\n")

    from core.data_loader import load_from_csv
    data_dir = Path("data")
    all_trades = []
    t0 = time.monotonic()

    for i, sym in enumerate(symbols, 1):
        csv = next((data_dir / f"{sym}_H1_{s}.csv"
                    for s in ["2y", "5y"]
                    if (data_dir / f"{sym}_H1_{s}.csv").exists()), None)

        print(f"[{i:2}/{len(symbols)}] {sym:10} ... ", end="", flush=True)
        if not csv:
            print("no CSV")
            continue

        try:
            df = load_from_csv(str(csv))
            trades = run_attribution(sym, df, step=args.step)
            wins = sum(1 for t in trades if t["outcome"] == "win")
            print(f"{len(trades)} trades, WR={wins/max(len(trades),1)*100:.0f}%")
            all_trades.extend(trades)
        except Exception as e:
            print(f"error: {str(e)[:50]}")

    duration = time.monotonic() - t0

    print(f"\n{'=' * 70}")
    print(f"ANALYSIS ({len(all_trades)} trades, {duration/60:.0f} min)")
    print(f"{'=' * 70}\n")

    result = analyze_attribution(all_trades)

    # Print engine attribution
    print("ENGINE ATTRIBUTION (sorted by contribution):")
    print(f"{'Engine':<18} {'Agree%':>7} {'WR Agree':>9} {'WR Oppose':>10} {'Contrib':>8}")
    print("-" * 55)
    for name, stats in sorted(result["engine_attribution"].items(),
                              key=lambda x: x[1]["contribution_score"], reverse=True):
        print(f"{name:<18} {stats['agreement_rate']:>6.1f}% "
              f"{stats['wr_when_agreed']:>8.1f}% "
              f"{stats['wr_when_opposed']:>9.1f}% "
              f"{stats['contribution_score']:>8.4f}")

    # Print top pairs
    if result["top_engine_pairs"]:
        print(f"\nTOP ENGINE PAIRS:")
        print(f"{'Pair':<35} {'Trades':>7} {'WR%':>6}")
        print("-" * 50)
        for p in result["top_engine_pairs"][:8]:
            print(f"{p['pair']:<35} {p['trades']:>7} {p['win_rate']:>6.1f}%")

    # Print regime
    if result["regime_performance"]:
        print(f"\nREGIME PERFORMANCE:")
        for r in result["regime_performance"]:
            print(f"  {r['regime']:<12} {r['trades']:>4} trades  WR={r['win_rate']:.1f}%")

    # Print score calibration
    if result["score_calibration"]:
        print(f"\nSCORE CALIBRATION (Score → Actual WR):")
        for c in result["score_calibration"]:
            bar = "█" * int(c["win_rate"] / 5)
            print(f"  {c['score_range']:>8}  n={c['trades']:>3}  WR={c['win_rate']:>5.1f}%  {bar}")

    # Print reversal veto
    rv = result["reversal_veto_analysis"]
    if "trades_that_would_be_vetoed" in rv:
        print(f"\nH013 REVERSAL VETO IMPACT:")
        print(f"  Would block: {rv['trades_that_would_be_vetoed']} trades")
        print(f"  Of which losses: {rv['of_which_were_losses']} "
              f"(accuracy: {rv['veto_accuracy']:.0f}%)")
        print(f"  Net: {rv['net_impact']}")

    # Save
    out_path = Path("storage") / f"engine_attribution_{datetime.now().strftime('%Y-%m-%d')}.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
