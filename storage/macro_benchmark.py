"""
storage/macro_benchmark.py
-----------------------------
Provider Benchmark & Data Quality Lab Phase 3 — D1 storage for
macro-benchmark runs and their per-(provider, series) results. Same
`_DDL` + `_init(con)` idiom as storage/provider_benchmark.py (Phase 1)
and storage/news_benchmark.py (Phase 2): idempotent
`CREATE TABLE IF NOT EXISTS`, called at the top of every public function.
No storage/migrations.py entry needed (brand-new tables — migrations are
ALTER-only).

MEASUREMENT / ADVISORY LAYER ONLY: nothing in this module is ever read by
main.py, scheduler.py, or engines/macro_engine.py — a macro benchmark
result is evidence an operator reviews manually, never an automatic
input to a live trading decision.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS macro_benchmark_runs (
    id              TEXT PRIMARY KEY,
    profile         TEXT NOT NULL,
    status          TEXT NOT NULL,   -- queued|running|finished|failed|cancelled
    series_json     TEXT NOT NULL,
    providers_json  TEXT,            -- null = per-series default provider set
    months          INTEGER,         -- null = per-series default lookback
    tolerance_pct   REAL NOT NULL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    error           TEXT
)
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS macro_benchmark_results (
    run_id                          TEXT NOT NULL,
    provider                        TEXT NOT NULL,
    series                          TEXT NOT NULL,
    fetch_ok                        INTEGER NOT NULL,
    error                           TEXT,
    latency_ms                      INTEGER,
    observation_count               INTEGER NOT NULL DEFAULT 0,
    completeness_score              REAL,
    freshness_score                 REAL,
    timestamp_integrity_score       REAL,
    latency_score                   REAL,
    cross_provider_agreement_score  REAL,
    composite_score                 REAL,
    latest_value                    REAL,
    latest_date                     TEXT,
    detail_json                     TEXT,
    created_at                      TEXT NOT NULL,
    PRIMARY KEY (run_id, provider, series)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mbr_run_series ON macro_benchmark_results(run_id, series)",
    "CREATE INDEX IF NOT EXISTS idx_mbr_run_provider ON macro_benchmark_results(run_id, provider)",
]


def _init(con) -> None:
    con.execute(_DDL_RUNS)
    con.execute(_DDL_RESULTS)
    for idx in _INDEXES:
        con.execute(idx)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_run(
    run_id: str,
    profile: str,
    series: list[str],
    providers: list[str] | None,
    months: int | None,
    tolerance_pct: float,
    status: str = "queued",
) -> None:
    """Idempotent — used both at run creation (API route) and at
    backtest/macro_benchmark.py's own CLI startup."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO macro_benchmark_runs
               (id, profile, status, series_json, providers_json, months, tolerance_pct, created_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status""",
            (run_id, profile, status, json.dumps(series),
             json.dumps(providers) if providers else None, months, tolerance_pct, _now()),
        )


def set_run_status(
    run_id: str,
    status: str,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    with d1_client.d1_connection() as con:
        _init(con)
        if started:
            con.execute(
                "UPDATE macro_benchmark_runs SET status=?, started_at=? WHERE id=?",
                (status, _now(), run_id),
            )
        elif finished:
            con.execute(
                "UPDATE macro_benchmark_runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, _now(), error, run_id),
            )
        else:
            con.execute(
                "UPDATE macro_benchmark_runs SET status=?, error=? WHERE id=?",
                (status, error, run_id),
            )


def get_run(run_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM macro_benchmark_runs WHERE id=?", (run_id,)).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def list_recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM macro_benchmark_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def record_result(run_id: str, result: Any) -> None:
    """`result` is a backtest.macro_benchmark.MacroBenchmarkResult —
    accepted duck-typed (not imported at module level) to avoid a
    storage<->backtest import cycle, matching provider_benchmark.py's own
    convention. Always INSERT — the (run_id, provider, series) primary
    key means a duplicate call for the same point is a real bug, never a
    resumable-write path (Phase 3 has no resume loop)."""
    detail = {
        "completeness_detail": result.completeness_detail,
        "timestamp_integrity_detail": result.timestamp_integrity_detail,
        "cross_provider_agreement_detail": result.cross_provider_agreement_detail,
    }
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO macro_benchmark_results
               (run_id, provider, series, fetch_ok, error, latency_ms, observation_count,
                completeness_score, freshness_score, timestamp_integrity_score, latency_score,
                cross_provider_agreement_score, composite_score, latest_value, latest_date,
                detail_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, result.provider, result.series, 1 if result.fetch_ok else 0, result.error,
             result.latency_ms, result.observation_count,
             result.completeness_score, result.freshness_score, result.timestamp_integrity_score,
             result.latency_score, result.cross_provider_agreement_score, result.composite_score,
             result.latest_value, result.latest_date, json.dumps(detail), _now()),
        )


def run_results(run_id: str, series: str | None = None, provider: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM macro_benchmark_results WHERE run_id=?"
    params: list[Any] = [run_id]
    if series:
        query += " AND series=?"
        params.append(series)
    if provider:
        query += " AND provider=?"
        params.append(provider)
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def run_progress(run_id: str) -> dict[str, Any]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT fetch_ok FROM macro_benchmark_results WHERE run_id=?", (run_id,)
        ).fetchall()
    total = len(rows)
    ok = sum(1 for r in rows if r["fetch_ok"])
    return {"total_results": total, "fetch_ok": ok, "fetch_failed": total - ok}
