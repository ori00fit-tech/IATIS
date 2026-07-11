#!/usr/bin/env python3
"""
Download 1-year H1 data for all 20 IATIS symbols using requests directly.
Yahoo Finance v8 chart API — no yfinance library needed.
"""
from __future__ import annotations
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.download_all_symbols import ALL_SYMBOLS

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
})
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"


def fetch_yf_chart(ticker: str, years: int = 1, interval: str = "1h") -> pd.DataFrame:
    """Fetch chart data from Yahoo Finance v8 API."""
    range_map = {1: "1y", 2: "2y", 3: "3y", 5: "5y"}
    range_str = range_map.get(years, f"{years}y")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"range": range_str, "interval": interval, "includeAdjustedClose": "true"}

    resp = SESSION.get(url, params=params, timeout=30, verify=CA_BUNDLE)
    resp.raise_for_status()
    data = resp.json()

    result = data.get("chart", {}).get("result")
    if not result:
        err = data.get("chart", {}).get("error", {})
        raise ValueError(f"No data: {err}")

    chart = result[0]
    timestamps = chart["timestamp"]
    quote = chart["indicators"]["quote"][0]
    adjclose_list = chart.get("indicators", {}).get("adjclose", [{}])
    adjclose = adjclose_list[0].get("adjclose", quote["close"]) if adjclose_list else quote["close"]

    df = pd.DataFrame({
        "open":   quote["open"],
        "high":   quote["high"],
        "low":    quote["low"],
        "close":  adjclose if adjclose else quote["close"],
        "volume": quote.get("volume", [0] * len(timestamps)),
    }, index=pd.to_datetime(timestamps, unit="s", utc=True))
    df.index.name = "datetime"
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--symbols", nargs="+", default=None)
    args = parser.parse_args()

    symbols = {s: t for s, t in ALL_SYMBOLS.items()
               if args.symbols is None or s in (args.symbols or [])}

    DATA_DIR.mkdir(exist_ok=True)
    interval = "1h" if args.years <= 2 else "1d"
    suffix = f"H1_{args.years}y" if args.years <= 2 else f"D1_{args.years}y"

    print(f"\n{'='*60}")
    print(f"IATIS Data Download — Yahoo Finance (requests-based)")
    print(f"Symbols: {len(symbols)} | Period: {args.years}yr | Interval: {interval}")
    print(f"{'='*60}\n")

    ok, fail = 0, 0
    for sym, ticker in symbols.items():
        out_path = DATA_DIR / f"{sym}_{suffix}.csv"
        if out_path.exists() and not args.force:
            df = pd.read_csv(out_path, index_col=0)
            if len(df) >= 100:
                print(f"  ✓ {sym:<10} {len(df):>5} bars (cached)")
                ok += 1
                continue

        print(f"  ↓ {sym:<10} ({ticker}) ... ", end="", flush=True)
        try:
            df = fetch_yf_chart(ticker, years=args.years, interval=interval)
            df.to_csv(out_path)
            print(f"{len(df)} bars ✓")
            ok += 1
            time.sleep(0.4)
        except Exception as e:
            print(f"FAILED: {str(e)[:80]}")
            fail += 1

    print(f"\nDone: {ok} downloaded, {fail} failed")
    print(f"Files in {DATA_DIR}:")
    for f in sorted(DATA_DIR.glob("*.csv")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
