"""
storage/shadow_divergence_membership.py
---------------------------------------
Hypothesis Discovery Engine -- Family Membership Writer (operator's own
locked Design Gate, built directly on Gate 0C's Family Membership
Record Contract and Gate 0B's Divergence-Claim Attempt Semantics).

A research_shadow_divergence_membership row is the IMMUTABLE,
once-only record of the moment one hypothesis_id first satisfied the
locked "attempted" condition (n_T >= 40 AND a catastrophic-divergence
p-value was actually produced) -- the sole event that admits a
hypothesis into the catastrophic-divergence statistical family. This
module is bookkeeping only: it NEVER decides whether the condition was
met (that is backtest.shadow_divergence_membership.record_family_
membership_if_attempted()'s own job, one layer up), never computes
tp_count/sl_count/p_value itself, and never reads storage.shadow_record
or backtest.shadow_outcome_aggregate. Matching every other storage/*.py
file in this codebase, this module never imports backtest/*.py.

NO n_eff/k_eff COLUMNS (operator's own locked decision): Gate 0F
designed the within-hypothesis dependence methodology (connected-
component clustering, one deterministic representative per cluster),
but that methodology is NOT YET wired into backtest.shadow_outcome_
aggregate.compute_catastrophic_divergence_p_value() -- which still
computes its p-value from the raw n_T. Adding n_eff/k_eff columns now
would be dishonest schema: columns with no current producer. A future,
separate, additive migration (ADD COLUMN only, matching storage.
migrations.py's own locked convention) is the correct way to add them
once a real producer exists.

n_T is NEVER stored as its own column -- it is always
tp_count_at_entry + sl_count_at_entry, derived, never a second source
of truth that could drift from the two counts it is built from.

IMMUTABLE / INSERT-ONLY (operator's own locked Gate 0C contract): one
row per hypothesis_id, written exactly once, at first qualifying entry.
No update/delete function exists here, matching storage.hypothesis_
promotion's and storage.shadow_decision_snapshot's own append-only
precedent. Later re-evaluations of the same hypothesis_id (whatever
their outcome) NEVER touch this row -- membership, once recorded, is a
historical fact, not a live state.

hypothesis_id IS the membership key -- PRIMARY KEY directly (Gate 0C's
own locked decision: "hypothesis_id نفسه هو membership key... لا نحتاج
hash(hypothesis_id)"). No composite key, no separate deterministic hash
-- the identity question this contract answers is simple enough not to
need one.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error

_DDL_DIVERGENCE_MEMBERSHIP = """
CREATE TABLE IF NOT EXISTS research_shadow_divergence_membership (
    hypothesis_id           TEXT PRIMARY KEY,
    hypothesis_fingerprint  TEXT NOT NULL,
    research_code_commit    TEXT,
    tp_count_at_entry       INTEGER NOT NULL,
    sl_count_at_entry       INTEGER NOT NULL,
    p_value_at_entry        REAL NOT NULL,
    baseline_p_at_entry     REAL NOT NULL,
    request_ids_json        TEXT NOT NULL,
    entered_at              TEXT NOT NULL
)
"""


def _init(con) -> None:
    con.execute(_DDL_DIVERGENCE_MEMBERSHIP)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    d["request_ids"] = json.loads(d.pop("request_ids_json"))
    return d


def try_insert_membership(
    *, hypothesis_id: str, hypothesis_fingerprint: str, research_code_commit: str | None,
    tp_count_at_entry: int, sl_count_at_entry: int, p_value_at_entry: float,
    baseline_p_at_entry: float, request_ids: list[str],
) -> dict[str, Any] | None:
    """A single INSERT, guarded by the PRIMARY KEY on hypothesis_id. If
    a membership row already exists for this hypothesis_id, the INSERT
    itself raises a genuine PRIMARY-KEY-constraint D1Error -- caught
    here and translated into an ordinary `None` return (nothing was
    inserted; the EXISTING row is untouched, membership stays exactly
    as it was first recorded). Any OTHER D1Error (a real database
    failure) is deliberately NOT caught here -- it propagates,
    fail-closed."""
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        try:
            con.execute(
                """INSERT INTO research_shadow_divergence_membership
                   (hypothesis_id, hypothesis_fingerprint, research_code_commit,
                    tp_count_at_entry, sl_count_at_entry, p_value_at_entry,
                    baseline_p_at_entry, request_ids_json, entered_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (hypothesis_id, hypothesis_fingerprint, research_code_commit,
                 tp_count_at_entry, sl_count_at_entry, p_value_at_entry,
                 baseline_p_at_entry, json.dumps(request_ids), now),
            )
        except D1Error as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
            return None
        row = con.execute(
            "SELECT * FROM research_shadow_divergence_membership WHERE hypothesis_id=?",
            (hypothesis_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_membership(hypothesis_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_shadow_divergence_membership WHERE hypothesis_id=?",
            (hypothesis_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def count_members() -> int:
    """COUNT(*) is exact and sufficient -- hypothesis_id is already the
    PRIMARY KEY, so no DISTINCT is needed. This is the future n_trials
    source for Bonferroni/family-size correction (NOT wired to it yet
    -- that remains a separate, future, independently-authorized step)."""
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT COUNT(*) AS n FROM research_shadow_divergence_membership").fetchone()
    return row["n"]
