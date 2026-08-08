"""
tests/test_analytics_benchmark_storage.py
----------------------------------------------
Provider Benchmark & Data Quality Lab Phase 4 — round-trip tests for
storage/analytics_benchmark.py. tests/conftest.py's autouse fake_d1
fixture gives every test an isolated, in-memory D1 stand-in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from storage import analytics_benchmark


@dataclass(frozen=True)
class _FakeResult:
    provider: str
    symbol: str
    fetch_ok: bool
    error: str | None = None
    latency_ms: int | None = 130
    article_count: int = 1
    coverage_score: float | None = 100.0
    determinism_score: float | None = 100.0
    determinism_detail: dict = field(default_factory=dict)
    freshness_score: float | None = 100.0
    latency_score: float | None = 100.0
    composite_score: float | None = 100.0


def test_upsert_and_get_run_round_trip():
    analytics_benchmark.upsert_run("run-1", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    run = analytics_benchmark.get_run("run-1")
    assert run is not None
    assert run["profile"] == "smoke"
    assert run["status"] == "queued"
    assert run["hours_back"] == 48
    assert run["article_limit"] == 20


def test_upsert_run_is_idempotent_and_updates_status():
    analytics_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    analytics_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["marketaux"], 48, 20, status="running")
    run = analytics_benchmark.get_run("run-2")
    assert run["status"] == "running"


def test_set_run_status_transitions():
    analytics_benchmark.upsert_run("run-3", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    analytics_benchmark.set_run_status("run-3", "running", started=True)
    run = analytics_benchmark.get_run("run-3")
    assert run["status"] == "running"
    assert run["started_at"] is not None

    analytics_benchmark.set_run_status("run-3", "finished", finished=True)
    run = analytics_benchmark.get_run("run-3")
    assert run["status"] == "finished"
    assert run["finished_at"] is not None


def test_set_run_status_records_error_on_failure():
    analytics_benchmark.upsert_run("run-4", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    analytics_benchmark.set_run_status("run-4", "failed", error="boom", finished=True)
    run = analytics_benchmark.get_run("run-4")
    assert run["status"] == "failed"
    assert run["error"] == "boom"


def test_get_run_missing_returns_none():
    assert analytics_benchmark.get_run("does-not-exist") is None


def test_list_recent_runs_newest_first_and_limit():
    for rid in ["r-a", "r-b", "r-c"]:
        analytics_benchmark.upsert_run(rid, "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    rows = analytics_benchmark.list_recent_runs(limit=2)
    assert len(rows) == 2
    ids = {r["id"] for r in analytics_benchmark.list_recent_runs(limit=10)}
    assert ids == {"r-a", "r-b", "r-c"}


def test_record_result_and_run_results_round_trip():
    analytics_benchmark.upsert_run("run-5", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    result = _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True)
    analytics_benchmark.record_result("run-5", result)

    rows = analytics_benchmark.run_results("run-5")
    assert len(rows) == 1
    assert rows[0]["provider"] == "marketaux"
    assert rows[0]["fetch_ok"] == 1
    assert rows[0]["composite_score"] == 100.0
    assert rows[0]["determinism_score"] == 100.0


def test_record_result_failed_fetch_preserves_error_text():
    analytics_benchmark.upsert_run("run-6", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    result = _FakeResult(
        provider="marketaux", symbol="XAUUSD", fetch_ok=False, error="not supported for this provider",
        coverage_score=None, determinism_score=None, freshness_score=None, latency_score=None, composite_score=None,
    )
    analytics_benchmark.record_result("run-6", result)
    rows = analytics_benchmark.run_results("run-6")
    assert rows[0]["fetch_ok"] == 0
    assert rows[0]["error"] == "not supported for this provider"
    assert rows[0]["composite_score"] is None


def test_run_results_filters_by_symbol_and_provider():
    analytics_benchmark.upsert_run("run-7", "smoke", ["EURUSD", "BTCUSD"], ["marketaux"], 48, 20)
    analytics_benchmark.record_result("run-7", _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True))
    analytics_benchmark.record_result("run-7", _FakeResult(provider="marketaux", symbol="BTCUSD", fetch_ok=True))

    assert len(analytics_benchmark.run_results("run-7")) == 2
    assert len(analytics_benchmark.run_results("run-7", symbol="EURUSD")) == 1
    assert len(analytics_benchmark.run_results("run-7", provider="marketaux")) == 2
    assert len(analytics_benchmark.run_results("run-7", symbol="EURUSD", provider="marketaux")) == 1


def test_record_result_pk_collision_raises():
    """A duplicate (run_id, provider, symbol) write is a real bug in
    Phase 4 (no resume loop exists) — pinned as raising, not silently
    overwriting or ignoring, matching Phase 1-3's own convention."""
    analytics_benchmark.upsert_run("run-8", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    result = _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True)
    analytics_benchmark.record_result("run-8", result)
    with pytest.raises(Exception):
        analytics_benchmark.record_result("run-8", result)


def test_run_progress_counts_ok_and_failed():
    analytics_benchmark.upsert_run("run-9", "smoke", ["EURUSD", "BTCUSD"], ["marketaux"], 48, 20)
    analytics_benchmark.record_result("run-9", _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True))
    analytics_benchmark.record_result("run-9", _FakeResult(
        provider="marketaux", symbol="BTCUSD", fetch_ok=False, composite_score=None,
    ))
    progress = analytics_benchmark.run_progress("run-9")
    assert progress == {"total_results": 2, "fetch_ok": 1, "fetch_failed": 1}


def test_run_progress_empty_run():
    analytics_benchmark.upsert_run("run-10", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    assert analytics_benchmark.run_progress("run-10") == {"total_results": 0, "fetch_ok": 0, "fetch_failed": 0}


def test_record_result_stores_determinism_detail_json():
    analytics_benchmark.upsert_run("run-11", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    result = _FakeResult(
        provider="marketaux", symbol="EURUSD", fetch_ok=True,
        determinism_detail={"overlap_count": 3, "matched": 2, "mismatched": 1},
    )
    analytics_benchmark.record_result("run-11", result)
    rows = analytics_benchmark.run_results("run-11")
    import json
    detail = json.loads(rows[0]["detail_json"])
    assert detail["overlap_count"] == 3
    assert detail["mismatched"] == 1
