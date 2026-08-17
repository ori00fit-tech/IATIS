#!/usr/bin/env python3
"""
scripts/download_dukascopy_history.py
----------------------------------------
Free, credential-free historical H1 (default) download from Dukascopy's
public tick-data feed (datafeed.dukascopy.com) — no account, no OAuth,
no broker session, unlike the MT5/cTrader scripts this mirrors the
CLI/manifest conventions of. Used as a cross-check/backfill data source
against the existing provider chains, per CLAUDE.md's data-quality
discipline (more independent sources = more confidence, never a silent
replacement for the live chains in core/data_providers.py).

Format: one LZMA-compressed .bi5 file per instrument per UTC hour, at
https://datafeed.dukascopy.com/datafeed/{INSTRUMENT}/{YEAR}/{MONTH_0IDX:02d}/{DAY:02d}/{HOUR:02d}h_ticks.bi5
Each decompressed record is 20 bytes, big-endian: ms_offset(i4),
ask_raw(i4), bid_raw(i4), ask_vol(f4), bid_vol(f4) — struct ">iiiff".
Weekend/holiday hours 404 (FX market closed) and are skipped, not
treated as a fatal error (one bad hour must never abort a whole
symbol's download — matches this project's established
per-item-isolation pattern, e.g. the Experiment Comparison resilience
fix and run_robustness_suite's per-point isolation).

Rate limiting (2026-08-11, confirmed live on the VPS — a 24-hour probe
window returned HTTP 429 on every single request): fetch_hour() now
retries a 429 response with exponential backoff (mirrors
download_twelve_data_history.py's own _td_get() 429-handling — a
minute-rate-exceeded response there gets the same "wait, then retry"
treatment, not an immediate failure), honoring a numeric Retry-After
header when the server sends one. DEFAULT_WORKERS was also lowered
from 12 to 4 — Dukascopy's public feed appears to rate-limit on
concurrent request volume, not just total throughput, so fewer
simultaneous in-flight requests reduces how often the retry path is
needed in the first place. A 429 that still fails after every retry is
recorded as a real failure (not silently treated as a closed-market
404) — download_symbol_hours()'s existing "N/M hour(s) failed" summary
surfaces it.

A second, real live finding immediately after the 429 fix: with 429s
gone, some fraction of hourly requests instead failed with a plain
requests.RequestException (ConnectTimeoutError specifically — 10/24
hours in one live probe window) — a transient network condition, not a
rate-limit response, so it needed its own retry path rather than being
folded into the 429 handling. fetch_hour() retries these too, with a
separate, shorter exponential backoff (MAX_NETWORK_RETRIES / _NETWORK_
BACKOFF_*), independent of the 429 retry budget.

Price scaling: Dukascopy's raw int32 values are price * a per-instrument
point value (100, 1000, 10000, or 100000 depending on the instrument's
usual decimal precision) with NO published, authoritative table. Rather
than hardcode a point-value per instrument (real risk of an off-by-one-
order-of-magnitude silent corruption for an instrument we guessed
wrong on), this script AUTO-DETECTS the correct point value per file by
trying each candidate against a known-plausible price *range* for that
symbol and picking the one that lands inside it — raising a clear error
if none match, rather than silently writing corrupted prices. This
mirrors core/data_providers.py's own "fail loud rather than silently
misroute" philosophy (see symbol_class()'s docstring).

Mid-price convention: backtesting/backtest_engine.py's REAL_SPREAD_PIPS
confirms the backtest engine applies spread cost separately on top of a
single-price OHLCV series — so tick-to-bar resampling here uses
mid = (bid + ask) / 2, matching every other provider in this codebase.

Timeframe support (2026-08-11, generalized from the original H1-only
build): ticks within every fetched hour are ALWAYS bucketed to M15
granularity first (4 sub-buckets by ms_offset, at most 4 bars/hour) —
M15 is the finest granularity backtest/runner.py's loader ever asks
for, so building it once and coarsening upward for every other
--timeframe is both the minimal common base and mathematically
lossless: OHLC/volume aggregation is associative, so coarsening 4 real
M15 bars into an H1 bar produces byte-identical results to the old
direct-per-hour aggregation this script originally shipped with — H1/
H4/D1 output is unchanged, M15 output is new. Output: data/
{SYMBOL}_{TIMEFRAME}_dukascopy.csv — backtest/runner.py's find_symbol_
csv() only ever loads the H1 file by default, or the M15 file when a
run's own timeframes override explicitly requests M15 as the base
(see backtest/runner.py::physical_load_timeframe); H4/D1 are never
loaded as separate physical files by anything in this codebase (always
derived from H1 via resampling) — this script's own --timeframe H4/D1
output exists for direct inspection/other tooling, not because the
backtest loader ever reads it.

NETWORK NOTE: this sandbox's outbound network policy blocks
datafeed.dukascopy.com (confirmed: a direct curl returns 403 through
the agent proxy). This script has NOT been live-verified against the
real feed from this session — the bi5 format/struct layout is well-
documented, stable, third-party-verified prior art, and --probe exists
specifically so the operator can run the real live check on their own
VPS before trusting a bulk download.

Depth: --years defaults to 10.0 (the operator's own "at least 10 years"
requirement) — Dukascopy's real tick archive genuinely extends this far
back for FX majors/crosses and metals (confirmed by public documentation
of the feed, not verified live from this sandbox); 10 years of hourly
fetches is ~87,600 HTTP requests per symbol, expensive but bounded by
--workers concurrency, same as any --years value.

Usage:
    python3 -m scripts.download_dukascopy_history --probe EURUSD                     # single hour, no file written
    python3 -m scripts.download_dukascopy_history --symbols EURUSD XAUUSD            # 10y H1, default
    python3 -m scripts.download_dukascopy_history --symbols EURUSD --timeframe M15   # 10y M15
    python3 -m scripts.download_dukascopy_history --symbols BTCUSD --years 1 --workers 16
"""
from __future__ import annotations

import argparse
import lzma
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import requests

DATA_DIR = PROJECT_ROOT / "data"

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
REQUEST_TIMEOUT = 15
DEFAULT_WORKERS = 4  # lowered from 12 (2026-08-11) — see module docstring's "Rate limiting" note
TICK_RECORD_STRUCT = struct.Struct(">iiiff")  # ms_offset, ask_raw, bid_raw, ask_vol, bid_vol
TICK_RECORD_SIZE = TICK_RECORD_STRUCT.size

# 429 retry/backoff — see module docstring's "Rate limiting" note.
MAX_429_RETRIES = 5
_429_BACKOFF_BASE_SECONDS = 4.0
_429_BACKOFF_MAX_SECONDS = 60.0

# Network-level retry/backoff (2026-08-11, confirmed live on the VPS
# immediately after the 429 fix landed: with 429s gone, some fraction of
# hourly requests instead failed with a requests.RequestException —
# ConnectTimeoutError specifically, 10/24 hours in one probe window —
# which fetch_hour() previously raised immediately with zero retry. A
# connect timeout to a public feed under concurrent load is exactly the
# kind of transient failure this project's other providers already
# retry (e.g. download_twelve_data_history.py's _td_get()) — distinct
# from the 429 case, so a separate, shorter/faster backoff and its own
# retry budget, not reuse of MAX_429_RETRIES.
MAX_NETWORK_RETRIES = 3
_NETWORK_BACKOFF_BASE_SECONDS = 2.0
_NETWORK_BACKOFF_MAX_SECONDS = 15.0

# Initial-burst stagger (2026-08-11, confirmed live on the VPS: two
# consecutive probe runs, both after the retry fixes above, still saw
# 429s survive every retry — but ONLY on the first `workers` hours
# submitted; everything after succeeded cleanly). download_symbol_hours()
# used to submit every future to the ThreadPoolExecutor in one tight
# dict-comprehension loop, so with max_workers=4 the first 4 HTTP
# connections fired within milliseconds of each other — exactly the
# synchronized burst a rate limiter is built to catch. Once the pool is
# actually running, later requests are already naturally staggered by
# each task's own completion timing, so only the initial fill needs
# spacing out, not the whole run (staggering every submission would
# throttle steady-state throughput on a 10-year/~87,600-hour run for no
# benefit).
_INITIAL_SUBMIT_STAGGER_SECONDS = 0.75

# Candidate point values tried, in this order, against each instrument's
# plausible price range below. Covers every precision Dukascopy actually
# uses across FX/metals/crypto (JPY pairs @ 1000, most FX/metals @
# 100000, some crypto CFDs @ 100 or 1000 depending on price magnitude).
_POINT_VALUE_CANDIDATES = (100, 1_000, 10_000, 100_000)

# Plausible historical price RANGES per internal symbol — deliberately
# wide (not a tight sanity band), used only to pick the correct order-of-
# magnitude scaling, never to reject genuinely volatile real prices.
_PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    # FX majors/minors — non-JPY pairs, historically 0.5-2.2
    "EURUSD": (0.7, 1.8), "GBPUSD": (0.9, 2.2), "USDCHF": (0.6, 1.4),
    "AUDUSD": (0.4, 1.2), "USDCAD": (0.9, 1.7), "NZDUSD": (0.35, 1.0),
    "EURGBP": (0.55, 1.0), "EURCHF": (0.85, 1.7),
    # JPY crosses — historically 60-220
    "USDJPY": (75.0, 165.0), "EURJPY": (85.0, 175.0), "GBPJPY": (100.0, 210.0),
    "AUDJPY": (55.0, 115.0),
    # Metals
    "XAUUSD": (250.0, 4000.0), "XAGUSD": (3.0, 60.0),
    # Crypto (best-effort — high historical range, low confidence mapping)
    "BTCUSD": (100.0, 150_000.0), "ETHUSD": (5.0, 6_000.0),
}

# Dukascopy instrument-code mapping, by confidence tier.
#  - HIGH: 1:1 with our internal symbol name, no separator (FX majors/
#    minors + metals) — verified against Dukascopy's own published
#    instrument list naming convention.
#  - crypto/indices: LOWER confidence, no live verification possible from
#    this sandbox — --probe is the intended way to confirm these before
#    a bulk run. USOIL/stocks/ETF are deliberately excluded (see module
#    docstring / CLAUDE.md's carrier-asset scope).
SYMBOL_MAP: dict[str, str] = {
    # FX majors/minors — high confidence
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY", "USDCHF": "USDCHF",
    "AUDUSD": "AUDUSD", "USDCAD": "USDCAD", "NZDUSD": "NZDUSD", "EURJPY": "EURJPY",
    "GBPJPY": "GBPJPY", "AUDJPY": "AUDJPY", "EURGBP": "EURGBP", "EURCHF": "EURCHF",
    # Metals — high confidence
    "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD",
    # Crypto — best-effort, unverified from this sandbox
    "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
}

DEFAULT_SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD",
]

_TF_MINUTES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}
_BASE_TIMEFRAME = "M15"  # every tick fetch is aggregated to this granularity first; all other timeframes coarsen from it


class DukascopyFetchError(Exception):
    """Raised for a genuine failure (not a 404 — those are silently
    skipped as market-closed hours, see fetch_hour())."""


def _hour_url(instrument: str, dt: datetime) -> str:
    return (
        f"{BASE_URL}/{instrument}/{dt.year:04d}/{dt.month - 1:02d}/"
        f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5"
    )


def _retry_after_seconds(resp: "requests.Response", attempt: int) -> float:
    """Honors a numeric Retry-After header when the server sends one;
    otherwise exponential backoff capped at _429_BACKOFF_MAX_SECONDS."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return min(_429_BACKOFF_MAX_SECONDS, _429_BACKOFF_BASE_SECONDS * (2 ** attempt))


def fetch_hour(instrument: str, dt: datetime) -> list[tuple[int, float, float, float, float]] | None:
    """One hour's raw ticks as (ms_offset, ask_raw, bid_raw, ask_vol,
    bid_vol) tuples. Returns None (not an error) on 404 — a closed
    market hour (weekend/holiday) is expected, not a fetch failure.
    Retries a 429 (rate-limited) response and a transient network
    exception (connect timeout, connection error) each with their own
    backoff/retry budget — see module docstring's "Rate limiting" note —
    before giving up as a real failure."""
    url = _hour_url(instrument, dt)
    resp: "requests.Response | None" = None
    network_attempt = 0
    attempt_429 = 0
    while True:
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            if network_attempt >= MAX_NETWORK_RETRIES:
                raise DukascopyFetchError(
                    f"{url}: request failed after {MAX_NETWORK_RETRIES + 1} attempts: {exc}"
                ) from exc
            time.sleep(min(_NETWORK_BACKOFF_MAX_SECONDS, _NETWORK_BACKOFF_BASE_SECONDS * (2 ** network_attempt)))
            network_attempt += 1
            continue

        if resp.status_code != 429:
            break
        if attempt_429 == MAX_429_RETRIES:
            raise DukascopyFetchError(f"{url}: HTTP 429 (rate limited) after {MAX_429_RETRIES + 1} attempts")
        time.sleep(_retry_after_seconds(resp, attempt_429))
        attempt_429 += 1

    assert resp is not None
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise DukascopyFetchError(f"{url}: HTTP {resp.status_code}")

    if not resp.content:
        return None

    try:
        raw = lzma.decompress(resp.content)
    except lzma.LZMAError as exc:
        raise DukascopyFetchError(f"{url}: LZMA decompress failed: {exc}") from exc

    if len(raw) % TICK_RECORD_SIZE != 0:
        raise DukascopyFetchError(
            f"{url}: decompressed size {len(raw)} not a multiple of "
            f"{TICK_RECORD_SIZE} bytes"
        )

    return [TICK_RECORD_STRUCT.unpack_from(raw, i) for i in range(0, len(raw), TICK_RECORD_SIZE)]


def detect_point_value(raw_prices: list[int], symbol: str) -> int:
    """Try each candidate point value against symbol's plausible price
    range; return the first that fits. Raises on no match rather than
    silently writing corrupted prices — mirrors this project's existing
    fail-loud data-provider convention."""
    if symbol not in _PLAUSIBLE_RANGES:
        raise DukascopyFetchError(
            f"No plausible price range configured for {symbol!r} — cannot "
            f"safely auto-detect its point value. Add an entry to "
            f"_PLAUSIBLE_RANGES before downloading this symbol."
        )
    lo, hi = _PLAUSIBLE_RANGES[symbol]
    sample = raw_prices[: min(50, len(raw_prices))]
    if not sample:
        raise DukascopyFetchError(f"{symbol}: no raw prices to detect a point value from.")

    for point_value in _POINT_VALUE_CANDIDATES:
        scaled = [p / point_value for p in sample]
        if all(lo <= s <= hi for s in scaled):
            return point_value

    raise DukascopyFetchError(
        f"{symbol}: no candidate point value in {_POINT_VALUE_CANDIDATES} "
        f"landed the sampled raw prices inside the plausible range "
        f"[{lo}, {hi}]. Refusing to guess — check the instrument code / "
        f"plausible range, or inspect the raw feed manually."
    )


def ticks_to_m15_bars(hour_start: datetime, hour_ticks: list[tuple], point_value: int) -> list[dict]:
    """Mid-price ((bid+ask)/2) OHLCV bars at M15 granularity from one
    hour's raw ticks — up to 4 bars (one per 15-minute sub-bucket of the
    hour), determined by each tick's own ms_offset (the first element of
    the unpacked tuple, previously discarded entirely by the old H1-only
    single-bucket implementation). A sub-bucket with zero ticks produces
    no bar (a genuinely quiet 15 minutes, not a fabricated one) — matches
    the pre-existing "no bar for a legitimately empty period" convention
    this function's H1-only predecessor already had for whole empty
    hours."""
    if not hour_ticks:
        return []
    buckets: dict[int, list[tuple]] = {}
    for tick in hour_ticks:
        ms_offset = tick[0]
        bucket_idx = min(3, ms_offset // (15 * 60 * 1000))  # clamp: a tick at exactly ms_offset=3_600_000 would be bucket 4, belongs in the last (3rd) sub-bucket instead
        buckets.setdefault(bucket_idx, []).append(tick)

    bars: list[dict] = []
    for bucket_idx in sorted(buckets):
        bucket_ticks = buckets[bucket_idx]
        mids = [
            ((ask_raw / point_value) + (bid_raw / point_value)) / 2.0
            for _, ask_raw, bid_raw, _, _ in bucket_ticks
        ]
        volume = sum(ask_vol + bid_vol for _, _, _, ask_vol, bid_vol in bucket_ticks)
        bars.append({
            "datetime": hour_start + timedelta(minutes=15 * bucket_idx),
            "open": mids[0],
            "high": max(mids),
            "low": min(mids),
            "close": mids[-1],
            "volume": volume,
        })
    return bars


def download_symbol_hours(
    instrument: str, symbol: str, hours: list[datetime], workers: int = DEFAULT_WORKERS
) -> pd.DataFrame:
    """Bounded-concurrency fetch of every hour in `hours` (one HTTP
    request per hour — Dukascopy has no batch-of-N-bars API, unlike
    MT5/cTrader, hence the ThreadPoolExecutor here instead of the
    sequential-with-sleep pattern those scripts use). Staggers only the
    initial fill of the worker pool — see the module-level
    _INITIAL_SUBMIT_STAGGER_SECONDS comment for why."""
    bars: list[dict] = []
    point_value: int | None = None
    failures = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_hour: dict = {}
        for i, h in enumerate(hours):
            if 0 < i < workers:
                time.sleep(_INITIAL_SUBMIT_STAGGER_SECONDS)
            future_to_hour[pool.submit(fetch_hour, instrument, h)] = h
        done = 0
        for future in as_completed(future_to_hour):
            hour = future_to_hour[future]
            done += 1
            try:
                ticks = future.result()
            except DukascopyFetchError as exc:
                failures += 1
                print(f"    [{done}/{len(hours)}] {hour.date()} {hour.hour:02d}h: SKIPPED ({exc})")
                continue

            if not ticks:
                continue  # market closed this hour — not a failure

            if point_value is None:
                point_value = detect_point_value([t[1] for t in ticks], symbol)
                print(f"    detected point value: {point_value} (from {hour.date()} {hour.hour:02d}h)")

            bars.extend(ticks_to_m15_bars(hour, ticks, point_value))

            if done % 200 == 0:
                print(f"    ...{done}/{len(hours)} hours checked, {len(bars)} bars so far")

    if failures:
        print(f"    {failures}/{len(hours)} hour(s) failed (not 404s) and were skipped.")

    if not bars:
        return pd.DataFrame()

    df = pd.DataFrame(bars).set_index("datetime").sort_index()
    df.index = pd.DatetimeIndex(df.index, tz="UTC")
    return df[~df.index.duplicated(keep="first")]


def _hour_range(years: float) -> list[datetime]:
    """`end` is already hour-aligned (minute/second/microsecond zeroed).
    Computing `total_hours` FIRST and deriving `start` from a whole-hour
    timedelta guarantees `start` (and therefore every hour in the
    returned list) stays hour-aligned too, for any `years` value —
    including a fractional one like --probe's years=0.02. The previous
    `start = end - timedelta(days=years * 365.25)` broke this for any
    non-whole-day span (0.02 * 365.25 = 7 days + 7h19m12s exactly), so
    every subsequent hour in the list silently inherited a non-:00
    time-of-day offset. Integer `years` (the real --years flag) was
    never actually affected — 365.25 * N is always a whole multiple of
    6 hours for integer N — but this fix removes the fragility
    entirely rather than relying on that coincidence."""
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    total_hours = int(years * 365.25 * 24)
    start = end - timedelta(hours=total_hours)
    return [start + timedelta(hours=i) for i in range(total_hours)]


def coarsen_bars(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Coarsens M15-base bars up to the requested timeframe. Identity
    when timeframe == _BASE_TIMEFRAME (M15) — every other target
    (H1/H4/D1) is a valid coarsen-only resample, never an upsample,
    since M15 is the finest granularity this script ever builds."""
    if timeframe == _BASE_TIMEFRAME:
        return df
    minutes = _TF_MINUTES[timeframe]
    rule = f"{minutes}min"
    out = df.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open", "high", "low", "close"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", help="single symbol, fetch a handful of recent hours, print result, no file written")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--timeframe", default="H1", choices=sorted(_TF_MINUTES))
    parser.add_argument("--years", type=float, default=10.0)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--force", action="store_true", help="re-download even if the output file exists")
    args = parser.parse_args()

    if args.probe:
        symbol = args.probe.upper()
        if symbol not in SYMBOL_MAP:
            raise SystemExit(f"{symbol}: no Dukascopy instrument mapping in SYMBOL_MAP.")
        instrument = SYMBOL_MAP[symbol]
        probe_hours = _hour_range(years=0.02)[-24:]  # ~last day's worth of hours
        print(f"Probing {symbol} -> Dukascopy instrument {instrument!r}, {len(probe_hours)} hour(s)...")
        df = download_symbol_hours(instrument, symbol, probe_hours, workers=min(args.workers, 8))
        if df.empty:
            print(f"{symbol}: no bars returned in the probe window (all closed hours, or a real fetch problem above).")
        else:
            print(f"{symbol}: {len(df)} bar(s), {df.index[0]} -> {df.index[-1]}")
            print(df.tail(3))
        return

    symbols = [s.upper() for s in (args.symbols or DEFAULT_SYMBOLS)]
    unknown = [s for s in symbols if s not in SYMBOL_MAP]
    if unknown:
        raise SystemExit(f"No Dukascopy instrument mapping for: {unknown}. Known symbols: {sorted(SYMBOL_MAP)}")

    DATA_DIR.mkdir(exist_ok=True)
    csvs: list[str] = []
    t0 = time.monotonic()
    hours = _hour_range(args.years)

    print("=" * 72)
    print(f"Dukascopy history download — {len(symbols)} symbol(s), "
          f"{args.years}y ({len(hours)} hours each) @ {args.timeframe}, "
          f"{args.workers} workers")
    print("=" * 72)

    for idx, sym in enumerate(symbols, 1):
        out_path = DATA_DIR / f"{sym}_{args.timeframe}_dukascopy.csv"
        print(f"[{idx}/{len(symbols)}] {sym} ... ", end="", flush=True)
        if out_path.exists() and not args.force:
            print(f"exists ({out_path}) — skipped, pass --force to re-download")
            csvs.append(str(out_path))
            continue
        print()

        instrument = SYMBOL_MAP[sym]
        try:
            base_df = download_symbol_hours(instrument, sym, hours, workers=args.workers)
        except DukascopyFetchError as exc:
            print(f"  {sym}: FAILED — {exc}")
            continue

        if base_df.empty:
            print(f"  {sym}: FAILED — no bars downloaded")
            continue

        out_df = coarsen_bars(base_df, args.timeframe)
        if out_df.empty:
            print(f"  {sym}: FAILED — resample to {args.timeframe} produced zero bars")
            continue

        print(f"  {sym}: {len(out_df)} bars, {out_df.index[0].date()} -> {out_df.index[-1].date()}")
        out_df.index.name = "datetime"
        out_df.to_csv(out_path)
        csvs.append(str(out_path))
        print(f"  saved: {out_path}  ({time.monotonic() - t0:.0f}s elapsed)")

    if not csvs:
        print("\nNo files downloaded — nothing to manifest.")
        return

    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    manifest = build_manifest(
        kind="dukascopy_history_download",
        config=load_config(),
        params={
            "symbols": symbols, "timeframe": args.timeframe, "years": args.years,
            "workers": args.workers,
        },
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"files_written": csvs},
    )
    outp = write_manifest(manifest, f"dukascopy_history_{time.strftime('%Y%m%d')}")
    print(f"\nManifest: {outp}")


if __name__ == "__main__":
    main()
