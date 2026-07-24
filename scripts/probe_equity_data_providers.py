#!/usr/bin/env python3
"""
scripts/probe_equity_data_providers.py
------------------------------------------
Feasibility probe for the new stocks/etf provider chains (config.yaml,
core/data_providers.py::_fetch_alpha_vantage_equity/_fetch_finnhub_equity,
built 2026-07-24 as Phase 1 of the Research Workspace redesign). This
sandbox has no network egress to twelvedata.com/alphavantage.co/
finnhub.io, so none of the following was verified against the real APIs
before this script existed — this is the same "not tested against a
live connection" gap as scripts/probe_crypto_positioning_data.py, and
the same fix: run this on the VPS before trusting the new chain for
anything beyond a syntax-level check.

Specifically unverified, in order of risk:
  1. Finnhub's /stock/candle free-tier access. Finnhub has, at various
     points, restricted historical stock candles to paid plans while
     keeping forex/crypto candles free — the module's own docstring
     claim ("Free tier supports... US stocks") predates this endpoint
     ever being called and has not been re-checked.
  2. Alpha Vantage's TIME_SERIES_DAILY/WEEKLY/INTRADAY for equities.
     These are AV's original, documented-free core endpoints (unlike
     FX_INTRADAY, already confirmed premium-gated) — expected to work,
     not confirmed.
  3. Alpaca's /v2/stocks/{symbol}/bars (added 2026-07-24, operator
     request). Alpaca was already integrated for crypto only — this
     project's own account already has ALPACA_API_KEY/SECRET, so auth
     isn't the risk; the unverified part is whether the free tier's
     historical depth/rate limit for STOCK bars specifically matches
     the crypto path's (Alpaca has, at points in its history, gated
     real-time stock data behind IEX-only vs. full-SIP tiers — whether
     that also constrains historical bars on the free tier is unchecked).
  4. Twelve Data equity coverage. The client is fully generic (verified
     by code inspection, not a live call) and Twelve Data's own product
     description covers US equities on the free tier — the lowest-risk
     of the four, still unconfirmed by an actual request.

Futures (--probe-futures): NO fetch code exists yet for this asset
class, deliberately — unlike stocks/ETFs, no provider chain here has been
even provisionally verified to serve individual futures contracts on a
free tier, and building a fetch path on an unverified assumption would
repeat exactly the mistake this project's "always verify experimentally"
discipline exists to prevent (see H019's OI investigation). This flag
only checks whether Twelve Data's generic time_series() returns anything
usable for a sample continuous-contract-style symbol; a real answer
(and any fetch implementation) waits for this result.

Usage (VPS):
    python3 -m scripts.probe_equity_data_providers
    python3 -m scripts.probe_equity_data_providers --symbols AAPL SPY
    python3 -m scripts.probe_equity_data_providers --probe-futures
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    # Explicit path — see scripts/collect_marketaux_sentiment.py and
    # scripts/download_ctrader_fx_history.py for why bare load_dotenv()
    # is unreliable depending on how this script is invoked. Without this,
    # every provider in this probe silently reports "API_KEY not set" even
    # when .env has real keys (observed on the VPS, 2026-07-24) — this
    # script forgot the load call every other credentialed script here has.
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
except PermissionError:
    _env_path = PROJECT_ROOT / ".env"
    raise SystemExit(
        f"Permission denied reading {_env_path} as the current user. "
        f".env is owned by the iatis service user (600) — run this as "
        f"that user instead:\n"
        f"  sudo -u iatis {sys.executable} -m scripts.probe_equity_data_providers ..."
    )

DEFAULT_SYMBOLS = ["AAPL", "NVDA", "SPY", "QQQ"]
# Twelve Data's own docs list individual commodity futures (e.g. corn,
# cocoa) but do not state free-tier availability explicitly — these are
# guesses at plausible Twelve Data futures symbol formats, not confirmed
# to exist or resolve to anything.
FUTURES_PROBE_SYMBOLS = ["CL1", "ES1", "GC1"]


def probe_alpaca(symbol: str) -> None:
    from core.data_providers import _fetch_alpaca_equity
    try:
        df = _fetch_alpaca_equity(symbol, "D1", outputsize=30)
        span = f"{df.index[0]} -> {df.index[-1]}" if len(df) else "empty"
        print(f"  [alpaca]      {symbol}: OK, {len(df)} bars ({span})")
    except Exception as exc:
        print(f"  [alpaca]      {symbol}: FAILED — {exc}")


def probe_twelve_data(symbol: str) -> None:
    from core.data_providers import _fetch_twelve_data
    try:
        df = _fetch_twelve_data(symbol, "D1", outputsize=30, use_cache=False)
        span = f"{df.index[0]} -> {df.index[-1]}" if len(df) else "empty"
        print(f"  [twelve_data] {symbol}: OK, {len(df)} bars ({span})")
    except Exception as exc:
        print(f"  [twelve_data] {symbol}: FAILED — {exc}")


def probe_finnhub(symbol: str) -> None:
    from core.data_providers import _fetch_finnhub_equity
    try:
        df = _fetch_finnhub_equity(symbol, "D1", outputsize=30)
        span = f"{df.index[0]} -> {df.index[-1]}" if len(df) else "empty"
        print(f"  [finnhub]     {symbol}: OK, {len(df)} bars ({span})")
    except Exception as exc:
        print(f"  [finnhub]     {symbol}: FAILED — {exc}")


def probe_alpha_vantage(symbol: str) -> None:
    from core.data_providers import _fetch_alpha_vantage_equity
    try:
        df = _fetch_alpha_vantage_equity(symbol, "D1", outputsize=30)
        span = f"{df.index[0]} -> {df.index[-1]}" if len(df) else "empty"
        print(f"  [alpha_vant.] {symbol}: OK, {len(df)} bars ({span})")
    except Exception as exc:
        print(f"  [alpha_vant.] {symbol}: FAILED — {exc}")


def probe_futures(symbol: str) -> None:
    """Twelve Data ONLY — no other integrated provider claims futures
    coverage at all. Purely diagnostic; a FAILED result here does not
    rule out futures support, it just means this guessed symbol/format
    didn't resolve (Twelve Data's real futures symbol convention, if any
    exists on the free tier, is unconfirmed)."""
    from core.data_providers import _fetch_twelve_data
    try:
        df = _fetch_twelve_data(symbol, "D1", outputsize=30, use_cache=False)
        span = f"{df.index[0]} -> {df.index[-1]}" if len(df) else "empty"
        print(f"  [twelve_data] {symbol}: OK, {len(df)} bars ({span})")
    except Exception as exc:
        print(f"  [twelve_data] {symbol}: FAILED — {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--probe-futures", action="store_true",
                     help="also probe guessed futures symbols via Twelve Data "
                          "(no fetch implementation exists yet — diagnostic only)")
    args = ap.parse_args()

    print("=" * 72)
    print("Equity/ETF data provider probe (H001-independent infrastructure)")
    print(f"Symbols: {args.symbols}")
    print("=" * 72)

    for symbol in args.symbols:
        print(f"\n{symbol}:")
        probe_alpaca(symbol)
        probe_twelve_data(symbol)
        probe_finnhub(symbol)
        probe_alpha_vantage(symbol)

    print("\n" + "=" * 72)
    print("Read each FAILED line's error message — it tells you whether the "
          "provider needs a different plan, a different symbol format, or "
          "whether the endpoint genuinely doesn't serve this asset class on "
          "the free tier. Do not assume OK on one symbol means OK for all "
          "(rate limits, plan gating can be symbol- or volume-specific).")

    if args.probe_futures:
        print("\n" + "=" * 72)
        print("FUTURES probe (diagnostic only, guessed symbol formats, "
              "no fetch implementation built on this yet)")
        print(f"Symbols: {FUTURES_PROBE_SYMBOLS}")
        print("=" * 72)
        for symbol in FUTURES_PROBE_SYMBOLS:
            print(f"\n{symbol}:")
            probe_futures(symbol)

    return 0


if __name__ == "__main__":
    sys.exit(main())
