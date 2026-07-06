#!/usr/bin/env python3
"""
scripts/fetch_m15_twelvedata.py
---------------------------------
Fetch REAL, deep M15 OHLCV from Twelve Data by paginating `end_date`
backwards. H008/H008b is an M15 hypothesis; the only M15 previously
available was H1-resampled (hourly bars mislabelled M15), which cannot
carry an intra-hour sweep→BOS→FVG test. This pulls genuine 15-minute bars.

Free tier: 5000 bars/request (~52 days), ~8 req/min. ~14 pages ≈ 2 years.

    python3 scripts/fetch_m15_twelvedata.py --symbols EUR/USD,GBP/USD,XAU/USD --pages 14

Writes data/{SYMBOL}_M15_real.csv (SYMBOL stripped of '/'), oldest first,
columns datetime,open,high,low,close,volume.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
import urllib.request
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv()
import os  # noqa: E402

DATA = PROJECT_ROOT / "data"
API = "https://api.twelvedata.com/time_series"


def _fetch(symbol: str, key: str, end: str | None) -> list[dict]:
    params = {"symbol": symbol, "interval": "15min", "outputsize": 5000,
              "apikey": key, "order": "DESC"}
    if end:
        params["end_date"] = end
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=40) as r:
        d = json.load(r)
    if d.get("status") == "error":
        raise RuntimeError(d.get("message", "unknown TD error"))
    return d.get("values", []) or []


def fetch_deep(symbol: str, key: str, pages: int, pause: float) -> list[dict]:
    """Page `end_date` backwards; return bars oldest-first, de-duplicated."""
    seen: dict[str, dict] = {}
    end: str | None = None
    for p in range(pages):
        try:
            vals = _fetch(symbol, key, end)
        except Exception as exc:
            print(f"  page {p+1}: {exc}")
            break
        if not vals:
            print(f"  page {p+1}: empty — reached start of history")
            break
        for v in vals:
            seen[v["datetime"]] = v
        oldest = vals[-1]["datetime"]
        print(f"  page {p+1}/{pages}: +{len(vals)} → {len(seen)} total (oldest {oldest})")
        # next page ends just before the oldest bar we have
        end = oldest
        if p < pages - 1:
            time.sleep(pause)
    rows = sorted(seen.values(), key=lambda v: v["datetime"])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="EUR/USD,GBP/USD,XAU/USD")
    ap.add_argument("--pages", type=int, default=14, help="~14 pages ≈ 2 years")
    ap.add_argument("--pause", type=float, default=8.0, help="seconds between requests (free tier ~8/min)")
    args = ap.parse_args()

    key = os.environ.get("TWELVE_DATA_API_KEY")
    if not key:
        sys.exit("TWELVE_DATA_API_KEY not set")
    DATA.mkdir(exist_ok=True)

    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        print(f"\n{sym}: fetching up to {args.pages} pages of real M15…")
        rows = fetch_deep(sym, key, args.pages, args.pause)
        if not rows:
            print(f"  {sym}: no data")
            continue
        out = DATA / f"{sym.replace('/', '')}_M15_real.csv"
        import csv
        with out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["datetime", "open", "high", "low", "close", "volume"])
            for v in rows:
                w.writerow([v["datetime"], v["open"], v["high"], v["low"],
                            v["close"], v.get("volume", 0)])
        print(f"  ✅ {out.name}: {len(rows)} bars "
              f"({rows[0]['datetime']} → {rows[-1]['datetime']})")


if __name__ == "__main__":
    main()
