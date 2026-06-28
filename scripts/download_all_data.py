#!/usr/bin/env python3
"""
scripts/download_all_data.py
------------------------------
Download all symbols × all timeframes using DataManager.

Usage:
    python3 scripts/download_all_data.py
    python3 scripts/download_all_data.py --sym XAUUSD BTCUSD
    python3 scripts/download_all_data.py --tf 5m 1h 4h
    python3 scripts/download_all_data.py --force
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.data_manager import DataManager, SYMBOL_REGISTRY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sym", nargs="+", default=None)
    parser.add_argument("--tf",  nargs="+",
                        default=["5m", "15m", "30m", "1h", "4h", "1d"])
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    dm = DataManager()
    symbols = args.sym or list(SYMBOL_REGISTRY.keys())

    total = len(symbols) * len(args.tf)
    print(f"\n{'='*60}")
    print(f"IATIS Data Manager — Smart Download")
    print(f"{'='*60}")
    print(f"Symbols:    {symbols}")
    print(f"Timeframes: {args.tf}")
    print(f"Days:       {args.days}")
    print(f"Total:      {total} files")
    print(f"Providers:  Binance → Yahoo → Stooq → TwelveData")
    print(f"Resample:   15m/30m from 5m | 4h/1w from 1h/1d")
    print(f"{'='*60}\n")

    ok = failed = cached = 0
    for sym in symbols:
        info = dm.symbol_info(sym)
        print(f"\n[{sym}] ({info.get('class','?')})")
        for tf in args.tf:
            print(f"  {tf:5}:", end=" ", flush=True)
            df = dm.get(sym, tf, days=args.days, force=args.force)
            if df is not None and len(df) > 10:
                days_cov = (df.index[-1] - df.index[0]).days
                print(f"✅ {len(df):>6} bars | {str(df.index[0])[:10]} → {str(df.index[-1])[:10]} ({days_cov}d)")
                ok += 1
            else:
                print(f"❌ no data")
                failed += 1

    print(f"\n{'='*60}")
    print(f"DONE: ✅ {ok} | ❌ {failed} | Total: {total}")
    print(f"Files: data/SYMBOL_TF_2y.csv")
    print(f"Next:  python3 scripts/m15_smart_backtest.py --all")


if __name__ == "__main__":
    main()
