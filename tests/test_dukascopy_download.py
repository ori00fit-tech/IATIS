"""tests/test_dukascopy_download.py

Regression coverage for scripts/download_dukascopy_history.py — the
free, credential-free Dukascopy historical-feed download script. No
live network call is ever made in these tests; requests.get is mocked
throughout (matching tests/test_download_deep_history.py's/
tests/test_ctrader_refresh_access_token.py's established convention for
this class of manual, operator-run script), since this sandbox's
outbound network policy blocks datafeed.dukascopy.com (confirmed via a
direct curl returning 403 through the agent proxy).
"""
from __future__ import annotations

import lzma
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from download_dukascopy_history import (  # noqa: E402
    DEFAULT_SYMBOLS,
    SYMBOL_MAP,
    TICK_RECORD_STRUCT,
    DukascopyFetchError,
    _DownloadInterrupted,
    _PLAUSIBLE_RANGES,
    _POINT_VALUE_CANDIDATES,
    _hour_range,
    _throttle,
    coarsen_bars,
    detect_point_value,
    download_symbol_hours,
    fetch_hour,
    ticks_to_m15_bars,
)
from core.data_validator import validate_ohlcv


@pytest.fixture(autouse=True)
def _reset_dukascopy_module_globals():
    """download_dukascopy_history.py added module-level mutable state this
    session (_cooldown_until, _next_request_time, _consecutive_transient_
    failures, _shutdown_requested) that fetch_hour()/_throttle() now write
    to as a side effect of ordinary retry/cooldown handling. Tests share
    one process, so without a reset a real _enter_cooldown()/
    _record_transient_failure() call inside one test's fetch_hour()
    invocation (even one that never explicitly touches these globals)
    could silently leak into a later, unrelated test — e.g. a nonzero
    _cooldown_until from an earlier 429-retry test making a later
    _throttle()-timing test wait/sleep unexpectedly. Reset before AND
    after every test in this file, defensively."""
    import download_dukascopy_history as m

    def _reset():
        m._cooldown_until = 0.0
        m._next_request_time = 0.0
        m._consecutive_transient_failures = 0
        m._shutdown_requested.clear()

    _reset()
    yield
    _reset()


def _compress_records(records: list[tuple]) -> bytes:
    raw = b"".join(TICK_RECORD_STRUCT.pack(*r) for r in records)
    return lzma.compress(raw)


def _fake_response(status_code: int = 200, content: bytes = b"", headers: dict | None = None):
    resp = Mock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# bi5 tick-record unpacking
# ---------------------------------------------------------------------------


def test_fetch_hour_unpacks_a_synthetic_compressed_blob():
    records = [
        (0, 108510, 108490, 1.5, 2.0),
        (1000, 108520, 108500, 1.0, 1.0),
    ]
    body = _compress_records(records)
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records


def test_fetch_hour_returns_none_on_404():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(404)):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 6, 10, tzinfo=timezone.utc))  # a Saturday
    assert ticks is None


def test_fetch_hour_returns_none_on_empty_200_body():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, b"")):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks is None


def test_fetch_hour_raises_immediately_on_a_permanent_rejection_status():
    # 400/401/403/410 are permanent rejections, never retried — distinct
    # from 429 (rate limit) and 5xx (transient backend unavailability),
    # both of which now DO retry (see the dedicated retry-loop tests
    # below). This test deliberately does NOT patch time.sleep/_throttle:
    # if a permanent-rejection status were ever accidentally routed into
    # a retry loop, this test would hang/sleep for real instead of
    # silently passing.
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(403)) as mock_get:
        with pytest.raises(DukascopyFetchError, match="HTTP 403"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert mock_get.call_count == 1


def test_fetch_hour_raises_on_malformed_lzma():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, b"not-lzma-data")):
        with pytest.raises(DukascopyFetchError, match="LZMA"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))


def test_fetch_hour_raises_on_truncated_record_size():
    body = lzma.compress(b"\x00" * 13)  # not a multiple of 20 bytes
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)):
        with pytest.raises(DukascopyFetchError, match="not a multiple"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))


def test_fetch_hour_raises_after_exhausting_network_retries():
    import requests
    import download_dukascopy_history as m

    with patch("download_dukascopy_history.requests.get", side_effect=requests.ConnectionError("boom")), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        with pytest.raises(DukascopyFetchError, match="request failed after"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert mock_sleep.call_count == m.MAX_NETWORK_RETRIES


# ---------------------------------------------------------------------------
# 429 retry/backoff (2026-08-11 — confirmed live on the VPS: all 24 probe
# hours returned HTTP 429 with the pre-fix code, zero retry/resilience)
# ---------------------------------------------------------------------------


def test_fetch_hour_retries_429_then_succeeds():
    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(429), _fake_response(429), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records
    assert mock_sleep.call_count == 2  # one sleep per 429 before the eventual success


def test_fetch_hour_raises_after_exhausting_429_retries():
    import download_dukascopy_history as m

    responses = [_fake_response(429)] * (m.MAX_429_RETRIES + 1)
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        with pytest.raises(DukascopyFetchError, match="HTTP 429"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert mock_sleep.call_count == m.MAX_429_RETRIES  # sleeps between attempts, not after the final failed one


def test_fetch_hour_honors_numeric_retry_after_header():
    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(429, headers={"Retry-After": "7"}), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    mock_sleep.assert_called_once_with(7.0)


def test_fetch_hour_falls_back_to_exponential_backoff_on_malformed_retry_after():
    import download_dukascopy_history as m

    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(429, headers={"Retry-After": "not-a-number"}), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    mock_sleep.assert_called_once()
    waited = mock_sleep.call_args[0][0]
    # attempt=0 -> base * 2**0 = base, +/-20% jitter (operator's own
    # requested spread) — no longer an exact value once jitter landed.
    assert m._429_BACKOFF_BASE_SECONDS * 0.8 <= waited <= m._429_BACKOFF_BASE_SECONDS * 1.2


def test_fetch_hour_still_treats_404_as_market_closed_not_a_retry_target():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(404)) as mock_get, \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 6, 10, tzinfo=timezone.utc))
    assert ticks is None
    assert mock_get.call_count == 1  # no retry for a 404 — it's an expected closed-market response
    mock_sleep.assert_not_called()


def test_fetch_hour_never_retries_401_or_410():
    for status in (401, 410):
        with patch("download_dukascopy_history.requests.get", return_value=_fake_response(status)) as mock_get, \
             patch("download_dukascopy_history.time.sleep") as mock_sleep, \
             patch("download_dukascopy_history._throttle"):
            with pytest.raises(DukascopyFetchError, match=f"HTTP {status}"):
                fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
        assert mock_get.call_count == 1
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# HTTP 5xx (transient backend unavailability, distinct from 429 rate
# limiting) retry/backoff (2026-08-18 — confirmed live on the VPS: a direct
# curl to datafeed.dukascopy.com's bare root returned 503 "No server is
# available to handle this request", then later requests succeeded — a
# genuinely transient backend condition needing its own retry budget/
# schedule, not folded into the 429 handling above).
# ---------------------------------------------------------------------------


def test_fetch_hour_retries_503_then_succeeds():
    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(503), _fake_response(503), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records
    assert mock_sleep.call_count == 2


def test_fetch_hour_raises_after_exhausting_503_retries():
    import download_dukascopy_history as m

    responses = [_fake_response(503)] * (m.MAX_5XX_RETRIES + 1)
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        with pytest.raises(DukascopyFetchError, match="HTTP 503"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert mock_sleep.call_count == m.MAX_5XX_RETRIES


@pytest.mark.parametrize("status", [500, 502, 504])
def test_fetch_hour_retries_other_5xx_statuses_then_succeeds(status):
    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(status), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records
    assert mock_sleep.call_count == 1


def test_5xx_and_429_retry_budgets_are_independent():
    """A request that alternates between 503 and 429 must exhaust BOTH
    retry budgets independently before giving up — proves the two loops
    don't share (or prematurely consume) each other's attempt counters,
    mirroring the existing network-vs-429 independence test."""
    import download_dukascopy_history as m

    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = (
        [_fake_response(503)] * m.MAX_5XX_RETRIES
        + [_fake_response(429)] * m.MAX_429_RETRIES
        + [_fake_response(200, body)]
    )
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records
    assert mock_sleep.call_count == m.MAX_5XX_RETRIES + m.MAX_429_RETRIES


# ---------------------------------------------------------------------------
# Global cooldown / consecutive-failure escalation (2026-08-18) — a 429/5xx
# hit must extend the SHARED _cooldown_until deadline (every worker pauses,
# not just the one that got the transient response), and enough consecutive
# transient failures across the whole process escalates to a longer,
# extended cooldown — reset ONLY by a genuine 200, never by a 404.
# ---------------------------------------------------------------------------


def test_fetch_hour_429_extends_the_global_cooldown():
    import download_dukascopy_history as m

    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(429), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep"), \
         patch("download_dukascopy_history._throttle"), \
         patch.object(m, "_enter_cooldown") as mock_enter_cooldown:
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    mock_enter_cooldown.assert_called_once_with(m._429_COOLDOWN_SECONDS)


def test_fetch_hour_503_extends_the_global_cooldown_with_its_own_shorter_duration():
    import download_dukascopy_history as m

    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(503), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep"), \
         patch("download_dukascopy_history._throttle"), \
         patch.object(m, "_enter_cooldown") as mock_enter_cooldown:
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    mock_enter_cooldown.assert_called_once_with(m._5XX_COOLDOWN_SECONDS)
    assert m._5XX_COOLDOWN_SECONDS < m._429_COOLDOWN_SECONDS  # shorter initial cooldown, per the operator's own spec


def test_enter_cooldown_never_shrinks_an_existing_longer_cooldown(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_cooldown_until", 0.0)
    with patch("download_dukascopy_history.time.monotonic", return_value=1000.0):
        m._enter_cooldown(100.0)  # deadline -> 1100.0
        assert m._cooldown_until == 1100.0
        m._enter_cooldown(10.0)  # a shorter cooldown from another worker/failure must NOT shrink it
        assert m._cooldown_until == 1100.0
        m._enter_cooldown(200.0)  # a longer one DOES extend it
        assert m._cooldown_until == 1200.0


def test_throttle_blocks_on_an_active_global_cooldown_shared_across_calls(monkeypatch):
    """One worker's 429 (_enter_cooldown) must block EVERY worker's next
    _throttle() call, not just the one that hit the 429 — the load-bearing
    property that makes this a GLOBAL, not per-thread, circuit breaker.
    v2 (2026-08-18): the cooldown-wait is a BOUNDED re-check loop (up to
    _COOLDOWN_MAX_RECHECKS iterations), not a single compute-once/
    sleep-once call — a worker that wakes from a wait must re-verify the
    cooldown is genuinely clear (another worker's failure may have
    extended it mid-wait) rather than proceed on a stale value. Under a
    frozen clock (as here), cooldown_wait can never naturally reach 0, so
    this also proves the loop terminates at the bound instead of hanging."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_next_request_time", 0.0)
    monkeypatch.setattr(m, "_cooldown_until", 0.0)
    with patch("download_dukascopy_history.time.monotonic", return_value=1000.0):
        m._enter_cooldown(30.0)  # one worker's 429 sets a 30s shared cooldown
    assert m._cooldown_until == pytest.approx(1030.0)

    with patch("download_dukascopy_history.time.monotonic", return_value=1000.0), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        m._throttle()

    # every cooldown-wait sleep call must be for the still-active 30s
    # remainder (the frozen clock never lets it shrink) — and the loop
    # must have re-checked, not slept once and moved on.
    cooldown_sleeps = [c for c in mock_sleep.call_args_list if c.args and c.args[0] == pytest.approx(30.0, abs=1e-9)]
    assert len(cooldown_sleeps) == m._COOLDOWN_MAX_RECHECKS


def test_throttle_recheck_loop_absorbs_a_mid_wait_cooldown_extension(monkeypatch):
    """The exact race the bounded re-check loop exists to close: a worker
    wakes from waiting out the cooldown to find ANOTHER worker's failure
    has since extended it further — it must re-check and wait again rather
    than firing a request against a cooldown that's actually still live."""
    import download_dukascopy_history as m

    fake_now = [0.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: fake_now[0])
    monkeypatch.setattr(m, "_cooldown_until", 10.0)
    monkeypatch.setattr(m, "_next_request_time", 0.0)

    sleep_calls: list[float] = []
    extended = {"done": False}

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        fake_now[0] += seconds
        if not extended["done"]:
            extended["done"] = True
            m._cooldown_until = fake_now[0] + 5.0  # simulates another worker's failure extending it mid-wait

    monkeypatch.setattr(m.time, "sleep", fake_sleep)
    m._throttle()

    assert len(sleep_calls) >= 2  # a single-shot wait would only ever produce one
    assert fake_now[0] >= 15.0  # never proceeded until the (extended) cooldown had genuinely cleared


def test_throttle_cooldown_recheck_loop_is_bounded_never_hangs(monkeypatch):
    """Regression pin for the two real hangs this session's earlier attempts
    at this loop produced: even a cooldown that appears to keep extending
    forever (or a frozen/mocked clock that never lets cooldown_wait reach
    0) must terminate after _COOLDOWN_MAX_RECHECKS iterations, never spin
    indefinitely."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m.time, "monotonic", lambda: 0.0)  # frozen — cooldown_wait can never naturally reach 0
    monkeypatch.setattr(m, "_cooldown_until", 100.0)
    monkeypatch.setattr(m, "_next_request_time", 0.0)
    sleep_calls: list[float] = []
    monkeypatch.setattr(m.time, "sleep", lambda s: sleep_calls.append(s))

    m._throttle()  # must return promptly, not hang

    assert len(sleep_calls) == m._COOLDOWN_MAX_RECHECKS


def test_consecutive_transient_failures_escalate_the_cooldown(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_consecutive_transient_failures", m._CONSECUTIVE_FAILURE_THRESHOLD - 1)
    with patch.object(m, "_enter_cooldown") as mock_enter_cooldown:
        m._record_transient_failure()
    mock_enter_cooldown.assert_called_once_with(m._CONSECUTIVE_FAILURE_COOLDOWN_SECONDS)


def test_consecutive_transient_failures_do_not_escalate_below_threshold(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_consecutive_transient_failures", 0)
    with patch.object(m, "_enter_cooldown") as mock_enter_cooldown:
        m._record_transient_failure()
    mock_enter_cooldown.assert_not_called()


def test_success_resets_the_consecutive_failure_counter(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_consecutive_transient_failures", 4)
    m._record_success()
    assert m._consecutive_transient_failures == 0


def test_fetch_hour_404_neither_resets_nor_increments_the_consecutive_failure_counter(monkeypatch):
    """The operator's own explicit requirement: a 404 is a normal
    closed-market result, not evidence the backend is healthy — it must
    not reset the counter the way a genuine 200 does."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_consecutive_transient_failures", 3)
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(404)), \
         patch("download_dukascopy_history._throttle"):
        fetch_hour("EURUSD", datetime(2024, 1, 6, 10, tzinfo=timezone.utc))
    assert m._consecutive_transient_failures == 3


def test_fetch_hour_200_resets_the_consecutive_failure_counter(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_consecutive_transient_failures", 3)
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history._throttle"):
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert m._consecutive_transient_failures == 0


# ---------------------------------------------------------------------------
# Connect-timeout / network-exception retry (2026-08-11 — confirmed live on
# the VPS immediately after the 429 fix: with 429s gone, some hours instead
# failed with a plain requests.RequestException (ConnectTimeoutError),
# 10/24 in one probe window — previously raised immediately, zero retry)
# ---------------------------------------------------------------------------


def test_fetch_hour_retries_a_network_exception_then_succeeds():
    import requests

    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    with patch(
        "download_dukascopy_history.requests.get",
        side_effect=[requests.ConnectTimeout("timed out"), requests.ConnectTimeout("timed out"), _fake_response(200, body)],
    ), patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records
    assert mock_sleep.call_count == 2


def test_fetch_hour_network_retry_budget_is_independent_of_429_budget():
    """A request that alternates between a network exception and a 429
    must be able to exhaust BOTH retry budgets independently before
    giving up — proves the two retry loops don't share (or prematurely
    consume) each other's attempt counters."""
    import requests
    import download_dukascopy_history as m

    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = (
        [requests.ConnectTimeout("timed out")] * m.MAX_NETWORK_RETRIES
        + [_fake_response(429)] * m.MAX_429_RETRIES
        + [_fake_response(200, body)]
    )
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records
    assert mock_sleep.call_count == m.MAX_NETWORK_RETRIES + m.MAX_429_RETRIES


# ---------------------------------------------------------------------------
# Global rate-limit pacer (2026-08-17 — confirmed live on the VPS: a real
# 2-year M15 GBPUSD run, AFTER the 429/network-retry/initial-burst-stagger
# fixes above, still lost roughly a third of requests to 429s scattered
# throughout the whole run, not just at startup — proving the earlier fixes
# retry around a per-request nuisance while the real cause is a SUSTAINED
# per-IP rate limit that 4 concurrent workers exceed continuously. _throttle()
# is the proactive fix: every actual HTTP attempt is paced to a fixed
# requests/second rate shared across every worker thread, regardless of
# --workers.)
# ---------------------------------------------------------------------------


def test_throttle_first_call_does_not_sleep(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_next_request_time", 0.0)
    with patch("download_dukascopy_history.time.monotonic", return_value=1000.0), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        _throttle()
    mock_sleep.assert_not_called()


def test_throttle_second_call_within_interval_sleeps_the_remainder(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_next_request_time", 0.0)
    with patch("download_dukascopy_history.time.monotonic", return_value=1000.0), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        _throttle()  # reserves [1000.0, 1000.0 + MIN_REQUEST_INTERVAL_SECONDS)
    mock_sleep.assert_not_called()

    with patch("download_dukascopy_history.time.monotonic", return_value=1000.1), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        _throttle()  # only 0.1s has passed — must wait for the rest of the interval
    mock_sleep.assert_called_once()
    waited = mock_sleep.call_args[0][0]
    assert waited == pytest.approx(m.MIN_REQUEST_INTERVAL_SECONDS - 0.1, abs=1e-9)


def test_throttle_call_after_interval_elapsed_does_not_sleep(monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_next_request_time", 0.0)
    with patch("download_dukascopy_history.time.monotonic", return_value=1000.0), \
         patch("download_dukascopy_history.time.sleep"):
        _throttle()

    with patch(
        "download_dukascopy_history.time.monotonic",
        return_value=1000.0 + m.MIN_REQUEST_INTERVAL_SECONDS + 5.0,
    ), patch("download_dukascopy_history.time.sleep") as mock_sleep:
        _throttle()
    mock_sleep.assert_not_called()


def test_throttle_reserves_strictly_increasing_slots_even_with_a_frozen_clock(monkeypatch):
    """Simulates several worker threads calling _throttle() back-to-back
    with zero real time elapsed between them (a frozen time.monotonic()) —
    proves the pacer correctly serializes reservations (each call gets its
    own, later slot) rather than every call computing the same near-zero
    wait against a clock that hasn't moved. This is the property that
    actually caps sustained throughput regardless of --workers."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_next_request_time", 0.0)
    waits: list[float] = []
    with patch("download_dukascopy_history.time.monotonic", return_value=2000.0), \
         patch("download_dukascopy_history.time.sleep", side_effect=lambda w: waits.append(w)):
        for _ in range(5):
            _throttle()
    # first call: no wait; each subsequent call must wait one more full
    # interval than the last, since the clock never advances on its own.
    assert waits == [
        pytest.approx(m.MIN_REQUEST_INTERVAL_SECONDS * i, abs=1e-9) for i in range(1, 5)
    ]


def test_throttle_is_called_before_every_actual_http_attempt_in_fetch_hour():
    """fetch_hour() must call _throttle() before the initial attempt AND
    before every retry — never just once — since a retry is still a real
    HTTP request that could itself trigger a fresh 429 if unthrottled."""
    import download_dukascopy_history as m

    responses = [_fake_response(429), _fake_response(429), _fake_response(404)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep"), \
         patch.object(m, "_throttle") as mock_throttle:
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert mock_throttle.call_count == 3  # initial attempt + 2 retries


# ---------------------------------------------------------------------------
# point-value auto-detection
# ---------------------------------------------------------------------------


def test_detect_point_value_eurusd_scale_100000():
    # 1.08510 * 100000 = 108510 — plausible EURUSD range is (0.7, 1.8)
    raw_prices = [108510, 108520, 108490]
    assert detect_point_value(raw_prices, "EURUSD") == 100_000


def test_detect_point_value_usdjpy_scale_1000():
    # 148.512 * 1000 = 148512 — plausible USDJPY range is (75, 165)
    raw_prices = [148512, 148600]
    assert detect_point_value(raw_prices, "USDJPY") == 1_000


def test_detect_point_value_xauusd_scale_1000():
    # 2050.35 * 1000 = 2050350 is out of int32-friendly realism for this
    # synthetic case, so use a smaller, still-plausible XAUUSD sample:
    # 1950.20 * 1000 = 1950200
    raw_prices = [1950200, 1951000]
    assert detect_point_value(raw_prices, "XAUUSD") == 1_000


def test_detect_point_value_raises_when_no_candidate_matches():
    # Deliberately implausible for every candidate scale on EURUSD.
    raw_prices = [7]
    with pytest.raises(DukascopyFetchError, match="no candidate point value"):
        detect_point_value(raw_prices, "EURUSD")


def test_detect_point_value_raises_for_unmapped_symbol():
    with pytest.raises(DukascopyFetchError, match="No plausible price range"):
        detect_point_value([108510], "NOTASYMBOL")


def test_detect_point_value_raises_on_empty_sample():
    with pytest.raises(DukascopyFetchError, match="no raw prices"):
        detect_point_value([], "EURUSD")


def test_every_symbol_map_entry_has_a_plausible_range():
    # detect_point_value() cannot run for any symbol missing this —
    # a config-drift regression guard.
    missing = [s for s in SYMBOL_MAP if s not in _PLAUSIBLE_RANGES]
    assert missing == []


def test_candidate_scales_cover_the_documented_set():
    assert _POINT_VALUE_CANDIDATES == (100, 1_000, 10_000, 100_000)


# ---------------------------------------------------------------------------
# tick -> M15 OHLCV bucketing correctness
# ---------------------------------------------------------------------------


def test_ticks_to_m15_bars_mid_price_and_ohlc_bounds():
    # All four ticks fall within ms_offset 0-3000, well inside the first
    # 15-minute sub-bucket -> exactly one bar, same values the old H1-only
    # implementation would have produced for this same input.
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [
        (0, 108520, 108500, 1.0, 1.0),      # mid 108510
        (1000, 108600, 108580, 0.5, 0.5),   # mid 108590 (high)
        (2000, 108400, 108380, 0.5, 0.5),   # mid 108390 (low)
        (3000, 108450, 108430, 1.0, 1.0),   # mid 108440 (close)
    ]
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 1
    bar = bars[0]
    assert bar["datetime"] == hour
    assert bar["open"] == pytest.approx(1.08510)
    assert bar["high"] == pytest.approx(1.08590)
    assert bar["low"] == pytest.approx(1.08390)
    assert bar["close"] == pytest.approx(1.08440)
    assert bar["volume"] == pytest.approx(6.0)
    # validate_ohlcv's exact structural constraints, satisfied by construction:
    assert bar["high"] >= bar["low"]
    assert bar["high"] >= max(bar["open"], bar["close"])
    assert bar["low"] <= min(bar["open"], bar["close"])


def test_ticks_to_m15_bars_returns_empty_list_for_empty_ticks():
    hour = datetime(2024, 1, 6, 3, tzinfo=timezone.utc)  # closed Saturday hour
    assert ticks_to_m15_bars(hour, [], point_value=100_000) == []


def test_ticks_to_m15_bars_splits_across_multiple_sub_buckets():
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [
        (0, 108520, 108500, 1.0, 1.0),                  # bucket 0 (0-15min)
        (14 * 60 * 1000, 108530, 108510, 1.0, 1.0),     # bucket 0 (0-15min)
        (15 * 60 * 1000, 108600, 108580, 1.0, 1.0),     # bucket 1 (15-30min)
        (44 * 60 * 1000, 108610, 108590, 1.0, 1.0),     # bucket 2 (30-45min)
        (46 * 60 * 1000, 108400, 108380, 1.0, 1.0),     # bucket 3 (45-60min)
    ]
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 4
    assert [b["datetime"] for b in bars] == [
        hour,
        hour + timedelta(minutes=15),
        hour + timedelta(minutes=30),
        hour + timedelta(minutes=45),
    ]
    assert bars[0]["volume"] == pytest.approx(4.0)  # 2 ticks in bucket 0
    assert bars[1]["volume"] == pytest.approx(2.0)  # 1 tick in bucket 1
    assert bars[2]["volume"] == pytest.approx(2.0)  # 1 tick in bucket 2
    assert bars[3]["volume"] == pytest.approx(2.0)  # 1 tick in bucket 3


def test_ticks_to_m15_bars_clamps_ms_offset_near_hour_boundary():
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [(3_599_999, 108520, 108500, 1.0, 1.0)]  # a hair under 60 min -> bucket 3
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 1
    assert bars[0]["datetime"] == hour + timedelta(minutes=45)


def test_ticks_to_m15_bars_clamps_exact_hour_boundary_ms_offset():
    # ms_offset=3_600_000 is exactly one hour -> 3_600_000 // 900_000 == 4,
    # which the min(3, ...) clamp must land in the LAST sub-bucket (3),
    # never raise or silently drop the tick.
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [(3_600_000, 108520, 108500, 1.0, 1.0)]
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 1
    assert bars[0]["datetime"] == hour + timedelta(minutes=45)


def test_download_symbol_hours_produces_a_validate_ohlcv_clean_frame():
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
        datetime(2024, 1, 6, 3, tzinfo=timezone.utc),  # weekend -> 404, skipped
    ]

    def fake_get(url, timeout=None):
        if "2024/00/06" in url:
            return _fake_response(404)
        hour_num = int(url.rsplit("/", 1)[-1][:2])
        base = 108500 + hour_num
        records = [
            (0, base + 10, base - 10, 1.0, 1.0),
            (30_000, base + 20, base, 1.0, 1.0),
        ]
        return _fake_response(200, _compress_records(records))

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get), \
         patch("download_dukascopy_history.time.sleep"):
        df = download_symbol_hours("EURUSD", "EURUSD", hours, workers=2)

    assert len(df) == 2  # the weekend hour contributed no bar
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert validate_ohlcv(df) is True


def test_download_symbol_hours_skips_a_bad_hour_without_aborting():
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
    ]
    good_body = _compress_records([(0, 108510, 108490, 1.0, 1.0), (30_000, 108520, 108500, 1.0, 1.0)])

    def fake_get(url, timeout=None):
        if "09h_ticks" in url:
            return _fake_response(500)  # a genuine, non-404 failure
        return _fake_response(200, good_body)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get), \
         patch("download_dukascopy_history.time.sleep"):
        df = download_symbol_hours("EURUSD", "EURUSD", hours, workers=2)

    assert len(df) == 1  # the bad hour was skipped, not fatal to the whole download


# ---------------------------------------------------------------------------
# Resume/checkpointing (2026-08-18) — see the module docstring's "Resume/
# checkpointing" note: a real Dukascopy server-side outage (429s/503s/
# connect-timeouts, confirmed live) means download_symbol_hours() must make
# monotonic forward progress across repeated attempts instead of throwing
# away everything on every interruption/failure.
# ---------------------------------------------------------------------------


def test_download_symbol_hours_default_does_not_checkpoint(tmp_path, monkeypatch):
    """checkpoint defaults False — every existing direct caller (this
    whole file's other tests, --probe) must see zero filesystem side
    effects, exactly as before this feature existed."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [datetime(2024, 1, 2, 9, tzinfo=timezone.utc)]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=1)
    assert not (tmp_path / "checkpoints").exists()


def test_download_symbol_hours_checkpoints_completed_hours(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
        datetime(2024, 1, 6, 3, tzinfo=timezone.utc),  # weekend -> 404, closed-market
    ]

    def fake_get(url, timeout=None):
        if "2024/00/06" in url:
            return _fake_response(404)
        hour_num = int(url.rsplit("/", 1)[-1][:2])
        base = 108500 + hour_num
        return _fake_response(200, _compress_records([(0, base + 10, base - 10, 1.0, 1.0)]))

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get), \
         patch("download_dukascopy_history.time.sleep"):
        df = download_symbol_hours("EURUSD", "EURUSD", hours, workers=2, checkpoint=True)

    assert len(df) == 2
    hours_path, bars_path = m._checkpoint_paths("EURUSD")
    completed_lines = hours_path.read_text().splitlines()
    assert len(completed_lines) == 3  # all 3 hours, including the confirmed-closed one
    assert bars_path.exists()
    assert len(pd.read_csv(bars_path)) == 2  # only the 2 hours that actually had ticks


def test_download_symbol_hours_resumes_and_skips_already_checkpointed_hours(tmp_path, monkeypatch):
    """The regression test for the exact live bug: a second call with the
    SAME hours list must only fetch the hours NOT already checkpointed —
    proven here by making requests.get raise for any hour the resumed
    call would (incorrectly) re-fetch."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 11, tzinfo=timezone.utc),
    ]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    # First run: hour 09 succeeds, hours 10/11 fail (simulating a partial,
    # interrupted, or server-flaky first attempt).
    def fake_get_first(url, timeout=None):
        if "09h_ticks" in url:
            return _fake_response(200, body)
        return _fake_response(500)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get_first), \
         patch("download_dukascopy_history.time.sleep"):
        df1 = download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)
    assert len(df1) == 1

    fetched_second_run: list[str] = []

    def fake_get_second(url, timeout=None):
        fetched_second_run.append(url)
        return _fake_response(200, body)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get_second), \
         patch("download_dukascopy_history.time.sleep"):
        df2 = download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    # Only hours 10 and 11 (the ones that failed last time) should have been
    # re-fetched — hour 09 must NOT appear in the second run's real requests.
    assert len(fetched_second_run) == 2
    assert all("09h_ticks" not in url for url in fetched_second_run)
    assert len(df2) == 3  # 1 resumed from checkpoint + 2 newly fetched


def test_download_symbol_hours_resume_produces_no_duplicate_bar_rows(tmp_path, monkeypatch):
    """Explicit dedicated proof (beyond the row-count check above) that a
    resumed run's on-disk checkpoint never accumulates a duplicate bar row
    for an hour that was already checkpointed — every checkpointed
    datetime must be unique in bars.csv after two consecutive resumed
    runs over the SAME hour list."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
    ]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    def fake_get_first(url, timeout=None):
        if "09h_ticks" in url:
            return _fake_response(200, body)
        return _fake_response(503)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get_first), \
         patch("download_dukascopy_history.time.sleep"):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"):
        df2 = download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    _, bars_path = m._checkpoint_paths("EURUSD")
    on_disk = pd.read_csv(bars_path, parse_dates=["datetime"])
    assert on_disk["datetime"].duplicated().sum() == 0
    assert len(on_disk) == 2
    assert df2.index.duplicated().sum() == 0
    assert len(df2) == 2


def test_download_symbol_hours_a_permanently_failed_hour_is_never_checkpointed(tmp_path, monkeypatch):
    """A hour that exhausts its retry budget (a real, non-404 failure)
    must NEVER be written to completed_hours.txt — it needs to be
    retried automatically on the next run, not silently skipped forever."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
    ]
    good_body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    def fake_get(url, timeout=None):
        if "09h_ticks" in url:
            return _fake_response(403)  # a permanent rejection, never retried, never checkpointed
        return _fake_response(200, good_body)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get), \
         patch("download_dukascopy_history.time.sleep"):
        df = download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    assert len(df) == 1
    completed, _ = m._load_checkpoint("EURUSD")
    assert datetime(2024, 1, 2, 10, tzinfo=timezone.utc) in completed
    assert datetime(2024, 1, 2, 9, tzinfo=timezone.utc) not in completed  # the failed hour must be retried next time


def test_checkpoint_writes_bars_before_marking_the_hour_complete(tmp_path, monkeypatch):
    """The 'completed hour -> its bars durably represented' invariant: an
    interruption between the two checkpoint writes must never mark an
    hour complete while its bars are still missing on disk — proven here
    by recording the real write order, not just the end result."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [datetime(2024, 1, 2, 9, tzinfo=timezone.utc)]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])
    hours_path, bars_path = m._checkpoint_paths("EURUSD")

    write_order: list[str] = []
    real_to_csv = pd.DataFrame.to_csv

    def tracking_to_csv(self, path_or_buf=None, *args, **kwargs):
        if path_or_buf == bars_path:
            write_order.append("bars")
        return real_to_csv(self, path_or_buf, *args, **kwargs)

    real_open = Path.open

    def tracking_open(self, *args, **kwargs):
        if self == hours_path:
            write_order.append("hours")
        return real_open(self, *args, **kwargs)

    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"), \
         patch.object(pd.DataFrame, "to_csv", tracking_to_csv), \
         patch.object(Path, "open", tracking_open):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    assert write_order == ["bars", "hours"]


def test_download_symbol_hours_ctrl_c_raises_download_interrupted_and_leaves_a_consistent_checkpoint(tmp_path, monkeypatch):
    """Simulates a KeyboardInterrupt (Ctrl+C) partway through a run: must
    raise _DownloadInterrupted (never propagate a raw KeyboardInterrupt or
    hang), and whatever DID get checkpointed must stay internally
    consistent (every checkpointed hour has its bar(s) on disk) and
    cleanly resumable afterward."""
    import concurrent.futures as cf
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [datetime(2024, 1, 2, h, tzinfo=timezone.utc) for h in range(5)]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    real_as_completed = cf.as_completed

    def interrupting_as_completed(fs, *a, **kw):
        for i, fut in enumerate(real_as_completed(fs, *a, **kw)):
            if i == 2:
                raise KeyboardInterrupt()
            yield fut

    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"), \
         patch("download_dukascopy_history.as_completed", interrupting_as_completed):
        with pytest.raises(m._DownloadInterrupted):
            download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    completed, bars = m._load_checkpoint("EURUSD")
    assert 0 < len(completed) < 5  # a real partial run — not everything, not nothing
    assert len(bars) == len(completed)  # every checkpointed hour's bar row is present (1 bar/hour in this fixture)
    # _shutdown_requested is deliberately left SET after an interrupt (not
    # cleared here) so any still-running worker thread gets the best chance
    # to notice it and bail before its next sleep/HTTP attempt — it's only
    # cleared defensively at the START of the next download_symbol_hours()
    # call (see below), which is the real proof this isn't "stuck".

    # Resuming afterward must complete cleanly and reach the full dataset —
    # proving the leftover SET flag from above does not block a fresh call.
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"):
        df2 = download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)
    assert len(df2) == 5
    assert not m._shutdown_requested.is_set()  # a normal completion never leaves it set


def test_download_symbol_hours_resume_with_nothing_new_to_fetch_returns_checkpointed_data(tmp_path, monkeypatch):
    """Every hour already checkpointed -> zero new HTTP requests, but the
    full, previously-checkpointed dataset is still returned."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [datetime(2024, 1, 2, 9, tzinfo=timezone.utc)]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    with patch("download_dukascopy_history.requests.get", side_effect=AssertionError("must not fetch")) as mock_get, \
         patch("download_dukascopy_history.time.sleep"):
        df = download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    mock_get.assert_not_called()
    assert len(df) == 1


def test_clear_checkpoint_removes_both_files(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [datetime(2024, 1, 2, 9, tzinfo=timezone.utc)]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    hours_path, bars_path = m._checkpoint_paths("EURUSD")
    assert hours_path.exists() and bars_path.exists()

    m._clear_checkpoint("EURUSD")
    assert not hours_path.exists()
    assert not bars_path.exists()


def test_clear_checkpoint_is_a_no_op_when_nothing_was_ever_checkpointed(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    m._clear_checkpoint("NEVER_CHECKPOINTED")  # must not raise


def test_load_checkpoint_ignores_malformed_lines(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    (tmp_path / "checkpoints").mkdir()
    hours_path, _ = m._checkpoint_paths("EURUSD")
    hours_path.write_text("2024-01-02T09:00:00+00:00\nnot-a-real-timestamp\n\n")
    completed, bars = m._load_checkpoint("EURUSD")
    assert completed == {datetime(2024, 1, 2, 9, tzinfo=timezone.utc)}
    assert bars == []


# ---------------------------------------------------------------------------
# Unresolved-hour tracking & dataset completeness (v2, 2026-08-18) — a
# persistent transient failure must read as UNRESOLVED/RETRYABLE, never as
# missing data or a closed market, and a dataset with any unresolved hours
# must never be silently treated as complete.
# ---------------------------------------------------------------------------


def test_write_unresolved_checkpoint_rewrites_not_appends(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    h1 = datetime(2024, 1, 2, 9, tzinfo=timezone.utc)
    h2 = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)

    m._write_unresolved_checkpoint("EURUSD", [h1, h2], {h1: {"reason": "boom", "retryable": True}})
    assert len(m._load_unresolved_hours("EURUSD")) == 2

    # A second, smaller call must fully REPLACE the file, never append on
    # top of the first call's rows — an hour that's resolved by now must
    # disappear, not linger alongside a stale duplicate entry.
    m._write_unresolved_checkpoint("EURUSD", [h2], {})
    second = m._load_unresolved_hours("EURUSD")
    assert len(second) == 1
    assert second[0]["hour"] == h2.isoformat()


def test_load_unresolved_hours_returns_empty_list_when_no_file_exists(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    assert m._load_unresolved_hours("NEVER_RUN") == []


def test_load_unresolved_hours_ignores_malformed_lines(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    (tmp_path / "checkpoints").mkdir()
    path = m._unresolved_checkpoint_path("EURUSD")
    path.write_text(
        '{"hour": "2024-01-02T09:00:00+00:00", "reason": "boom", "retryable": true}\n'
        "not json\n"
        "\n"
    )
    records = m._load_unresolved_hours("EURUSD")
    assert len(records) == 1
    assert records[0]["hour"] == "2024-01-02T09:00:00+00:00"


def test_download_symbol_hours_persistent_timeout_is_unresolved_not_completed(tmp_path, monkeypatch):
    """The critical v2 semantic (explicit operator instruction): a
    persistently-failing TIMEOUT must read as UNRESOLVED/RETRYABLE — it
    must NEVER be silently reinterpreted as missing data or a closed
    market (a confirmed 404)."""
    import requests
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    bad_hour = datetime(2024, 1, 2, 9, tzinfo=timezone.utc)
    good_hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    def fake_get(url, timeout=None):
        if "09h_ticks" in url:
            raise requests.Timeout("boom")
        return _fake_response(200, body)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get), \
         patch("download_dukascopy_history.time.sleep"):
        download_symbol_hours("EURUSD", "EURUSD", [bad_hour, good_hour], workers=1, checkpoint=True)

    unresolved = m._load_unresolved_hours("EURUSD")
    assert len(unresolved) == 1
    record = unresolved[0]
    assert record["hour"] == bad_hour.isoformat()
    assert record["retryable"] is True
    assert "missing" not in record["reason"].lower()
    assert "closed" not in record["reason"].lower()

    completed, _ = m._load_checkpoint("EURUSD")
    assert bad_hour not in completed
    assert good_hour in completed


def test_download_symbol_hours_confirmed_closed_market_is_not_unresolved(tmp_path, monkeypatch):
    """The positive control distinguishing the case above: a genuine 404
    (confirmed closed market) is resolved, never left in the unresolved
    snapshot — proving the two outcomes are not conflated in either
    direction."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hour = datetime(2024, 1, 6, 3, tzinfo=timezone.utc)  # weekend
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(404)):
        download_symbol_hours("EURUSD", "EURUSD", [hour], workers=1, checkpoint=True)
    assert m._load_unresolved_hours("EURUSD") == []


def test_download_symbol_hours_permanent_rejection_is_unresolved_and_not_retryable(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hour = datetime(2024, 1, 2, 9, tzinfo=timezone.utc)
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(403)):
        download_symbol_hours("EURUSD", "EURUSD", [hour], workers=1, checkpoint=True)
    unresolved = m._load_unresolved_hours("EURUSD")
    assert len(unresolved) == 1
    assert unresolved[0]["retryable"] is False


def test_ctrl_c_interrupt_writes_unresolved_hours_including_never_attempted(tmp_path, monkeypatch):
    """Ctrl+C -> no false checkpoint, extended: every requested hour is
    accounted for after an interrupt — either checkpointed complete, or
    present in the unresolved snapshot with the honest 'not attempted this
    run' reason for hours the interrupt cut off before they were ever
    reached."""
    import concurrent.futures as cf
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [datetime(2024, 1, 2, h, tzinfo=timezone.utc) for h in range(5)]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    real_as_completed = cf.as_completed

    def interrupting_as_completed(fs, *a, **kw):
        for i, fut in enumerate(real_as_completed(fs, *a, **kw)):
            if i == 2:
                raise KeyboardInterrupt()
            yield fut

    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"), \
         patch("download_dukascopy_history.as_completed", interrupting_as_completed):
        with pytest.raises(m._DownloadInterrupted):
            download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    completed, _ = m._load_checkpoint("EURUSD")
    unresolved = m._load_unresolved_hours("EURUSD")
    unresolved_hours = {datetime.fromisoformat(r["hour"]) for r in unresolved}

    assert unresolved_hours == set(hours) - completed  # every hour accounted for, none silently dropped
    assert unresolved  # a real interrupt with only 1 worker leaves something unresolved
    # nothing in this run ever failed (every response was 200) — so every
    # unresolved hour got there purely by being cut off, never a real error.
    assert all("not attempted this run" in r["reason"] for r in unresolved)


def test_resume_after_persistent_failure_retries_and_clears_unresolved_hours(tmp_path, monkeypatch):
    """Resume -> retries unresolved hours, extended: once a previously-
    unresolved hour succeeds on a later run, it must disappear from the
    unresolved snapshot (rewritten, not appended)."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / "checkpoints")
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
    ]
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    def fake_get_first(url, timeout=None):
        if "09h_ticks" in url:
            return _fake_response(500)
        return _fake_response(200, body)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get_first), \
         patch("download_dukascopy_history.time.sleep"):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    unresolved_after_first = m._load_unresolved_hours("EURUSD")
    assert len(unresolved_after_first) == 1
    assert unresolved_after_first[0]["retryable"] is True

    # Resume: the previously-failing hour succeeds this time.
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=1, checkpoint=True)

    assert m._load_unresolved_hours("EURUSD") == []


def test_main_keeps_checkpoint_and_reports_partial_when_hours_unresolved(tmp_path, monkeypatch):
    """dataset-with-unresolved-hours -> PARTIAL: main() must NOT clear the
    checkpoint when a symbol finishes with any unresolved hours — the
    exact real bug this v2 work fixes (a partially-successful run
    previously destroyed its own resumability)."""
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / ".dukascopy_checkpoints")
    monkeypatch.setattr(sys, "argv", ["prog", "--symbols", "EURUSD", "--years", "0.001", "--workers", "1"])
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    failed_once = {"done": False}

    def fake_get(url, timeout=None):
        if not failed_once["done"]:
            failed_once["done"] = True
            return _fake_response(403)  # a permanent rejection — never resolves, never retried
        return _fake_response(200, body)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get), \
         patch("download_dukascopy_history.time.sleep"), \
         patch("research.manifest.write_manifest"):
        m.main()

    hours_path, bars_path = m._checkpoint_paths("EURUSD")
    assert hours_path.exists() and bars_path.exists()  # checkpoint KEPT — a partial run must stay resumable
    unresolved = m._load_unresolved_hours("EURUSD")
    assert len(unresolved) == 1
    assert unresolved[0]["retryable"] is False
    assert (tmp_path / "EURUSD_H1_dukascopy.csv").exists()  # a partial-but-real CSV is still written and useful


def test_main_clears_checkpoint_after_a_fully_successful_symbol_download(tmp_path, monkeypatch):
    import download_dukascopy_history as m

    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    monkeypatch.setattr(m, "_CHECKPOINT_DIR", tmp_path / ".dukascopy_checkpoints")
    monkeypatch.setattr(sys, "argv", ["prog", "--symbols", "EURUSD", "--years", "0.001", "--workers", "1"])
    body = _compress_records([(0, 108510, 108490, 1.0, 1.0)])

    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)), \
         patch("download_dukascopy_history.time.sleep"), \
         patch("research.manifest.write_manifest"):
        m.main()

    hours_path, bars_path = m._checkpoint_paths("EURUSD")
    assert not hours_path.exists()
    assert not bars_path.exists()
    assert not m._unresolved_checkpoint_path("EURUSD").exists()  # zero unresolved hours -> COMPLETE -> checkpoint (incl. unresolved snapshot) fully cleared
    assert (tmp_path / "EURUSD_H1_dukascopy.csv").exists()


def test_download_symbol_hours_staggers_only_the_initial_worker_pool_fill():
    """2026-08-11 — confirmed live on the VPS, twice: with all-429s and
    connect-timeouts fixed, the ONLY hours that still exhausted their
    full retry budget were the first `workers` submitted — the initial
    ThreadPoolExecutor fill firing near-simultaneously. Proves the
    stagger applies exactly workers-1 times (submissions 1..workers-1),
    never once the pool is already running (submissions workers..N-1)."""
    import download_dukascopy_history as m

    hours = [datetime(2024, 1, 2, h, tzinfo=timezone.utc) for h in range(10)]
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(404)), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep, \
         patch("download_dukascopy_history._throttle"):
        download_symbol_hours("EURUSD", "EURUSD", hours, workers=3)
    assert mock_sleep.call_count == 2  # workers - 1
    mock_sleep.assert_called_with(m._INITIAL_SUBMIT_STAGGER_SECONDS)


# ---------------------------------------------------------------------------
# coarsening M15 (base) -> H1/H4/D1
# ---------------------------------------------------------------------------


def test_coarsen_bars_m15_is_a_passthrough():
    idx = pd.date_range("2024-01-02", periods=3, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {"open": [1.0, 1.1, 1.2], "high": [1.05, 1.15, 1.25], "low": [0.95, 1.05, 1.15],
         "close": [1.02, 1.12, 1.22], "volume": [10, 20, 30]},
        index=idx,
    )
    out = coarsen_bars(df, "M15")
    pd.testing.assert_frame_equal(out, df)


def test_coarsen_bars_m15_to_h1_aggregates_correctly():
    idx = pd.date_range("2024-01-02 00:00", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {"open": [1.0, 1.1, 1.2, 1.3], "high": [1.05, 1.15, 1.25, 1.35],
         "low": [0.95, 1.05, 1.15, 1.25], "close": [1.02, 1.12, 1.22, 1.32],
         "volume": [10, 20, 30, 40]},
        index=idx,
    )
    out = coarsen_bars(df, "H1")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 1.0
    assert row["close"] == 1.32
    assert row["high"] == 1.35
    assert row["low"] == 0.95
    assert row["volume"] == 100
    assert validate_ohlcv(out) is True


def test_coarsen_bars_m15_to_h4_aggregates_across_16_bars():
    idx = pd.date_range("2024-01-02 00:00", periods=16, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1.0 + 0.01 * i for i in range(16)],
            "high": [1.05 + 0.01 * i for i in range(16)],
            "low": [0.95 + 0.01 * i for i in range(16)],
            "close": [1.02 + 0.01 * i for i in range(16)],
            "volume": [10] * 16,
        },
        index=idx,
    )
    out = coarsen_bars(df, "H4")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == pytest.approx(df["open"].iloc[0])
    assert row["close"] == pytest.approx(df["close"].iloc[-1])
    assert row["high"] == pytest.approx(df["high"].max())
    assert row["low"] == pytest.approx(df["low"].min())
    assert row["volume"] == pytest.approx(160)
    assert validate_ohlcv(out) is True


def test_m15_to_h1_coarsening_matches_direct_single_hour_tick_aggregation():
    """The module docstring's load-bearing correctness claim: building M15
    bars first, then coarsening to H1, must be mathematically identical to
    directly aggregating the whole hour's raw ticks into one H1 bar."""
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [
        (0, 108520, 108500, 1.0, 1.0),                # bucket 0 -> open
        (5 * 60 * 1000, 108600, 108580, 0.5, 0.5),    # bucket 0 -> overall high
        (20 * 60 * 1000, 108400, 108380, 0.5, 0.5),   # bucket 1 -> overall low
        (50 * 60 * 1000, 108450, 108430, 1.0, 1.0),   # bucket 3 -> close
    ]
    point_value = 100_000

    m15_bars = ticks_to_m15_bars(hour, ticks, point_value)
    m15_df = pd.DataFrame(m15_bars).set_index("datetime").sort_index()
    m15_df.index = pd.DatetimeIndex(m15_df.index, tz="UTC")
    coarsened = coarsen_bars(m15_df, "H1")

    # Direct single-hour aggregation of the SAME ticks, bypassing M15 entirely.
    mids = [((ask / point_value) + (bid / point_value)) / 2.0 for _, ask, bid, _, _ in ticks]
    total_volume = sum(ask_vol + bid_vol for _, _, _, ask_vol, bid_vol in ticks)

    assert len(coarsened) == 1
    row = coarsened.iloc[0]
    assert row["open"] == pytest.approx(mids[0])
    assert row["high"] == pytest.approx(max(mids))
    assert row["low"] == pytest.approx(min(mids))
    assert row["close"] == pytest.approx(mids[-1])
    assert row["volume"] == pytest.approx(total_volume)


# ---------------------------------------------------------------------------
# _hour_range — every returned hour must be hour-aligned (regression:
# the old start = end - timedelta(days=years * 365.25) computation broke
# this for any fractional-years value, e.g. --probe's hardcoded years=0.02,
# since 0.02 * 365.25 = 7 days + 7h19m12s exactly, not a whole number of
# hours — every subsequent hour in the returned list silently inherited
# that non-:00 time-of-day offset).
# ---------------------------------------------------------------------------


def _assert_all_hour_aligned(hours: list[datetime]) -> None:
    for h in hours:
        assert h.minute == 0 and h.second == 0 and h.microsecond == 0, h


def test_hour_range_fractional_years_stays_hour_aligned():
    # years=0.02 is exactly --probe's own hardcoded value — the case that
    # reproduced the bug live.
    hours = _hour_range(0.02)
    assert len(hours) > 0
    _assert_all_hour_aligned(hours)


def test_hour_range_consecutive_hours_are_exactly_one_hour_apart():
    hours = _hour_range(0.02)
    for a, b in zip(hours, hours[1:]):
        assert b - a == timedelta(hours=1)


def test_hour_range_last_entry_matches_hour_aligned_now():
    hours = _hour_range(0.02)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    # The range ends at (aligned "now" minus 1h) — the returned list never
    # includes the still-in-progress current hour.
    assert hours[-1] == end - timedelta(hours=1)


def test_hour_range_integer_years_matches_old_hour_count():
    # Integer years were never actually broken (365.25 * N is always a
    # whole multiple of 6 hours for integer N) — pin that the fix doesn't
    # change behavior for the real --years flag's normal usage.
    hours = _hour_range(1.0)
    assert len(hours) == int(1.0 * 365.25 * 24)
    _assert_all_hour_aligned(hours)


def test_hour_range_zero_years_returns_empty():
    assert _hour_range(0.0) == []


# ---------------------------------------------------------------------------
# symbol-map coverage / scope
# ---------------------------------------------------------------------------


def test_symbol_map_scoped_to_fx_metals_crypto_excludes_usoil_and_stocks():
    excluded = {"USOIL", "US30", "NAS100", "SPX500", "AAPL", "NVDA", "SPY", "QQQ"}
    assert excluded.isdisjoint(SYMBOL_MAP)


def test_default_symbols_are_all_mapped():
    assert set(DEFAULT_SYMBOLS).issubset(SYMBOL_MAP)


def test_fx_majors_minors_and_metals_map_1_to_1():
    high_confidence = [
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
        "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF", "XAUUSD", "XAGUSD",
    ]
    for sym in high_confidence:
        assert SYMBOL_MAP[sym] == sym
