"""
storage/matrix_ai_recommendations.py
-----------------------------------------
Hypothesis Discovery Engine, Phase 3B — AI Research Orchestrator, Stage
3B.3: Recommendation persistence / audit trail.

One row per AI-proposed research plan (backtest.matrix_research_planner
+ ai.ai_analyzer.AIAnalyzer.propose_matrix_research_plan()). Same
`_DDL` + `_init(con)` idiom as storage/research_matrix.py — brand-new
tables, created lazily on first use, no storage/migrations.py entry needed
(that file is for ALTERing existing tables — see its own docstring).

NON-NEGOTIABLE (operator's own explicit Phase 3B boundary): `status` here
is ALWAYS one of DRAFT / APPROVED / REJECTED — deliberately a DIFFERENT
vocabulary than research_matrix_cells.status's own QUEUED/.../VALIDATED/
REJECTED state machine, so a recommendation's review status can never be
visually or textually confused with a Matrix cell's own authoritative
evidence-gate verdict. APPROVED means "a human reviewed this proposal and
thinks it's worth generating real cells for" — it does NOT create,
promote, or validate anything by itself. review_recommendation() below
executes exactly one atomic UPDATE, against this table (plus an append-
only INSERT into the review-history table below) — it never calls
anything in storage.research_matrix, so "APPROVED" is structurally
incapable of creating a cell/family or touching registry.json. Converting
an approved recommendation into real research_matrix_cells rows would be
Phase 3C ("Controlled Recommendation Conversion" — a genuinely new
capability boundary, not built here) and remains, until then, a
deliberate, separate, human-triggered action through the existing
POST /research/matrix/generate endpoint, never automatic.

Phase 3B-H hardening pass 2 (second audit's findings):
  - `model` (a single column that could silently mismatch what actually
    ran) is replaced by `requested_model`/`actual_model` — see
    record_recommendation()'s own docstring.
  - `review_recommendation()` is now an atomic compare-and-swap
    (`UPDATE ... WHERE status='DRAFT'`) instead of a separate SELECT-
    then-UPDATE, and raises a distinguishable error when the target row
    is no longer DRAFT (the caller maps this to HTTP 409) rather than
    silently overwriting a prior review.
  - Every review action (including ones later superseded) is appended to
    `research_matrix_ai_recommendation_reviews`, an append-only history
    table — re-reviewing a recommendation never erases the record of an
    earlier review. The main row's own reviewed_by/reviewed_at/
    review_note columns remain a denormalized "latest review" cache for
    simple queries; the history table is the authoritative full record.

Phase 3B-H re-audit fix (operator's own follow-up, 2026-08-22): the CAS
UPDATE and the history INSERT above USED TO be two separate
`con.execute()` calls. storage/d1_client.py's own module docstring is
explicit that this is NOT atomic as a group — each `execute()` is its
own independent HTTPS request/independent D1 statement, so a crash or
network failure between the two calls could leave a recommendation's
status changed with NO corresponding row in the append-only history
table, silently breaking the "every status transition is recorded"
guarantee this table exists for. Fixed by moving both statements into
one `d1_client.d1_batch()` call (Cloudflare D1's own `.batch()` API —
"all succeed or all fail", the exact same primitive storage/
decision_db.py already relies on for its own decision+engine_votes
atomicity). The history INSERT is written as a conditional
`INSERT ... SELECT ... WHERE changes() = 1` — SQLite's `changes()`
reports how many rows the immediately preceding statement in the SAME
connection/batch modified — so a CAS that lost the race (WHERE
status='DRAFT' matched zero rows) can never produce a spurious history
row claiming a transition happened when it didn't, regardless of what
status the row happens to already be in.

Hash verification procedure (documented per the second audit's LOW
finding): evidence_snapshot_hash (execution/routes/matrix_ai.py, via
backtest.matrix_research_planner.evidence_snapshot_hash) is computed as
sha256(json.dumps(context, sort_keys=True, default=str)). To verify a
stored row's evidence_snapshot_json against its evidence_snapshot_hash,
an auditor MUST re-canonicalize before hashing — `json.loads()` the
stored text back into a dict, then `json.dumps(parsed, sort_keys=True,
default=str)` before sha256 — never hash the raw stored TEXT directly
(it was persisted via plain `json.dumps(..., default=str)`, without
sort_keys, so its key order is not guaranteed to match the hash's own
canonical serialization even though the CONTENT is identical).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_RECOMMENDATIONS = """
CREATE TABLE IF NOT EXISTS research_matrix_ai_recommendations (
    recommendation_id           TEXT PRIMARY KEY,
    provider                    TEXT NOT NULL,
    requested_model             TEXT,
    actual_model                TEXT,
    input_family_ids_json       TEXT NOT NULL,
    input_cell_ids_json         TEXT,
    evidence_snapshot_json      TEXT NOT NULL,
    evidence_snapshot_hash      TEXT NOT NULL,
    constraints_used_json       TEXT NOT NULL,
    focus_hint                  TEXT,
    reasoning_summary           TEXT NOT NULL,
    coverage_gaps_json          TEXT,
    proposed_next_cells_json    TEXT NOT NULL,
    distinct_from_dead_list     TEXT,
    priority                    TEXT,
    status                      TEXT NOT NULL,
    reviewed_by                 TEXT,
    reviewed_at                 TEXT,
    review_note                 TEXT,
    converted_at                TEXT,
    created_at                  TEXT NOT NULL
)
"""
_DDL_STATUS_IDX = "CREATE INDEX IF NOT EXISTS idx_rmair_status ON research_matrix_ai_recommendations(status)"

# Append-only review history — Phase 3B-H hardening pass 2. One row per
# review ACTION (not per recommendation): a recommendation reviewed twice
# has two rows here, oldest first by rowid/reviewed_at.
_DDL_REVIEWS = """
CREATE TABLE IF NOT EXISTS research_matrix_ai_recommendation_reviews (
    review_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id TEXT NOT NULL,
    old_status        TEXT NOT NULL,
    new_status        TEXT NOT NULL,
    reviewed_by       TEXT,
    reviewed_at       TEXT NOT NULL,
    review_note       TEXT
)
"""
_DDL_REVIEWS_IDX = "CREATE INDEX IF NOT EXISTS idx_rmairr_recommendation ON research_matrix_ai_recommendation_reviews(recommendation_id)"

# Phase 3C (Controlled Recommendation Conversion) — kept as a SEPARATE
# table from the reviews table above rather than adding nullable
# conversion-only columns to it: a conversion carries a materially
# different payload (family_id, cell count, commit provenance, a content
# hash) than a review action (old/new status, a note), and a
# recommendation is converted AT MOST ONCE (both recommendation_id and
# family_id are UNIQUE here — one recommendation can never produce two
# families, and one family can never claim to come from two
# recommendations). converted_at above is a denormalized "latest state"
# cache on the main row, matching how reviewed_at already sits there;
# this table is the authoritative, append-worthy-in-principle (though
# never more than one row per recommendation today) detail record.
_DDL_CONVERSIONS = """
CREATE TABLE IF NOT EXISTS research_matrix_ai_recommendation_conversions (
    conversion_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id         TEXT NOT NULL UNIQUE,
    family_id                 TEXT NOT NULL UNIQUE,
    proposed_next_cells_hash  TEXT NOT NULL,
    cells_considered          INTEGER NOT NULL,
    proposed_at_commit        TEXT,
    converted_at_commit       TEXT,
    commit_drift              INTEGER NOT NULL,
    converted_by              TEXT,
    converted_at              TEXT NOT NULL
)
"""
_DDL_CONVERSIONS_IDX = "CREATE INDEX IF NOT EXISTS idx_rmaic_recommendation ON research_matrix_ai_recommendation_conversions(recommendation_id)"

DRAFT = "DRAFT"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
CONVERTED = "CONVERTED"
RECOMMENDATION_STATUSES: tuple[str, ...] = (DRAFT, APPROVED, REJECTED, CONVERTED)


def _init(con) -> None:
    con.execute(_DDL_RECOMMENDATIONS)
    con.execute(_DDL_STATUS_IDX)
    con.execute(_DDL_REVIEWS)
    con.execute(_DDL_REVIEWS_IDX)
    con.execute(_DDL_CONVERSIONS)
    con.execute(_DDL_CONVERSIONS_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def record_recommendation(
    recommendation_id: str,
    *,
    provider: str,
    requested_model: str | None,
    actual_model: str | None,
    input_family_ids: list[str],
    input_cell_ids: list[str] | None,
    evidence_snapshot: dict[str, Any],
    evidence_snapshot_hash: str,
    constraints_used: dict[str, Any],
    focus_hint: str | None,
    reasoning_summary: str,
    coverage_gaps: list[Any] | None,
    proposed_next_cells: list[dict[str, Any]],
    distinct_from_dead_list: str | None,
    priority: str | None,
) -> None:
    """Always INSERT, always status=DRAFT — a recommendation is persisted
    exactly once, the moment AIAnalyzer.propose_matrix_research_plan()
    returns status="ok" (execution/routes/matrix_ai.py never persists a
    failed/disabled AI call — there is no plan to audit). Every field the
    operator's own audit-trail schema asked for is captured verbatim,
    including the full evidence_snapshot (not just its hash) so a human
    reviewing this later can see exactly what the AI saw without having
    to trust that current Matrix state still matches.

    `requested_model` is whatever was ASKED for (an explicit per-call
    override, or the base config's own ai.model — possibly None if
    unset). `actual_model` is what the constructed AI provider instance
    ACTUALLY used (AIAnalyzer.resolved_model) — the caller must pass None
    here rather than guess/fabricate a value when it cannot be
    established (in practice this only happens on a disabled/unavailable
    provider, a state execution/routes/matrix_ai.py never reaches this
    function from, since persistence only happens after a successful
    call — but this function itself makes no such assumption)."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_matrix_ai_recommendations
               (recommendation_id, provider, requested_model, actual_model,
                input_family_ids_json, input_cell_ids_json,
                evidence_snapshot_json, evidence_snapshot_hash, constraints_used_json, focus_hint,
                reasoning_summary, coverage_gaps_json, proposed_next_cells_json,
                distinct_from_dead_list, priority, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                recommendation_id, provider, requested_model, actual_model,
                json.dumps(input_family_ids), json.dumps(input_cell_ids) if input_cell_ids is not None else None,
                json.dumps(evidence_snapshot, default=str), evidence_snapshot_hash,
                json.dumps(constraints_used), focus_hint,
                reasoning_summary, json.dumps(coverage_gaps) if coverage_gaps is not None else None,
                json.dumps(proposed_next_cells), distinct_from_dead_list, priority, DRAFT, _now_iso(),
            ),
        )


def get_recommendation(recommendation_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_matrix_ai_recommendations WHERE recommendation_id=?", (recommendation_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_recommendations(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, optionally filtered by review status."""
    query = "SELECT * FROM research_matrix_ai_recommendations WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]


def review_recommendation(recommendation_id: str, *, status: str, reviewed_by: str | None, review_note: str | None) -> None:
    """Human approval/rejection — the ONLY way a recommendation's status
    ever changes after creation (never AI-driven, never automatic).

    Raises ValueError for:
      - an unknown recommendation_id (message contains "unknown
        recommendation_id" — the route maps this to 404).
      - an invalid target status (DRAFT is the creation-only initial
        value; a reviewer may only move a recommendation to APPROVED or
        REJECTED, never back to DRAFT or to a Matrix-cell-style status
        like VALIDATED — message contains "status must be one of").
      - a recommendation that is no longer DRAFT (message contains
        "already reviewed" — the route maps this to 409 Conflict).

    Phase 3B-H hardening pass 2 + re-audit fix: the transition itself is
    an ATOMIC compare-and-swap (`UPDATE ... WHERE status='DRAFT'`,
    mirroring the same per-statement atomic-claim pattern storage.
    research_matrix uses for its own Matrix cell state machine), and the
    matching history-table append happens in the SAME `d1_batch()` call
    as that UPDATE — never as a second, independent `execute()` — so the
    two can no longer become separated by a crash/network failure
    between them (see module docstring). A re-review can change the
    CURRENT state but can never erase that an earlier review happened."""
    if status not in (APPROVED, REJECTED):
        raise ValueError(f"review_recommendation: status must be one of ({APPROVED!r}, {REJECTED!r}), got {status!r}")
    with d1_client.d1_connection() as con:
        _init(con)
        existing = con.execute(
            "SELECT status FROM research_matrix_ai_recommendations WHERE recommendation_id=?", (recommendation_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"review_recommendation: unknown recommendation_id {recommendation_id!r}")
        old_status = existing["status"]

    now = _now_iso()
    update_cursor, _insert_cursor = d1_client.d1_batch([
        (
            """UPDATE research_matrix_ai_recommendations
               SET status=?, reviewed_by=?, reviewed_at=?, review_note=?
               WHERE recommendation_id=? AND status=?""",
            (status, reviewed_by, now, review_note, recommendation_id, DRAFT),
        ),
        (
            # changes() reflects the UPDATE immediately above (same
            # batch, same underlying D1 connection) — this INSERT
            # writes a history row IF AND ONLY IF that UPDATE actually
            # transitioned this row, never merely because the row's
            # CURRENT status happens to already equal the target status.
            """INSERT INTO research_matrix_ai_recommendation_reviews
               (recommendation_id, old_status, new_status, reviewed_by, reviewed_at, review_note)
               SELECT ?, ?, ?, ?, ?, ? WHERE changes() = 1""",
            (recommendation_id, old_status, status, reviewed_by, now, review_note),
        ),
    ])
    if update_cursor.rowcount != 1:
        raise ValueError(
            f"review_recommendation: {recommendation_id!r} has already reviewed (current status={old_status!r}) "
            f"— re-review is refused, not silently overwritten."
        )


def list_recommendation_reviews(recommendation_id: str) -> list[dict[str, Any]]:
    """The full, append-only review history for one recommendation,
    oldest first — every review action ever taken, including ones a
    later re-review superseded. Empty for a recommendation still DRAFT."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_matrix_ai_recommendation_reviews WHERE recommendation_id=? ORDER BY review_id ASC",
            (recommendation_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# --- Phase 3C: Controlled Recommendation Conversion -------------------------


def convert_recommendation(
    recommendation_id: str, *, family_id: str,
    proposed_next_cells_hash: str, cells_considered: int,
    proposed_at_commit: str | None, converted_at_commit: str | None,
    converted_by: str | None,
) -> None:
    """The ONLY writer of status=CONVERTED. Structurally identical to
    review_recommendation()'s own CAS-plus-history-append pattern:

      - ATOMIC compare-and-swap (`UPDATE ... WHERE status='APPROVED'`) --
        a REJECTED row's status is never 'APPROVED' (so converting a
        rejected recommendation is refused by construction, no special-
        case branch needed) and an already-CONVERTED row's status is no
        longer 'APPROVED' either (so a second conversion attempt --
        replay -- is refused the exact same way a second review is).
      - the matching conversions-table INSERT happens in the SAME
        `d1_batch()` call, guarded by `WHERE changes() = 1`, so the
        status transition and its audit record can never become
        separated by a crash/network failure between two independent
        `execute()` calls (the same class of bug storage/d1_client.py's
        own module docstring warns about, and the exact bug this
        function's sibling review_recommendation() was fixed for).

    Every argument here is caller-supplied because THIS function has no
    way to independently derive them (family creation, proposal-time
    commit lookup, and current-commit resolution all happen in the
    caller, execution/routes/matrix_ai.py, which already has access to
    backtest.research_matrix.resolve_research_code_commit() and the
    recommendation's own constraints_used_json) — this function's own
    job is strictly the atomic bookkeeping, not the conversion's business
    logic. `converted_by` is expected to already be server-derived
    (storage.audit_log._mask_actor), never trusted from a request body,
    mirroring reviewed_by's own convention exactly.

    `commit_drift` is computed here, once, as `proposed_at_commit !=
    converted_at_commit` — informational only (CLAUDE.md rule 6 doesn't
    apply to research-code commits; the resulting cells are correctly
    stamped with the CURRENT commit at conversion time either way), never
    a precondition for conversion succeeding.

    Raises ValueError for an unknown recommendation_id, or for a
    recommendation that is not (or no longer) APPROVED (message contains
    "cannot be converted" — the route maps this to 404/409)."""
    commit_drift = proposed_at_commit != converted_at_commit
    with d1_client.d1_connection() as con:
        _init(con)
        existing = con.execute(
            "SELECT status FROM research_matrix_ai_recommendations WHERE recommendation_id=?", (recommendation_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"convert_recommendation: unknown recommendation_id {recommendation_id!r}")
        old_status = existing["status"]

    now = _now_iso()
    update_cursor, _insert_cursor = d1_client.d1_batch([
        (
            """UPDATE research_matrix_ai_recommendations
               SET status=?, converted_at=?
               WHERE recommendation_id=? AND status=?""",
            (CONVERTED, now, recommendation_id, APPROVED),
        ),
        (
            # changes() reflects the UPDATE immediately above, same as
            # review_recommendation()'s own history INSERT -- a lost CAS
            # (row was DRAFT, REJECTED, or already CONVERTED) can never
            # produce a spurious conversion-audit row.
            """INSERT INTO research_matrix_ai_recommendation_conversions
               (recommendation_id, family_id, proposed_next_cells_hash, cells_considered,
                proposed_at_commit, converted_at_commit, commit_drift, converted_by, converted_at)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE changes() = 1""",
            (
                recommendation_id, family_id, proposed_next_cells_hash, cells_considered,
                proposed_at_commit, converted_at_commit, int(commit_drift), converted_by, now,
            ),
        ),
    ])
    if update_cursor.rowcount != 1:
        raise ValueError(
            f"convert_recommendation: {recommendation_id!r} cannot be converted (current status={old_status!r}) "
            f"— only an APPROVED recommendation may be converted, and only once."
        )


def get_conversion(recommendation_id: str) -> dict[str, Any] | None:
    """The conversion-audit record for one recommendation, if it has been
    converted. None for a recommendation still DRAFT/APPROVED/REJECTED."""
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_matrix_ai_recommendation_conversions WHERE recommendation_id=?",
            (recommendation_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None
