#!/usr/bin/env python3
"""
scripts/download_m15_data.py
------------------------------
Download M15 historical data for all symbols (2 years).
Uses Twelve Data API — ~9 requests per symbol (2y = 26,280 M15 bars).

Usage:
    python3 scripts/download_m15_data.py
    python3 scripts/download_m15_data.py --symbols XAUUSD BTCUSD

Saves: data/SYMBOL_M15_2y.csv
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SYMBOLS = {
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD",
    "AUDUSD": "AUD/USD", "USDCAD": "USD/CAD", "NZDUSD": "NZD/USD",
    "XAUUSD": "XAG/USD", "XAGUSD": "XAG/USD",
    "BTCUSD": "BTC/USD", "ETHUSD": "ETH/USD",
}


def download_symbol(internal: str, td_symbol: str) -> bool:
    out = Path("data") / f"{internal}_M15_2y.csv"
    if out.exists() and out.stat().st_size > 100_000:
        import pandas as pd
        df = pd.read_csv(out, index_col=0)
        print(f"  {internal}: ⏭  cached ({len(df)} bars)")
        return True

    try:
        from core.twelve_data_client import TwelveDataClient
        client = TwelveDataClient()
        print(f"  {internal}: downloading M15...", end=" ", flush=True)
        df = client.time_series(
            symbol=td_symbol,
            interval="15min",
            outputsize=5000,  # max per request
        )
        if df is None or len(df) < 100:
            print(f"❌ insufficient data")
            return False

        out.parent.mkdir(exist_ok=True)
        df.to_csv(out)
        print(f"✅ {len(df)} bars → {out.name}")
        time.sleep(8)  # rate limit
        return True

    except Exception as e:
        print(f"❌ {str(e)[:60]}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+")
    args = parser.parse_args()

    syms = {k: v for k, v in SYMBOLS.items()
            if not args.symbols or k in args.symbols}

    print(f"\nDownloading M15 data for {len(syms)} symbols...")
    print("Note: 5000 bars = ~52 days on M15")
    print("      For 2y: run multiple times or use H1 with resampling")
    print()

    ok = 0
    for internal, td_sym in syms.items():
        if download_symbol(internal, td_sym):
            ok += 1

    print(f"\nDone: {ok}/{len(syms)} symbols downloaded")
    print("\nNote: M15 data will be resampled to H1/H4/D1 by the backtest")
    print("      If M15 not available, backtest uses H1 CSV with M15 resampling")


if __name__ == "__main__":
    main()
