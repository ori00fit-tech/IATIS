#!/usr/bin/env python3
"""
scripts/probe_crypto_positioning_data.py
--------------------------------------------
H019 feasibility probe (research/results/registry.json — "Crypto positioning/
sentiment as an internal confluence modulator"). The hypothesis's own
registered data_sources notes are explicit that this must run BEFORE any
H019 pipeline code is written:

  - Funding rate (Binance perpetuals via ccxt fetchFundingRateHistory):
    claimed "free, multi-year depth, no auth required" but not actually
    verified against a real request.
  - Open interest (ccxt fetchOpenInterestHistory): explicitly flagged
    UNVERIFIED — "free tier historical depth is likely shallow... MUST be
    validated as a standalone data-availability check before the full
    study starts; if depth is insufficient for a chronological OOS split,
    drop OI from this hypothesis rather than substitute a shorter/biased
    window." A partial check from the audit SANDBOX (2026-07-11) hit a
    geo-block (HTTP 451/403) — inconclusive, explicitly deferred to the VPS.
  - Fear & Greed Index (alternative.me): reachability was already confirmed
    2026-07-11; this script re-checks it for completeness in one place.

This script ONLY answers "how much history is actually available," for
BTCUSD/ETHUSD. It does not build any backtest, engine wiring, or A/B
harness — per H019's own notes, that work only starts once this question
is answered. NOT tested against a live connection by the agent that wrote
it (ccxt is not installed in that sandbox, and the funding-rate/OI/geo
questions can only be answered from network egress the VPS actually has) —
verify the output makes sense before trusting it.

Usage (VPS):
    python3 -m scripts.probe_crypto_positioning_data
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ccxt unified symbols for Binance USDT-margined perpetual futures — NOT
# the spot symbols core/ccxt_provider.py's CCXT_SYMBOLS uses (BTC/USDT).
# Funding rate / open interest only exist on the derivatives market.
PERPETUAL_SYMBOLS = {"BTCUSD": "BTC/USDT:USDT", "ETHUSD": "ETH/USDT:USDT"}

PROBE_YEARS_BACK = 3  # how far back to ask for — we're measuring what
                      # actually comes back, not assuming this succeeds


def _since_ms(years_back: float) -> int:
    dt = datetime.now(timezone.utc) - timedelta(days=years_back * 365.25)
    return int(dt.timestamp() * 1000)


def probe_funding_rate(exchange, internal_symbol: str, ccxt_symbol: str) -> None:
    print(f"\n--- Funding rate: {internal_symbol} ({ccxt_symbol}) ---")
    try:
        since = _since_ms(PROBE_YEARS_BACK)
        history = exchange.fetchFundingRateHistory(ccxt_symbol, since=since, limit=1000)
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return
    if not history:
        print("  Empty result — no funding rate history returned.")
        return
    first_ts = history[0].get("timestamp")
    last_ts = history[-1].get("timestamp")
    span_days = (last_ts - first_ts) / 86_400_000 if first_ts and last_ts else None
    print(f"  {len(history)} records returned "
          f"(requested up to {PROBE_YEARS_BACK}y back — ccxt/Binance may cap "
          f"a single request; this is what came back, not necessarily the "
          f"true depth without pagination)")
    if first_ts:
        print(f"  oldest: {datetime.fromtimestamp(first_ts/1000, tz=timezone.utc)}")
    if last_ts:
        print(f"  newest: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)}")
    if span_days is not None:
        print(f"  span: ~{span_days:.0f} days")
    print(f"  sample record: {history[0]}")


def probe_open_interest(exchange, internal_symbol: str, ccxt_symbol: str) -> None:
    print(f"\n--- Open interest: {internal_symbol} ({ccxt_symbol}) ---")
    try:
        since = _since_ms(PROBE_YEARS_BACK)
        history = exchange.fetchOpenInterestHistory(
            ccxt_symbol, timeframe="1d", since=since, limit=1000
        )
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        print("  If this is a NotSupported/AttributeError, this exchange's "
              "ccxt implementation doesn't offer OI history the way this "
              "probe assumed — try Bybit as a fallback (exchange_id='bybit') "
              "before concluding OI is unavailable entirely.")
        return
    if not history:
        print("  Empty result — no open interest history returned.")
        return
    first_ts = history[0].get("timestamp")
    last_ts = history[-1].get("timestamp")
    span_days = (last_ts - first_ts) / 86_400_000 if first_ts and last_ts else None
    print(f"  {len(history)} records returned "
          f"(requested up to {PROBE_YEARS_BACK}y back)")
    if first_ts:
        print(f"  oldest: {datetime.fromtimestamp(first_ts/1000, tz=timezone.utc)}")
    if last_ts:
        print(f"  newest: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)}")
    if span_days is not None:
        print(f"  span: ~{span_days:.0f} days")
        if span_days < 180:
            print("  WARNING: under 6 months of history — per H019's own "
                  "decision rule, this is likely too shallow for a "
                  "chronological TRAIN/TEST split. Consider dropping OI "
                  "from H019 rather than using a short/biased window.")
    print(f"  sample record: {history[0]}")


def probe_fear_greed() -> None:
    print("\n--- Fear & Greed Index (alternative.me) ---")
    try:
        import requests
        resp = requests.get("https://api.alternative.me/fng/", params={"limit": 0}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return
    rows = data.get("data", [])
    if not rows:
        print("  Empty response.")
        return
    print(f"  {len(rows)} daily records returned")
    oldest = rows[-1] if rows else None
    newest = rows[0] if rows else None
    if oldest:
        print(f"  oldest: {oldest.get('timestamp')} value={oldest.get('value')} "
              f"({oldest.get('value_classification')})")
    if newest:
        print(f"  newest: {newest.get('timestamp')} value={newest.get('value')} "
              f"({newest.get('value_classification')})")


def main() -> int:
    try:
        import ccxt
    except ImportError:
        print("ccxt not installed — pip install ccxt (should already be in "
              "requirements.txt per core/ccxt_provider.py's usage).")
        return 1

    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})

    print("=" * 72)
    print("H019 data-feasibility probe — funding rate / open interest / "
          "Fear & Greed")
    print("Answers a data-availability question only. No pipeline/engine "
          "code runs from this script.")
    print("=" * 72)

    for internal_symbol, ccxt_symbol in PERPETUAL_SYMBOLS.items():
        probe_funding_rate(exchange, internal_symbol, ccxt_symbol)
        probe_open_interest(exchange, internal_symbol, ccxt_symbol)

    probe_fear_greed()

    print("\n" + "=" * 72)
    print("Next: record what actually came back in H019's registry entry "
          "(research/results/registry.json) before writing any A/B harness. "
          "If OI depth is insufficient, H019 proceeds WITHOUT it per the "
          "hypothesis's own pre-registered fallback, not with a shortened "
          "or cherry-picked window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
