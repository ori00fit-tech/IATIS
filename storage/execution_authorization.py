"""
storage/execution_authorization.py
---------------------------------------
Hypothesis Discovery Engine, Phase 9 — D1 persistence for execution.
authorization's ExecutionAuthorization state machine. Same `_DDL` +
`_init(con)` idiom as every other ledger in this engine, and the SAME
atomic compare-and-set idiom already proven in storage/research_matrix.py
(claim_queued_cells()/claim_candidate_cells(), Forensic Audit Finding 1):
every state transition is a single `UPDATE ... WHERE authorization_id=?
AND status=<expected>` statement, and the caller trusts ONLY `D1Cursor.
rowcount == 1` to know whether ITS OWN call was the one that actually
applied the transition. A concurrent caller racing on the same
authorization_id sees rowcount == 0 and loses the race cleanly — no
double-claim, no double-submission, no last-writer-wins ambiguity.

NON-NEGOTIABLE: this module is bookkeeping + atomic transitions only. It
never decides what MAY be submitted (that is execution.authorization's
job, one layer up, which already imports this module), never calls a
broker, never imports execution.trade_executor or any broker client.
Matching every other storage/*.py file in this codebase, this module
never imports backtest/*.py or execution/*.py.

Locked Phase 9 state machine (operator's own final contract):

    ISSUED
     ├── CLAIMED
     │    └── SUBMITTED
     │          ├── CONFIRMED
     │          ├── REJECTED
     │          └── UNKNOWN
     │                 ├── CONFIRMED
     │                 └── REJECTED
     ├── REVOKED   (from ISSUED only -- never from CLAIMED or later)
     └── EXPIRED   (materialized LAZILY, at a claim() attempt past
                    expires_at -- never a proactive background sweep)

Deliberately absent from this module (per the locked contract): no
reclaim/retry/auto-expire-from-CLAIMED function exists anywhere here. An
authorization orphaned at CLAIMED or SUBMITTED by a process death stays
exactly there, forever, until a FUTURE, separate recovery contract
defines what may happen to it. Inventing that now was explicitly refused.

`resolved_at` is set ONLY by the two functions that reach a genuinely
terminal CONFIRMED/REJECTED outcome (resolve(), reconcile()) -- NEVER by
mark_unknown(), whose entire purpose is to represent "not yet known",
not "resolved to unknown".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client

ISSUED = "ISSUED"
CLAIMED = "CLAIMED"
SUBMITTED = "SUBMITTED"
UNKNOWN = "UNKNOWN"
CONFIRMED = "CONFIRMED"
REJECTED = "REJECTED"
REVOKED = "REVOKED"
EXPIRED = "EXPIRED"

TERMINAL_STATUSES = frozenset({CONFIRMED, REJECTED, REVOKED, EXPIRED})

_DDL_EXECUTION_AUTHORIZATIONS = """
CREATE TABLE IF NOT EXISTS research_execution_authorizations (
    seq                     INTEGER PRIMARY KEY AUTOINCREMENT,
    authorization_id        TEXT NOT NULL UNIQUE,

    decision_id             TEXT NOT NULL,
    policy_event_id         TEXT NOT NULL,
    hypothesis_id           TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    timeframe               TEXT NOT NULL,
    side                    TEXT NOT NULL,
    entry_type              TEXT NOT NULL,

    risk_preset             TEXT NOT NULL,
    risk_definition_hash    TEXT NOT NULL,

    status                  TEXT NOT NULL,
    client_order_id         TEXT NOT NULL UNIQUE,

    broker_order_id         TEXT,
    broker_state            TEXT,

    issued_at               TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    claimed_at              TEXT,
    submitted_at            TEXT,
    resolved_at             TEXT,
    revoked_at              TEXT,

    denial_reason           TEXT
)
"""
_DDL_EXECUTION_AUTHORIZATIONS_DECISION_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rea_decision ON research_execution_authorizations(decision_id)"
)
_DDL_EXECUTION_AUTHORIZATIONS_STATUS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rea_status ON research_execution_authorizations(status)"
)
_DDL_EXECUTION_AUTHORIZATIONS_CLIENT_ORDER_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rea_client_order ON research_execution_authorizations(client_order_id)"
)


def _init(con) -> None:
    con.execute(_DDL_EXECUTION_AUTHORIZATIONS)
    con.execute(_DDL_EXECUTION_AUTHORIZATIONS_DECISION_IDX)
    con.execute(_DDL_EXECUTION_AUTHORIZATIONS_STATUS_IDX)
    con.execute(_DDL_EXECUTION_AUTHORIZATIONS_CLIENT_ORDER_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def insert_issued(
    *, authorization_id: str, decision_id: str, policy_event_id: str, hypothesis_id: str,
    symbol: str, timeframe: str, side: str, entry_type: str,
    risk_preset: str, risk_definition_hash: str, client_order_id: str,
    issued_at: str, expires_at: str,
) -> dict[str, Any]:
    """A plain INSERT of a new ISSUED row. Uniqueness of authorization_id
    and client_order_id is enforced by the table's own UNIQUE
    constraints -- a caller-side collision (should never happen given
    random generation one layer up) surfaces as a D1Error, not a silent
    overwrite."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_execution_authorizations
               (authorization_id, decision_id, policy_event_id, hypothesis_id, symbol, timeframe,
                side, entry_type, risk_preset, risk_definition_hash, status, client_order_id,
                issued_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                authorization_id, decision_id, policy_event_id, hypothesis_id, symbol, timeframe,
                side, entry_type, risk_preset, risk_definition_hash, ISSUED, client_order_id,
                issued_at, expires_at,
            ),
        )
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row)


def get_authorization(authorization_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def try_claim(authorization_id: str) -> dict[str, Any] | None:
    """Atomic ISSUED -> CLAIMED. Two statements, both guarded by rowcount:

    1. The happy path: flip to CLAIMED only if still ISSUED and not yet
       past expires_at. rowcount==1 means THIS call won the race.
    2. Only if (1) didn't apply: lazily materialize EXPIRED for a row
       that's still (nominally) ISSUED but whose expires_at has already
       passed -- the locked contract's own "materialize at claim time,
       never a proactive sweep" rule. rowcount==1 here just means this
       call was the one that recorded the expiry; it never means this
       call may proceed.

    Either way, returns the CURRENT row after the attempt (whatever
    status it now holds) so the caller can tell exactly what happened --
    never raises for an ordinary lost race or an expired authorization,
    matching claim_queued_cells()'s own established precedent that a
    lost race is a normal, non-exceptional outcome.

    Returns None only if authorization_id does not exist at all."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """UPDATE research_execution_authorizations
               SET status=?, claimed_at=? WHERE authorization_id=? AND status=? AND expires_at>?""",
            (CLAIMED, now, authorization_id, ISSUED, now),
        )
        con.execute(
            """UPDATE research_execution_authorizations
               SET status=?, denial_reason=? WHERE authorization_id=? AND status=? AND expires_at<=?""",
            (EXPIRED, "expired before being claimed", authorization_id, ISSUED, now),
        )
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def try_revoke(authorization_id: str, reason: str) -> dict[str, Any] | None:
    """Atomic ISSUED -> REVOKED only -- the locked contract's own explicit
    refusal to allow revoke after CLAIMED (a CLAIMED authorization is
    already committed to, at most, one submission attempt; racing a
    revoke against that would reopen exactly the ambiguity atomicity was
    built to remove)."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """UPDATE research_execution_authorizations
               SET status=?, revoked_at=?, denial_reason=? WHERE authorization_id=? AND status=?""",
            (REVOKED, now, reason, authorization_id, ISSUED),
        )
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def try_begin_submission(authorization_id: str) -> dict[str, Any] | None:
    """Atomic CLAIMED -> SUBMITTED. This IS the "at most one submission
    attempt" gate: it must be called, and must be seen to succeed
    (rowcount==1), BEFORE any adapter/broker call is made -- once this
    returns a row with status==SUBMITTED, the system must assume an
    external submission attempt MAY have occurred, even if the adapter
    call that follows never returns or throws before telling us anything.
    A second call against an already-SUBMITTED (or later) row always
    loses -- there is no function anywhere in this module that could
    make it succeed twice."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """UPDATE research_execution_authorizations
               SET status=?, submitted_at=? WHERE authorization_id=? AND status=?""",
            (SUBMITTED, now, authorization_id, CLAIMED),
        )
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def try_resolve(
    authorization_id: str, outcome: str, *, broker_order_id: str | None, broker_state: str | None,
) -> dict[str, Any] | None:
    """Atomic SUBMITTED -> {CONFIRMED, REJECTED}. Sets resolved_at --
    this is a genuinely terminal outcome, reached directly from a
    definitive adapter response (not through UNKNOWN)."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """UPDATE research_execution_authorizations
               SET status=?, resolved_at=?, broker_order_id=?, broker_state=?
               WHERE authorization_id=? AND status=?""",
            (outcome, now, broker_order_id, broker_state, authorization_id, SUBMITTED),
        )
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def try_mark_unknown(authorization_id: str, *, broker_state: str | None) -> dict[str, Any] | None:
    """Atomic SUBMITTED -> UNKNOWN. resolved_at is deliberately NEVER
    touched here -- UNKNOWN is "not yet known", not a resolution."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """UPDATE research_execution_authorizations
               SET status=?, broker_state=? WHERE authorization_id=? AND status=?""",
            (UNKNOWN, broker_state, authorization_id, SUBMITTED),
        )
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def try_reconcile(
    authorization_id: str, outcome: str, *, broker_order_id: str | None, broker_state: str | None,
) -> dict[str, Any] | None:
    """Atomic UNKNOWN -> {CONFIRMED, REJECTED}. Sets resolved_at -- this
    IS the moment an ambiguous outcome becomes genuinely resolved. There
    is deliberately no "still unknown" write: a reconciliation attempt
    that cannot yet resolve the authorization simply makes no call here
    at all (see list_unknown_authorizations() for how such rows are
    found again on a later pass) -- never a transition to itself, never a
    retry counter, never an automatic timeout-based resolution."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """UPDATE research_execution_authorizations
               SET status=?, resolved_at=?, broker_order_id=COALESCE(?, broker_order_id), broker_state=?
               WHERE authorization_id=? AND status=?""",
            (outcome, now, broker_order_id, broker_state, authorization_id, UNKNOWN),
        )
        row = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE authorization_id=?", (authorization_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_unknown_authorizations(limit: int = 100) -> list[dict[str, Any]]:
    """The reconciliation worker's own read primitive -- lists rows stuck
    at UNKNOWN, oldest first. This is NOT the forbidden "enumerate the
    Policy Registry to decide what to run" shape (backtest.hypothesis_
    live_request's own non-negotiable #1): it never selects what to
    AUTHORIZE or SUBMIT, only what already-submitted, already-ambiguous
    outcomes still need a human/operational reconciliation pass."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_execution_authorizations WHERE status=? ORDER BY seq ASC LIMIT ?",
            (UNKNOWN, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
