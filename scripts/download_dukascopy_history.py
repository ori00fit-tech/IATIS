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

Output: data/{SYMBOL}_H1_dukascopy.csv (H1-only — backtest/runner.py's
find_symbol_csv() only ever globs `{SYMBOL}_H1_*.csv`/`.parquet`; other
timeframes are resampled internally via core/timeframe_sync.py, they
are never separately downloaded/discovered).

NETWORK NOTE: this sandbox's outbound network policy blocks
datafeed.dukascopy.com (confirmed: a direct curl returns 403 through
the agent proxy). This script has NOT been live-verified against the
real feed from this session — the bi5 format/struct layout is well-
documented, stable, third-party-verified prior art, and --probe exists
specifically so the operator can run the real live check on their own
VPS before trusting a bulk download.

Usage:
    python3 -m scripts.download_dukascopy_history --probe EURUSD                 # single hour, no file written
    python3 -m scripts.download_dukascopy_history --symbols EURUSD XAUUSD --years 2
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
DEFAULT_WORKERS = 12
TICK_RECORD_STRUCT = struct.Struct(">iiiff")  # ms_offset, ask_raw, bid_raw, ask_vol, bid_vol
TICK_RECORD_SIZE = TICK_RECORD_STRUCT.size

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

_TF_MINUTES = {"H1": 60, "H4": 240, "D1": 1440}


class DukascopyFetchError(Exception):
    """Raised for a genuine failure (not a 404 — those are silently
    skipped as market-closed hours, see fetch_hour())."""


def _hour_url(instrument: str, dt: datetime) -> str:
    return (
        f"{BASE_URL}/{instrument}/{dt.year:04d}/{dt.month - 1:02d}/"
        f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5"
    )


def fetch_hour(instrument: str, dt: datetime) -> list[tuple[int, float, float, float, float]] | None:
    """One hour's raw ticks as (ms_offset, ask_raw, bid_raw, ask_vol,
    bid_vol) tuples. Returns None (not an error) on 404 — a closed
    market hour (weekend/holiday) is expected, not a fetch failure."""
    url = _hour_url(instrument, dt)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise DukascopyFetchError(f"{url}: request failed: {exc}") from exc

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


def ticks_to_h1_bar(day: datetime, hour_ticks: list[tuple], point_value: int) -> dict | None:
    """Mid-price ((bid+ask)/2) OHLCV for one hour's ticks. None if the
    hour had zero ticks (a legitimately closed market hour)."""
    if not hour_ticks:
        return None
    mids = [
        ((ask_raw / point_value) + (bid_raw / point_value)) / 2.0
        for _, ask_raw, bid_raw, _, _ in hour_ticks
    ]
    volume = sum(ask_vol + bid_vol for _, _, _, ask_vol, bid_vol in hour_ticks)
    return {
        "datetime": day,
        "open": mids[0],
        "high": max(mids),
        "low": min(mids),
        "close": mids[-1],
        "volume": volume,
    }


def download_symbol_hours(
    instrument: str, symbol: str, hours: list[datetime], workers: int = DEFAULT_WORKERS
) -> pd.DataFrame:
    """Bounded-concurrency fetch of every hour in `hours` (one HTTP
    request per hour — Dukascopy has no batch-of-N-bars API, unlike
    MT5/cTrader, hence the ThreadPoolExecutor here instead of the
    sequential-with-sleep pattern those scripts use)."""
    bars: list[dict] = []
    point_value: int | None = None
    failures = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_hour = {pool.submit(fetch_hour, instrument, h): h for h in hours}
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

            bar = ticks_to_h1_bar(hour, ticks, point_value)
            if bar is not None:
                bars.append(bar)

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
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=years * 365.25)
    total_hours = int((end - start).total_seconds() // 3600)
    return [start + timedelta(hours=i) for i in range(total_hours)]


def resample_h1_to(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "H1":
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
    parser.add_argument("--years", type=float, default=2.0)
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
            h1_df = download_symbol_hours(instrument, sym, hours, workers=args.workers)
        except DukascopyFetchError as exc:
            print(f"  {sym}: FAILED — {exc}")
            continue

        if h1_df.empty:
            print(f"  {sym}: FAILED — no bars downloaded")
            continue

        out_df = resample_h1_to(h1_df, args.timeframe)
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
