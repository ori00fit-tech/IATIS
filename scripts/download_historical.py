#!/usr/bin/env python3
"""
scripts/download_historical.py
--------------------------------
Downloads long historical OHLCV data from Yahoo Finance for all
configured symbols and saves to data/ directory.

Usage:
    python3 scripts/download_historical.py                    # all enabled symbols
    python3 scripts/download_historical.py --symbols EURUSD XAUUSD
    python3 scripts/download_historical.py --years 5 --interval D1
    python3 scripts/download_historical.py --interval H1 --years 2

Yahoo Finance limits:
    D1  → up to 10 years (best for backtesting)
    H1  → up to 2 years
    M15 → up to 60 days
    M5  → up to 60 days

Cost: FREE — no API key needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical data via yfinance")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Symbols to download (default: all enabled in config)")
    parser.add_argument("--interval", default="D1",
                        choices=["M5", "M15", "H1", "D1", "W1"],
                        help="Timeframe (default: D1)")
    parser.add_argument("--years", type=int, default=5,
                        help="Years of history to fetch (default: 5, max: 10 for D1)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing files")
    args = parser.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance not installed. Run: pip install yfinance")

    from core.alt_data_loader import load_from_yfinance, _YF_SYMBOLS
    from core.asset_profiles import PROFILES
    from utils.helpers import load_config

    config = load_config()
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    # Determine symbols
    if args.symbols:
        symbols = [s.upper().replace("/", "") for s in args.symbols]
    else:
        # Use symbols from config twelve_data_symbols list
        td_syms = config.get("data", {}).get("twelve_data_symbols", [])
        symbols = [
            s.get("internal", s.get("symbol", "").replace("/", "")).upper()
            for s in td_syms
            if s.get("enabled", False)
        ]
        if not symbols:
            symbols = ["EURUSD", "GBPUSD", "XAUUSD"]

    print(f"\nDownloading {args.interval} data for {len(symbols)} symbols "
          f"({args.years} years via Yahoo Finance)\n")

    results = {"success": [], "failed": [], "skipped": []}

    for sym in symbols:
        filename = f"{sym}_{args.interval}_{args.years}y.csv"
        filepath = data_dir / filename

        if filepath.exists() and not args.force:
            print(f"  ⏭  {sym}: already exists ({filepath}) — use --force to overwrite")
            results["skipped"].append(sym)
            continue

        if sym not in _YF_SYMBOLS:
            print(f"  ⚠️  {sym}: no Yahoo Finance mapping — skipping")
            results["failed"].append(sym)
            continue

        try:
            print(f"  ⬇  {sym} ({_YF_SYMBOLS[sym]}) @ {args.interval} × {args.years}y ...",
                  end=" ", flush=True)
            df = load_from_yfinance(sym, interval=args.interval, period=f"{min(args.years, 10)}y")

            if df.empty:
                print("❌ empty")
                results["failed"].append(sym)
                continue

            df.to_csv(filepath)
            size_kb = filepath.stat().st_size // 1024
            print(f"✅ {len(df)} bars → {filename} ({size_kb}KB)")
            results["success"].append(sym)

        except Exception as exc:
            print(f"❌ {exc}")
            results["failed"].append(sym)

    print(f"\n{'='*50}")
    print(f"Done: {len(results['success'])} downloaded, "
          f"{len(results['skipped'])} skipped, "
          f"{len(results['failed'])} failed")

    if results["success"]:
        print(f"\nFiles saved in: {data_dir.resolve()}/")
        for sym in results["success"]:
            filename = f"{sym}_{args.interval}_{args.years}y.csv"
            print(f"  data/{filename}")

    if results["failed"]:
        print(f"\nFailed: {results['failed']}")
        print("Tip: FX pairs like EURUSD use '=X' suffix in Yahoo. "
              "Futures like XAUUSD use 'GC=F'. Check _YF_SYMBOLS mapping.")


if __name__ == "__main__":
    main()
