#!/usr/bin/env python3
"""
run_h002b.py
-------------
Run H002b: multi-symbol qualified sweep aggregation.
Loads EURUSD + GBPUSD + XAUUSD from local CSVs (Yahoo Finance 2yr data).

Usage:
    python3 run_h002b.py
    python3 run_h002b.py --symbols EURUSD GBPUSD   # subset
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+",
                        default=["EURUSD", "GBPUSD", "XAUUSD"])
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    from core.data_loader import load_from_csv
    from core.timeframe_sync import resample

    data_dir = Path("data")
    symbols_data = {}
    sources = []

    print(f"\n{'='*55}")
    print("H002b — Multi-Symbol Qualified Sweep Test")
    print(f"{'='*55}")
    print(f"Symbols: {args.symbols}")
    print()

    for sym in args.symbols:
        csv_file = data_dir / f"{sym}_H1_2y.csv"
        if not csv_file.exists():
            print(f"  ⚠️  {sym}: {csv_file} not found — skipping")
            print(f"       Run: python3 scripts/download_historical.py --symbols {sym} --interval H1 --years 2")
            continue

        print(f"  Loading {sym}...")
        df_h1 = load_from_csv(str(csv_file))
        df_m15 = resample(df_h1, "M15")
        print(f"    H1: {len(df_h1)} bars | M15: {len(df_m15)} bars")

        symbols_data[sym] = (df_m15, df_h1)
        sources.append(f"real:Yahoo_{sym}_H1_2y_{df_h1.index[0].date()}_{df_h1.index[-1].date()}")

    if not symbols_data:
        sys.exit("No data available. Download CSVs first.")

    print(f"\nRunning H002b on {len(symbols_data)} symbols...")
    from research.experiments.H002b_multisymbol_sweep import run_experiment
    result = run_experiment(symbols_data, sources)

    print(f"\n{'='*55}")
    print(f"H002b RESULT: {result.status}")
    print(f"{'='*55}")
    print(f"Total qualified sweeps: {result.total_n}")
    print()
    print("Per-symbol breakdown:")
    for sym, stats in result.per_symbol.items():
        wr = f"{stats['win_rate']:.2%}" if stats['win_rate'] else "—"
        print(f"  {sym}: n={stats['qualified_n']}, WR={wr} (raw={stats['raw_sweeps']})")

    print()
    if result.combined_win_rate:
        print(f"Combined WR:   {result.combined_win_rate:.2%}")
        print(f"H001 baseline: {result.h001_baseline:.2%}")
        print(f"Improvement:   {result.improvement:+.2%}")
        print(f"p-value:       {result.p_value:.4f}")
    print(f"Notes:         {result.notes}")

    if result.status == "PASSED":
        print("\n✅ H002b PASSED! SMC Advanced engine can now be activated.")
        print("Update registry.json H002 status to PASSED.")
    elif result.status == "INCONCLUSIVE":
        print("\n⚠️  Still need more data. Options:")
        print("  1. Add more symbols (USDJPY, EURUSD 5yr)")
        print("  2. Lower ATR_MULTIPLIER further (0.3)")
        print("  3. Extend to 3yr+ data")
    else:
        print("\n❌ H002b FAILED. The qualified sweep edge is not present.")
        print("Consider: session-filtered sweeps (H003), or focusing on")
        print("GBPUSD only where WR may be higher.")

    print(f"\nResult saved: research/results/H002b_result.json")


if __name__ == "__main__":
    main()
