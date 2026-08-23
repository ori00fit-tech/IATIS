"""
storage/hypothesis_factory.py
-------------------------------
Hypothesis Discovery Engine, Phase 2 — D1 persistence for backtest.
hypothesis_factory.Hypothesis. Same `_DDL` + `_init(con)` idiom as
storage/research_matrix.py.

NON-NEGOTIABLE: this module is bookkeeping only — INSERT-OR-IGNORE
persistence of already-generated Hypothesis records, plus read access.
It never computes a Hypothesis (that's backtest.hypothesis_factory.
generate_hypotheses()'s job), never touches research_matrix_cells/
families (a Hypothesis's evidence lives entirely in its own Matrix cell,
joined by matrix_cell_fingerprint — this module never writes that table),
and never touches research/results/registry.json, config.yaml, config/
engines.yaml, or config/symbols.yaml. Discovery != Evidence != Promotion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_HYPOTHESES = """
CREATE TABLE IF NOT EXISTS research_hypotheses (
    hypothesis_id             TEXT PRIMARY KEY,
    symbol                    TEXT NOT NULL,
    engine                    TEXT NOT NULL,
    engine_version            TEXT NOT NULL,
    timeframe                 TEXT NOT NULL,
    risk_preset               TEXT NOT NULL,
    claim                     TEXT NOT NULL,
    matrix_cell_fingerprint   TEXT NOT NULL,
    proposed_at               TEXT NOT NULL
)
"""
_DDL_HYPOTHESES_SYMBOL_IDX = "CREATE INDEX IF NOT EXISTS idx_rh_symbol ON research_hypotheses(symbol)"
_DDL_HYPOTHESES_ENGINE_IDX = "CREATE INDEX IF NOT EXISTS idx_rh_engine ON research_hypotheses(engine)"
_DDL_HYPOTHESES_TIMEFRAME_IDX = "CREATE INDEX IF NOT EXISTS idx_rh_timeframe ON research_hypotheses(timeframe)"
_DDL_HYPOTHESES_FINGERPRINT_IDX = "CREATE INDEX IF NOT EXISTS idx_rh_fingerprint ON research_hypotheses(matrix_cell_fingerprint)"


def _init(con) -> None:
    con.execute(_DDL_HYPOTHESES)
    con.execute(_DDL_HYPOTHESES_SYMBOL_IDX)
    con.execute(_DDL_HYPOTHESES_ENGINE_IDX)
    con.execute(_DDL_HYPOTHESES_TIMEFRAME_IDX)
    con.execute(_DDL_HYPOTHESES_FINGERPRINT_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def record_hypotheses(hypotheses: list[Any]) -> dict[str, int]:
    """hypotheses: list[backtest.hypothesis_factory.Hypothesis]. INSERT OR
    IGNORE keyed by hypothesis_id — re-proposing a combination that was
    already proposed is silently a no-op (not an error, not a duplicate
    row): the same combination always deterministically produces the same
    hypothesis_id (it shares its Matrix cell's own fingerprint), so
    re-running generate_hypotheses() with overlapping inputs is always
    safe. Returns {"inserted": n, "duplicate": n}."""
    inserted = 0
    duplicate = 0
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        for h in hypotheses:
            existing = con.execute(
                "SELECT 1 FROM research_hypotheses WHERE hypothesis_id=?", (h.hypothesis_id,)
            ).fetchone()
            if existing is not None:
                duplicate += 1
                continue
            con.execute(
                """INSERT OR IGNORE INTO research_hypotheses
                   (hypothesis_id, symbol, engine, engine_version, timeframe, risk_preset,
                    claim, matrix_cell_fingerprint, proposed_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    h.hypothesis_id, h.symbol, h.engine, h.engine_version, h.timeframe,
                    h.risk_preset, h.claim, h.matrix_cell_fingerprint, now,
                ),
            )
            inserted += 1
    return {"inserted": inserted, "duplicate": duplicate}


def get_hypothesis(hypothesis_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM research_hypotheses WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_hypotheses(
    symbol: str | None = None, engine: str | None = None, timeframe: str | None = None, limit: int = 500,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM research_hypotheses WHERE 1=1"
    params: list[Any] = []
    if symbol is not None:
        query += " AND symbol=?"
        params.append(symbol)
    if engine is not None:
        query += " AND engine=?"
        params.append(engine)
    if timeframe is not None:
        query += " AND timeframe=?"
        params.append(timeframe)
    query += " ORDER BY proposed_at DESC LIMIT ?"
    params.append(limit)
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]
