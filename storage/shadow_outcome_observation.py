"""
storage/shadow_outcome_observation.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15E — D1 persistence for the SHADOW
Outcome Observation event log.

LOCKED MODEL (operator's own Persistence Semantics Design Gate):
APPEND-ONLY OBSERVATION EVENT LOG. `request_id` is DELIBERATELY NOT
unique in this table -- a single governed decision (one request_id) may
legitimately accumulate many independent observation rows over time, as
backtest.shadow_outcome_resolver.resolve_decision_outcome() is called
again at different wall-clock moments and sees more (or different) bars.
Each row is an honest, timestamped record of "what the resolver concluded
at evaluated_at" -- never a claim that any outcome is the final, terminal
truth. TP_HIT/SL_HIT/TIMEOUT/DATA_GAP's own terminality is explicitly
NOT assumed here or anywhere in this phase (operator's own locked Gate 0
finding: historical OHLC immutability is NOT PROVEN anywhere in this
codebase).

NON-NEGOTIABLE: this module is bookkeeping only. It NEVER computes an
outcome itself (that is backtest.shadow_outcome_resolver's own job,
unchanged, still pure, still unaware this module exists), and it NEVER
performs an UPDATE of any kind -- confirmed by this module's own
structural tests. A row, once inserted, is never modified. Matching
every other storage/*.py file in this codebase, this module never
imports backtest/*.py.

The real, DB-level `UNIQUE(request_id, evaluated_at)` index is the
collision guard for retries -- it is NOT a general "one evidence per
decision" constraint (there is a SEPARATE, non-unique index on
request_id alone for ordinary lookups). Translating a UNIQUE-constraint
violation into "the same observation, recorded again" vs. "a genuinely
different, colliding observation" is backtest.shadow_outcome_observation's
own job, one layer up -- this module only ever returns `None` on any
such violation, as bookkeeping, never a verdict about which case applies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error

_DDL_SHADOW_OUTCOME_OBSERVATIONS = """
CREATE TABLE IF NOT EXISTS research_shadow_outcome_observations (
    seq                  INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id       TEXT NOT NULL UNIQUE,
    request_id           TEXT NOT NULL,
    hypothesis_id        TEXT NOT NULL,
    outcome              TEXT NOT NULL,
    resolved_bar_time    TEXT,
    evaluated_at         TEXT NOT NULL,
    created_at           TEXT NOT NULL
)
"""
_DDL_SHADOW_OUTCOME_OBSERVATIONS_REQUEST_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rsoo_request ON research_shadow_outcome_observations(request_id)"
)
_DDL_SHADOW_OUTCOME_OBSERVATIONS_HYPOTHESIS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rsoo_hypothesis ON research_shadow_outcome_observations(hypothesis_id)"
)
# THE collision guard -- a real DB constraint, never a general
# one-row-per-decision uniqueness rule (see this module's own docstring).
_DDL_SHADOW_OUTCOME_OBSERVATIONS_RETRY_UNIQUE_IDX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rsoo_request_evaluated_at ON "
    "research_shadow_outcome_observations(request_id, evaluated_at)"
)


def _init(con) -> None:
    con.execute(_DDL_SHADOW_OUTCOME_OBSERVATIONS)
    con.execute(_DDL_SHADOW_OUTCOME_OBSERVATIONS_REQUEST_IDX)
    con.execute(_DDL_SHADOW_OUTCOME_OBSERVATIONS_HYPOTHESIS_IDX)
    con.execute(_DDL_SHADOW_OUTCOME_OBSERVATIONS_RETRY_UNIQUE_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def try_insert(
    *, observation_id: str, request_id: str, hypothesis_id: str, outcome: str,
    resolved_bar_time: str | None, evaluated_at: str,
) -> dict[str, Any] | None:
    """A single INSERT -- always a NEW row, never an update of an
    existing one (this function contains no UPDATE statement anywhere,
    confirmed by this phase's own structural tests). Guarded only by the
    UNIQUE index on (request_id, evaluated_at): a genuine collision on
    that exact pair raises a D1Error containing "UNIQUE constraint
    failed", caught here and translated into an ordinary `None` return --
    this module makes NO judgment about whether the colliding row is an
    idempotent retry or a real conflict; that classification is
    backtest.shadow_outcome_observation's own job. Any OTHER D1Error (a
    real database failure) is deliberately NOT caught here -- it
    propagates, fail-closed."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        try:
            con.execute(
                """INSERT INTO research_shadow_outcome_observations
                   (observation_id, request_id, hypothesis_id, outcome, resolved_bar_time,
                    evaluated_at, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (observation_id, request_id, hypothesis_id, outcome, resolved_bar_time, evaluated_at, now),
            )
        except D1Error as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
            return None
        row = con.execute(
            "SELECT * FROM research_shadow_outcome_observations WHERE observation_id=?", (observation_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_observation_by_request_id_and_evaluated_at(request_id: str, evaluated_at: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_shadow_outcome_observations WHERE request_id=? AND evaluated_at=?",
            (request_id, evaluated_at),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_observations_for_request(request_id: str) -> list[dict[str, Any]]:
    """Every observation ever recorded for one request_id, newest first
    -- deliberately NOT collapsed to "the latest one"; this module itself
    never decides what "latest" means, that is a pure, backtest-layer
    concern."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_shadow_outcome_observations WHERE request_id=? ORDER BY seq DESC",
            (request_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_observations_for_hypothesis(hypothesis_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """The most recent `limit` observation rows for one hypothesis_id,
    newest first -- a plain row-count window, mirroring storage.
    hypothesis_live_request.list_live_identity_requests_for_hypothesis()'s
    own exact read shape. May contain multiple rows for the same
    request_id -- reducing that to "latest per request_id" is NOT this
    module's job (see backtest.shadow_outcome_observation.
    reduce_to_latest_observations())."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            """SELECT * FROM research_shadow_outcome_observations
               WHERE hypothesis_id=? ORDER BY seq DESC LIMIT ?""",
            (hypothesis_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
