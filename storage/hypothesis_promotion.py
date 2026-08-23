"""
storage/hypothesis_promotion.py
---------------------------------
Hypothesis Discovery Engine, Phase 5 — D1 persistence for backtest.
hypothesis_promotion.record_promotion()'s promotion decision records.
Same `_DDL` + `_init(con)` idiom as storage/hypothesis_mission.py.

NON-NEGOTIABLE: this module is bookkeeping only. It never fetches a
Hypothesis/Mission/Cell, never decides PROMOTED/NOT_PROMOTED/BLOCKED,
never reads or writes research_matrix_cells (governance evidence stays
owned entirely by storage.research_matrix — this module only records
independent DECISIONS made about it). Matching every other storage/*.py
file in this codebase, this module never imports backtest/*.py — the
decision logic and identity-chain verification live one layer up, in
backtest.hypothesis_promotion (which already imports THIS module, the
one-way direction this codebase actually enforces).

One table: `research_hypothesis_promotions` — one row per promotion_id
(a deterministic hash of the identity triple PLUS the governance snapshot
it was decided from — see backtest.hypothesis_promotion.compute_
promotion_id()'s own docstring for why the snapshot is part of the
identity). Append-only: re-evaluating the SAME triple against the SAME
governance state is idempotent (same promotion_id, INSERT OR IGNORE); a
real change in governance state produces a genuinely new, coexisting row,
never an UPDATE of the earlier one — no update/delete function exists
here, matching storage.hypothesis_factory's and storage.research_matrix's
own cell-immutability precedent.

Never touches research/results/registry.json, config.yaml, config/
engines.yaml, or config/symbols.yaml. Promotion != Symbol Policy != Live
execution — this module is Promotion bookkeeping only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_PROMOTIONS = """
CREATE TABLE IF NOT EXISTS research_hypothesis_promotions (
    promotion_id              TEXT PRIMARY KEY,
    hypothesis_id              TEXT NOT NULL,
    hypothesis_fingerprint    TEXT NOT NULL,
    mission_id                 TEXT NOT NULL,
    cell_id                    TEXT NOT NULL,
    symbol                     TEXT NOT NULL,
    decision                  TEXT NOT NULL,
    decision_reason            TEXT NOT NULL,
    governance_snapshot_json  TEXT NOT NULL,
    research_code_commit       TEXT,
    data_snapshot_id           TEXT,
    created_by                 TEXT,
    created_at                 TEXT NOT NULL
)
"""
_DDL_PROMOTIONS_HYPOTHESIS_IDX = "CREATE INDEX IF NOT EXISTS idx_rhp_hypothesis ON research_hypothesis_promotions(hypothesis_id)"
_DDL_PROMOTIONS_MISSION_IDX = "CREATE INDEX IF NOT EXISTS idx_rhp_mission ON research_hypothesis_promotions(mission_id)"
_DDL_PROMOTIONS_CELL_IDX = "CREATE INDEX IF NOT EXISTS idx_rhp_cell ON research_hypothesis_promotions(cell_id)"
_DDL_PROMOTIONS_SYMBOL_IDX = "CREATE INDEX IF NOT EXISTS idx_rhp_symbol ON research_hypothesis_promotions(symbol)"
_DDL_PROMOTIONS_DECISION_IDX = "CREATE INDEX IF NOT EXISTS idx_rhp_decision ON research_hypothesis_promotions(decision)"


def _init(con) -> None:
    con.execute(_DDL_PROMOTIONS)
    con.execute(_DDL_PROMOTIONS_HYPOTHESIS_IDX)
    con.execute(_DDL_PROMOTIONS_MISSION_IDX)
    con.execute(_DDL_PROMOTIONS_CELL_IDX)
    con.execute(_DDL_PROMOTIONS_SYMBOL_IDX)
    con.execute(_DDL_PROMOTIONS_DECISION_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    if d.get("governance_snapshot_json") is not None:
        d["governance_snapshot"] = json.loads(d["governance_snapshot_json"])
    return d


def persist_promotion(
    promotion_id: str, hypothesis_id: str, hypothesis_fingerprint: str, mission_id: str, cell_id: str,
    symbol: str, decision: str, decision_reason: str, governance_snapshot: dict[str, Any], *,
    research_code_commit: str | None = None, data_snapshot_id: str | None = None,
    created_by: str | None = None,
) -> None:
    """INSERT OR IGNORE keyed by promotion_id — a repeat call with the
    same promotion_id (a retried record_promotion() after a crash, or a
    legitimate idempotent re-evaluation against unchanged governance
    state) is always safe and writes nothing a second time."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT OR IGNORE INTO research_hypothesis_promotions
               (promotion_id, hypothesis_id, hypothesis_fingerprint, mission_id, cell_id, symbol,
                decision, decision_reason, governance_snapshot_json, research_code_commit,
                data_snapshot_id, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                promotion_id, hypothesis_id, hypothesis_fingerprint, mission_id, cell_id, symbol,
                decision, decision_reason, json.dumps(governance_snapshot), research_code_commit,
                data_snapshot_id, created_by, _now_iso(),
            ),
        )


def get_promotion(promotion_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_hypothesis_promotions WHERE promotion_id=?", (promotion_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_promotions_for_hypothesis(hypothesis_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Every promotion decision ever recorded for this hypothesis, newest
    first — ordinarily one per distinct governance-state snapshot it has
    passed through (e.g. an early BLOCKED while Stage B was pending,
    later superseded by a PROMOTED once Stage B confirmed); never
    collapsed to "the latest one" here, since the full history is the
    forensic point."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_hypothesis_promotions WHERE hypothesis_id=? ORDER BY created_at DESC LIMIT ?",
            (hypothesis_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_promotions_for_mission(mission_id: str, limit: int = 500) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_hypothesis_promotions WHERE mission_id=? ORDER BY created_at DESC LIMIT ?",
            (mission_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_promotions(
    decision: str | None = None, symbol: str | None = None, limit: int = 500,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM research_hypothesis_promotions WHERE 1=1"
    params: list[Any] = []
    if decision is not None:
        query += " AND decision=?"
        params.append(decision)
    if symbol is not None:
        query += " AND symbol=?"
        params.append(symbol)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]
