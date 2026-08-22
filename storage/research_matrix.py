"""
storage/research_matrix.py
------------------------------
D1 persistence for the Hypothesis Discovery Engine's Matrix Engine
(backtest/research_matrix.py, backtest/matrix_orchestrator.py). Same
`_DDL` + `_init(con)` idiom as storage/research_missions.py.

Two tables, mirroring research_missions.py's own "deliberately minimal"
framing: `research_matrix_cells` (one row per fingerprinted hypothesis
combination — persistent identity/status/evidence) and
`research_matrix_runs` (one row per bounded orchestrator batch, for
resumability/observability — never a substitute for a cell's own status,
which is the real checkpoint).

Dedup is enforced HERE, not by the orchestrator: upsert_cells() is
`INSERT OR IGNORE` keyed by cell_id (== MATRIX-CELL-<fingerprint>) — a
cell whose fingerprint already exists is never re-queued, satisfying the
operator's condition #2 ("any change in hypothesis produces a new cell,
never a reuse of an old result") from the other direction: an UNCHANGED
hypothesis never gets a second, redundant cell either.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_CELLS = """
CREATE TABLE IF NOT EXISTS research_matrix_cells (
    cell_id                     TEXT PRIMARY KEY,
    fingerprint                 TEXT NOT NULL,
    symbol                      TEXT NOT NULL,
    bundle_json                 TEXT NOT NULL,
    risk_preset                 TEXT NOT NULL,
    confluence_overrides_json   TEXT,
    engine_variants_json        TEXT,
    data_provider                TEXT,
    status                      TEXT NOT NULL,
    rejection_reason            TEXT,
    stage_a_mission_id          TEXT,
    stage_a_trial_number        INTEGER,
    stage_a_metrics_json        TEXT,
    stage_a_p_value             REAL,
    lead_id                     TEXT,
    stage_b_validation_id       TEXT,
    stage_b_verdict             TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
)
"""

_DDL_CELLS_STATUS_IDX = "CREATE INDEX IF NOT EXISTS idx_rmc_status ON research_matrix_cells(status)"
_DDL_CELLS_SYMBOL_IDX = "CREATE INDEX IF NOT EXISTS idx_rmc_symbol ON research_matrix_cells(symbol)"

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS research_matrix_runs (
    run_id                      TEXT PRIMARY KEY,
    status                      TEXT NOT NULL,
    batch_size                  INTEGER NOT NULL,
    cells_claimed                INTEGER NOT NULL DEFAULT 0,
    cells_screened               INTEGER NOT NULL DEFAULT 0,
    cells_promoted                INTEGER NOT NULL DEFAULT 0,
    cells_validated                INTEGER NOT NULL DEFAULT 0,
    matrix_significance_json    TEXT,
    error                       TEXT,
    created_at                  TEXT NOT NULL,
    started_at                  TEXT,
    finished_at                 TEXT
)
"""


def _init(con) -> None:
    con.execute(_DDL_CELLS)
    con.execute(_DDL_CELLS_STATUS_IDX)
    con.execute(_DDL_CELLS_SYMBOL_IDX)
    con.execute(_DDL_RUNS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def upsert_cells(cells: list[Any]) -> dict[str, int]:
    """cells: list[backtest.research_matrix.MatrixCellSpec]. INSERT OR
    IGNORE keyed by cell_id — a duplicate fingerprint is silently skipped
    (not an error), never re-queued or re-run. Returns {"inserted": n,
    "duplicate": n}."""
    inserted = 0
    duplicate = 0
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        for cell in cells:
            existing = con.execute(
                "SELECT 1 FROM research_matrix_cells WHERE cell_id=?", (cell.cell_id,)
            ).fetchone()
            if existing is not None:
                duplicate += 1
                continue
            con.execute(
                """INSERT OR IGNORE INTO research_matrix_cells
                   (cell_id, fingerprint, symbol, bundle_json, risk_preset,
                    confluence_overrides_json, engine_variants_json, data_provider,
                    status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cell.cell_id, cell.fingerprint, cell.symbol, json.dumps(cell.bundle), cell.risk_preset,
                    json.dumps(cell.confluence_overrides) if cell.confluence_overrides is not None else None,
                    json.dumps(cell.engine_variants) if cell.engine_variants is not None else None,
                    cell.data_provider, "QUEUED", now, now,
                ),
            )
            inserted += 1
    return {"inserted": inserted, "duplicate": duplicate}


def get_cell(cell_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM research_matrix_cells WHERE cell_id=?", (cell_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_cells(status: str | None = None, symbol: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    query = "SELECT * FROM research_matrix_cells WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        query += " AND status=?"
        params.append(status)
    if symbol is not None:
        query += " AND symbol=?"
        params.append(symbol)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]


_ALLOWED_UPDATE_FIELDS = (
    "status", "rejection_reason", "stage_a_mission_id", "stage_a_trial_number",
    "stage_a_metrics_json", "stage_a_p_value", "lead_id", "stage_b_validation_id",
    "stage_b_verdict",
)


def update_cell(cell_id: str, **fields: Any) -> None:
    """Generic field-setter for whichever stage's result applies.
    `status` is always set to `fields["status"]` when provided; every
    other key must be one of _ALLOWED_UPDATE_FIELDS (fail loud on a typo
    rather than silently no-op)."""
    unknown = set(fields) - set(_ALLOWED_UPDATE_FIELDS)
    if unknown:
        raise ValueError(f"update_cell: unknown field(s) {sorted(unknown)}")
    if not fields:
        return
    set_clause = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
    params = list(fields.values()) + [_now_iso(), cell_id]
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(f"UPDATE research_matrix_cells SET {set_clause} WHERE cell_id=?", tuple(params))


def claim_queued_cells(limit: int) -> list[dict[str, Any]]:
    """Atomically (within one D1 connection) selects up to `limit` QUEUED
    cells and marks them RUNNING, returning the pre-claim rows. This is
    the batch-checkpointing primitive: a fresh orchestrator invocation
    only ever sees cells that are genuinely still QUEUED — nothing else
    is touched or re-run."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_matrix_cells WHERE status=? ORDER BY created_at ASC LIMIT ?",
            (QUEUED_STATUS, limit),
        ).fetchall()
        claimed = [_row_to_dict(r) for r in rows]
        now = _now_iso()
        for cell in claimed:
            con.execute(
                "UPDATE research_matrix_cells SET status=?, updated_at=? WHERE cell_id=?",
                (RUNNING_STATUS, now, cell["cell_id"]),
            )
    return claimed


def requeue_stale_running_cells(older_than_seconds: float) -> int:
    """Crash-recovery / resume primitive (operator's condition #5:
    bounded + resumable + checkpointed). A cell left in RUNNING because
    its orchestrator process died mid-Stage-A is not a permanent loss —
    it's simply requeued once its `updated_at` is older than the given
    staleness threshold. Returns the count requeued."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT cell_id FROM research_matrix_cells WHERE status=? AND updated_at < ?",
            (RUNNING_STATUS, cutoff),
        ).fetchall()
        stale_ids = [r["cell_id"] for r in rows]
        now = _now_iso()
        for cell_id in stale_ids:
            con.execute(
                "UPDATE research_matrix_cells SET status=?, updated_at=? WHERE cell_id=?",
                (QUEUED_STATUS, now, cell_id),
            )
    return len(stale_ids)


def cells_for_matrix_correction() -> list[dict[str, Any]]:
    """Every currently-SCREENED cell with a real stage_a_p_value, across
    ALL runs so far (not just the current batch) — the family the
    orchestrator's matrix-wide Bonferroni correction is computed over
    (operator's condition #3: this is a SEPARATE, matrix-wide correction
    layered on top of — never a substitute for — each Stage A trial's own
    within-mission correction)."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_matrix_cells WHERE status=? AND stage_a_p_value IS NOT NULL",
            (SCREENED_STATUS,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# Local aliases so this module never has to import backtest.research_matrix
# (storage modules stay leaf-level, matching every other storage/*.py file
# in this codebase — the status STRING VALUES are the actual contract, not
# the module they're spelled out in).
QUEUED_STATUS = "QUEUED"
RUNNING_STATUS = "RUNNING"
SCREENED_STATUS = "SCREENED"


# ---------------------------------------------------------------------------
# Runs (batches)
# ---------------------------------------------------------------------------


def upsert_run(run_id: str, status: str, batch_size: int) -> None:
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_matrix_runs (run_id, status, batch_size, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET status=excluded.status""",
            (run_id, status, batch_size, _now_iso()),
        )


def set_run_status(
    run_id: str, status: str, *, error: str | None = None, started: bool = False, finished: bool = False,
    cells_claimed: int | None = None, cells_screened: int | None = None,
    cells_promoted: int | None = None, cells_validated: int | None = None,
    matrix_significance: dict[str, Any] | None = None,
) -> None:
    set_parts = ["status=?"]
    params: list[Any] = [status]
    if error is not None:
        set_parts.append("error=?")
        params.append(error)
    if started:
        set_parts.append("started_at=?")
        params.append(_now_iso())
    if finished:
        set_parts.append("finished_at=?")
        params.append(_now_iso())
    for field_name, value in (
        ("cells_claimed", cells_claimed), ("cells_screened", cells_screened),
        ("cells_promoted", cells_promoted), ("cells_validated", cells_validated),
    ):
        if value is not None:
            set_parts.append(f"{field_name}=?")
            params.append(value)
    if matrix_significance is not None:
        set_parts.append("matrix_significance_json=?")
        params.append(json.dumps(matrix_significance))
    params.append(run_id)
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(f"UPDATE research_matrix_runs SET {', '.join(set_parts)} WHERE run_id=?", tuple(params))


def get_run(run_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM research_matrix_runs WHERE run_id=?", (run_id,)).fetchone()
    return _row_to_dict(row) if row else None
