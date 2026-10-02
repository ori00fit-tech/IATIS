"""
storage/policy_registry.py
---------------------------------------
Hypothesis Discovery Engine, Phase 10 — D1 persistence for the Policy
Registry. A DIFFERENT table and a DIFFERENT purpose from storage.
hypothesis_policy's `research_symbol_policy_events` ledger (operator's
own locked distinction, Phase 10 contract):

    Policy Event Ledger (Phase 6, UNCHANGED, never extended)
        = append-only history of GRANT/REVOKE actions -- evidence/audit.
    Policy Registry (this module, Phase 10, NEW)
        = the CURRENT state of what is eligible to be selected for live
          consideration -- a first-class entity with its own lifecycle,
          never a derived view or an extension of the ledger's columns.

NON-NEGOTIABLE: this module is bookkeeping + atomic transitions only. It
never calls storage.hypothesis_policy.get_latest_policy_event() itself --
that FRESH re-verification against the Phase 6 ledger is backtest.
policy_registry's own job (one layer up), at BOTH validate_policy() time
AND activate_policy() time (the operator's own explicit TOCTOU closure:
a GRANT revoked between VALIDATED and ACTIVATE must deny the activation,
never silently proceed on a stale VALIDATED status alone). Matching every
other storage/*.py file in this codebase, this module never imports
backtest/*.py.

Locked Phase 10 lifecycle (operator's own final contract):

    DRAFT
     ├── VALIDATED
     │    ├── ACTIVE
     │    └── REVOKED
     └── REVOKED
    ACTIVE
     └── REVOKED

REVOKED is terminal from anywhere non-terminal. There is no transition
out of REVOKED, and (per the locked contract) DRAFT/VALIDATED/ACTIVE
never transition directly into each other except along this exact chain.

Uniqueness is a REAL DB-level invariant, not a Python-side check: a
partial UNIQUE index on (symbol, timeframe, regime_profile) WHERE
status='ACTIVE' makes "two ACTIVE policies for the same scope" a state
the database itself refuses to create -- try_activate()'s own atomic
UPDATE can raise a genuine UNIQUE-constraint D1Error, which this module
translates into an ordinary, non-exceptional denial (the row stays
VALIDATED) -- matching storage.execution_authorization's own "a lost
race is not a bug" precedent, never a crash.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error

DRAFT = "DRAFT"
VALIDATED = "VALIDATED"
ACTIVE = "ACTIVE"
REVOKED = "REVOKED"

TERMINAL_STATUSES = frozenset({REVOKED})

_DDL_POLICIES = """
CREATE TABLE IF NOT EXISTS research_policies (
    seq                     INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id               TEXT NOT NULL UNIQUE,
    policy_version          TEXT NOT NULL,
    status                  TEXT NOT NULL,

    symbol                  TEXT NOT NULL,
    timeframe               TEXT NOT NULL,
    regime_profile          TEXT NOT NULL,

    hypothesis_id           TEXT NOT NULL,
    engine                  TEXT NOT NULL,
    engine_version          TEXT NOT NULL,
    decision_type           TEXT NOT NULL,
    risk_preset             TEXT NOT NULL,
    risk_definition_hash    TEXT NOT NULL,
    bundle_id               TEXT,

    policy_event_id         TEXT NOT NULL,

    created_at              TEXT NOT NULL,
    activated_at            TEXT,
    revoked_at              TEXT,
    denial_reason           TEXT
)
"""
# NOT unique -- several DRAFT/VALIDATED/REVOKED rows for the same scope
# may coexist (re-validation attempts, history); only ACTIVE is
# exclusive, enforced by the partial index below.
_DDL_POLICIES_SCOPE_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rp_scope ON research_policies(symbol, timeframe, regime_profile)"
)
# THE uniqueness invariant -- a real DB constraint, not a Python check
# (operator's own explicit requirement). SQLite (and D1, which is
# SQLite-backed) supports partial unique indexes.
_DDL_POLICIES_ACTIVE_SCOPE_UNIQUE_IDX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rp_active_scope ON research_policies"
    "(symbol, timeframe, regime_profile) WHERE status='ACTIVE'"
)
_DDL_POLICIES_STATUS_IDX = "CREATE INDEX IF NOT EXISTS idx_rp_status ON research_policies(status)"


def _init(con) -> None:
    con.execute(_DDL_POLICIES)
    con.execute(_DDL_POLICIES_SCOPE_IDX)
    con.execute(_DDL_POLICIES_ACTIVE_SCOPE_UNIQUE_IDX)
    con.execute(_DDL_POLICIES_STATUS_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def insert_draft(
    *, policy_id: str, policy_version: str, symbol: str, timeframe: str, regime_profile: str,
    hypothesis_id: str, engine: str, engine_version: str, decision_type: str,
    risk_preset: str, risk_definition_hash: str, bundle_id: str | None, policy_event_id: str,
) -> dict[str, Any]:
    """A plain INSERT of a new DRAFT row. This function performs NO
    verification of policy_event_id against the real Phase 6 ledger --
    that happens later, at validate_policy() and again at
    activate_policy() (backtest.policy_registry's own job)."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_policies
               (policy_id, policy_version, status, symbol, timeframe, regime_profile,
                hypothesis_id, engine, engine_version, decision_type, risk_preset,
                risk_definition_hash, bundle_id, policy_event_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                policy_id, policy_version, DRAFT, symbol, timeframe, regime_profile,
                hypothesis_id, engine, engine_version, decision_type, risk_preset,
                risk_definition_hash, bundle_id, policy_event_id, _now_iso(),
            ),
        )
        row = con.execute("SELECT * FROM research_policies WHERE policy_id=?", (policy_id,)).fetchone()
    return _row_to_dict(row)


def get_policy(policy_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM research_policies WHERE policy_id=?", (policy_id,)).fetchone()
    return _row_to_dict(row) if row else None


def try_set_validated(policy_id: str) -> dict[str, Any] | None:
    """Atomic DRAFT -> VALIDATED. The caller (backtest.policy_registry)
    must have ALREADY confirmed a fresh GRANTED Phase 6 event exists
    before calling this -- this function only performs the atomic
    bookkeeping transition itself, never the re-verification."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            "UPDATE research_policies SET status=? WHERE policy_id=? AND status=?",
            (VALIDATED, policy_id, DRAFT),
        )
        row = con.execute("SELECT * FROM research_policies WHERE policy_id=?", (policy_id,)).fetchone()
    return _row_to_dict(row) if row else None


def try_set_active(policy_id: str) -> dict[str, Any] | None:
    """Atomic VALIDATED -> ACTIVE, guarded by the partial UNIQUE index on
    (symbol, timeframe, regime_profile) WHERE status='ACTIVE'. A
    concurrent activation of a DIFFERENT policy for the SAME scope
    causes this UPDATE itself to raise a genuine UNIQUE-constraint
    D1Error -- caught here and translated into an ordinary denial (the
    row stays VALIDATED, `denial_reason` records why), matching storage.
    execution_authorization's own "a lost race is not a bug" precedent.
    Any OTHER D1Error (a real database failure) is deliberately NOT
    caught here -- it propagates, fail-closed."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        try:
            con.execute(
                "UPDATE research_policies SET status=?, activated_at=? WHERE policy_id=? AND status=?",
                (ACTIVE, now, policy_id, VALIDATED),
            )
        except D1Error as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
            con.execute(
                "UPDATE research_policies SET denial_reason=? WHERE policy_id=? AND status=?",
                ("another ACTIVE policy already holds this symbol/timeframe/regime_profile scope",
                 policy_id, VALIDATED),
            )
        row = con.execute("SELECT * FROM research_policies WHERE policy_id=?", (policy_id,)).fetchone()
    return _row_to_dict(row) if row else None


def try_revoke(policy_id: str, reason: str) -> dict[str, Any] | None:
    """Atomic {DRAFT, VALIDATED, ACTIVE} -> REVOKED -- the locked
    contract's own explicit allowance: REVOKED is reachable from every
    non-terminal state, since in Phase 10 it means "this policy itself is
    no longer usable", not "a specific execution attempt was cancelled"
    (storage.execution_authorization's own, narrower REVOKED, from
    ISSUED only)."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            "UPDATE research_policies SET status=?, revoked_at=?, denial_reason=? "
            "WHERE policy_id=? AND status IN (?,?,?)",
            (REVOKED, now, reason, policy_id, DRAFT, VALIDATED, ACTIVE),
        )
        row = con.execute("SELECT * FROM research_policies WHERE policy_id=?", (policy_id,)).fetchone()
    return _row_to_dict(row) if row else None


def find_active_policy(symbol: str, timeframe: str, regime_profile: str) -> list[dict[str, Any]]:
    """The resolver's ONE read primitive -- a single query by exact
    scope, never an enumeration of grants/hypotheses/drafts. Returns
    every ACTIVE row matching this exact scope: 0 (NO_POLICY), 1
    (ELIGIBLE_POLICY), or -- only possible via a corrupted/legacy write
    that bypassed the partial unique index -- more than 1 (CONFLICT).
    backtest.policy_registry's own resolve_eligible_policy() classifies
    the result; this function never does."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_policies WHERE symbol=? AND timeframe=? AND regime_profile=? AND status=?",
            (symbol, timeframe, regime_profile, ACTIVE),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
