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

A third, real live finding (2026-08-17, confirmed on the VPS: a real
2-year M15 GBPUSD run, run AFTER both fixes above): 429s kept surfacing
throughout the WHOLE run, not just at startup — a large fraction of
hours were still SKIPPED after exhausting all 6 retry attempts (up to
~120s of backoff each), scattered continuously rather than clustered at
the beginning. This proves the earlier two fixes treat 429 as a
per-request nuisance to retry around, but the real cause is a SUSTAINED
per-IP rate limit that 4 concurrently-firing workers exceed on every
steady-state cycle, not just in a synchronized initial burst — retrying
harder after the fact cannot fix a rate that's exceeded continuously.
The fix: a global, thread-safe request pacer (_throttle(), MIN_REQUEST_
INTERVAL_SECONDS) that every actual HTTP request — the first attempt AND
every retry — must pass through before firing, capping total throughput
to a fixed requests/second rate shared across every worker thread,
regardless of --workers. This is proactive rate-limiting rather than
reactive backoff, and it's the layer that should make the 429 retry
logic above rarely need to fire at all going forward (kept in place as
defense-in-depth, not removed). The chosen default interval was picked
from the observed failure pattern above, not independently verified
against a published Dukascopy rate limit (none exists) — tune via
--request-interval if it's still too aggressive/too slow.

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

Resume/checkpointing (2026-08-18, confirmed live on the VPS: a real
GBPUSD run mixed 429s and connect-timeouts to datafeed.dukascopy.com,
and a direct `curl` to the bare domain root returned HTTP 503 "No
server is available to handle this request" — Dukascopy's OWN backend
is intermittently unavailable, not something client-side pacing can
fix, and not an IP-level block either (the curl connected and got a
clean HTTP response, just an error one). Against a server this
unreliable, download_symbol_hours() previously threw away ALL progress
on a Ctrl+C/crash/timeout (bars only ever accumulated in memory, one
`out_df.to_csv()` at the very end) and every retry re-fetched every
hour from scratch even when most of them had already succeeded last
time — the worst possible strategy against a flaky server. Now, when
invoked from main() (checkpoint=True), every hour that completes
(ticks or a confirmed-closed-market hour) is durably appended to
data/.dukascopy_checkpoints/{SYMBOL}_completed_hours.txt and
{SYMBOL}_bars.csv as it happens — a subsequent run only re-fetches
hours NOT already in that file, so repeated attempts make monotonic
forward progress instead of restarting at zero. Only a genuinely
FAILED hour (429/timeout that exhausted its retries) is left off the
checkpoint, so it's retried automatically next time. Checkpoints are
cleared once a symbol's full download completes successfully — a
later --force re-download starts genuinely fresh, not from stale
partial state. Direct callers of download_symbol_hours() (tests,
--probe) default to checkpoint=False — this is an opt-in behavior of
the full CLI download flow, not the fetch function's own default.

Usage:
    python3 -m scripts.download_dukascopy_history --probe EURUSD                     # single hour, no file written
    python3 -m scripts.download_dukascopy_history --symbols EURUSD XAUUSD            # 10y H1, default
    python3 -m scripts.download_dukascopy_history --symbols EURUSD --timeframe M15   # 10y M15
    python3 -m scripts.download_dukascopy_history --symbols BTCUSD --years 1 --workers 16
    python3 -m scripts.download_dukascopy_history --symbols GBPUSD --request-interval 5.0  # even more conservative than the 3.0s default, if still hitting 429s/503s
    python3 -m scripts.download_dukascopy_history --probe GBPUSD --workers 1 --request-interval 5  # safe, sequential live check before any bulk run
    # Interrupted or partially-failed run? Just re-run the same command —
    # already-completed hours are skipped automatically (see "Resume/
    # checkpointing" above), no flag needed. Ctrl+C during a run is safe:
    # it's caught cooperatively (see "Ctrl+C / cancellation" below), never
    # corrupts the checkpoint, and the same re-run resumes cleanly.

Ctrl+C / cancellation (2026-08-18): a real live run mixed 429s/503s/
connect-timeouts AND ended with the operator's own Ctrl+C producing a
long hang/traceback — traced to `with ThreadPoolExecutor(...) as pool:`
implicitly calling `pool.shutdown(wait=True)` on exit, which blocks
until every in-flight/queued future finishes, including ones stuck deep
in a multi-minute retry backoff. Python cannot forcibly kill a running
thread, so this can never be made instant — but download_symbol_hours()
now checks a shared, cooperative `_shutdown_requested` flag (set by
main()'s own KeyboardInterrupt handler) BEFORE every sleep and every new
HTTP attempt — never during an already-started one, since Python cannot
preempt a sleeping/blocked thread — and shuts the pool down with
`wait=False, cancel_futures=True` instead of blocking. This bounds the
worst case to whatever single wait/request is already in progress on
each worker (a request timeout, a retry backoff, or — rarely — an
active global cooldown, which can run up to
_CONSECUTIVE_FAILURE_COOLDOWN_SECONDS/180s in the escalated case), never
the whole remaining queue of hours. Whatever hours had already completed
(and, for a checkpointed CLI run, been durably written) stay exactly as
safe as any other interruption — see "Resume/checkpointing" below.

No verified published Dukascopy rate limit exists (the 3.0s default
below, and every retry/backoff/cooldown constant, is empirical, derived
from live failures observed on this project's own VPS runs — see the
"Rate limiting" section below for the full history). The intended
behavior is adaptive, not a fixed guarantee: healthy conditions run at
the full configured rate; a 429 backs off and enters a shared cooldown
that slows every worker, escalating on repeated hits; a 503 (backend
unavailable, not a rate limit) starts with a SHORTER initial cooldown
than a 429 but escalates the same way on repeats. Do not read any
interval/backoff constant in this file as "guarantees no 429/503" —
Dukascopy publishes no rate limit to guarantee against. Always run
--probe with a single worker before a bulk/--years download against a
feed that hasn't been exercised recently.
"""
from __future__ import annotations

import argparse
import lzma
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import requests

DATA_DIR = PROJECT_ROOT / "data"
_CHECKPOINT_DIR = DATA_DIR / ".dukascopy_checkpoints"

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
REQUEST_TIMEOUT = 15
DEFAULT_WORKERS = 4  # lowered from 12 (2026-08-11) — see module docstring's "Rate limiting" note
TICK_RECORD_STRUCT = struct.Struct(">iiiff")  # ms_offset, ask_raw, bid_raw, ask_vol, bid_vol
TICK_RECORD_SIZE = TICK_RECORD_STRUCT.size

# Global request-rate pacer (2026-08-17, defaults raised 2026-08-18 — see
# module docstring's "Rate limiting" note, fourth finding). A module-level,
# mutable default so a single-process CLI run can override it once via
# --request-interval (main() reassigns this global before any download
# starts) without threading a new parameter through fetch_hour()'s
# signature, which is also called directly by --probe and by every
# existing test. 3.0s (not the earlier 0.5s) is a conservative EMPIRICAL
# safety default, not an official Dukascopy published rate limit (none
# exists) — live evidence 2026-08-18 showed 0.5s (~2 req/s) still drew
# sustained 429s. Tune with --request-interval if still too aggressive
# (or, once the feed is confirmed healthy via --probe, too slow).
MIN_REQUEST_INTERVAL_SECONDS = 3.0
_rate_limit_lock = threading.Lock()
_next_request_time = 0.0

# Global circuit breaker / cooldown (2026-08-18) — see module docstring's
# "Rate limiting" note, fourth finding: a curl straight to the bare
# datafeed.dukascopy.com root returned HTTP 503 "No server is available
# to handle this request" — Dukascopy's OWN backend is intermittently
# unavailable, a genuinely different failure mode from per-request rate
# limiting, and one where 4 workers independently retrying makes things
# worse, not better. _cooldown_until is a shared monotonic deadline: any
# thread hitting a transient failure (429/5xx) EXTENDS it (never shrinks
# an existing longer cooldown set by another thread), and _throttle() —
# the single gate every actual HTTP attempt already passes through, first
# attempt or retry — blocks every worker until it clears. This turns
# "4 workers each retry independently" into "one shared, extend-only
# cooldown all workers wait out together," per the operator's own
# explicit design request.
_cooldown_lock = threading.Lock()
_cooldown_until = 0.0
_shutdown_requested = threading.Event()  # cooperative Ctrl+C — see download_symbol_hours()


def _enter_cooldown(seconds: float) -> None:
    """Extends the shared cooldown deadline — never shrinks a longer one
    another thread already set (e.g. thread A's 120s 429 backoff must not
    get overwritten by thread B's shorter 503 backoff arriving a moment
    later)."""
    global _cooldown_until
    if seconds <= 0:
        return
    with _cooldown_lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + seconds)


def _throttle() -> None:
    """Blocks the calling thread until it's safe to send the next
    Dukascopy request — first waiting out any active global cooldown
    (see _enter_cooldown above), then reserving the next per-request
    pacing slot — and reserves that slot for whoever calls next. Locks
    are held only long enough to read/reserve shared state — never
    across the sleep itself — so multiple worker threads queue up
    cleanly with correctly staggered wake times instead of serializing
    on each other's I/O. Raises _ShutdownRequested (cooperative
    cancellation, see download_symbol_hours()) instead of ever sleeping
    once a Ctrl+C has been requested."""
    global _next_request_time
    if _shutdown_requested.is_set():
        raise _ShutdownRequested()
    # Single-shot cooldown wait (compute once, sleep once — no re-check
    # loop): a loop that re-reads _cooldown_until after waking would be
    # more precise against a cooldown another thread keeps extending
    # while this one sleeps, but re-deriving the remaining wait from
    # time.monotonic() on every iteration cannot be safely mocked the way
    # this whole file's test suite mocks time.sleep() (a frozen/mocked
    # clock with a no-op sleep spins forever instead of advancing). A
    # thread that wakes slightly early from a stale cooldown value just
    # makes one attempt sooner than ideal — self-correcting, since a
    # continued outage re-triggers _enter_cooldown() again — and matches
    # this module's own "empirical, not a guarantee" framing (see the
    # docstring's "Rate limiting" note) rather than a hard requirement.
    with _cooldown_lock:
        cooldown_wait = max(0.0, _cooldown_until - time.monotonic())
    if cooldown_wait > 0:
        if _shutdown_requested.is_set():
            raise _ShutdownRequested()
        time.sleep(cooldown_wait)
    with _rate_limit_lock:
        now = time.monotonic()
        wait = max(0.0, _next_request_time - now)
        reserved_start = now + wait
        _next_request_time = reserved_start + MIN_REQUEST_INTERVAL_SECONDS
    if wait > 0:
        if _shutdown_requested.is_set():
            raise _ShutdownRequested()
        time.sleep(wait)


def _jittered(seconds: float, spread: float = 0.2) -> float:
    """±spread jitter (default ±20%, per the operator's own spec) so
    concurrent workers backing off from the same failure don't wake and
    retry in lockstep."""
    import random
    return seconds * random.uniform(1.0 - spread, 1.0 + spread)


class _ShutdownRequested(Exception):
    """Internal-only cooperative-cancellation signal — raised by
    _throttle() once Ctrl+C has been requested, instead of starting (or
    continuing to back off before) another HTTP attempt. Never crosses
    download_symbol_hours()'s own boundary as this exception type; it's
    translated there into a clean, checkpoint-preserving stop."""


# 429 (rate-limited) retry/backoff — see module docstring's "Rate
# limiting" note. Distinct budget/schedule from 5xx below: rate limiting
# and backend unavailability are different failure modes with different
# expected recovery times.
MAX_429_RETRIES = 5
_429_BACKOFF_BASE_SECONDS = 10.0  # 10, 20, 40, 60(capped), 120(capped) — empirical, see module docstring
_429_BACKOFF_MAX_SECONDS = 120.0
_429_COOLDOWN_SECONDS = 30.0  # extends the GLOBAL cooldown so every worker (not just the one that got the 429) pauses

# HTTP 5xx (500/502/503/504 — transient server/backend errors, distinct
# from 429) retry/backoff. 2026-08-18, confirmed live: a direct curl to
# datafeed.dukascopy.com's bare root returned 503 "No server is available
# to handle this request", then later requests to the same host succeeded
# — genuinely transient backend unavailability, not a client-side problem
# a longer request interval alone fixes, and not the same failure class as
# 429 (rate limiting is Dukascopy telling you to slow down; 5xx is
# Dukascopy's own infrastructure being unhealthy).
_TRANSIENT_5XX_STATUSES = frozenset({500, 502, 503, 504})
MAX_5XX_RETRIES = 5
_5XX_BACKOFF_BASE_SECONDS = 5.0  # 5, 10, 20, 40, 60(capped) — empirical
_5XX_BACKOFF_MAX_SECONDS = 60.0
_5XX_COOLDOWN_SECONDS = 10.0  # shorter initial global cooldown than 429 — see _consecutive_transient_failures below for escalation

# Network-level retry/backoff (2026-08-11, confirmed live on the VPS
# immediately after the 429 fix landed: with 429s gone, some fraction of
# hourly requests instead failed with a requests.RequestException —
# ConnectTimeoutError specifically, 10/24 hours in one probe window —
# which fetch_hour() previously raised immediately with zero retry. A
# connect timeout to a public feed under concurrent load is exactly the
# kind of transient failure this project's other providers already
# retry (e.g. download_twelve_data_history.py's _td_get()) — distinct
# from the 429/5xx cases, so its own backoff/retry budget.
MAX_NETWORK_RETRIES = 4
_NETWORK_BACKOFF_BASE_SECONDS = 3.0  # 3, 6, 12, 24(capped) — per the operator's own spec
_NETWORK_BACKOFF_MAX_SECONDS = 24.0

# Consecutive-failure escalation (2026-08-18) — a run of transient
# failures ACROSS ALL WORKERS (not per-thread) is stronger evidence of a
# real outage than any single failure, and earns a longer global cooldown
# on top of whatever that failure's own cooldown already set. Reset to
# zero only by a genuine 200 response — NOT by a 404, which is a normal
# closed-market result, not evidence the backend is healthy (operator's
# own explicit requirement).
_CONSECUTIVE_FAILURE_THRESHOLD = 5
_CONSECUTIVE_FAILURE_COOLDOWN_SECONDS = 180.0
_consecutive_lock = threading.Lock()
_consecutive_transient_failures = 0


def _record_transient_failure() -> None:
    global _consecutive_transient_failures
    with _consecutive_lock:
        _consecutive_transient_failures += 1
        escalate = _consecutive_transient_failures >= _CONSECUTIVE_FAILURE_THRESHOLD
    if escalate:
        print(f"    COOLDOWN: {_consecutive_transient_failures} consecutive transient failures "
              f"across all workers — extending global cooldown {_CONSECUTIVE_FAILURE_COOLDOWN_SECONDS:.0f}s")
        _enter_cooldown(_CONSECUTIVE_FAILURE_COOLDOWN_SECONDS)


def _record_success() -> None:
    global _consecutive_transient_failures
    with _consecutive_lock:
        _consecutive_transient_failures = 0

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


# Statuses that indicate a permanent client-side/request problem, never a
# transient condition — retrying is pointless and would just hammer a
# rejection. 404 is handled separately (a legitimate closed-market hour,
# not an error at all).
_NO_RETRY_STATUSES = frozenset({400, 401, 403, 410})


def _retry_after_seconds(resp: "requests.Response", attempt: int, *, base: float, cap: float) -> float:
    """Honors a numeric Retry-After header VERBATIM (the server's own
    authoritative value — never jittered) when the server sends one;
    otherwise exponential backoff off `base`, capped at `cap`, with the
    operator's own requested +/-20% jitter (_jittered) so concurrent
    workers backing off from the same failure don't wake and retry in
    lockstep. Shared by both the 429 and 5xx retry loops in fetch_hour()
    below — same header convention, different budgets/schedules."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return _jittered(min(cap, base * (2 ** attempt)))


def _sleep_unless_shutdown(seconds: float) -> None:
    """A literal time.sleep() call (not Event.wait) so every existing/new
    test can assert on it exactly like every other backoff assertion in
    this file — but skipped entirely, raising _ShutdownRequested instead,
    if a shutdown was already requested before this sleep would start.
    Does NOT shorten a sleep already in progress (Python cannot preempt a
    running thread) — see the module docstring's "Ctrl+C / cancellation"
    note for the accepted, bounded worst case this implies."""
    if _shutdown_requested.is_set():
        raise _ShutdownRequested()
    time.sleep(seconds)


def fetch_hour(instrument: str, dt: datetime) -> list[tuple[int, float, float, float, float]] | None:
    """One hour's raw ticks as (ms_offset, ask_raw, bid_raw, ask_vol,
    bid_vol) tuples. Returns None (not an error) on 404 — a closed
    market hour (weekend/holiday) is expected, not a fetch failure, and
    never retried.

    Every actual HTTP attempt — the first one AND every retry — is paced
    by _throttle() first (a global, cross-worker-thread rate limiter —
    see module docstring's "Rate limiting" note, third finding). Three
    distinct transient failure classes are each retried with their OWN
    budget/backoff schedule (429 rate-limiting, 5xx backend
    unavailability, network-level exceptions) since they have different
    expected recovery times; a genuine transient hit on any of them
    also EXTENDS the shared global cooldown (_enter_cooldown) so every
    other worker thread pauses too, not just this one. 400/401/403/410
    are permanent rejections and are never retried. Consecutive
    transient failures across ALL workers (not just this call) are
    tracked via _record_transient_failure()/_record_success() — reset
    ONLY by a genuine 200, never by a 404 (a normal closed-market result
    is not evidence the backend is healthy)."""
    url = _hour_url(instrument, dt)
    resp: "requests.Response | None" = None
    network_attempt = 0
    attempt_429 = 0
    attempt_5xx = 0
    while True:
        _throttle()
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            _record_transient_failure()
            label = "TIMEOUT" if isinstance(exc, (requests.Timeout, requests.ConnectTimeout, requests.ReadTimeout)) else "NETWORK_ERROR"
            if network_attempt >= MAX_NETWORK_RETRIES:
                raise DukascopyFetchError(
                    f"{url}: request failed after {MAX_NETWORK_RETRIES + 1} attempts: {exc}"
                ) from exc
            wait = _jittered(min(_NETWORK_BACKOFF_MAX_SECONDS, _NETWORK_BACKOFF_BASE_SECONDS * (2 ** network_attempt)))
            print(f"    {label}: {url} ({exc}) — retrying in {wait:.1f}s ({network_attempt + 1}/{MAX_NETWORK_RETRIES})")
            _sleep_unless_shutdown(wait)
            network_attempt += 1
            continue

        if resp.status_code == 429:
            _record_transient_failure()
            _enter_cooldown(_429_COOLDOWN_SECONDS)
            if attempt_429 == MAX_429_RETRIES:
                raise DukascopyFetchError(f"{url}: HTTP 429 (rate limited) after {MAX_429_RETRIES + 1} attempts")
            wait = _retry_after_seconds(resp, attempt_429, base=_429_BACKOFF_BASE_SECONDS, cap=_429_BACKOFF_MAX_SECONDS)
            print(f"    RATE_LIMIT: {url} — retrying in {wait:.1f}s ({attempt_429 + 1}/{MAX_429_RETRIES})")
            _sleep_unless_shutdown(wait)
            attempt_429 += 1
            continue

        if resp.status_code in _TRANSIENT_5XX_STATUSES:
            _record_transient_failure()
            _enter_cooldown(_5XX_COOLDOWN_SECONDS)
            label = "SERVICE_UNAVAILABLE" if resp.status_code == 503 else "SERVER_ERROR"
            if attempt_5xx == MAX_5XX_RETRIES:
                raise DukascopyFetchError(f"{url}: HTTP {resp.status_code} (server error) after {MAX_5XX_RETRIES + 1} attempts")
            wait = _retry_after_seconds(resp, attempt_5xx, base=_5XX_BACKOFF_BASE_SECONDS, cap=_5XX_BACKOFF_MAX_SECONDS)
            print(f"    {label}: {url} (HTTP {resp.status_code}) — retrying in {wait:.1f}s ({attempt_5xx + 1}/{MAX_5XX_RETRIES})")
            _sleep_unless_shutdown(wait)
            attempt_5xx += 1
            continue

        break

    assert resp is not None
    if resp.status_code == 404:
        return None  # closed-market hour — never a success or a failure for the consecutive-failure escalation
    if resp.status_code in _NO_RETRY_STATUSES:
        raise DukascopyFetchError(f"{url}: HTTP {resp.status_code} (not retried — permanent rejection)")
    if resp.status_code != 200:
        raise DukascopyFetchError(f"{url}: HTTP {resp.status_code}")

    _record_success()  # a genuine 200 — the ONLY thing that resets the consecutive-transient-failure counter

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


def _classify_failure(exc: "DukascopyFetchError") -> str:
    """Maps a raised DukascopyFetchError to one of the end-of-symbol
    summary buckets, by the exact message shapes fetch_hour() itself
    produces above — kept in one place so the summary in
    download_symbol_hours() can't silently drift from what fetch_hour()
    actually raises."""
    msg = str(exc)
    if "HTTP 429" in msg:
        return "failed_429"
    if "server error" in msg:
        return "failed_5xx"
    if "request failed after" in msg:
        return "failed_network"
    return "failed_other"


class _DownloadInterrupted(Exception):
    """Raised by download_symbol_hours() when Ctrl+C (KeyboardInterrupt)
    interrupts an in-progress download, after the pool has been shut down
    non-blockingly and whatever hours had already durably completed are
    safely checkpointed (see the module docstring's "Ctrl+C /
    cancellation" note). main() catches this to stop cleanly — no output
    CSV write, no checkpoint clear — for exactly the interrupted symbol,
    without silently continuing as if the run had finished."""

    def __init__(self, done: int, total: int):
        super().__init__(f"interrupted after {done}/{total} hour(s) this run")
        self.done = done
        self.total = total


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


def _checkpoint_paths(symbol: str) -> tuple[Path, Path]:
    """(hours_path, bars_path) for a symbol's resume checkpoint — see the
    module docstring's "Resume/checkpointing" note. hours_path is a
    plain one-ISO-timestamp-per-line append log of every hour that has
    ALREADY completed (ticks fetched, or confirmed market-closed);
    bars_path holds the M15-bucketed OHLCV rows for the (non-empty)
    hours among those, in the base timeframe so it's reusable
    regardless of --timeframe."""
    return (
        _CHECKPOINT_DIR / f"{symbol}_completed_hours.txt",
        _CHECKPOINT_DIR / f"{symbol}_bars.csv",
    )


def _load_checkpoint(symbol: str) -> tuple[set[datetime], list[dict]]:
    """Returns (already-completed hours, their M15 bars) — both empty
    if no checkpoint exists yet for this symbol. Malformed/partial
    checkpoint files (e.g. a line half-written when a previous run was
    killed) are skipped line-by-line rather than aborting the whole
    load — a checkpoint is an optimization, never something a resume
    run should crash over."""
    hours_path, bars_path = _checkpoint_paths(symbol)
    completed: set[datetime] = set()
    if hours_path.exists():
        for line in hours_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                completed.add(datetime.fromisoformat(line))
            except ValueError:
                continue
    bars: list[dict] = []
    if bars_path.exists():
        try:
            checkpoint_df = pd.read_csv(bars_path, parse_dates=["datetime"])
            bars = checkpoint_df.to_dict("records")
        except (pd.errors.EmptyDataError, ValueError):
            pass
    return completed, bars


def _clear_checkpoint(symbol: str) -> None:
    """Called once a symbol's full download completes successfully — a
    later --force re-download should start genuinely fresh, not resume
    stale partial state from a different --years/--timeframe request."""
    for path in _checkpoint_paths(symbol):
        path.unlink(missing_ok=True)


def download_symbol_hours(
    instrument: str, symbol: str, hours: list[datetime], workers: int = DEFAULT_WORKERS,
    checkpoint: bool = False,
) -> pd.DataFrame:
    """Bounded-concurrency fetch of every hour in `hours` (one HTTP
    request per hour — Dukascopy has no batch-of-N-bars API, unlike
    MT5/cTrader, hence the ThreadPoolExecutor here instead of the
    sequential-with-sleep pattern those scripts use). Staggers only the
    initial fill of the worker pool — see the module-level
    _INITIAL_SUBMIT_STAGGER_SECONDS comment for why.

    checkpoint=True (main()'s CLI path only — see module docstring's
    "Resume/checkpointing" note) skips hours already recorded as
    complete from a previous run, and durably appends each newly-
    completed hour as it finishes rather than only at the very end, so
    a Ctrl+C/crash/timeout loses at most the in-flight hours, not the
    whole run's progress. Defaults False so every existing direct
    caller (tests, --probe) is completely unaffected. Checkpoint writes
    are ordered bars-THEN-hours (never the reverse) — an interruption
    between the two writes must never mark an hour "complete" while its
    bars are still missing on disk, which would otherwise cause a later
    resume to silently skip that hour forever (permanent, silent data
    loss). Ctrl+C (KeyboardInterrupt) is caught cooperatively — see the
    module docstring's "Ctrl+C / cancellation" note — and raises
    _DownloadInterrupted after shutting the pool down non-blockingly;
    whatever completed before that point stays exactly as durably
    checkpointed as any other interruption."""
    _shutdown_requested.clear()  # defensive: a prior interrupted call in the same process must never silently block a fresh one
    already_done: set[datetime] = set()
    bars: list[dict] = []
    if checkpoint:
        _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        already_done, bars = _load_checkpoint(symbol)
    remaining_hours = [h for h in hours if h not in already_done] if checkpoint else hours
    if checkpoint and already_done:
        print(f"    resuming: {len(already_done)}/{len(hours)} hour(s) already checkpointed, "
              f"{len(remaining_hours)} remaining")

    point_value: int | None = None
    counts = {
        "completed": 0, "closed_market": 0,
        "failed_429": 0, "failed_5xx": 0, "failed_network": 0, "failed_other": 0,
    }
    hours_path, bars_path = _checkpoint_paths(symbol) if checkpoint else (None, None)

    pool = ThreadPoolExecutor(max_workers=workers)
    done = 0
    interrupted: "_DownloadInterrupted | None" = None
    try:
        future_to_hour: dict = {}
        for i, h in enumerate(remaining_hours):
            if 0 < i < workers:
                time.sleep(_INITIAL_SUBMIT_STAGGER_SECONDS)
            future_to_hour[pool.submit(fetch_hour, instrument, h)] = h
        try:
            for future in as_completed(future_to_hour):
                hour = future_to_hour[future]
                done += 1
                try:
                    ticks = future.result()
                except DukascopyFetchError as exc:
                    counts[_classify_failure(exc)] += 1
                    print(f"    [{done}/{len(remaining_hours)}] {hour.date()} {hour.hour:02d}h: SKIPPED ({exc})")
                    continue
                except _ShutdownRequested:
                    # this hour's fetch was aborted mid-retry by a shutdown
                    # already in progress — never checkpointed, retried on
                    # the next resume, not counted as a failure.
                    continue

                hour_bars: list[dict] = []
                if ticks:
                    if point_value is None:
                        point_value = detect_point_value([t[1] for t in ticks], symbol)
                        print(f"    detected point value: {point_value} (from {hour.date()} {hour.hour:02d}h)")
                    hour_bars = ticks_to_m15_bars(hour, ticks, point_value)
                    bars.extend(hour_bars)
                    counts["completed"] += 1
                else:
                    counts["closed_market"] += 1  # market closed this hour — not a failure, still checkpointed as done below

                if checkpoint:
                    # bars written BEFORE the hour is marked complete — see
                    # this function's own docstring above for why the order
                    # matters.
                    if hour_bars:
                        pd.DataFrame(hour_bars).to_csv(
                            bars_path, mode="a", header=not bars_path.exists(), index=False,
                        )
                    with hours_path.open("a") as f:
                        f.write(hour.isoformat() + "\n")

                if done % 200 == 0:
                    print(f"    ...{done}/{len(remaining_hours)} hours checked, {len(bars)} bars so far")
        except KeyboardInterrupt:
            _shutdown_requested.set()
            interrupted = _DownloadInterrupted(done, len(remaining_hours))
    finally:
        pool.shutdown(wait=interrupted is None, cancel_futures=interrupted is not None)

    failures = counts["failed_429"] + counts["failed_5xx"] + counts["failed_network"] + counts["failed_other"]
    print(
        f"    Summary: {counts['completed']} completed, {counts['closed_market']} closed-market, "
        f"{counts['failed_429']} failed(429), {counts['failed_5xx']} failed(5xx), "
        f"{counts['failed_network']} failed(network), {counts['failed_other']} failed(other), "
        f"{len(bars)} bars total"
    )
    if failures:
        print(f"    {failures}/{len(remaining_hours)} hour(s) failed (not 404s) and were skipped.")

    if interrupted is not None:
        print(f"\n    Interrupted (Ctrl+C) — {interrupted.done}/{interrupted.total} hour(s) processed this run"
              f"{' (already-completed hours are safely checkpointed; re-run the same command to resume)' if checkpoint else ''}.")
        raise interrupted

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
    global MIN_REQUEST_INTERVAL_SECONDS
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", help="single symbol, fetch a handful of recent hours, print result, no file written")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--timeframe", default="H1", choices=sorted(_TF_MINUTES))
    parser.add_argument("--years", type=float, default=10.0)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--request-interval", type=float, default=MIN_REQUEST_INTERVAL_SECONDS,
        help="minimum seconds between the START of consecutive Dukascopy requests, "
             "shared across every worker thread (raise this if 429s are still frequent "
             "even after the retry/backoff logic; see module docstring's 'Rate limiting' note)",
    )
    parser.add_argument("--force", action="store_true", help="re-download even if the output file exists")
    args = parser.parse_args()

    MIN_REQUEST_INTERVAL_SECONDS = args.request_interval

    if args.probe:
        symbol = args.probe.upper()
        if symbol not in SYMBOL_MAP:
            raise SystemExit(f"{symbol}: no Dukascopy instrument mapping in SYMBOL_MAP.")
        instrument = SYMBOL_MAP[symbol]
        probe_hours = _hour_range(years=0.02)[-24:]  # ~last day's worth of hours
        print(f"Probing {symbol} -> Dukascopy instrument {instrument!r}, {len(probe_hours)} hour(s) "
              f"({args.workers} worker(s), {MIN_REQUEST_INTERVAL_SECONDS}s min request interval). "
              f"No file will be written. Per-hour RATE_LIMIT/SERVICE_UNAVAILABLE/SERVER_ERROR/"
              f"TIMEOUT/NETWORK_ERROR/COOLDOWN lines above the summary below classify anything "
              f"observed live.")
        try:
            df = download_symbol_hours(instrument, symbol, probe_hours, workers=min(args.workers, 8))
        except _DownloadInterrupted as exc:
            print(f"\n{symbol}: probe interrupted (Ctrl+C) — {exc}.")
            return
        if df.empty:
            print(f"{symbol}: no bars returned in the probe window (all closed hours, or a real fetch problem above — "
                  f"check the per-hour classification lines and the Summary line above).")
        else:
            print(f"{symbol}: healthy — {len(df)} bar(s), {df.index[0]} -> {df.index[-1]}")
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
            base_df = download_symbol_hours(instrument, sym, hours, workers=args.workers, checkpoint=True)
        except _DownloadInterrupted as exc:
            print(f"\n  {sym}: interrupted ({exc}) — progress is safely checkpointed, "
                  f"re-run the same command to resume. Stopping (Ctrl+C) — no further symbols will start.")
            break
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
        _clear_checkpoint(sym)  # this symbol is now fully, freshly downloaded — no stale partial state to resume next time

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
