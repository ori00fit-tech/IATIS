"""
storage/matrix_ai_recommendations.py
-----------------------------------------
Hypothesis Discovery Engine, Phase 3B — AI Research Orchestrator, Stage
3B.3: Recommendation persistence / audit trail.

One row per AI-proposed research plan (backtest.matrix_research_planner
+ ai.ai_analyzer.AIAnalyzer.propose_matrix_research_plan()). Same
`_DDL` + `_init(con)` idiom as storage/research_matrix.py — a brand-new
table, created lazily on first use, no storage/migrations.py entry needed
(that file is for ALTERing existing tables — see its own docstring).

NON-NEGOTIABLE (operator's own explicit Phase 3B boundary): `status` here
is ALWAYS one of DRAFT / APPROVED / REJECTED — deliberately a DIFFERENT
vocabulary than research_matrix_cells.status's own QUEUED/.../VALIDATED/
REJECTED state machine, so a recommendation's review status can never be
visually or textually confused with a Matrix cell's own authoritative
evidence-gate verdict. APPROVED means "a human reviewed this proposal and
thinks it's worth generating real cells for" — it does NOT create,
promote, or validate anything by itself. Converting an approved
recommendation into real research_matrix_cells rows (Phase 3B.5, not
built in this pass — see backtest/matrix_research_planner.py's module
docstring) remains a deliberate, separate, human-triggered action through
the existing POST /research/matrix/generate endpoint, never automatic.
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
    model                       TEXT NOT NULL,
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
    created_at                  TEXT NOT NULL
)
"""
_DDL_STATUS_IDX = "CREATE INDEX IF NOT EXISTS idx_rmair_status ON research_matrix_ai_recommendations(status)"

DRAFT = "DRAFT"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
RECOMMENDATION_STATUSES: tuple[str, ...] = (DRAFT, APPROVED, REJECTED)


def _init(con) -> None:
    con.execute(_DDL_RECOMMENDATIONS)
    con.execute(_DDL_STATUS_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def record_recommendation(
    recommendation_id: str,
    *,
    provider: str,
    model: str,
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
    to trust that current Matrix state still matches."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_matrix_ai_recommendations
               (recommendation_id, provider, model, input_family_ids_json, input_cell_ids_json,
                evidence_snapshot_json, evidence_snapshot_hash, constraints_used_json, focus_hint,
                reasoning_summary, coverage_gaps_json, proposed_next_cells_json,
                distinct_from_dead_list, priority, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                recommendation_id, provider, model,
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
    ever changes after creation (never AI-driven, never automatic). Raises
    ValueError for an unknown recommendation_id or an invalid target
    status (DRAFT is the creation-only initial value — a reviewer may only
    move a recommendation to APPROVED or REJECTED, never back to DRAFT or
    to a Matrix-cell-style status like VALIDATED)."""
    if status not in (APPROVED, REJECTED):
        raise ValueError(f"review_recommendation: status must be one of ({APPROVED!r}, {REJECTED!r}), got {status!r}")
    with d1_client.d1_connection() as con:
        _init(con)
        existing = con.execute(
            "SELECT status FROM research_matrix_ai_recommendations WHERE recommendation_id=?", (recommendation_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"review_recommendation: unknown recommendation_id {recommendation_id!r}")
        con.execute(
            """UPDATE research_matrix_ai_recommendations
               SET status=?, reviewed_by=?, reviewed_at=?, review_note=?
               WHERE recommendation_id=?""",
            (status, reviewed_by, _now_iso(), review_note, recommendation_id),
        )
