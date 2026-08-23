"""
storage/hypothesis_mission.py
---------------------------------
Hypothesis Discovery Engine, Phase 4 — D1 persistence for backtest.
hypothesis_mission.record_mission()'s Mission/binding records. Same
`_DDL` + `_init(con)` idiom as storage/hypothesis_factory.py and
storage/research_matrix.py.

NON-NEGOTIABLE: this module is bookkeeping only. It never fetches a
Hypothesis, never verifies a fingerprint, never calls backtest.
hypothesis_execution.build_execution_request(), and never writes
research_matrix_cells/research_matrix_families itself (those stay owned
by storage.research_matrix's own upsert_family()/upsert_cells(), called
by the orchestration layer BEFORE persist_mission() below is reached).
Matching every other storage/*.py file in this codebase, this module
never imports backtest/*.py — the orchestration/verification lives one
layer up, in backtest.hypothesis_mission (which already imports THIS
module, the one-way direction this codebase actually enforces).

Two tables:
  `research_hypothesis_missions` — one row per Mission (mission_id is the
  deterministic backtest.hypothesis_mission.compute_mission_id() output).
  `research_hypothesis_mission_bindings` — one row per (mission_id,
  hypothesis_id) pair actually bound into that Mission, carrying the
  hypothesis's own fingerprint (at binding time) and the real cell_id it
  resolved to — the forensic join target for "what did this Mission
  actually request, and where did it land."

Never touches research/results/registry.json, config.yaml, config/
engines.yaml, or config/symbols.yaml. Discovery != Evidence != Promotion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_MISSIONS = """
CREATE TABLE IF NOT EXISTS research_hypothesis_missions (
    mission_id             TEXT PRIMARY KEY,
    family_id               TEXT NOT NULL,
    research_code_commit    TEXT,
    data_snapshot_id        TEXT,
    created_by              TEXT,
    created_at              TEXT NOT NULL
)
"""
_DDL_MISSIONS_FAMILY_IDX = "CREATE INDEX IF NOT EXISTS idx_rhm_family ON research_hypothesis_missions(family_id)"

_DDL_BINDINGS = """
CREATE TABLE IF NOT EXISTS research_hypothesis_mission_bindings (
    mission_id               TEXT NOT NULL,
    hypothesis_id             TEXT NOT NULL,
    hypothesis_fingerprint   TEXT NOT NULL,
    cell_id                   TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    PRIMARY KEY (mission_id, hypothesis_id)
)
"""
# Forensic join direction: "which Mission(s) did this Hypothesis end up
# bound into" — the answer to the operator's own required chain question
# "why was this experiment run", read backwards from a hypothesis_id.
_DDL_BINDINGS_HYPOTHESIS_IDX = "CREATE INDEX IF NOT EXISTS idx_rhmb_hypothesis ON research_hypothesis_mission_bindings(hypothesis_id)"
_DDL_BINDINGS_CELL_IDX = "CREATE INDEX IF NOT EXISTS idx_rhmb_cell ON research_hypothesis_mission_bindings(cell_id)"


def _init(con) -> None:
    con.execute(_DDL_MISSIONS)
    con.execute(_DDL_MISSIONS_FAMILY_IDX)
    con.execute(_DDL_BINDINGS)
    con.execute(_DDL_BINDINGS_HYPOTHESIS_IDX)
    con.execute(_DDL_BINDINGS_CELL_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def persist_mission(
    mission_id: str, family_id: str, bindings: list[dict[str, Any]], *,
    research_code_commit: str | None = None, data_snapshot_id: str | None = None,
    created_by: str | None = None,
) -> dict[str, int]:
    """INSERT OR IGNORE the Mission row (keyed by mission_id) and every
    binding row (keyed by mission_id+hypothesis_id) — a repeat call with
    the same mission_id/bindings (a retried record_mission() after a
    crash, or a second legitimate idempotent re-binding) is always safe:
    the mission row is written at most once, and each binding is written
    at most once. `bindings` is a list of {hypothesis_id,
    hypothesis_fingerprint, cell_id} dicts, exactly as backtest.
    hypothesis_mission.record_mission() constructs them.

    Returns {"bindings_inserted": n, "bindings_duplicate": n}."""
    now = _now_iso()
    inserted = 0
    duplicate = 0
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT OR IGNORE INTO research_hypothesis_missions
               (mission_id, family_id, research_code_commit, data_snapshot_id, created_by, created_at)
               VALUES (?,?,?,?,?,?)""",
            (mission_id, family_id, research_code_commit, data_snapshot_id, created_by, now),
        )
        for binding in bindings:
            existing = con.execute(
                "SELECT 1 FROM research_hypothesis_mission_bindings WHERE mission_id=? AND hypothesis_id=?",
                (mission_id, binding["hypothesis_id"]),
            ).fetchone()
            if existing is not None:
                duplicate += 1
                continue
            con.execute(
                """INSERT OR IGNORE INTO research_hypothesis_mission_bindings
                   (mission_id, hypothesis_id, hypothesis_fingerprint, cell_id, created_at)
                   VALUES (?,?,?,?,?)""",
                (mission_id, binding["hypothesis_id"], binding["hypothesis_fingerprint"], binding["cell_id"], now),
            )
            inserted += 1
    return {"bindings_inserted": inserted, "bindings_duplicate": duplicate}


def get_mission(mission_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_hypothesis_missions WHERE mission_id=?", (mission_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_mission_bindings(mission_id: str) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_hypothesis_mission_bindings WHERE mission_id=? ORDER BY hypothesis_id ASC",
            (mission_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_missions_for_hypothesis(hypothesis_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """The reverse forensic direction: every Mission a given Hypothesis has
    ever been bound into (ordinarily zero or one, but never assumed to be
    at most one — a different research_code_commit legitimately produces
    a second, coexisting Mission for the same hypothesis_id)."""
    with d1_client.d1_connection() as con:
        _init(con)
        mission_ids = con.execute(
            "SELECT DISTINCT mission_id FROM research_hypothesis_mission_bindings WHERE hypothesis_id=?",
            (hypothesis_id,),
        ).fetchall()
        ids = [r["mission_id"] for r in mission_ids]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = con.execute(
            f"SELECT * FROM research_hypothesis_missions WHERE mission_id IN ({placeholders}) "
            f"ORDER BY created_at DESC LIMIT ?",
            (*ids, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
