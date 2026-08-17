"""tests/test_execution_attempts.py
--------------------------------------
storage/execution_attempts.py — a first-class, persisted record of every
attempted broker order, separate from storage/outcome_tracker.py's
`outcomes` table (see that module's own docstring for why the separation
matters: writing a REJECTED/TIMEOUT_UNKNOWN row into `outcomes` would
reopen the orphaned-open-position bug an earlier fix closed).
"""
from __future__ import annotations

import pytest

from storage.execution_attempts import (
    ACCEPTED,
    CANCELLED,
    REJECTED,
    TIMEOUT_UNKNOWN,
    record_execution_attempt,
    recent_attempts,
)


def test_record_accepted_attempt_round_trips():
    attempt_id = record_execution_attempt(
        symbol="EURUSD", broker="ctrader", direction="BUY", status=ACCEPTED,
        signal_id="sig1", requested_volume=1000.0,
        requested_entry=1.0850, requested_sl=1.0800, requested_tp=1.0950,
        normalized_entry=1.08501, position_id="pos123",
    )
    assert attempt_id

    rows = recent_attempts(symbol="EURUSD")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "ACCEPTED"
    assert row["symbol"] == "EURUSD"
    assert row["broker"] == "ctrader"
    assert row["position_id"] == "pos123"
    assert row["signal_id"] == "sig1"


def test_record_rejected_attempt_stores_broker_error():
    record_execution_attempt(
        symbol="ETHUSD", broker="ctrader", direction="BUY", status=REJECTED,
        requested_volume=200.0, requested_entry=3214.67,
        requested_sl=3200.0, requested_tp=3300.0,
        broker_error_code="INVALID_REQUEST",
        broker_error_message="Relative stop loss has invalid precision",
    )
    rows = recent_attempts(symbol="ETHUSD")
    assert rows[0]["status"] == "REJECTED"
    assert rows[0]["broker_error_code"] == "INVALID_REQUEST"
    assert "invalid precision" in rows[0]["broker_error_message"]


def test_record_timeout_unknown_attempt():
    record_execution_attempt(
        symbol="XAUUSD", broker="ctrader", direction="SELL", status=TIMEOUT_UNKNOWN,
        broker_error_message="Order timed out after 15.0s",
    )
    rows = recent_attempts(symbol="XAUUSD")
    assert rows[0]["status"] == "TIMEOUT_UNKNOWN"


def test_invalid_status_raises_rather_than_silently_recording_garbage():
    with pytest.raises(ValueError, match="status must be one of"):
        record_execution_attempt(
            symbol="EURUSD", broker="ctrader", direction="BUY", status="MAYBE",
        )


def test_cancelled_is_a_valid_terminal_status():
    record_execution_attempt(
        symbol="GBPUSD", broker="ctrader", direction="BUY", status=CANCELLED,
    )
    rows = recent_attempts(symbol="GBPUSD")
    assert rows[0]["status"] == "CANCELLED"


def test_recent_attempts_scoped_by_symbol_does_not_leak_other_symbols():
    record_execution_attempt(symbol="AAA", broker="ctrader", direction="BUY", status=ACCEPTED)
    record_execution_attempt(symbol="BBB", broker="ctrader", direction="BUY", status=ACCEPTED)
    rows = recent_attempts(symbol="AAA")
    assert all(r["symbol"] == "AAA" for r in rows)


def test_recent_attempts_unscoped_returns_across_symbols():
    record_execution_attempt(symbol="CCC", broker="ctrader", direction="BUY", status=ACCEPTED)
    record_execution_attempt(symbol="DDD", broker="ctrader", direction="SELL", status=REJECTED)
    rows = recent_attempts()
    symbols = {r["symbol"] for r in rows}
    assert "CCC" in symbols and "DDD" in symbols


def test_recent_attempts_newest_first():
    record_execution_attempt(symbol="ORDR", broker="ctrader", direction="BUY", status=ACCEPTED)
    record_execution_attempt(symbol="ORDR", broker="ctrader", direction="SELL", status=REJECTED)
    rows = recent_attempts(symbol="ORDR")
    assert rows[0]["direction"] == "SELL"  # most recent insert first
    assert rows[1]["direction"] == "BUY"


def test_recent_attempts_respects_limit():
    for _ in range(5):
        record_execution_attempt(symbol="LIMSYM", broker="ctrader", direction="BUY", status=ACCEPTED)
    rows = recent_attempts(symbol="LIMSYM", limit=2)
    assert len(rows) == 2


def test_record_execution_attempt_never_raises_on_a_storage_failure(monkeypatch):
    """The public write function is best-effort: a D1/storage-layer
    failure must never propagate out to the caller (a real execution
    outcome must never be masked by a bookkeeping failure)."""
    import storage.execution_attempts as m

    def _boom(*a, **kw):
        raise RuntimeError("D1 unreachable")

    monkeypatch.setattr(m, "_init_db", _boom)
    attempt_id = record_execution_attempt(
        symbol="ERRSYM", broker="ctrader", direction="BUY", status=ACCEPTED,
    )
    assert attempt_id  # still returns a generated id even though persistence failed
