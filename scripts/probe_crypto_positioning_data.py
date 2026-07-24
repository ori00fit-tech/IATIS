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

PROBE_YEARS_BACK = 6  # Binance BTC/ETH perpetuals have existed since ~2019-2020;
                      # asking generously deep and letting the exchange clamp
                      # to whatever actually exists is how we find the true
                      # floor, not a guess at it — we're measuring what
                      # actually comes back, not assuming this succeeds


def _since_ms(years_back: float) -> int:
    dt = datetime.now(timezone.utc) - timedelta(days=years_back * 365.25)
    return int(dt.timestamp() * 1000)


def probe_funding_rate(exchange, internal_symbol: str, ccxt_symbol: str) -> None:
    """Paginate FORWARD from PROBE_YEARS_BACK, mirroring
    core/ccxt_provider.py::fetch_ccxt's already-working pattern exactly
    (since=<oldest known timestamp>, advance since to last_batch_ts+1,
    stop when a batch comes back shorter than the request limit — that's
    ccxt's own signal for "reached the end"). The first run (2026-07-24,
    a single un-paginated request) only proved ~333 days — that was one
    page, not the true depth."""
    import time as _time

    print(f"\n--- Funding rate: {internal_symbol} ({ccxt_symbol}) ---")
    since = _since_ms(PROBE_YEARS_BACK)
    all_records: list[dict] = []
    requests_made = 0
    max_requests = 15  # ~15 x 1000 = 15,000 records, generous ceiling for 3y of 8h-interval data (~3,285 expected)

    while requests_made < max_requests:
        try:
            batch = exchange.fetchFundingRateHistory(ccxt_symbol, since=since, limit=1000)
        except Exception as exc:
            print(f"  FAILED on request #{requests_made + 1}: {type(exc).__name__}: {exc}")
            break
        requests_made += 1
        if not batch:
            print(f"  (empty batch after {requests_made} request(s) — reached the start of available history)")
            break
        all_records.extend(batch)
        newest_in_batch = batch[-1]["timestamp"]
        print(f"  request #{requests_made}: +{len(batch)} records "
              f"(total {len(all_records)}), newest so far "
              f"{datetime.fromtimestamp(newest_in_batch/1000, tz=timezone.utc).date()}")
        if len(batch) < 1000:
            print("  batch shorter than the request limit — reached the present")
            break
        since = newest_in_batch + 1
        _time.sleep(exchange.rateLimit / 1000)

    if not all_records:
        print("  No funding rate history obtained.")
        return
    first_ts, last_ts = all_records[0]["timestamp"], all_records[-1]["timestamp"]
    span_days = (last_ts - first_ts) / 86_400_000
    print(f"  TOTAL: {len(all_records)} records across {requests_made} request(s)")
    print(f"  oldest: {datetime.fromtimestamp(first_ts/1000, tz=timezone.utc)}")
    print(f"  newest: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)}")
    print(f"  span: ~{span_days:.0f} days")


def probe_open_interest(exchange, internal_symbol: str, ccxt_symbol: str) -> None:
    """The first run (2026-07-24) failed with Binance error -1130
    ('startTime is invalid') when asked for 3 years back — consistent
    with the hypothesis's own prior that public OI endpoints commonly
    cap at recent weeks, but that error alone doesn't establish the
    actual window. Tries a ladder of progressively shorter lookbacks
    (the hypothesis's own 180-day floor first, then narrower) to find
    where Binance actually starts accepting the request."""
    print(f"\n--- Open interest: {internal_symbol} ({ccxt_symbol}) ---")
    ladder_days = [365, 180, 90, 30, 7, 1]
    for days_back in ladder_days:
        since = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp() * 1000)
        try:
            history = exchange.fetchOpenInterestHistory(
                ccxt_symbol, timeframe="1d", since=since, limit=1000,
            )
        except Exception as exc:
            print(f"  {days_back}d back: FAILED — {type(exc).__name__}: {exc}")
            continue
        if not history:
            print(f"  {days_back}d back: request succeeded but returned nothing")
            continue
        first_ts = history[0].get("timestamp")
        last_ts = history[-1].get("timestamp")
        print(f"  {days_back}d back: SUCCESS — {len(history)} records, "
              f"{datetime.fromtimestamp(first_ts/1000, tz=timezone.utc).date()} -> "
              f"{datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).date()}")
        print(f"  sample record: {history[0]}")
        if days_back < 180:
            print("  WARNING: actual OI depth is under 180 days — per H019's "
                  "own decision rule, likely too shallow for a chronological "
                  "TRAIN/TEST split. Consider dropping OI from H019 rather "
                  "than using a short/biased window.")
        return  # found the working window — no need to try shorter ones too
    print("  Every lookback in the ladder failed on this exchange. Try "
          "the other one before concluding OI is unavailable entirely:\n"
          "    python3 -m scripts.probe_crypto_positioning_data --exchange bybit")


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
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", default="binance",
                        help="ccxt exchange id (default: binance). Try "
                             "'bybit' if Binance's OI history is unusable.")
    args = parser.parse_args()

    try:
        import ccxt
    except ImportError:
        print("ccxt not installed — pip install ccxt (should already be in "
              "requirements.txt per core/ccxt_provider.py's usage).")
        return 1

    exchange = getattr(ccxt, args.exchange)(
        {"enableRateLimit": True, "options": {"defaultType": "future"}}
    )

    print("=" * 72)
    print(f"Exchange: {args.exchange}")
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
