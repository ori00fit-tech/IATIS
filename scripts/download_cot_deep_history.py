#!/usr/bin/env python3
"""
scripts/download_cot_deep_history.py
--------------------------------------
H012 (research/results/registry.json) data-acquisition gap, identified in
research/results/data_feasibility_report.md Part B.2: scripts/download_cot.py
only ever fetches CFTC's CURRENT-WEEK file (deafut.txt) and keeps a
12-week rolling cache — nowhere near enough for a chronological OOS
split. This script closes that gap using the SAME free, official CFTC
source, just a different (yearly archive) endpoint.

Source: CFTC's own historical compressed Legacy Futures-Only report,
one zip per year (https://www.cftc.gov/files/dea/history/deacot{YEAR}.zip
per the documented URL pattern used by the community `cot_reports`
library — https://github.com/NDelventhal/cot_reports). Free, no key, no
rate limit contract published. Each zip contains a single text file
(observed name "annual.txt" in that library's source; this script does
NOT hardcode that name — it takes whichever single .txt member the zip
actually contains, since exact naming has not been experimentally
verified across all years from this sandbox — see CAVEAT below) holding
one row PER CONTRACT PER WEEK for that calendar year, in the same column
layout as deafut.txt (scripts/download_cot.py's own comment already
established this for the current-week file; this script reuses that
file's iter_cot_rows() parser unchanged rather than re-deriving the
column layout).

CAVEAT — NOT YET LIVE-VERIFIED (per this project's own "always verify
experimentally" discipline): this sandbox's network egress cannot reach
cftc.gov (blocked by the sandbox's own proxy policy — confirmed via a
direct curl attempt, same class of restriction previously hit and
documented for api.binance.com/api.bybit.com during H019's investigation,
i.e. a sandbox artifact, not evidence against feasibility). The URL
template, zip member name, and per-year availability are taken from a
working, actively-used open-source library's source code, not guessed —
but this script's `--probe` mode MUST be run on the VPS (which has open
egress, per H019/H104's own precedent) before any full 1986-present
backfill is trusted. `--probe` fetches exactly one year and prints
diagnostics without writing anything.

Output: data/cot/{SYMBOL}_deep_history.json — DELIBERATELY SEPARATE from
data/cot/{SYMBOL}.json (scripts/download_cot.py's live rolling-cache
file, which engines/sentiment_engine.py reads) so this backfill can never
corrupt or fight with the live weekly collector. Merging the two into one
evaluation dataset (if H012's A/B harness needs it) is the harness's job,
not this collector's.

Usage:
    python3 -m scripts.download_cot_deep_history --probe            # 1 year, print only
    python3 -m scripts.download_cot_deep_history                    # full 1986-present backfill
    python3 -m scripts.download_cot_deep_history --start-year 2015   # partial range
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

from scripts.download_cot import COT_SYMBOLS, _cot_dir, iter_cot_rows
from utils.logger import get_logger

logger = get_logger(__name__)

CFTC_HISTORY_URL_TEMPLATE = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"

# CFTC's own documented start of the legacy futures-only report.
EARLIEST_YEAR = 1986

REQUEST_TIMEOUT_SEC = 30
REQUEST_SLEEP_SEC = 1.0  # polite delay between yearly requests


def _current_completed_year() -> int:
    """The most recent year CFTC has plausibly finished publishing — the
    current calendar year's own zip may not exist yet or may be partial;
    the live weekly collector already covers recent weeks anyway."""
    return datetime.now(timezone.utc).year - 1


def fetch_year_zip(year: int) -> bytes | None:
    """Download one year's archive. Returns None (not an exception) on a
    404/not-found, which this script treats as "not published" — the
    correct stop signal for years before EARLIEST_YEAR's actual coverage
    or after the most recent published year, not a fatal error."""
    url = CFTC_HISTORY_URL_TEMPLATE.format(year=year)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "IATIS-research/1.0"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.info(f"{year}: not published (404) — {url}")
            return None
        logger.warning(f"{year}: HTTP {exc.code} fetching {url}")
        return None
    except Exception as exc:
        logger.warning(f"{year}: fetch failed ({exc}) — {url}")
        return None


def extract_annual_text(zip_bytes: bytes) -> str | None:
    """Pull the single data file out of a yearly zip. Does not assume the
    exact member name (see module docstring CAVEAT) — takes the one .txt
    member if there's exactly one, otherwise logs and gives up on that
    year rather than guessing wrong."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            txt_members = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if len(txt_members) != 1:
                logger.warning(
                    f"expected exactly one .txt member in archive, found "
                    f"{txt_members!r} — skipping this year (format drift?)"
                )
                return None
            return zf.read(txt_members[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        logger.warning("downloaded bytes are not a valid zip — skipping this year")
        return None


def parse_year(text: str) -> dict[str, list[dict]]:
    """One year's annual.txt -> {internal_symbol: [record, record, ...]},
    one record per weekly report found for that contract. Unlike
    scripts.download_cot.parse_cot_text (current-week file, last-row-wins),
    this KEEPS every row — a full year has ~52 rows per contract."""
    out: dict[str, list[dict]] = {}
    for internal, rec in iter_cot_rows(text):
        out.setdefault(internal, []).append(rec)
    return out


def merge_into_history(all_years: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Sort each symbol's accumulated records chronologically and dedupe
    by report_date (defensive against any year-boundary overlap in the
    source archives)."""
    merged: dict[str, list[dict]] = {}
    for symbol, records in all_years.items():
        by_date = {r["report_date"]: r for r in records}  # last wins on a dup date
        merged[symbol] = sorted(by_date.values(), key=lambda r: r["report_date"])
    return merged


def write_deep_history(merged: dict[str, list[dict]]) -> list[str]:
    out_dir = _cot_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    written = []
    for symbol, records in merged.items():
        path = out_dir / f"{symbol}_deep_history.json"
        payload = {
            "symbol": symbol,
            "source": "CFTC yearly archive (deacotYYYY.zip)",
            "n_records": len(records),
            "date_range": [records[0]["report_date"], records[-1]["report_date"]] if records else None,
            "fetched_at": now,
            "history": records,
        }
        path.write_text(json.dumps(payload, indent=1))
        written.append(symbol)
        span = payload["date_range"]
        logger.info(f"COT deep history {symbol}: {len(records)} weekly records ({span[0]} to {span[1]})" if span
                    else f"COT deep history {symbol}: 0 records")
    return written


def run(start_year: int, end_year: int, symbols: list[str] | None = None) -> dict[str, list[dict]]:
    wanted = set(symbols) if symbols else set(COT_SYMBOLS)
    all_years: dict[str, list[dict]] = {s: [] for s in wanted}
    consecutive_misses = 0
    for year in range(start_year, end_year + 1):
        zip_bytes = fetch_year_zip(year)
        if zip_bytes is None:
            consecutive_misses += 1
            # Two years in a row missing near the requested end = treat as
            # "no more data published", stop early rather than grinding
            # through the rest of the range for nothing.
            if consecutive_misses >= 2 and year > start_year + 1:
                logger.info(f"stopping early at {year}: {consecutive_misses} consecutive missing years")
                break
            time.sleep(REQUEST_SLEEP_SEC)
            continue
        consecutive_misses = 0
        text = extract_annual_text(zip_bytes)
        if text is None:
            time.sleep(REQUEST_SLEEP_SEC)
            continue
        parsed = parse_year(text)
        for symbol in wanted:
            all_years[symbol].extend(parsed.get(symbol, []))
        logger.info(f"{year}: parsed {sum(len(v) for v in parsed.values())} matching rows "
                    f"across {len(parsed)} symbols")
        time.sleep(REQUEST_SLEEP_SEC)
    return merge_into_history(all_years)


def probe(year: int) -> int:
    """Fetch exactly one year, print diagnostics, write nothing. Run this
    on the VPS first (see module docstring CAVEAT) before trusting a full
    backfill."""
    print(f"Probing {CFTC_HISTORY_URL_TEMPLATE.format(year=year)} ...")
    zip_bytes = fetch_year_zip(year)
    if zip_bytes is None:
        print(f"FAILED: no data returned for {year}")
        return 1
    print(f"Downloaded {len(zip_bytes):,} bytes")
    text = extract_annual_text(zip_bytes)
    if text is None:
        print("FAILED: could not extract a single .txt member from the zip")
        return 1
    print(f"Extracted text: {len(text):,} chars, {text.count(chr(10)):,} lines")
    parsed = parse_year(text)
    if not parsed:
        print("FAILED: 0 symbols matched — column layout may differ from deafut.txt's, or "
              "COT_SYMBOLS contract names don't match this year's naming")
        return 1
    for symbol, records in sorted(parsed.items()):
        dates = sorted(r["report_date"] for r in records)
        print(f"  {symbol:8s} {len(records):3d} weekly records, "
              f"{dates[0]} .. {dates[-1]}")
    missing = sorted(set(COT_SYMBOLS) - set(parsed))
    if missing:
        print(f"  (no rows matched for: {missing})")
    print("OK — format looks consistent with deafut.txt's layout. "
          "Safe to run a full backfill.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                     help="fetch one year only, print diagnostics, write nothing")
    ap.add_argument("--probe-year", type=int, default=None,
                     help=f"year to use with --probe (default: {_current_completed_year()})")
    ap.add_argument("--start-year", type=int, default=EARLIEST_YEAR)
    ap.add_argument("--end-year", type=int, default=_current_completed_year())
    ap.add_argument("--symbols", nargs="*", default=None,
                     help="restrict to these internal symbols (default: all COT_SYMBOLS)")
    ap.add_argument("--dry-run", action="store_true", help="parse but don't write")
    args = ap.parse_args()

    if args.probe:
        return probe(args.probe_year or _current_completed_year())

    if args.start_year < EARLIEST_YEAR:
        logger.warning(f"--start-year {args.start_year} predates CFTC's documented "
                        f"legacy futures-only start ({EARLIEST_YEAR}) — will 404 and skip.")

    merged = run(args.start_year, args.end_year, args.symbols)
    total = sum(len(v) for v in merged.values())
    if total == 0:
        logger.error("0 records collected across the entire range — aborting write "
                      "(likely a format or connectivity problem, see --probe)")
        return 1

    if args.dry_run:
        for symbol, records in sorted(merged.items()):
            span = (records[0]["report_date"], records[-1]["report_date"]) if records else (None, None)
            print(f"{symbol:8s} {len(records):4d} records  {span[0]} .. {span[1]}")
        return 0

    written = write_deep_history(merged)
    print(f"COT deep history written for {len(written)} symbols → {_cot_dir()}/*_deep_history.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
