"""
storage/live_roster.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15A — D1 persistence for the SHADOW
Live Evaluation Roster.

Roster membership is a durable, auditable FACT -- "this hypothesis_id was
admitted to SHADOW consideration, and here is the promotion_gate snapshot
that justified it at that moment" -- never a cache of CURRENT eligibility.
A fresh eligibility re-check every SHADOW cycle (backtest.live_roster.
check_current_eligibility(), consuming a FRESH backtest.promotion_gate.
evaluate_promotion_gate() result the caller supplies) is a completely
separate question from "is this hypothesis_id still on the Roster" -- this
module only answers the second question, never the first.

NON-NEGOTIABLE: this module is bookkeeping only. It never calls
backtest.promotion_gate.evaluate_promotion_gate() itself, never inspects
promotion_gate_snapshot_json's own contents to make a decision, and never
auto-removes a row -- a failed fresh eligibility check skips that cycle
only (backtest.shadow_observation's job, one layer up), it never calls
try_remove() as a side effect of that. Matching every other storage/*.py
file in this codebase, this module never imports backtest/*.py.

Uniqueness is a REAL DB-level invariant (Phase 10's own established
pattern, reused verbatim): a partial UNIQUE index on (hypothesis_id)
WHERE status='ACTIVE' makes "two ACTIVE roster entries for the same
hypothesis_id" a state the database itself refuses to create.
try_insert_active()'s own INSERT can raise a genuine UNIQUE-constraint
D1Error -- caught here and translated into an ordinary, non-exceptional
`None` return (nothing was inserted, the existing ACTIVE row is
untouched), matching storage.policy_registry's own "a lost race is not a
bug" precedent, never a crash.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error

ACTIVE = "ACTIVE"
REMOVED = "REMOVED"

_DDL_LIVE_ROSTER = """
CREATE TABLE IF NOT EXISTS research_live_roster (
    seq                            INTEGER PRIMARY KEY AUTOINCREMENT,
    roster_entry_id                TEXT NOT NULL UNIQUE,
    hypothesis_id                  TEXT NOT NULL,
    status                         TEXT NOT NULL,

    added_at                       TEXT NOT NULL,
    added_by                       TEXT NOT NULL,
    added_reason                   TEXT NOT NULL,

    removed_at                     TEXT,
    removed_by                     TEXT,
    removed_reason                 TEXT,

    promotion_gate_snapshot_json   TEXT NOT NULL
)
"""
_DDL_LIVE_ROSTER_HYPOTHESIS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rlr_hypothesis ON research_live_roster(hypothesis_id)"
)
# THE uniqueness invariant -- a real DB constraint, not a Python check
# (Phase 10's own established precedent, reused verbatim).
_DDL_LIVE_ROSTER_ACTIVE_HYPOTHESIS_UNIQUE_IDX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rlr_active_hypothesis ON research_live_roster"
    "(hypothesis_id) WHERE status='ACTIVE'"
)
_DDL_LIVE_ROSTER_STATUS_IDX = "CREATE INDEX IF NOT EXISTS idx_rlr_status ON research_live_roster(status)"


def _init(con) -> None:
    con.execute(_DDL_LIVE_ROSTER)
    con.execute(_DDL_LIVE_ROSTER_HYPOTHESIS_IDX)
    con.execute(_DDL_LIVE_ROSTER_ACTIVE_HYPOTHESIS_UNIQUE_IDX)
    con.execute(_DDL_LIVE_ROSTER_STATUS_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def try_insert_active(
    *, roster_entry_id: str, hypothesis_id: str, added_by: str, added_reason: str,
    promotion_gate_snapshot_json: str,
) -> dict[str, Any] | None:
    """A single INSERT of a new ACTIVE roster row, guarded by the partial
    UNIQUE index on (hypothesis_id) WHERE status='ACTIVE'. If an ACTIVE
    entry already exists for this hypothesis_id, the INSERT itself raises
    a genuine UNIQUE-constraint D1Error -- caught here and translated into
    an ordinary `None` return (nothing was inserted; the EXISTING ACTIVE
    row is untouched), never a crash. Any OTHER D1Error (a real database
    failure) is deliberately NOT caught here -- it propagates, fail-closed."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        try:
            con.execute(
                """INSERT INTO research_live_roster
                   (roster_entry_id, hypothesis_id, status, added_at, added_by, added_reason,
                    promotion_gate_snapshot_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (roster_entry_id, hypothesis_id, ACTIVE, now, added_by, added_reason,
                 promotion_gate_snapshot_json),
            )
        except D1Error as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
            return None
        row = con.execute(
            "SELECT * FROM research_live_roster WHERE roster_entry_id=?", (roster_entry_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_roster_entry(roster_entry_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_live_roster WHERE roster_entry_id=?", (roster_entry_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_active_entry_for_hypothesis(hypothesis_id: str) -> dict[str, Any] | None:
    """ONE query by exact hypothesis_id -- never an enumeration. The
    partial unique index guarantees at most one ACTIVE row can ever exist
    for a given hypothesis_id, so this returns a single row or None, never
    a list the caller would need to disambiguate."""
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_live_roster WHERE hypothesis_id=? AND status=?",
            (hypothesis_id, ACTIVE),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_active_entries() -> list[dict[str, Any]]:
    """Every current ACTIVE roster row -- the one enumeration this module
    exposes, for an external caller (a future scheduler, out of this
    phase's scope) to build its own per-cycle entry list from. backtest.
    shadow_observation.run_shadow_cycle() itself never calls this -- it
    only consumes an already-built list the caller supplies."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_live_roster WHERE status=? ORDER BY seq", (ACTIVE,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def try_remove(roster_entry_id: str, removed_by: str, removed_reason: str) -> dict[str, Any] | None:
    """Atomic ACTIVE -> REMOVED. Returns the row UNCHANGED (whatever its
    current status already is) if it isn't ACTIVE -- a no-op, never an
    exception -- matching storage.policy_registry.try_revoke()'s own
    idempotent-no-op precedent. Returns None only when roster_entry_id is
    genuinely unknown."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            "UPDATE research_live_roster SET status=?, removed_at=?, removed_by=?, removed_reason=? "
            "WHERE roster_entry_id=? AND status=?",
            (REMOVED, now, removed_by, removed_reason, roster_entry_id, ACTIVE),
        )
        row = con.execute(
            "SELECT * FROM research_live_roster WHERE roster_entry_id=?", (roster_entry_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None
