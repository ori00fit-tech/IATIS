#!/usr/bin/env python3
"""
run_h008b.py
-------------
H008b: BOS+FVG with London session (02-10 UTC) + ATR quality filter.
Target: raise WR from 55.2% to 60%+ for statistical significance.

Usage:
    python3 scripts/run_h008b.py                    # EURUSD only
    python3 scripts/run_h008b.py --all              # all available CSVs
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root (moved into scripts/ 2026-07-23, audit P2-3)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv; load_dotenv()
    from core.data_loader import load_from_csv
    from core.timeframe_sync import resample
    from research.experiments.H008b_session_filtered_bos import run_experiment

    data_dir = Path("data")
    symbols = (
        [(f.name.split("_")[0], f) for f in sorted(data_dir.glob("*_H1_*.csv"))]
        if args.all
        else [(args.symbol, data_dir / f"{args.symbol}_H1_2y.csv")]
    )

    print(f"\n{'='*55}")
    print("H008b — BOS+FVG + London Session + ATR Quality Filter")
    print(f"{'='*55}")
    print("Hypothesis: London open BOS+FVG raises WR from 55% → 60%+")
    print()

    for symbol, csv_file in symbols:
        if not csv_file.exists():
            print(f"  ⚠️  {symbol}: {csv_file} not found"); continue
        print(f"Loading {symbol}...")
        df_h1 = load_from_csv(str(csv_file))
        df_m15 = resample(df_h1, "M15")

        source = f"real:Yahoo_{symbol}_H1_2y"
        result = run_experiment(df_m15, source=source, symbol=symbol)

        icon = {"PASSED": "✅", "FAILED": "❌", "INCONCLUSIVE": "⚠️"}.get(result.status, "?")
        print(f"\n{icon} {symbol}: {result.status}")
        print(f"   Total BOS+FVG: {result.n_total_setups}")
        print(f"   After filters: {result.n_session_filtered}")
        fs = result.filter_stats
        print(f"   Session rejected: {fs['session_rejected']}")
        print(f"   ATR rejected:     {fs['atr_rejected']}")
        print(f"   Retention:        {fs['filter_retention_pct']}%")
        if result.win_rate:
            print(f"   Win rate: {result.win_rate:.2%} (target: 60%+)")
            print(f"   Improvement: {result.improvement:+.2%}")
            print(f"   p-value: {result.p_value:.4f}")
        print(f"   {result.notes}")

if __name__ == "__main__":
    main()
