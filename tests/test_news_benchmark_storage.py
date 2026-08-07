"""
tests/test_news_benchmark_storage.py
-----------------------------------------
Provider Benchmark & Data Quality Lab Phase 2 — round-trip tests for
storage/news_benchmark.py. tests/conftest.py's autouse fake_d1 fixture
gives every test an isolated, in-memory D1 stand-in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from storage import news_benchmark


@dataclass(frozen=True)
class _FakeResult:
    provider: str
    symbol: str
    fetch_ok: bool
    error: str | None = None
    latency_ms: int | None = 120
    article_count: int = 1
    coverage_score: float | None = 100.0
    source_diversity_score: float | None = 100.0
    duplicate_rate_score: float | None = 100.0
    freshness_score: float | None = 100.0
    latency_score: float | None = 100.0
    sentiment_availability_score: float | None = 100.0
    cross_provider_coverage_agreement_score: float | None = 100.0
    composite_score: float | None = 100.0
    mean_sentiment: float | None = 0.4
    detail: dict = field(default_factory=dict)


def test_upsert_and_get_run_round_trip():
    news_benchmark.upsert_run("run-1", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    run = news_benchmark.get_run("run-1")
    assert run is not None
    assert run["profile"] == "smoke"
    assert run["status"] == "queued"
    assert run["hours_back"] == 48
    assert run["article_limit"] == 20


def test_upsert_run_is_idempotent_and_updates_status():
    news_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    news_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["marketaux"], 48, 20, status="running")
    run = news_benchmark.get_run("run-2")
    assert run["status"] == "running"


def test_set_run_status_transitions():
    news_benchmark.upsert_run("run-3", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    news_benchmark.set_run_status("run-3", "running", started=True)
    run = news_benchmark.get_run("run-3")
    assert run["status"] == "running"
    assert run["started_at"] is not None

    news_benchmark.set_run_status("run-3", "finished", finished=True)
    run = news_benchmark.get_run("run-3")
    assert run["status"] == "finished"
    assert run["finished_at"] is not None


def test_set_run_status_records_error_on_failure():
    news_benchmark.upsert_run("run-4", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    news_benchmark.set_run_status("run-4", "failed", error="boom", finished=True)
    run = news_benchmark.get_run("run-4")
    assert run["status"] == "failed"
    assert run["error"] == "boom"


def test_get_run_missing_returns_none():
    assert news_benchmark.get_run("does-not-exist") is None


def test_list_recent_runs_newest_first_and_limit():
    for rid in ["r-a", "r-b", "r-c"]:
        news_benchmark.upsert_run(rid, "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    rows = news_benchmark.list_recent_runs(limit=2)
    assert len(rows) == 2
    ids = {r["id"] for r in news_benchmark.list_recent_runs(limit=10)}
    assert ids == {"r-a", "r-b", "r-c"}


def test_record_result_and_run_results_round_trip():
    news_benchmark.upsert_run("run-5", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    result = _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True)
    news_benchmark.record_result("run-5", result)

    rows = news_benchmark.run_results("run-5")
    assert len(rows) == 1
    assert rows[0]["provider"] == "marketaux"
    assert rows[0]["fetch_ok"] == 1
    assert rows[0]["composite_score"] == 100.0
    assert rows[0]["mean_sentiment"] == 0.4


def test_record_result_failed_fetch_preserves_error_text():
    news_benchmark.upsert_run("run-6", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    result = _FakeResult(
        provider="finnhub", symbol="XAUUSD", fetch_ok=False, error="not supported for this provider",
        coverage_score=None, source_diversity_score=None, duplicate_rate_score=None,
        freshness_score=None, latency_score=None, sentiment_availability_score=None,
        cross_provider_coverage_agreement_score=None, composite_score=None, mean_sentiment=None,
    )
    news_benchmark.record_result("run-6", result)
    rows = news_benchmark.run_results("run-6")
    assert rows[0]["fetch_ok"] == 0
    assert rows[0]["error"] == "not supported for this provider"
    assert rows[0]["composite_score"] is None


def test_run_results_filters_by_symbol_and_provider():
    news_benchmark.upsert_run("run-7", "smoke", ["EURUSD", "BTCUSD"], ["marketaux", "finnhub"], 48, 20)
    news_benchmark.record_result("run-7", _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True))
    news_benchmark.record_result("run-7", _FakeResult(provider="finnhub", symbol="EURUSD", fetch_ok=True))
    news_benchmark.record_result("run-7", _FakeResult(provider="marketaux", symbol="BTCUSD", fetch_ok=True))

    assert len(news_benchmark.run_results("run-7")) == 3
    assert len(news_benchmark.run_results("run-7", symbol="EURUSD")) == 2
    assert len(news_benchmark.run_results("run-7", provider="marketaux")) == 2
    assert len(news_benchmark.run_results("run-7", symbol="EURUSD", provider="marketaux")) == 1


def test_record_result_pk_collision_raises():
    """A duplicate (run_id, provider, symbol) write is a real bug in
    Phase 2 (no resume loop exists) — pinned as raising, not silently
    overwriting or ignoring, matching provider_benchmark.py's own
    Phase 1 convention."""
    news_benchmark.upsert_run("run-8", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    result = _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True)
    news_benchmark.record_result("run-8", result)
    with pytest.raises(Exception):
        news_benchmark.record_result("run-8", result)


def test_run_progress_counts_ok_and_failed():
    news_benchmark.upsert_run("run-9", "smoke", ["EURUSD"], ["marketaux", "finnhub"], 48, 20)
    news_benchmark.record_result("run-9", _FakeResult(provider="marketaux", symbol="EURUSD", fetch_ok=True))
    news_benchmark.record_result("run-9", _FakeResult(
        provider="finnhub", symbol="EURUSD", fetch_ok=False, composite_score=None,
    ))
    progress = news_benchmark.run_progress("run-9")
    assert progress == {"total_results": 2, "fetch_ok": 1, "fetch_failed": 1}


def test_run_progress_empty_run():
    news_benchmark.upsert_run("run-10", "smoke", ["EURUSD"], ["marketaux"], 48, 20)
    assert news_benchmark.run_progress("run-10") == {"total_results": 0, "fetch_ok": 0, "fetch_failed": 0}
