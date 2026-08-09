"""
storage/engine_benchmark.py
------------------------------
Engine Benchmark — D1 storage for standalone-engine-ablation backtest
runs and their per-(engine, symbol) results.

Same `_DDL` + `_init(con)` idiom as storage/provider_benchmark.py:
idempotent `CREATE TABLE IF NOT EXISTS`, called at the top of every
public function. No storage/migrations.py entry needed (brand-new
tables — migrations are ALTER-only, per storage/research_missions.py's
own precedent).

MEASUREMENT / ADVISORY LAYER ONLY: nothing in this module is ever read
by main.py, scheduler.py, or confluence/ — a benchmark result is
descriptive evidence an operator reads, never an automatic input to a
live trading decision, and never a ranking/promotion mechanism (see
backtest/engine_benchmark.py's own module docstring for the CLAUDE.md
dead-list reasoning).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_RUNS = """
CREATE TABLE IF NOT EXISTS engine_benchmark_runs (
    id           TEXT PRIMARY KEY,
    profile      TEXT NOT NULL,
    status       TEXT NOT NULL,   -- queued|running|finished|failed|cancelled
    symbols_json TEXT NOT NULL,
    engines_json TEXT NOT NULL,
    start_date   TEXT,
    end_date     TEXT,
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    error        TEXT
)
"""

_DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS engine_benchmark_results (
    run_id          TEXT NOT NULL,
    engine          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    run_ok          INTEGER NOT NULL,
    error           TEXT,
    total_trades    INTEGER NOT NULL DEFAULT 0,
    win_rate        REAL,
    profit_factor   REAL,
    sharpe_ratio    REAL,
    sortino_ratio   REAL,
    max_drawdown    REAL,
    expectancy_r    REAL,
    expectancy      REAL,
    bars_used       INTEGER NOT NULL DEFAULT 0,
    data_start      TEXT,
    data_end        TEXT,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (run_id, engine, symbol)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ebr_run_symbol ON engine_benchmark_results(run_id, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_ebr_run_engine ON engine_benchmark_results(run_id, engine)",
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
    engines: list[str],
    start: str | None,
    end: str | None,
    status: str = "queued",
) -> None:
    """Idempotent — used both at run creation (API route) and at
    backtest/engine_benchmark.py's own CLI startup."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO engine_benchmark_runs
               (id, profile, status, symbols_json, engines_json, start_date, end_date, created_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status""",
            (run_id, profile, status, json.dumps(symbols), json.dumps(engines), start, end, _now()),
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
                "UPDATE engine_benchmark_runs SET status=?, started_at=? WHERE id=?",
                (status, _now(), run_id),
            )
        elif finished:
            con.execute(
                "UPDATE engine_benchmark_runs SET status=?, finished_at=?, error=? WHERE id=?",
                (status, _now(), error, run_id),
            )
        else:
            con.execute(
                "UPDATE engine_benchmark_runs SET status=?, error=? WHERE id=?",
                (status, error, run_id),
            )


def get_run(run_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM engine_benchmark_runs WHERE id=?", (run_id,)).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def list_recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM engine_benchmark_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def record_result(run_id: str, result: Any) -> None:
    """`result` is a backtest.engine_benchmark.EngineBenchmarkResult —
    accepted duck-typed (not imported at module level) to avoid a
    storage<->backtest import cycle, matching every other storage/*.py
    module's lazy-import convention in this codebase. Always INSERT —
    the (run_id, engine, symbol) primary key means a duplicate call for
    the same point is a real bug, never a resumable-write path (this
    module has no resume loop, unlike research_missions.py's trials)."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO engine_benchmark_results
               (run_id, engine, symbol, run_ok, error, total_trades, win_rate, profit_factor,
                sharpe_ratio, sortino_ratio, max_drawdown, expectancy_r, expectancy,
                bars_used, data_start, data_end, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, result.engine, result.symbol, 1 if result.run_ok else 0, result.error,
             result.total_trades, result.win_rate, result.profit_factor,
             result.sharpe_ratio, result.sortino_ratio, result.max_drawdown,
             result.expectancy_r, result.expectancy,
             result.bars_used, result.data_start, result.data_end, _now()),
        )


def run_results(run_id: str, symbol: str | None = None, engine: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM engine_benchmark_results WHERE run_id=?"
    params: list[Any] = [run_id]
    if symbol:
        query += " AND symbol=?"
        params.append(symbol)
    if engine:
        query += " AND engine=?"
        params.append(engine)
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(query, tuple(params)).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def run_progress(run_id: str) -> dict[str, Any]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT run_ok FROM engine_benchmark_results WHERE run_id=?", (run_id,)
        ).fetchall()
    total = len(rows)
    ok = sum(1 for r in rows if r["run_ok"])
    return {"total_results": total, "run_ok": ok, "run_failed": total - ok}
