#!/usr/bin/env python3
"""
scripts/diagnose_h009_gap.py
-------------------------------
Diagnoses why current walk-forward results are worse than H009.

Tests 4 configurations on the same data to isolate the cause:
  A: Current v0.5.1 (9 engines, weight-based, step=8)
  B: 6 original engines + weight-based voting
  C: 6 original engines + count-based voting (closest to H009)
  D: 9 engines + count-based voting

By comparing A vs B vs C vs D, we learn:
  B better than A → extra 3 engines hurt
  C better than B → weight-based voting hurt
  If C ≈ H009 original → bug fixes didn't hurt
  If C << H009 original → bug fixes changed the edge

Usage:
    python3 scripts/diagnose_h009_gap.py
    python3 scripts/diagnose_h009_gap.py --symbols XAUUSD BTCUSD
"""
from __future__ import annotations
import argparse, sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.backtest_engine import BacktestConfig, run_backtest
from confluence.voting_system import tally_votes
from core.data_loader import load_from_csv
from scripts.download_all_symbols import PIP_SIZE, ASSET_CLASS, DOLLAR_PER_POINT
from utils.helpers import load_config
import pandas as pd

WINDOWS = [
    ("2024-01-01", "2025-12-31", "2026-01-01", "2026-06-28", "W3: Test 2026"),
]

SYMBOLS = ["XAUUSD", "BTCUSD", "SPX500", "USDCAD", "AUDUSD", "ETHUSD", "GBPUSD"]


def run_config(label, symbols, engine_enabled, use_weights, step):
    """Run walk-forward with specific config."""
    cfg = load_config()

    # Override engines
    cfg["engines"]["enabled"] = engine_enabled

    # Override voting: if not using weights, set all to equal
    if not use_weights:
        n = sum(1 for v in engine_enabled.values() if v)
        equal_w = round(1.0 / max(n, 1), 4)
        cfg["confluence"]["weights"] = {k: equal_w if engine_enabled.get(k) else 0.0
                                         for k in cfg["confluence"]["weights"]}

    results = []
    for sym in symbols:
        csv = None
        for pattern in [f"{sym}_H1_2y.csv", f"{sym}_H1_5y.csv"]:
            p = Path("data") / pattern
            if p.exists():
                csv = p
                break
        if not csv:
            continue

        df = load_from_csv(str(csv))
        df.index = pd.to_datetime(df.index, utc=True)

        for train_s, train_e, test_s, test_e, wlabel in WINDOWS:
            test_df = df.loc[test_s:test_e].copy()
            if len(test_df) < 200:
                continue

            config = BacktestConfig(
                symbol=sym,
                pip_size=PIP_SIZE.get(sym, 0.0001),
                asset_class=ASSET_CLASS.get(sym, "forex"),
                dollar_per_point=DOLLAR_PER_POINT.get(sym, 1.0),
                step_bars=step,
            )

            try:
                r = run_backtest(test_df, config, cfg)
                results.append({
                    "symbol": sym,
                    "trades": r.execute_count,
                    "wr": round(r.win_rate * 100, 1),
                    "pf": round(r.profit_factor, 2),
                    "return": round(r.total_return_pct * 100, 1),
                })
            except Exception as e:
                results.append({"symbol": sym, "trades": 0, "wr": 0, "pf": 0, "return": 0, "error": str(e)[:50]})

    # Aggregate
    valid = [r for r in results if r.get("trades", 0) >= 5]
    total_trades = sum(r["trades"] for r in valid)
    total_wins = sum(round(r["trades"] * r["wr"] / 100) for r in valid)
    avg_pf = sum(r["pf"] for r in valid) / max(len(valid), 1)
    avg_wr = total_wins / max(total_trades, 1) * 100

    return {
        "label": label,
        "total_trades": total_trades,
        "avg_wr": round(avg_wr, 1),
        "avg_pf": round(avg_pf, 2),
        "per_symbol": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"IATIS H009 GAP DIAGNOSTIC")
    print(f"{'='*70}")
    print(f"Testing 4 configurations on 2026 out-of-sample data")
    print(f"Symbols: {', '.join(args.symbols)}\n")

    SIX_ENGINES = {
        "smc": True, "price_action": True, "ict": True,
        "nnfx": True, "quant": True, "wyckoff": True,
        "divergence": False, "market_structure": False,
        "sentiment": False, "macro": False,
    }
    NINE_ENGINES = {
        "smc": True, "price_action": True, "ict": True,
        "nnfx": True, "quant": True, "wyckoff": True,
        "divergence": True, "market_structure": True,
        "sentiment": True, "macro": False,
    }

    configs = [
        ("A: H009 original (6eng, count, step=4)", SIX_ENGINES, False, 4),
        ("B: 6 engines + weight-based voting", SIX_ENGINES, True, 4),
        ("C: 9 engines + count-based voting", NINE_ENGINES, False, 4),
        ("D: Current v0.5.1 (9eng, weight, step=8)", NINE_ENGINES, True, 8),
    ]

    all_results = []
    for label, engines, use_weights, step in configs:
        print(f"Testing: {label}...")
        r = run_config(label, args.symbols, engines, use_weights, step)
        all_results.append(r)
        print(f"  → {r['total_trades']} trades, WR={r['avg_wr']}%, PF={r['avg_pf']}\n")

    # Summary
    print(f"{'='*70}")
    print(f"COMPARISON TABLE")
    print(f"{'='*70}")
    print(f"{'Config':<45} {'Trades':>7} {'WR%':>6} {'PF':>6}")
    print("-" * 67)
    for r in all_results:
        print(f"  {r['label']:<43} {r['total_trades']:>7} {r['avg_wr']:>5.1f}% {r['avg_pf']:>5.2f}")

    # Diagnosis
    a, b, c, d = [r['avg_pf'] for r in all_results]
    print(f"\n{'='*70}")
    print("DIAGNOSIS")
    print(f"{'='*70}")

    if a > d:
        gap = a - d
        print(f"Total PF gap: {gap:+.2f} (H009 config better than current)")
        if a > b:
            print(f"  Weight-based voting costs:    {a-b:+.2f} PF")
        else:
            print(f"  Weight-based voting helps:    {b-a:+.2f} PF")
        if b > d:
            print(f"  Extra 3 engines cost:         {b-c:+.2f} PF (6→9 engines)")
        if c > d:
            print(f"  Step size costs:              {c-d:+.2f} PF (step=4→8)")
    else:
        print(f"Current config is BETTER than H009 original (+{d-a:.2f} PF)")

    print(f"\nRecommendation:")
    best = max(all_results, key=lambda x: x['avg_pf'])
    print(f"  Best config: {best['label']}")
    print(f"  PF={best['avg_pf']}, WR={best['avg_wr']}%, Trades={best['total_trades']}")

    # Save
    out = Path("storage") / "h009_diagnostic.json"
    out.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
