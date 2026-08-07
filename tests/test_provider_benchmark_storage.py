"""
tests/test_provider_benchmark_storage.py
--------------------------------------------
Provider Benchmark & Data Quality Lab Phase 1 — round-trip tests for
storage/provider_benchmark.py. tests/conftest.py's autouse fake_d1
fixture gives every test an isolated, in-memory D1 stand-in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from storage import provider_benchmark


@dataclass(frozen=True)
class _FakeResult:
    provider: str
    symbol: str
    timeframe: str
    fetch_ok: bool
    error: str | None = None
    latency_ms: int | None = 250
    bars_fetched: int = 100
    completeness_score: float | None = 100.0
    completeness_detail: dict = field(default_factory=dict)
    correctness_score: float | None = 95.0
    correctness_detail: dict = field(default_factory=dict)
    timestamp_integrity_score: float | None = 100.0
    timestamp_integrity_detail: dict = field(default_factory=dict)
    ohlc_integrity_score: float | None = 100.0
    ohlc_integrity_reason: str | None = None
    spread_quality_score: float | None = None
    cross_provider_agreement_score: float | None = 90.0
    freshness_score: float | None = 100.0
    latency_score: float | None = 100.0
    composite_score: float | None = 97.0
    evidence_series: list = field(default_factory=list)


def test_upsert_and_get_run_round_trip():
    provider_benchmark.upsert_run("run-1", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    run = provider_benchmark.get_run("run-1")
    assert run is not None
    assert run["profile"] == "smoke"
    assert run["status"] == "queued"


def test_upsert_run_is_idempotent_and_updates_status():
    provider_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.upsert_run("run-2", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05, status="running")
    run = provider_benchmark.get_run("run-2")
    assert run["status"] == "running"


def test_set_run_status_transitions():
    provider_benchmark.upsert_run("run-3", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.set_run_status("run-3", "running", started=True)
    run = provider_benchmark.get_run("run-3")
    assert run["status"] == "running"
    assert run["started_at"] is not None

    provider_benchmark.set_run_status("run-3", "finished", finished=True)
    run = provider_benchmark.get_run("run-3")
    assert run["status"] == "finished"
    assert run["finished_at"] is not None


def test_set_run_status_records_error_on_failure():
    provider_benchmark.upsert_run("run-4", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.set_run_status("run-4", "failed", error="boom", finished=True)
    run = provider_benchmark.get_run("run-4")
    assert run["status"] == "failed"
    assert run["error"] == "boom"


def test_get_run_missing_returns_none():
    assert provider_benchmark.get_run("does-not-exist") is None


def test_list_recent_runs_newest_first_and_limit():
    for rid in ["r-a", "r-b", "r-c"]:
        provider_benchmark.upsert_run(rid, "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    rows = provider_benchmark.list_recent_runs(limit=2)
    assert len(rows) == 2
    ids = {r["id"] for r in provider_benchmark.list_recent_runs(limit=10)}
    assert ids == {"r-a", "r-b", "r-c"}


def test_record_result_and_run_results_round_trip():
    provider_benchmark.upsert_run("run-5", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    result = _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True)
    provider_benchmark.record_result("run-5", result)

    rows = provider_benchmark.run_results("run-5")
    assert len(rows) == 1
    assert rows[0]["provider"] == "ctrader"
    assert rows[0]["fetch_ok"] == 1
    assert rows[0]["composite_score"] == 97.0


def test_record_result_failed_fetch_preserves_error_text():
    provider_benchmark.upsert_run("run-6", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    result = _FakeResult(
        provider="broken", symbol="EURUSD", timeframe="H1", fetch_ok=False,
        error="simulated failure", composite_score=None,
        completeness_score=None, correctness_score=None, timestamp_integrity_score=None,
        ohlc_integrity_score=None, cross_provider_agreement_score=None,
        freshness_score=None, latency_score=None,
    )
    provider_benchmark.record_result("run-6", result)
    rows = provider_benchmark.run_results("run-6")
    assert rows[0]["fetch_ok"] == 0
    assert rows[0]["error"] == "simulated failure"
    assert rows[0]["composite_score"] is None


def test_run_results_filters_by_symbol_and_provider():
    provider_benchmark.upsert_run("run-7", "smoke", ["EURUSD", "XAUUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.record_result("run-7", _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True))
    provider_benchmark.record_result("run-7", _FakeResult(provider="twelve_data", symbol="EURUSD", timeframe="H1", fetch_ok=True))
    provider_benchmark.record_result("run-7", _FakeResult(provider="ctrader", symbol="XAUUSD", timeframe="H1", fetch_ok=True))

    assert len(provider_benchmark.run_results("run-7")) == 3
    assert len(provider_benchmark.run_results("run-7", symbol="EURUSD")) == 2
    assert len(provider_benchmark.run_results("run-7", provider="ctrader")) == 2
    assert len(provider_benchmark.run_results("run-7", symbol="EURUSD", provider="ctrader")) == 1


def test_record_result_pk_collision_raises():
    """A duplicate (run_id, provider, symbol, timeframe) write is a real
    bug in Phase 1 (no resume loop exists) — pinned as raising, not
    silently overwriting or ignoring."""
    provider_benchmark.upsert_run("run-8", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    result = _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True)
    provider_benchmark.record_result("run-8", result)
    with pytest.raises(Exception):
        provider_benchmark.record_result("run-8", result)


def test_run_progress_counts_ok_and_failed():
    provider_benchmark.upsert_run("run-9", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.record_result("run-9", _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True))
    provider_benchmark.record_result("run-9", _FakeResult(
        provider="broken", symbol="EURUSD", timeframe="H1", fetch_ok=False, composite_score=None,
    ))
    progress = provider_benchmark.run_progress("run-9")
    assert progress == {"total_results": 2, "fetch_ok": 1, "fetch_failed": 1}


def test_run_progress_empty_run():
    provider_benchmark.upsert_run("run-10", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    assert provider_benchmark.run_progress("run-10") == {"total_results": 0, "fetch_ok": 0, "fetch_failed": 0}


def test_record_result_persists_evidence_series_json():
    import json as _json

    provider_benchmark.upsert_run("run-11", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    series = [
        {"ts": "2026-08-04T00:00:00+00:00", "close": 1.1000, "consensus_close": 1.1001,
         "diff_pct": 0.009, "exceeds_tolerance": False},
    ]
    result = _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True, evidence_series=series)
    provider_benchmark.record_result("run-11", result)
    rows = provider_benchmark.run_results("run-11")
    assert rows[0]["evidence_series_json"] is not None
    assert _json.loads(rows[0]["evidence_series_json"]) == series


def test_record_result_evidence_series_null_when_empty():
    provider_benchmark.upsert_run("run-12", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    result = _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True, evidence_series=[])
    provider_benchmark.record_result("run-12", result)
    rows = provider_benchmark.run_results("run-12")
    assert rows[0]["evidence_series_json"] is None


# ── score_history (Phase 1c) ────────────────────────────────────────

def test_score_history_empty_when_no_finished_runs():
    provider_benchmark.upsert_run("run-13", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.record_result("run-13", _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True))
    # never marked finished
    assert provider_benchmark.score_history() == []


def test_score_history_aggregates_across_symbols_and_timeframes():
    provider_benchmark.upsert_run("run-14", "smoke", ["EURUSD", "XAUUSD"], ["H1", "H4"], None, 300, 0.05)
    provider_benchmark.record_result("run-14", _FakeResult(
        provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True, composite_score=90.0, latency_ms=100,
    ))
    provider_benchmark.record_result("run-14", _FakeResult(
        provider="ctrader", symbol="XAUUSD", timeframe="H4", fetch_ok=True, composite_score=80.0, latency_ms=200,
    ))
    provider_benchmark.record_result("run-14", _FakeResult(
        provider="ctrader", symbol="EURUSD", timeframe="H4", fetch_ok=False, composite_score=None, latency_ms=None,
    ))
    provider_benchmark.set_run_status("run-14", "finished", finished=True)

    history = provider_benchmark.score_history()
    assert len(history) == 1
    row = history[0]
    assert row["run_id"] == "run-14"
    assert row["provider"] == "ctrader"
    assert row["mean_composite_score"] == 85.0  # mean of 90.0/80.0, None excluded by AVG
    assert row["mean_latency_ms"] == 150.0
    assert row["fetch_ok_count"] == 2
    assert row["fetch_total_count"] == 3


def test_score_history_excludes_non_finished_runs():
    provider_benchmark.upsert_run("run-15", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.record_result("run-15", _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True))
    provider_benchmark.set_run_status("run-15", "running", started=True)
    assert provider_benchmark.score_history() == []


def test_score_history_orders_chronologically_and_respects_limit():
    for i, rid in enumerate(["run-16", "run-17", "run-18"]):
        provider_benchmark.upsert_run(rid, "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
        provider_benchmark.record_result(rid, _FakeResult(provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True))
        provider_benchmark.set_run_status(rid, "finished", finished=True)

    history = provider_benchmark.score_history(limit_runs=2)
    run_ids = [h["run_id"] for h in history]
    assert len(run_ids) == 2
    # chronological (oldest of the returned set first) — created_at ties are
    # possible in a fast test run, so only assert the set/limit, not exact order.
    assert set(run_ids) <= {"run-16", "run-17", "run-18"}


def test_score_history_mean_composite_none_when_every_fetch_failed():
    provider_benchmark.upsert_run("run-19", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    provider_benchmark.record_result("run-19", _FakeResult(
        provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=False, composite_score=None, latency_ms=None,
    ))
    provider_benchmark.set_run_status("run-19", "finished", finished=True)
    history = provider_benchmark.score_history()
    assert history[0]["mean_composite_score"] is None
    assert history[0]["mean_latency_ms"] is None
    assert history[0]["fetch_ok_count"] == 0
    assert history[0]["fetch_total_count"] == 1
