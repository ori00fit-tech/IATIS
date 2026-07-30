"""
storage/research_mission_validations.py
------------------------------------------
AI Research Lab / Mission Center Phase 3 (2026-07-30) — D1 storage for
multi-stage VALIDATION runs against one operator-chosen mission trial.

Deliberately a separate module from storage/research_missions.py, not an
addition to it — that module's own docstring explicitly frames itself as
minimal by design ("Two tables only, deliberately not the larger table
list originally proposed"), and a validation is a distinct lifecycle
(created once a mission has already finished, references a mission+trial
but plays no part in that mission's own resume/replay logic). Matches
this repo's one-concern-per-storage-module convention (storage/
outcome_tracker.py, storage/engine_tracker.py, etc. are all separate
files rather than one mega-storage module).

Two tables, same `_DDL` + `_init(con)` idiom as research_missions.py:
`research_mission_validations` (one row per validation run) and
`research_mission_validation_results` (one row per validation symbol
within that run — never filtered to only the passing ones).

HARD SAFETY GUARANTEE: nothing in this module, or in
backtest/mission_validator.py (the only writer), ever touches
research/results/registry.json, config.yaml, or config/engines.yaml.
`overall_verdict` (NO_EDGE/WEAK_LEAD/STRONG_LEAD) is a LEAD label stored
ONLY here — never a hypothesis status, never a promotion. See
backtest/mission_validator.py's own module docstring for the full
guarantee and its pinning tests.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_VALIDATIONS = """
CREATE TABLE IF NOT EXISTS research_mission_validations (
    id                       TEXT PRIMARY KEY,
    mission_id               TEXT NOT NULL,
    trial_number             INTEGER NOT NULL,
    trial_symbol             TEXT NOT NULL,
    status                   TEXT NOT NULL,   -- queued|running|finished|failed|cancelled
    validation_symbols_json  TEXT NOT NULL,
    objective_metric         TEXT NOT NULL,
    criteria_json            TEXT NOT NULL,
    overall_verdict          TEXT,            -- NO_EDGE|WEAK_LEAD|STRONG_LEAD, NULL until finished
    passing_symbols          INTEGER,
    total_symbols            INTEGER,
    created_at               TEXT NOT NULL,
    started_at               TEXT,
    finished_at              TEXT,
    error                    TEXT
)
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS research_mission_validation_results (
    validation_id            TEXT NOT NULL,
    symbol                   TEXT NOT NULL,
    passed                   INTEGER NOT NULL,   -- 0/1
    metrics_json             TEXT,
    monte_carlo_json         TEXT,
    walk_forward_json        TEXT,
    robustness_json          TEXT,
    criteria_breakdown_json  TEXT NOT NULL,       -- {criterion: {actual, threshold, passed}} — every one, always
    feature_mining_json      TEXT,                -- Feature Mining Phase 1 (2026-07-30) — diagnostic only, never a criterion
    error                    TEXT,
    started_at               TEXT NOT NULL,
    finished_at              TEXT NOT NULL,
    PRIMARY KEY (validation_id, symbol)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_rmv_mission ON research_mission_validations(mission_id)",
    "CREATE INDEX IF NOT EXISTS idx_rmvr_validation ON research_mission_validation_results(validation_id)",
]


def _init(con) -> None:
    con.execute(_DDL_VALIDATIONS)
    con.execute(_DDL_RESULTS)
    for idx in _INDEXES:
        con.execute(idx)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_validation(
    validation_id: str,
    mission_id: str,
    trial_number: int,
    trial_symbol: str,
    validation_symbols: list[str],
    objective_metric: str,
    criteria: dict,
    status: str = "queued",
) -> None:
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_mission_validations
               (id, mission_id, trial_number, trial_symbol, status,
                validation_symbols_json, objective_metric, criteria_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status""",
            (validation_id, mission_id, trial_number, trial_symbol, status,
             json.dumps(validation_symbols), objective_metric, json.dumps(criteria), _now()),
        )


def set_validation_status(
    validation_id: str,
    status: str,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
    overall_verdict: str | None = None,
    passing_symbols: int | None = None,
    total_symbols: int | None = None,
) -> None:
    with d1_client.d1_connection() as con:
        _init(con)
        if started:
            con.execute(
                "UPDATE research_mission_validations SET status=?, started_at=? WHERE id=?",
                (status, _now(), validation_id),
            )
        elif finished:
            con.execute(
                """UPDATE research_mission_validations
                   SET status=?, finished_at=?, error=?, overall_verdict=?,
                       passing_symbols=?, total_symbols=?
                   WHERE id=?""",
                (status, _now(), error, overall_verdict, passing_symbols, total_symbols, validation_id),
            )
        else:
            con.execute(
                "UPDATE research_mission_validations SET status=?, error=? WHERE id=?",
                (status, error, validation_id),
            )


def get_validation(validation_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_mission_validations WHERE id=?", (validation_id,)
        ).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def list_validations(mission_id: str) -> list[dict[str, Any]]:
    """Newest first — every validation ever run for this mission, pass or
    fail, nothing hidden."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_mission_validations WHERE mission_id=? ORDER BY created_at DESC",
            (mission_id,),
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def record_validation_result(
    validation_id: str,
    symbol: str,
    passed: bool,
    metrics: dict | None,
    monte_carlo: dict | None,
    walk_forward: dict | None,
    robustness: dict | None,
    criteria_breakdown: dict,
    error: str | None,
    started_at: str,
    finished_at: str,
    feature_mining: dict | None = None,
) -> None:
    """Always INSERT — one row per (validation_id, symbol), written
    exactly once as backtest/mission_validator.py finishes that symbol.
    feature_mining (Phase 1, 2026-07-30) is diagnostic-only — it never
    participates in `passed`/`criteria_breakdown`."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_mission_validation_results
               (validation_id, symbol, passed, metrics_json, monte_carlo_json,
                walk_forward_json, robustness_json, criteria_breakdown_json,
                feature_mining_json, error, started_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (validation_id, symbol, 1 if passed else 0,
             json.dumps(metrics) if metrics is not None else None,
             json.dumps(monte_carlo) if monte_carlo is not None else None,
             json.dumps(walk_forward) if walk_forward is not None else None,
             json.dumps(robustness) if robustness is not None else None,
             json.dumps(criteria_breakdown),
             json.dumps(feature_mining) if feature_mining is not None else None,
             error, started_at, finished_at),
        )


def list_recent_finished_validations(limit: int = 5) -> list[dict[str, Any]]:
    """Newest-first finished validations across every mission. Feature
    Mining / Hypothesis Discovery Phase 1 (2026-07-30) — mirrors
    storage.research_missions.list_recent_missions() exactly; the accessor
    execution/routes/ai.py's _feature_mining_leads() needs to ground a
    hypothesis suggestion in real, recent feature-mining leads without
    already knowing validation IDs."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_mission_validations WHERE status=? ORDER BY finished_at DESC LIMIT ?",
            ("finished", limit),
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def validation_results(validation_id: str) -> list[dict[str, Any]]:
    """Every validated symbol, never filtered — mirrors research_missions.
    leaderboard()'s report-everything convention."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_mission_validation_results WHERE validation_id=? ORDER BY symbol ASC",
            (validation_id,),
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]
