"""
tests/test_engine_benchmark_storage.py
--------------------------------------------
Engine Benchmark — round-trip tests for storage/engine_benchmark.py.
tests/conftest.py's autouse fake_d1 fixture gives every test an
isolated, in-memory D1 stand-in.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from storage import engine_benchmark


@dataclass(frozen=True)
class _FakeResult:
    engine: str
    symbol: str
    run_ok: bool
    error: str | None = None
    total_trades: int = 10
    win_rate: float | None = 0.5
    profit_factor: float | None = 1.4
    sharpe_ratio: float | None = 0.5
    sortino_ratio: float | None = 0.7
    max_drawdown: float | None = 6.0
    expectancy_r: float | None = 0.12
    expectancy: float | None = 18.0
    bars_used: int = 400
    data_start: str | None = "2023-01-01T00:00:00"
    data_end: str | None = "2023-06-01T00:00:00"


def test_upsert_and_get_run_round_trip():
    engine_benchmark.upsert_run("run-1", "smoke", ["EURUSD"], ["nnfx"], None, None)
    run = engine_benchmark.get_run("run-1")
    assert run is not None
    assert run["profile"] == "smoke"
    assert run["status"] == "queued"


def test_upsert_run_is_idempotent_and_updates_status():
    engine_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["nnfx"], None, None)
    engine_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["nnfx"], None, None, status="running")
    run = engine_benchmark.get_run("run-2")
    assert run["status"] == "running"


def test_set_run_status_transitions():
    engine_benchmark.upsert_run("run-3", "smoke", ["EURUSD"], ["nnfx"], None, None)
    engine_benchmark.set_run_status("run-3", "running", started=True)
    run = engine_benchmark.get_run("run-3")
    assert run["status"] == "running"
    assert run["started_at"] is not None

    engine_benchmark.set_run_status("run-3", "finished", finished=True)
    run = engine_benchmark.get_run("run-3")
    assert run["status"] == "finished"
    assert run["finished_at"] is not None


def test_set_run_status_records_error_on_failure():
    engine_benchmark.upsert_run("run-4", "smoke", ["EURUSD"], ["nnfx"], None, None)
    engine_benchmark.set_run_status("run-4", "failed", error="boom", finished=True)
    run = engine_benchmark.get_run("run-4")
    assert run["status"] == "failed"
    assert run["error"] == "boom"


def test_get_run_missing_returns_none():
    assert engine_benchmark.get_run("does-not-exist") is None


def test_list_recent_runs_newest_first_and_limit():
    for rid in ["r-a", "r-b", "r-c"]:
        engine_benchmark.upsert_run(rid, "smoke", ["EURUSD"], ["nnfx"], None, None)
    rows = engine_benchmark.list_recent_runs(limit=2)
    assert len(rows) == 2
    ids = {r["id"] for r in engine_benchmark.list_recent_runs(limit=10)}
    assert ids == {"r-a", "r-b", "r-c"}


def test_record_result_and_run_results_round_trip():
    engine_benchmark.upsert_run("run-5", "smoke", ["EURUSD"], ["nnfx"], None, None)
    result = _FakeResult(engine="nnfx", symbol="EURUSD", run_ok=True)
    engine_benchmark.record_result("run-5", result)

    rows = engine_benchmark.run_results("run-5")
    assert len(rows) == 1
    assert rows[0]["engine"] == "nnfx"
    assert rows[0]["run_ok"] == 1
    assert rows[0]["profit_factor"] == 1.4


def test_record_result_failed_run_preserves_error_text():
    engine_benchmark.upsert_run("run-6", "smoke", ["EURUSD"], ["nnfx"], None, None)
    result = _FakeResult(
        engine="nnfx", symbol="EURUSD", run_ok=False, error="simulated failure",
        total_trades=0, win_rate=None, profit_factor=None, sharpe_ratio=None,
        sortino_ratio=None, max_drawdown=None, expectancy_r=None, expectancy=None,
    )
    engine_benchmark.record_result("run-6", result)
    rows = engine_benchmark.run_results("run-6")
    assert rows[0]["run_ok"] == 0
    assert rows[0]["error"] == "simulated failure"
    assert rows[0]["profit_factor"] is None


def test_run_results_filters_by_symbol_and_engine():
    engine_benchmark.upsert_run("run-7", "smoke", ["EURUSD", "XAUUSD"], ["nnfx", "smc"], None, None)
    engine_benchmark.record_result("run-7", _FakeResult(engine="nnfx", symbol="EURUSD", run_ok=True))
    engine_benchmark.record_result("run-7", _FakeResult(engine="smc", symbol="EURUSD", run_ok=True))
    engine_benchmark.record_result("run-7", _FakeResult(engine="nnfx", symbol="XAUUSD", run_ok=True))

    assert len(engine_benchmark.run_results("run-7")) == 3
    assert len(engine_benchmark.run_results("run-7", symbol="EURUSD")) == 2
    assert len(engine_benchmark.run_results("run-7", engine="nnfx")) == 2
    assert len(engine_benchmark.run_results("run-7", symbol="EURUSD", engine="nnfx")) == 1


def test_record_result_pk_collision_raises():
    """A duplicate (run_id, engine, symbol) write is a real bug (no
    resume loop exists) — pinned as raising, not silently overwriting or
    ignoring, matching storage/provider_benchmark.py's own precedent."""
    engine_benchmark.upsert_run("run-8", "smoke", ["EURUSD"], ["nnfx"], None, None)
    result = _FakeResult(engine="nnfx", symbol="EURUSD", run_ok=True)
    engine_benchmark.record_result("run-8", result)
    with pytest.raises(Exception):
        engine_benchmark.record_result("run-8", result)


def test_run_progress_counts_ok_and_failed():
    engine_benchmark.upsert_run("run-9", "smoke", ["EURUSD"], ["nnfx"], None, None)
    engine_benchmark.record_result("run-9", _FakeResult(engine="nnfx", symbol="EURUSD", run_ok=True))
    engine_benchmark.record_result("run-9", _FakeResult(
        engine="smc", symbol="EURUSD", run_ok=False, profit_factor=None,
    ))
    progress = engine_benchmark.run_progress("run-9")
    assert progress == {"total_results": 2, "run_ok": 1, "run_failed": 1}


def test_run_progress_empty_run():
    engine_benchmark.upsert_run("run-10", "smoke", ["EURUSD"], ["nnfx"], None, None)
    assert engine_benchmark.run_progress("run-10") == {"total_results": 0, "run_ok": 0, "run_failed": 0}


def test_upsert_run_persists_start_end_dates():
    engine_benchmark.upsert_run("run-11", "standard", ["EURUSD"], ["nnfx"], "2023-01-01", "2023-12-31")
    run = engine_benchmark.get_run("run-11")
    assert run["start_date"] == "2023-01-01"
    assert run["end_date"] == "2023-12-31"
