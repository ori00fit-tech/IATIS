"""
tests/test_macro_benchmark_storage.py
-----------------------------------------
Provider Benchmark & Data Quality Lab Phase 3 — round-trip tests for
storage/macro_benchmark.py. tests/conftest.py's autouse fake_d1 fixture
gives every test an isolated, in-memory D1 stand-in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from storage import macro_benchmark


@dataclass(frozen=True)
class _FakeResult:
    provider: str
    series: str
    fetch_ok: bool
    error: str | None = None
    latency_ms: int | None = 150
    observation_count: int = 30
    completeness_score: float | None = 100.0
    completeness_detail: dict = field(default_factory=dict)
    freshness_score: float | None = 100.0
    timestamp_integrity_score: float | None = 100.0
    timestamp_integrity_detail: dict = field(default_factory=dict)
    latency_score: float | None = 100.0
    cross_provider_agreement_score: float | None = 100.0
    cross_provider_agreement_detail: dict = field(default_factory=dict)
    composite_score: float | None = 100.0
    latest_value: float | None = 4.25
    latest_date: str | None = "2026-07-09T00:00:00+00:00"


def test_upsert_and_get_run_round_trip():
    macro_benchmark.upsert_run("run-1", "smoke", ["VIX", "DXY", "US10Y"], None, None, 1.0)
    run = macro_benchmark.get_run("run-1")
    assert run is not None
    assert run["profile"] == "smoke"
    assert run["status"] == "queued"
    assert run["tolerance_pct"] == 1.0
    assert run["providers_json"] is None
    assert run["months"] is None


def test_upsert_run_records_explicit_providers_and_months():
    macro_benchmark.upsert_run("run-1b", "standard", ["VIX"], ["cboe", "fred"], 6, 1.0)
    run = macro_benchmark.get_run("run-1b")
    assert run["providers_json"] == '["cboe", "fred"]'
    assert run["months"] == 6


def test_upsert_run_is_idempotent_and_updates_status():
    macro_benchmark.upsert_run("run-2", "smoke", ["VIX"], None, None, 1.0)
    macro_benchmark.upsert_run("run-2", "smoke", ["VIX"], None, None, 1.0, status="running")
    run = macro_benchmark.get_run("run-2")
    assert run["status"] == "running"


def test_set_run_status_transitions():
    macro_benchmark.upsert_run("run-3", "smoke", ["VIX"], None, None, 1.0)
    macro_benchmark.set_run_status("run-3", "running", started=True)
    run = macro_benchmark.get_run("run-3")
    assert run["status"] == "running"
    assert run["started_at"] is not None

    macro_benchmark.set_run_status("run-3", "finished", finished=True)
    run = macro_benchmark.get_run("run-3")
    assert run["status"] == "finished"
    assert run["finished_at"] is not None


def test_set_run_status_records_error_on_failure():
    macro_benchmark.upsert_run("run-4", "smoke", ["VIX"], None, None, 1.0)
    macro_benchmark.set_run_status("run-4", "failed", error="boom", finished=True)
    run = macro_benchmark.get_run("run-4")
    assert run["status"] == "failed"
    assert run["error"] == "boom"


def test_get_run_missing_returns_none():
    assert macro_benchmark.get_run("does-not-exist") is None


def test_list_recent_runs_newest_first_and_limit():
    for rid in ["r-a", "r-b", "r-c"]:
        macro_benchmark.upsert_run(rid, "smoke", ["VIX"], None, None, 1.0)
    rows = macro_benchmark.list_recent_runs(limit=2)
    assert len(rows) == 2
    ids = {r["id"] for r in macro_benchmark.list_recent_runs(limit=10)}
    assert ids == {"r-a", "r-b", "r-c"}


def test_record_result_and_run_results_round_trip():
    macro_benchmark.upsert_run("run-5", "smoke", ["US10Y"], None, None, 1.0)
    result = _FakeResult(provider="fred", series="US10Y", fetch_ok=True)
    macro_benchmark.record_result("run-5", result)

    rows = macro_benchmark.run_results("run-5")
    assert len(rows) == 1
    assert rows[0]["provider"] == "fred"
    assert rows[0]["composite_score"] == 100.0
    assert rows[0]["latest_value"] == 4.25


def test_record_result_failed_fetch_preserves_error_text():
    macro_benchmark.upsert_run("run-6", "smoke", ["US10Y"], None, None, 1.0)
    result = _FakeResult(
        provider="cboe", series="US10Y", fetch_ok=False, error="cboe only supplies VIX, not 'US10Y'",
        completeness_score=None, freshness_score=None, timestamp_integrity_score=None,
        latency_score=None, cross_provider_agreement_score=None, composite_score=None,
        latest_value=None, latest_date=None, observation_count=0,
    )
    macro_benchmark.record_result("run-6", result)
    rows = macro_benchmark.run_results("run-6")
    assert rows[0]["fetch_ok"] == 0
    assert rows[0]["error"] == "cboe only supplies VIX, not 'US10Y'"
    assert rows[0]["composite_score"] is None


def test_run_results_filters_by_series_and_provider():
    macro_benchmark.upsert_run("run-7", "smoke", ["VIX", "US10Y"], None, None, 1.0)
    macro_benchmark.record_result("run-7", _FakeResult(provider="fred", series="VIX", fetch_ok=True))
    macro_benchmark.record_result("run-7", _FakeResult(provider="cboe", series="VIX", fetch_ok=True))
    macro_benchmark.record_result("run-7", _FakeResult(provider="fred", series="US10Y", fetch_ok=True))

    assert len(macro_benchmark.run_results("run-7")) == 3
    assert len(macro_benchmark.run_results("run-7", series="VIX")) == 2
    assert len(macro_benchmark.run_results("run-7", provider="fred")) == 2
    assert len(macro_benchmark.run_results("run-7", series="VIX", provider="fred")) == 1


def test_record_result_pk_collision_raises():
    """A duplicate (run_id, provider, series) write is a real bug in
    Phase 3 (no resume loop exists) — pinned as raising, not silently
    overwriting or ignoring, matching Phase 1/2's own convention."""
    macro_benchmark.upsert_run("run-8", "smoke", ["VIX"], None, None, 1.0)
    result = _FakeResult(provider="fred", series="VIX", fetch_ok=True)
    macro_benchmark.record_result("run-8", result)
    with pytest.raises(Exception):
        macro_benchmark.record_result("run-8", result)


def test_run_progress_counts_ok_and_failed():
    macro_benchmark.upsert_run("run-9", "smoke", ["VIX"], None, None, 1.0)
    macro_benchmark.record_result("run-9", _FakeResult(provider="cboe", series="VIX", fetch_ok=True))
    macro_benchmark.record_result("run-9", _FakeResult(
        provider="fred", series="VIX", fetch_ok=False, composite_score=None,
    ))
    progress = macro_benchmark.run_progress("run-9")
    assert progress == {"total_results": 2, "fetch_ok": 1, "fetch_failed": 1}


def test_run_progress_empty_run():
    macro_benchmark.upsert_run("run-10", "smoke", ["VIX"], None, None, 1.0)
    assert macro_benchmark.run_progress("run-10") == {"total_results": 0, "fetch_ok": 0, "fetch_failed": 0}


def test_record_result_stores_detail_json():
    macro_benchmark.upsert_run("run-11", "smoke", ["VIX"], None, None, 1.0)
    result = _FakeResult(
        provider="fred", series="VIX", fetch_ok=True,
        completeness_detail={"observations": 30, "expected_approx": 26},
        cross_provider_agreement_detail={"providers_compared": ["cboe", "fred"], "max_diff_pct": 0.5},
    )
    macro_benchmark.record_result("run-11", result)
    rows = macro_benchmark.run_results("run-11")
    import json
    detail = json.loads(rows[0]["detail_json"])
    assert detail["completeness_detail"]["observations"] == 30
    assert detail["cross_provider_agreement_detail"]["max_diff_pct"] == 0.5
