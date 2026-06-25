#!/usr/bin/env python3
"""
run_h008.py
------------
Run H008: BOS + FVG confluence entry experiment.

Usage:
    python3 run_h008.py                           # EURUSD from CSV
    python3 run_h008.py --symbol GBPUSD
    python3 run_h008.py --all                     # all available CSVs
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run_one(symbol: str, df_m15, df_h1, source: str):
    from research.experiments.H008_bos_fvg import run_experiment
    result = run_experiment(df_m15, source=source, df_h1=df_h1)

    status_icon = {"PASSED": "✅", "FAILED": "❌", "INCONCLUSIVE": "⚠️"}.get(result.status, "?")
    print(f"\n{status_icon} {symbol}: {result.status}")
    print(f"   BOS+FVG setups: {result.n_fvg_entries}")
    if result.win_rate:
        print(f"   Win rate: {result.win_rate:.2%} (baseline: {result.h001_baseline:.2%})")
        print(f"   Improvement: {result.improvement:+.2%}")
        print(f"   p-value: {result.p_value:.4f}")
    print(f"   {result.notes}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--all", action="store_true", help="Run on all available CSVs")
    args = parser.parse_args()

    from dotenv import load_dotenv; load_dotenv()
    from core.data_loader import load_from_csv
    from core.timeframe_sync import resample

    print(f"\n{'='*55}")
    print("H008 — BOS + FVG Confluence Entry Test")
    print(f"{'='*55}")

    data_dir = Path("data")

    if args.all:
        csv_files = sorted(data_dir.glob("*_H1_*.csv"))
        symbols = [(f.name.split("_")[0], f) for f in csv_files]
    else:
        csv_file = data_dir / f"{args.symbol}_H1_2y.csv"
        if not csv_file.exists():
            sys.exit(f"File not found: {csv_file}\nRun: python3 scripts/download_historical.py --symbols {args.symbol} --interval H1 --years 2")
        symbols = [(args.symbol, csv_file)]

    all_results = []
    for symbol, csv_file in symbols:
        print(f"\nLoading {symbol} from {csv_file.name}...")
        df_h1 = load_from_csv(str(csv_file))
        df_m15 = resample(df_h1, "M15")
        print(f"  H1: {len(df_h1)} bars | M15: {len(df_m15)} bars")
        source = f"real:Yahoo_{symbol}_H1_2y"
        result = run_one(symbol, df_m15, df_h1, source)
        all_results.append(result)

    if len(all_results) > 1:
        print(f"\n{'='*55}")
        print("Summary:")
        for r in all_results:
            sym = r.data_source.split("_")[1] if "_" in r.data_source else "?"
            wr = f"{r.win_rate:.2%}" if r.win_rate else "--"
            imp = f"{r.improvement:+.2%}" if r.improvement else "--"
            print(f"  {sym}: n={r.n_fvg_entries}, WR={wr}, Δ={imp}, status={r.status}")


if __name__ == "__main__":
    main()


def run_combined(symbols_to_include: list[str] = None):
    """Run H008 on multiple symbols and test combined hypothesis.
    
    Only includes symbols that individually show positive improvement.
    Excludes symbols like GBPUSD that show no edge (WR < baseline).
    """
    from core.data_loader import load_from_csv
    from core.timeframe_sync import resample
    from research.experiments.H008_bos_fvg import detect_bos_fvg_setups, H001_BASELINE
    from math import erf, sqrt, ceil
    
    if symbols_to_include is None:
        # Only include symbols with positive edge
        symbols_to_include = ["EURUSD", "XAUUSD"]
    
    data_dir = Path("data")
    all_outcomes = []
    per_symbol = {}
    
    print(f"\n{'='*55}")
    print("H008b — Combined Symbol Test (positive-edge symbols only)")
    print(f"Symbols: {symbols_to_include}")
    print(f"{'='*55}")
    
    for sym in symbols_to_include:
        # Try both 5yr and 2yr files
        csv_file = (data_dir / f"{sym}_H1_5y.csv") 
        if not csv_file.exists():
            csv_file = data_dir / f"{sym}_H1_2y.csv"
        if not csv_file.exists():
            print(f"  ⚠️  {sym}: no CSV found")
            continue
        
        print(f"\nLoading {sym} from {csv_file.name}...")
        df_h1 = load_from_csv(str(csv_file))
        df_m15 = resample(df_h1, "M15")
        print(f"  {len(df_h1)} H1 bars | {len(df_m15)} M15 bars")
        
        setups = detect_bos_fvg_setups(df_m15)
        outcomes = [s["won"] for s in setups]
        n = len(outcomes)
        wr = sum(outcomes) / n if n > 0 else 0
        
        per_symbol[sym] = {"n": n, "win_rate": round(wr, 4)}
        all_outcomes.extend(outcomes)
        print(f"  {sym}: n={n}, WR={wr:.1%}")
    
    if not all_outcomes:
        print("No data available.")
        return
    
    total_n = len(all_outcomes)
    combined_wr = sum(all_outcomes) / total_n
    improvement = combined_wr - H001_BASELINE
    
    # Two-proportion z-test
    p0 = H001_BASELINE
    se = sqrt(p0 * (1-p0) * (1/total_n + 1/225))
    z = (combined_wr - p0) / se if se > 0 else 0
    p_value = float(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))))
    
    print(f"\n{'='*55}")
    print(f"H008b Combined Result:")
    print(f"  Total setups: {total_n}")
    print(f"  Combined WR:  {combined_wr:.2%}")
    print(f"  Baseline:     {H001_BASELINE:.2%}")
    print(f"  Improvement:  {improvement:+.2%}")
    print(f"  p-value:      {p_value:.4f}")
    print(f"  Status:       {'✅ PASSED' if p_value <= 0.05 and improvement >= 0.05 else '❌ FAILED' if p_value > 0.05 else '⚠️ PROMISING'}")
    
    # Required n for significance
    if p_value > 0.05 and improvement > 0:
        z_req = 1.645 + 0.842
        effect = improvement / sqrt(p0 * (1-p0))
        n_needed = ceil((z_req / effect) ** 2) if effect > 0 else 9999
        print(f"\n  Need n≥{n_needed} for p<0.05 (have {total_n})")
        print(f"  Run with 5yr data: python3 scripts/download_historical.py")
        print(f"    --symbols {' '.join(symbols_to_include)} --interval H1 --years 5 --force")
    print(f"{'='*55}")


if __name__ == "__main__":
    import sys
    if "--combined" in sys.argv:
        run_combined()
    else:
        main()
