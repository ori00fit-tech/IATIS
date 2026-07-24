#!/usr/bin/env python3
"""
scripts/download_crypto_positioning_history.py
---------------------------------------------------
H019 data download (research/results/registry.json — "Crypto positioning/
sentiment as an internal confluence modulator"). Feasibility fully resolved
2026-07-24 (three exchanges tested via scripts/probe_crypto_positioning_data.py):
funding rate is deep and clean on Binance (~6 years), Fear & Greed is deep
and clean on alternative.me (since 2018), open interest is DROPPED per the
hypothesis's own pre-registered fallback (no exchange gave more than ~200
days — see registry.json's feasibility_probe field for the full record).

Downloads BOTH remaining legs to local CSVs for backtesting:
  data/{SYMBOL}_funding_rate.csv   (BTCUSD, ETHUSD)
  data/fear_greed_index.csv        (shared across both symbols — one
                                     market-wide daily series, not per-symbol)

IMPORTANT — this script saves RAW historical records with their real
settlement/publish timestamps. It does NOT do any causal alignment to
decision bars. H019's own registered notes require a strict look-ahead
guard: a backtest may only use a funding-rate record whose settlement
timestamp is strictly BEFORE the decision bar's close (Binance settles on
a fixed 8h schedule — 00:00/08:00/16:00 UTC), and a Fear & Greed value
whose timestamp is strictly before the decision bar's close. That
alignment is the A/B harness's job, at the point it reads these CSVs —
get it wrong there and the guard is violated regardless of how clean this
download is (same class of bug the trade-management "+100%" mirage and
the raw H008 BOS+FVG result both turned out to be, per H019's own notes).

Usage (VPS):
    python3 -m scripts.download_crypto_positioning_history
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
except PermissionError:
    raise SystemExit(
        f"Permission denied reading {PROJECT_ROOT / '.env'} as the current "
        f"user. Run this as the iatis service user:\n"
        f"  sudo -u iatis {sys.executable} -m scripts.download_crypto_positioning_history"
    )

DATA_DIR = PROJECT_ROOT / "data"
PERPETUAL_SYMBOLS = {"BTCUSD": "BTC/USDT:USDT", "ETHUSD": "ETH/USDT:USDT"}
PROBE_YEARS_BACK = 6  # confirmed feasible depth, scripts/probe_crypto_positioning_data.py 2026-07-24


def _since_ms(years_back: float) -> int:
    from datetime import timedelta
    dt = datetime.now(timezone.utc) - timedelta(days=years_back * 365.25)
    return int(dt.timestamp() * 1000)


def download_funding_rate(exchange, internal_symbol: str, ccxt_symbol: str):
    """Reuses the exact pagination discipline validated on the VPS
    2026-07-24 (scripts/probe_crypto_positioning_data.py::_paginate_forward) —
    that fix is why this script imports it rather than re-implementing
    pagination a third time in this codebase."""
    import pandas as pd

    from scripts.probe_crypto_positioning_data import _paginate_forward

    since_start = _since_ms(PROBE_YEARS_BACK)
    records = _paginate_forward(
        lambda since: exchange.fetchFundingRateHistory(ccxt_symbol, since=since, limit=1000),
        since_start, "records",
    )
    if not records:
        return None
    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime")[["fundingRate", "timestamp"]].sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.rename(columns={"fundingRate": "funding_rate", "timestamp": "settlement_ts_ms"})
    return df


def download_fear_greed():
    import pandas as pd
    import requests

    resp = requests.get("https://api.alternative.me/fng/", params={"limit": 0}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("data", [])
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["timestamp"] = df["timestamp"].astype(int)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df["value"] = df["value"].astype(int)
    df = df.set_index("datetime")[["value", "value_classification", "timestamp"]].sort_index()
    df = df.rename(columns={"timestamp": "published_ts_s"})
    return df[~df.index.duplicated(keep="first")]


def main() -> int:
    try:
        import ccxt
    except ImportError:
        print("ccxt not installed — pip install ccxt.")
        return 1

    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    DATA_DIR.mkdir(exist_ok=True)
    t0 = time.monotonic()

    print("=" * 72)
    print("H019 data download — funding rate (Binance) + Fear & Greed")
    print("=" * 72)

    for internal_symbol, ccxt_symbol in PERPETUAL_SYMBOLS.items():
        print(f"\n{internal_symbol} funding rate...")
        df = download_funding_rate(exchange, internal_symbol, ccxt_symbol)
        if df is None or df.empty:
            print(f"  FAILED — no data for {internal_symbol}")
            continue
        out = DATA_DIR / f"{internal_symbol}_funding_rate.csv"
        df.to_csv(out)
        print(f"  {len(df)} records, {df.index[0]} -> {df.index[-1]}")
        print(f"  saved: {out}  ({time.monotonic() - t0:.0f}s elapsed)")

    print("\nFear & Greed Index...")
    fg = download_fear_greed()
    if fg is None or fg.empty:
        print("  FAILED — no data")
    else:
        out = DATA_DIR / "fear_greed_index.csv"
        fg.to_csv(out)
        print(f"  {len(fg)} records, {fg.index[0]} -> {fg.index[-1]}")
        print(f"  saved: {out}")

    print(f"\nDone in {time.monotonic() - t0:.0f}s.")
    print("Next: build the A/B harness (main.run_pipeline()-based, per the "
          "2026-07-24 operator decision — NOT backtesting/backtest_engine.py, "
          "which is missing meta_decision) that reads these CSVs with a "
          "strict causal look-ahead guard, per H019's own registered notes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
