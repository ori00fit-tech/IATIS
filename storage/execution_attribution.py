"""
storage/execution_attribution.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15C — D1 persistence for the Execution
Attribution Foundation.

A research_execution_attribution row is a DECISION-IDENTITY record only --
"this real, stored Live Identity Request (Phase 8C's own request_id) is
attributable to this hypothesis_id" -- NEVER evidence that any downstream
execution happened. authorization_id/execution_attempt_id/trade_id/
outcome_signal_id are reserved, NULLABLE columns with NO populator in this
phase: nothing in Phase 15C ever writes a non-NULL value into any of them.
They exist so a FUTURE phase (15D) can populate them once real
authorization/attempt/fill/outcome entities exist, without ever altering
this table's schema again.

NON-NEGOTIABLE: this module is bookkeeping only. It never resolves a
request_id's hypothesis_id itself (backtest.execution_attribution's own
job, one layer up, by reading storage.hypothesis_live_request fresh) and
never writes to research_live_identity_requests, research_execution_
authorizations, execution_attempts, fills, or outcomes. Matching every
other storage/*.py file in this codebase, this module never imports
backtest/*.py.

request_id is UNIQUE -- one Live Identity Request has at most one
attribution row (1:1), never deduplicated across different request_ids for
the same hypothesis_id (a hypothesis legitimately has many decisions,
hence many attribution rows over its SHADOW lifetime).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error

_DDL_EXECUTION_ATTRIBUTION = """
CREATE TABLE IF NOT EXISTS research_execution_attribution (
    seq                     INTEGER PRIMARY KEY AUTOINCREMENT,
    attribution_id          TEXT NOT NULL UNIQUE,
    request_id              TEXT NOT NULL UNIQUE,
    hypothesis_id           TEXT NOT NULL,
    policy_id               TEXT,
    authorization_id        TEXT,
    execution_attempt_id    TEXT,
    trade_id                TEXT,
    outcome_signal_id       TEXT,
    created_at              TEXT NOT NULL
)
"""
_DDL_EXECUTION_ATTRIBUTION_HYPOTHESIS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rea_hypothesis ON research_execution_attribution(hypothesis_id)"
)


def _init(con) -> None:
    con.execute(_DDL_EXECUTION_ATTRIBUTION)
    con.execute(_DDL_EXECUTION_ATTRIBUTION_HYPOTHESIS_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def try_insert(*, attribution_id: str, request_id: str, hypothesis_id: str) -> dict[str, Any] | None:
    """A single INSERT, guarded by the UNIQUE index on request_id. If a row
    already exists for this request_id, the INSERT itself raises a genuine
    UNIQUE-constraint D1Error -- caught here and translated into an
    ordinary `None` return (nothing was inserted; the EXISTING row is
    untouched) -- backtest.execution_attribution's own job to then fetch
    and return that existing row (idempotent re-creation), never a crash.
    policy_id/authorization_id/execution_attempt_id/trade_id/
    outcome_signal_id are always NULL on insert -- this function accepts no
    parameters for any of them. Any OTHER D1Error (a real database failure)
    is deliberately NOT caught here -- it propagates, fail-closed."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        try:
            con.execute(
                """INSERT INTO research_execution_attribution
                   (attribution_id, request_id, hypothesis_id, created_at)
                   VALUES (?,?,?,?)""",
                (attribution_id, request_id, hypothesis_id, now),
            )
        except D1Error as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
            return None
        row = con.execute(
            "SELECT * FROM research_execution_attribution WHERE attribution_id=?", (attribution_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_attribution_by_request_id(request_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_execution_attribution WHERE request_id=?", (request_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_attribution_by_id(attribution_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_execution_attribution WHERE attribution_id=?", (attribution_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None
