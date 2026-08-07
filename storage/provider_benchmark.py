"""
storage/provider_benchmark.py
--------------------------------
Provider Benchmark & Data Quality Lab Phase 1 — D1 storage for
price-benchmark runs and their per-(provider, symbol, timeframe) results.

Same `_DDL` + `_init(con)` idiom as storage/research_missions.py:
idempotent `CREATE TABLE IF NOT EXISTS`, called at the top of every
public function. No storage/migrations.py entry needed (brand-new
tables — migrations are ALTER-only, per research_missions.py's own
precedent).

MEASUREMENT / ADVISORY LAYER ONLY: nothing in this module is ever read
by core/data_providers.py, main.py, or scheduler.py — a benchmark result
is evidence an operator reviews and acts on manually via config.yaml,
never an automatic input to a live trading decision.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS provider_benchmark_runs (
    id              TEXT PRIMARY KEY,
    profile         TEXT NOT NULL,
    status          TEXT NOT NULL,   -- queued|running|finished|failed|cancelled
    symbols_json    TEXT NOT NULL,
    timeframes_json TEXT NOT NULL,
    providers_json  TEXT,            -- null = per-symbol default chain
    outputsize      INTEGER NOT NULL,
    tolerance_pct   REAL NOT NULL,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    error           TEXT
)
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS provider_benchmark_results (
    run_id                          TEXT NOT NULL,
    provider                        TEXT NOT NULL,
    symbol                          TEXT NOT NULL,
    timeframe                       TEXT NOT NULL,
    fetch_ok                        INTEGER NOT NULL,
    error                           TEXT,
    latency_ms                      INTEGER,
    bars_fetched                    INTEGER NOT NULL DEFAULT 0,
    completeness_score              REAL,
    correctness_score               REAL,
    timestamp_integrity_score       REAL,
    ohlc_integrity_score            REAL,
    ohlc_integrity_reason           TEXT,
    spread_quality_score            REAL,
    cross_provider_agreement_score  REAL,
    freshness_score                 REAL,
    latency_score                   REAL,
    composite_score                 REAL,
    detail_json                     TEXT,   -- full-precision completeness/correctness/timestamp_integrity detail dicts
    created_at                      TEXT NOT NULL,
    PRIMARY KEY (run_id, provider, symbol, timeframe)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pbr_run_symbol ON provider_benchmark_results(run_id, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_pbr_run_provider ON provider_benchmark_results(run_id, provider)",
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
    timeframes: list[str],
    providers: list[str] | None,
    outputsize: int,
    tolerance_pct: float,
    status: str = "queued",
) -> None:
    """Idempotent — used both at run creation (API route) and at
    backtest/price_benchmark.py's own CLI startup."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO provider_benchmark_runs
               (id, profile, status, symbols_json, timeframes_json, providers_json,
                outputsize, tolerance_pct, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status""",
            (run_id, profile, status, json.dumps(symbols), json.dumps(timeframes),
             json.dumps(providers) if providers is not None else None,
             outputsize, tolerance_pct, _now()),
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
                "UPDATE provider_benchmark_runs SET status=?, started_at=? WHERE id=?",
                (status, _now(), run_id),
            )
        elif finished:
            con.execute(
                "UPDATE provider_benchmark_runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, _now(), error, run_id),
            )
        else:
            con.execute(
                "UPDATE provider_benchmark_runs SET status=?, error=? WHERE id=?",
                (status, error, run_id),
            )


def get_run(run_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM provider_benchmark_runs WHERE id=?", (run_id,)).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def list_recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM provider_benchmark_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def record_result(run_id: str, result: Any) -> None:
    """`result` is a backtest.price_benchmark.BenchmarkResult — accepted
    duck-typed (not imported at module level) to avoid a storage<->backtest
    import cycle, matching every other storage/*.py module's lazy-import
    convention in this codebase. Always INSERT — the
    (run_id, provider, symbol, timeframe) primary key means a duplicate
    call for the same point is a real bug, never a resumable-write path
    (Phase 1 has no resume loop, unlike research_missions.py's trials)."""
    detail = {
        "completeness_detail": result.completeness_detail,
        "correctness_detail": result.correctness_detail,
        "timestamp_integrity_detail": result.timestamp_integrity_detail,
    }
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO provider_benchmark_results
               (run_id, provider, symbol, timeframe, fetch_ok, error, latency_ms, bars_fetched,
                completeness_score, correctness_score, timestamp_integrity_score,
                ohlc_integrity_score, ohlc_integrity_reason, spread_quality_score,
                cross_provider_agreement_score, freshness_score, latency_score, composite_score,
                detail_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, result.provider, result.symbol, result.timeframe,
             1 if result.fetch_ok else 0, result.error, result.latency_ms, result.bars_fetched,
             result.completeness_score, result.correctness_score, result.timestamp_integrity_score,
             result.ohlc_integrity_score, result.ohlc_integrity_reason, result.spread_quality_score,
             result.cross_provider_agreement_score, result.freshness_score, result.latency_score,
             result.composite_score, json.dumps(detail), _now()),
        )


def run_results(run_id: str, symbol: str | None = None, provider: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM provider_benchmark_results WHERE run_id=?"
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
            "SELECT fetch_ok FROM provider_benchmark_results WHERE run_id=?", (run_id,)
        ).fetchall()
    total = len(rows)
    ok = sum(1 for r in rows if r["fetch_ok"])
    return {"total_results": total, "fetch_ok": ok, "fetch_failed": total - ok}
