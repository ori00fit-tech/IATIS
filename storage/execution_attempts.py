"""
storage/execution_attempts.py
--------------------------------
A first-class, persisted record of every ATTEMPTED broker order — filled,
rejected, or unconfirmed-on-timeout — separate from `storage/
outcome_tracker.py`'s `outcomes` table.

Why a separate table rather than writing into `outcomes` directly: an
earlier fix (main.py -> scheduler.py, see that commit's own message)
deliberately stopped logging a signal into `outcomes` unless
`exec_result.executed` is True, specifically because logging every
EXECUTE-verdict signal regardless of the broker's real response had
created "orphan" rows that inflated `risk/live_portfolio_state.py`'s
`current_open_risk_pct` and confused `execution/reconciliation.py`'s
broker-vs-internal diff — a real live-trading bug (4 phantom open
positions, MISMATCH x6, exposure pinned at cap). Writing a REJECTED/
TIMEOUT_UNKNOWN row into `outcomes` would reopen that exact bug.

This table exists so a rejected/timeout order is never just a log line —
an operator (or a future dashboard panel) can see WHAT was attempted,
WHY the broker declined it, and what the requested vs. normalized values
were, without that record ever being mistaken for an open position by
any of `outcomes`' existing consumers (all of which stay untouched).
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from storage import d1_client
from utils.logger import get_logger

logger = get_logger(__name__)

ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
TIMEOUT_UNKNOWN = "TIMEOUT_UNKNOWN"
CANCELLED = "CANCELLED"
TERMINAL_STATUSES = (ACCEPTED, REJECTED, TIMEOUT_UNKNOWN, CANCELLED)


@contextmanager
def _conn():
    with d1_client.d1_connection() as con:
        yield con


def _init_db() -> None:
    with _conn() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS execution_attempts (
            id                TEXT PRIMARY KEY,
            signal_id         TEXT,
            symbol            TEXT NOT NULL,
            broker            TEXT NOT NULL,
            direction         TEXT NOT NULL,
            requested_volume  REAL,
            requested_entry   REAL,
            requested_sl      REAL,
            requested_tp      REAL,
            normalized_entry  REAL,
            normalized_sl     REAL,
            normalized_tp     REAL,
            status            TEXT NOT NULL,
            broker_error_code TEXT,
            broker_error_message TEXT,
            position_id       TEXT,
            created_at        TEXT NOT NULL
        )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_exec_attempts_symbol ON execution_attempts(symbol)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_exec_attempts_status ON execution_attempts(status)"
        )


def record_execution_attempt(
    *,
    symbol: str,
    broker: str,
    direction: str,
    status: str,
    signal_id: str | None = None,
    requested_volume: float | None = None,
    requested_entry: float | None = None,
    requested_sl: float | None = None,
    requested_tp: float | None = None,
    normalized_entry: float | None = None,
    normalized_sl: float | None = None,
    normalized_tp: float | None = None,
    broker_error_code: str | None = None,
    broker_error_message: str | None = None,
    position_id: str | None = None,
) -> str:
    """Persist one terminal execution attempt. Returns the generated id.

    `status` must be one of TERMINAL_STATUSES — every attempted order
    ends in exactly one of these, never left ambiguous. Never raises out
    to the caller on a storage hiccup (matches every other non-critical
    D1 write in this codebase, e.g. scheduler.py's Symbol Health check) —
    a failure to PERSIST the record must never itself abort or mask the
    real execution outcome the caller already has in hand.
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"status must be one of {TERMINAL_STATUSES}, got {status!r}")

    attempt_id = uuid.uuid4().hex
    try:
        _init_db()
        with _conn() as con:
            con.execute(
                """
                INSERT INTO execution_attempts
                (id, signal_id, symbol, broker, direction, requested_volume,
                 requested_entry, requested_sl, requested_tp,
                 normalized_entry, normalized_sl, normalized_tp,
                 status, broker_error_code, broker_error_message, position_id,
                 created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    attempt_id, signal_id, symbol, broker, direction,
                    requested_volume, requested_entry, requested_sl, requested_tp,
                    normalized_entry, normalized_sl, normalized_tp,
                    status, broker_error_code, broker_error_message, position_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception as exc:
        logger.warning(f"execution_attempts: failed to persist {status} for {symbol}: {exc}")
    return attempt_id


def recent_attempts(symbol: str | None = None, limit: int = 50) -> list[dict]:
    """Most recent attempts, newest first — optionally scoped to one symbol."""
    _init_db()
    with _conn() as con:
        if symbol:
            cur = con.execute(
                "SELECT * FROM execution_attempts WHERE symbol=? "
                "ORDER BY created_at DESC LIMIT ?",
                (symbol, limit),
            )
        else:
            cur = con.execute(
                "SELECT * FROM execution_attempts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(row) for row in cur.fetchall()]
