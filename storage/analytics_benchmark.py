"""
storage/analytics_benchmark.py
----------------------------------
Provider Benchmark & Data Quality Lab Phase 4 — D1 storage for
analytics-benchmark (reproducibility) runs and their per-(provider,
symbol) results. Same `_DDL` + `_init(con)` idiom as storage/
news_benchmark.py/storage/macro_benchmark.py: idempotent
`CREATE TABLE IF NOT EXISTS`, called at the top of every public function.
No storage/migrations.py entry needed (brand-new tables — migrations are
ALTER-only).

MEASUREMENT / ADVISORY LAYER ONLY: nothing in this module is ever read by
main.py, scheduler.py, or engines/sentiment_engine.py — an analytics
benchmark result is evidence an operator reviews manually, never an
automatic input to a live trading decision.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS analytics_benchmark_runs (
    id              TEXT PRIMARY KEY,
    profile         TEXT NOT NULL,
    status          TEXT NOT NULL,   -- queued|running|finished|failed|cancelled
    symbols_json    TEXT NOT NULL,
    providers_json  TEXT NOT NULL,
    hours_back      INTEGER NOT NULL,
    article_limit   INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    error           TEXT
)
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS analytics_benchmark_results (
    run_id              TEXT NOT NULL,
    provider            TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    fetch_ok            INTEGER NOT NULL,
    error               TEXT,
    latency_ms          INTEGER,
    article_count       INTEGER NOT NULL DEFAULT 0,
    coverage_score      REAL,
    determinism_score   REAL,
    freshness_score     REAL,
    latency_score       REAL,
    composite_score     REAL,
    detail_json         TEXT,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (run_id, provider, symbol)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_abr_run_symbol ON analytics_benchmark_results(run_id, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_abr_run_provider ON analytics_benchmark_results(run_id, provider)",
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
    symbols: list[str],
    providers: list[str],
    hours_back: int,
    article_limit: int,
    status: str = "queued",
) -> None:
    """Idempotent — used both at run creation (API route) and at
    backtest/analytics_benchmark.py's own CLI startup."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO analytics_benchmark_runs
               (id, profile, status, symbols_json, providers_json, hours_back, article_limit, created_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status""",
            (run_id, profile, status, json.dumps(symbols), json.dumps(providers), hours_back, article_limit, _now()),
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
                "UPDATE analytics_benchmark_runs SET status=?, started_at=? WHERE id=?",
                (status, _now(), run_id),
            )
        elif finished:
            con.execute(
                "UPDATE analytics_benchmark_runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, _now(), error, run_id),
            )
        else:
            con.execute(
                "UPDATE analytics_benchmark_runs SET status=?, error=? WHERE id=?",
                (status, error, run_id),
            )


def get_run(run_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM analytics_benchmark_runs WHERE id=?", (run_id,)).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def list_recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM analytics_benchmark_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def record_result(run_id: str, result: Any) -> None:
    """`result` is a backtest.analytics_benchmark.AnalyticsBenchmarkResult
    — accepted duck-typed (not imported at module level) to avoid a
    storage<->backtest import cycle, matching provider_benchmark.py's own
    convention. Always INSERT — the (run_id, provider, symbol) primary
    key means a duplicate call for the same point is a real bug, never a
    resumable-write path (Phase 4 has no resume loop)."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO analytics_benchmark_results
               (run_id, provider, symbol, fetch_ok, error, latency_ms, article_count,
                coverage_score, determinism_score, freshness_score, latency_score,
                composite_score, detail_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, result.provider, result.symbol, 1 if result.fetch_ok else 0, result.error,
             result.latency_ms, result.article_count,
             result.coverage_score, result.determinism_score, result.freshness_score,
             result.latency_score, result.composite_score,
             json.dumps(result.determinism_detail), _now()),
        )


def run_results(run_id: str, symbol: str | None = None, provider: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM analytics_benchmark_results WHERE run_id=?"
    params: list[Any] = [run_id]
    if symbol:
        query += " AND symbol=?"
        params.append(symbol)
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
            "SELECT fetch_ok FROM analytics_benchmark_results WHERE run_id=?", (run_id,)
        ).fetchall()
    total = len(rows)
    ok = sum(1 for r in rows if r["fetch_ok"])
    return {"total_results": total, "fetch_ok": ok, "fetch_failed": total - ok}
