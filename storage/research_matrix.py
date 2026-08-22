"""
storage/research_matrix.py
------------------------------
D1 persistence for the Hypothesis Discovery Engine's Matrix Engine
(backtest/research_matrix.py, backtest/matrix_orchestrator.py). Same
`_DDL` + `_init(con)` idiom as storage/research_missions.py.

Three tables: `research_matrix_families` (Forensic Audit hardening,
Finding 4 — one row per FIXED research family; `planned_n` is set once,
at family-creation time, to the total number of cells generated for that
family, and never changes -- this is the authoritative multiple-testing
correction denominator, deliberately NOT "however many cells happen to be
SCREENED right now"), `research_matrix_cells` (one row per fingerprinted
hypothesis combination, always belonging to exactly one family), and
`research_matrix_runs` (one row per bounded orchestrator batch, for
resumability/observability only -- never a substitute for a cell's own
status or a family's own planned_n).

Dedup is enforced HERE, not by the orchestrator: upsert_cells() is
`INSERT OR IGNORE` keyed by cell_id (== MATRIX-CELL-<fingerprint>) — a
cell whose fingerprint already exists is never re-queued, satisfying the
operator's condition #2 ("any change in hypothesis produces a new cell,
never a reuse of an old result") from the other direction: an UNCHANGED
hypothesis never gets a second, redundant cell either.

Forensic Audit hardening (Finding 1) — claim_queued_cells() is an ATOMIC
compare-and-set at the individual-SQL-statement level (`UPDATE ...
WHERE cell_id=? AND status='QUEUED'`), not a SELECT-then-loop-UPDATE.
storage/d1_client.py's D1Connection has "no client-side transaction
state" (its own docstring), so cross-statement atomicity cannot be
assumed -- but each INDIVIDUAL statement IS atomic (D1/SQLite serializes
writes to one row), and D1Cursor.rowcount reports the real per-statement
row-modification count (meta.changes from the D1 Worker response). Two
concurrent claim_queued_cells() calls racing on the same cell will see
EXACTLY ONE of their conditional UPDATEs report rowcount==1 -- the other
reports 0 and correctly does not treat that cell as claimed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_FAMILIES = """
CREATE TABLE IF NOT EXISTS research_matrix_families (
    family_id       TEXT PRIMARY KEY,
    planned_n       INTEGER NOT NULL,
    family_alpha    REAL NOT NULL,
    symbols_json    TEXT,
    created_at      TEXT NOT NULL
)
"""

_DDL_CELLS = """
CREATE TABLE IF NOT EXISTS research_matrix_cells (
    cell_id                     TEXT PRIMARY KEY,
    family_id                    TEXT NOT NULL,
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
    requeue_count                INTEGER NOT NULL DEFAULT 0,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
)
"""

_DDL_CELLS_STATUS_IDX = "CREATE INDEX IF NOT EXISTS idx_rmc_status ON research_matrix_cells(status)"
_DDL_CELLS_SYMBOL_IDX = "CREATE INDEX IF NOT EXISTS idx_rmc_symbol ON research_matrix_cells(symbol)"
_DDL_CELLS_FAMILY_IDX = "CREATE INDEX IF NOT EXISTS idx_rmc_family ON research_matrix_cells(family_id)"

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS research_matrix_runs (
    run_id                      TEXT PRIMARY KEY,
    family_id                    TEXT,
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
    con.execute(_DDL_FAMILIES)
    con.execute(_DDL_CELLS)
    con.execute(_DDL_CELLS_STATUS_IDX)
    con.execute(_DDL_CELLS_SYMBOL_IDX)
    con.execute(_DDL_CELLS_FAMILY_IDX)
    con.execute(_DDL_RUNS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# Status string constants (kept as plain strings, not imported from
# backtest.research_matrix — storage modules stay leaf-level, matching
# every other storage/*.py file in this codebase; the STRING VALUES are
# the actual contract, not the module they're spelled out in).
QUEUED_STATUS = "QUEUED"
RUNNING_STATUS = "RUNNING"
SCREENED_STATUS = "SCREENED"


# ---------------------------------------------------------------------------
# Families (Finding 4 — the fixed multiple-testing correction denominator)
# ---------------------------------------------------------------------------


def upsert_family(family_id: str, planned_n: int, family_alpha: float, symbols_json: str | None = None) -> None:
    """Always INSERT — a family_id is minted once, at generation time, and
    planned_n/family_alpha never change afterward (the whole point of
    Finding 4's fix: every batch and every resume reads the SAME fixed
    denominator, forever, for this family_id)."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            "INSERT INTO research_matrix_families (family_id, planned_n, family_alpha, symbols_json, created_at) VALUES (?,?,?,?,?)",
            (family_id, planned_n, family_alpha, symbols_json, _now_iso()),
        )


def get_family(family_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM research_matrix_families WHERE family_id=?", (family_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_families(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first — Phase 2B Matrix Dashboard's family-browser list. No
    aggregation happens here (that's GET /research/matrix/families/{id}/
    summary's job, via backtest.matrix_evidence) — this is a lightweight
    index over the family rows themselves, matching storage.research_
    missions.list_recent_missions()'s own precedent."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_matrix_families ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------


def upsert_cells(cells: list[Any], family_id: str) -> dict[str, int]:
    """cells: list[backtest.research_matrix.MatrixCellSpec]. INSERT OR
    IGNORE keyed by cell_id — a duplicate fingerprint is silently skipped
    (not an error), never re-queued or re-run. Every cell in one call
    belongs to the SAME family_id (one /generate call == one family).
    Returns {"inserted": n, "duplicate": n}."""
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
                   (cell_id, family_id, fingerprint, symbol, bundle_json, risk_preset,
                    confluence_overrides_json, engine_variants_json, data_provider,
                    status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cell.cell_id, family_id, cell.fingerprint, cell.symbol, json.dumps(cell.bundle), cell.risk_preset,
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


def list_cells(
    status: str | None = None, symbol: str | None = None, family_id: str | None = None, limit: int = 500,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM research_matrix_cells WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        query += " AND status=?"
        params.append(status)
    if symbol is not None:
        query += " AND symbol=?"
        params.append(symbol)
    if family_id is not None:
        query += " AND family_id=?"
        params.append(family_id)
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


def claim_queued_cells(family_id: str, limit: int) -> list[dict[str, Any]]:
    """Forensic Audit hardening (Finding 1) — ATOMIC compare-and-set claim,
    scoped to one family. For each QUEUED candidate (within this family),
    issues `UPDATE ... SET status='RUNNING' WHERE cell_id=? AND
    status='QUEUED'` and only treats the cell as claimed by THIS call when
    D1Cursor.rowcount == 1 (i.e. this exact statement execution is the one
    that actually flipped QUEUED -> RUNNING). A concurrent caller racing on
    the same cell_id will see rowcount == 0 for that row and correctly
    skip it — no double-claim, no double Stage-A execution, no last-writer-
    wins ambiguity on which claim "wins": whichever UPDATE the database
    actually applies first is the only one that ever sees rowcount == 1.

    A batch may legitimately claim FEWER than `limit` cells under real
    contention (some candidates lose their race) — this is expected,
    correct behavior, not a bug: it never double-executes, it just
    processes less than the cap when multiple workers compete for the
    same family's QUEUED pool."""
    with d1_client.d1_connection() as con:
        _init(con)
        candidates = con.execute(
            "SELECT cell_id FROM research_matrix_cells WHERE family_id=? AND status=? ORDER BY created_at ASC LIMIT ?",
            (family_id, QUEUED_STATUS, limit),
        ).fetchall()
        now = _now_iso()
        claimed_ids: list[str] = []
        for row in candidates:
            cell_id = row["cell_id"]
            cur = con.execute(
                "UPDATE research_matrix_cells SET status=?, updated_at=? WHERE cell_id=? AND status=?",
                (RUNNING_STATUS, now, cell_id, QUEUED_STATUS),
            )
            if cur.rowcount == 1:
                claimed_ids.append(cell_id)
        if not claimed_ids:
            return []
        placeholders = ",".join("?" for _ in claimed_ids)
        rows = con.execute(
            f"SELECT * FROM research_matrix_cells WHERE cell_id IN ({placeholders})",
            tuple(claimed_ids),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def requeue_stale_running_cells(older_than_seconds: float) -> int:
    """Crash-recovery / resume primitive — deliberately NOT family-scoped
    (a stale RUNNING cell should be requeued regardless of which family it
    belongs to; each cell's own family_id column is untouched by this, so
    it always returns to its own family's QUEUED pool). A cell left in
    RUNNING because its orchestrator process died mid-Stage-A is not a
    permanent loss — it's simply requeued once its `updated_at` is older
    than the given staleness threshold. Returns the count requeued.

    Phase 2A (Evidence Read Model): increments the cell's own persisted
    `requeue_count` on every requeue, so "how many times was this cell
    resumed after an interruption" is real, queryable evidence rather than
    only inferable from log lines — the operator's own explicit ask for
    "resumptions/requeues" in the family evidence summary."""
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
                "UPDATE research_matrix_cells SET status=?, updated_at=?, requeue_count=requeue_count+1 WHERE cell_id=?",
                (QUEUED_STATUS, now, cell_id),
            )
    return len(stale_ids)


def cells_for_matrix_correction(family_id: str) -> list[dict[str, Any]]:
    """Every currently-SCREENED cell WITHIN THIS FAMILY that has a real
    stage_a_p_value — the set apply_matrix_wide_correction() evaluates
    against the family's own FIXED planned_n (never this list's own
    length; see get_family()). A cell whose trial had std_rr==0 never
    appears here at all — it terminates as INSUFFICIENT_DATA before ever
    reaching SCREENED (Finding 5's fix), so this filter's `stage_a_p_value
    IS NOT NULL` clause is now a defensive invariant, not a live escape
    hatch."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_matrix_cells WHERE family_id=? AND status=? AND stage_a_p_value IS NOT NULL",
            (family_id, SCREENED_STATUS),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Runs (batches)
# ---------------------------------------------------------------------------


def upsert_run(run_id: str, status: str, batch_size: int, family_id: str | None = None) -> None:
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_matrix_runs (run_id, family_id, status, batch_size, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET status=excluded.status""",
            (run_id, family_id, status, batch_size, _now_iso()),
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


def list_runs(family_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, optionally scoped to one family — Phase 2B Matrix
    Dashboard's "recent batch runs" panel."""
    query = "SELECT * FROM research_matrix_runs WHERE 1=1"
    params: list[Any] = []
    if family_id is not None:
        query += " AND family_id=?"
        params.append(family_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]
