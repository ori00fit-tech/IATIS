"""
storage/shadow_decision_snapshot.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15D — D1 persistence for the SHADOW
Decision Snapshot.

A research_shadow_decision_snapshots row is the IMMUTABLE record of what a
governed SHADOW decision's own entry/stop/target levels actually were at
the exact moment backtest.hypothesis_live_request.evaluate_live_identity_
request() (Phase 8C) computed them — captured via that function's own
`decision_snapshot` return key (a Phase 15D-authorized controlled
extension of Phase 8C, never a re-run of run_pipeline()). This module
NEVER computes, derives, or validates any of these values itself — it is
bookkeeping only, trusting the caller's already-validated snapshot dict
completely (backtest.shadow_decision_snapshot.capture_decision_snapshot()
is the one place that validation already happened).

NON-NEGOTIABLE: this module never writes to research_live_identity_
requests, research_execution_attribution, outcomes, or shadow_signals,
and never resolves an outcome, reads a market bar, or computes divergence
— that is a separate, later Phase 15D producer's job, not this phase's.
Matching every other storage/*.py file in this codebase, this module
never imports backtest/*.py.

request_id is UNIQUE — one Live Identity Request has at most one snapshot
(1:1), mirroring Phase 15C's own research_execution_attribution cardinality
rule exactly.

bar_time is NOT NULL and is never substituted with this table's own
captured_at — bar_time is the market bar timestamp the ORIGINAL report's
computation was based on (T_D for every future look-ahead check, per the
Phase 15D pre-registration spec, docs/
PHASE_15D_SHADOW_OUTCOME_DIVERGENCE_EVIDENCE.md); captured_at is merely
this row's own wall-clock insert time and must never be read as T_D by
anything downstream.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error

_DDL_SHADOW_DECISION_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS research_shadow_decision_snapshots (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id    TEXT NOT NULL UNIQUE,
    request_id     TEXT NOT NULL UNIQUE,
    hypothesis_id  TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    bar_time       TEXT NOT NULL,
    side           TEXT NOT NULL,
    entry_price    REAL NOT NULL,
    stop_loss      REAL NOT NULL,
    take_profit    REAL NOT NULL,
    captured_at    TEXT NOT NULL
)
"""
_DDL_SHADOW_DECISION_SNAPSHOTS_HYPOTHESIS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rsds_hypothesis ON research_shadow_decision_snapshots(hypothesis_id)"
)


def _init(con) -> None:
    con.execute(_DDL_SHADOW_DECISION_SNAPSHOTS)
    con.execute(_DDL_SHADOW_DECISION_SNAPSHOTS_HYPOTHESIS_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def try_insert(
    *, snapshot_id: str, request_id: str, hypothesis_id: str, symbol: str, timeframe: str,
    bar_time: str, side: str, entry_price: float, stop_loss: float, take_profit: float,
) -> dict[str, Any] | None:
    """A single INSERT, guarded by the UNIQUE index on request_id. If a
    snapshot already exists for this request_id, the INSERT itself raises
    a genuine UNIQUE-constraint D1Error -- caught here and translated into
    an ordinary `None` return (nothing was inserted; the EXISTING row is
    untouched) -- backtest.shadow_decision_snapshot's own job to then
    fetch and return that existing row (idempotent re-capture), never a
    crash. Any OTHER D1Error (a real database failure) is deliberately NOT
    caught here -- it propagates, fail-closed."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        try:
            con.execute(
                """INSERT INTO research_shadow_decision_snapshots
                   (snapshot_id, request_id, hypothesis_id, symbol, timeframe, bar_time, side,
                    entry_price, stop_loss, take_profit, captured_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (snapshot_id, request_id, hypothesis_id, symbol, timeframe, bar_time, side,
                 entry_price, stop_loss, take_profit, now),
            )
        except D1Error as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
            return None
        row = con.execute(
            "SELECT * FROM research_shadow_decision_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_snapshot_by_request_id(request_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_shadow_decision_snapshots WHERE request_id=?", (request_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_request_ids_for_hypothesis(hypothesis_id: str) -> list[str]:
    """Every request_id that ever had a decision snapshot captured for
    this hypothesis_id -- complete, duplicate-free (request_id is UNIQUE
    on this table), deliberately NO LIMIT. This is the canonical
    enumeration source for n_T(H) (docs/SHADOW_EVIDENCE_UNIT_CLOSURE.md):
    unlike list_observations_for_hypothesis() (storage.shadow_outcome_
    observation, a row-count LIMIT window that can also hold several
    rows per request_id), a row here exists iff that request's entry/
    stop/target were captured at all -- the one precondition resolve_
    decision_outcome() needs to ever run for it."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT request_id FROM research_shadow_decision_snapshots WHERE hypothesis_id=?",
            (hypothesis_id,),
        ).fetchall()
    return [row["request_id"] for row in rows]
