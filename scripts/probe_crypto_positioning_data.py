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


def _paginate_forward(fetch_fn, since_start: int, label: str,
                      max_requests: int = 60) -> list[dict]:
    """Generic forward pagination, robust to exchanges whose actual
    per-request page size is smaller than the `limit` requested (found
    2026-07-24: Bybit silently caps at 200 records/request regardless of
    limit=1000, which made the original `len(batch) < 1000` stop
    condition falsely conclude 'reached the present' after just one page
    — comparing against the REQUESTED limit rather than tracking actual
    progress undercounts depth on any exchange with a smaller true cap.
    Stops only on: an empty batch, no forward progress (safety net
    against an infinite loop), or the batch's newest timestamp reaching
    within 1 day of now — never on batch size."""
    import time as _time

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since = since_start
    all_records: list[dict] = []
    requests_made = 0

    while requests_made < max_requests:
        try:
            batch = fetch_fn(since)
        except Exception as exc:
            print(f"  FAILED on request #{requests_made + 1}: {type(exc).__name__}: {exc}")
            break
        requests_made += 1
        if not batch:
            print(f"  (empty batch after {requests_made} request(s) — reached the start of available history)")
            break
        all_records.extend(batch)
        newest_in_batch = batch[-1]["timestamp"]
        print(f"  request #{requests_made}: +{len(batch)} {label} "
              f"(total {len(all_records)}), newest so far "
              f"{datetime.fromtimestamp(newest_in_batch/1000, tz=timezone.utc).date()}")
        if newest_in_batch >= now_ms - 86_400_000:
            print("  reached the present")
            break
        if newest_in_batch <= since:
            print("  no forward progress on the timestamp cursor — stopping "
                  "(exchange may not be advancing with `since`)")
            break
        since = newest_in_batch + 1
        _time.sleep(0.3)  # conservative fixed pace — this is a probe, not a
                          # production downloader; correctness over speed

    return all_records


def probe_funding_rate(exchange, internal_symbol: str, ccxt_symbol: str) -> None:
    print(f"\n--- Funding rate: {internal_symbol} ({ccxt_symbol}) ---")
    since_start = _since_ms(PROBE_YEARS_BACK)
    all_records = _paginate_forward(
        lambda since: exchange.fetchFundingRateHistory(ccxt_symbol, since=since, limit=1000),
        since_start, "records",
    )

    if not all_records:
        print("  No funding rate history obtained.")
        return
    first_ts, last_ts = all_records[0]["timestamp"], all_records[-1]["timestamp"]
    span_days = (last_ts - first_ts) / 86_400_000
    print(f"  TOTAL: {len(all_records)} records across the run")
    print(f"  oldest: {datetime.fromtimestamp(first_ts/1000, tz=timezone.utc)}")
    print(f"  newest: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)}")
    print(f"  span: ~{span_days:.0f} days")


def probe_open_interest(exchange, internal_symbol: str, ccxt_symbol: str) -> None:
    """Two-step: (1) a ladder of progressively shorter lookbacks finds the
    widest `since` the exchange accepts without erroring (Binance rejects
    anything past ~7d with error -1130; Bybit accepted 365d in the first
    run) — that alone does NOT establish true depth, since a request can
    succeed but be silently truncated to a small page (found 2026-07-24:
    Bybit returned only 200 records for a 365d-back request, and it was
    unclear whether that's the real floor or just one page). (2) once a
    working `since` is found, paginate forward from it with the same
    robust helper funding rate uses, to get the actual full depth rather
    than trusting a single response."""
    print(f"\n--- Open interest: {internal_symbol} ({ccxt_symbol}) ---")
    ladder_days = [365, 180, 90, 30, 7, 1]
    working_since = None
    for days_back in ladder_days:
        since = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp() * 1000)
        try:
            probe_resp = exchange.fetchOpenInterestHistory(
                ccxt_symbol, timeframe="1d", since=since, limit=1000,
            )
        except Exception as exc:
            print(f"  {days_back}d back: FAILED — {type(exc).__name__}: {exc}")
            continue
        if not probe_resp:
            print(f"  {days_back}d back: request succeeded but returned nothing")
            continue
        print(f"  {days_back}d back: SUCCESS (widest accepted lookback) — "
              f"{len(probe_resp)} records in this one response; paginating "
              f"from here to find the true depth...")
        working_since = since
        break

    if working_since is None:
        print("  Every lookback in the ladder failed on this exchange. Try "
              "the other one before concluding OI is unavailable entirely:\n"
              "    python3 -m scripts.probe_crypto_positioning_data --exchange bybit")
        return

    all_records = _paginate_forward(
        lambda since: exchange.fetchOpenInterestHistory(ccxt_symbol, timeframe="1d", since=since, limit=1000),
        working_since, "records",
    )
    if not all_records:
        print("  Paginated depth check returned nothing (unexpected — the "
              "ladder probe above got data). Investigate before trusting "
              "either result.")
        return
    first_ts, last_ts = all_records[0]["timestamp"], all_records[-1]["timestamp"]
    span_days = (last_ts - first_ts) / 86_400_000
    print(f"  TOTAL: {len(all_records)} records across the run")
    print(f"  oldest: {datetime.fromtimestamp(first_ts/1000, tz=timezone.utc)}")
    print(f"  newest: {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc)}")
    print(f"  span: ~{span_days:.0f} days")
    if span_days < 180:
        print("  WARNING: actual OI depth is under 180 days — per H019's "
              "own decision rule, likely too shallow for a chronological "
              "TRAIN/TEST split. Consider dropping OI from H019 rather "
              "than using a short/biased window.")


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
