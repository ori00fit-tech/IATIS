"""
tests/test_provider_scorecard_routes.py
---------------------------------------------
Provider Benchmark & Data Quality Lab Phase 5 — contract tests for
execution/routes/provider_scorecard.py, matching the established
`client`/`HDR` fixture conventions from tests/test_analytics_benchmark_
routes.py etc. Seeds real rows into each domain's own D1 tables (via the
`fake_d1` autouse fixture — real SQLite semantics, faked transport only)
using each domain's own real result dataclass, then exercises the two
new read-only endpoints end-to-end.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"
    from execution.api_server import app
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="fastapi not installed")

HDR = {"X-API-Key": "test-key-123"}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _seed_price(run_id: str, symbol: str, timeframe: str, provider_scores: dict[str, float | None]) -> None:
    from backtest.price_benchmark import BenchmarkResult
    from storage import provider_benchmark

    provider_benchmark.upsert_run(run_id, "smoke", [symbol], [timeframe], list(provider_scores), 300, 0.05)
    provider_benchmark.set_run_status(run_id, "finished", started=True)
    provider_benchmark.set_run_status(run_id, "finished", finished=True)
    for provider, score in provider_scores.items():
        provider_benchmark.record_result(run_id, BenchmarkResult(
            provider=provider, symbol=symbol, timeframe=timeframe,
            fetch_ok=score is not None, error=None if score is not None else "fetch failed",
            latency_ms=100, bars_fetched=300 if score is not None else 0,
            completeness_score=score, composite_score=score,
        ))


def _seed_macro(run_id: str, series: str, provider_scores: dict[str, float | None]) -> None:
    from backtest.macro_benchmark import MacroBenchmarkResult
    from storage import macro_benchmark

    macro_benchmark.upsert_run(run_id, "smoke", [series], list(provider_scores), None, 0.05)
    macro_benchmark.set_run_status(run_id, "finished", started=True)
    macro_benchmark.set_run_status(run_id, "finished", finished=True)
    for provider, score in provider_scores.items():
        macro_benchmark.record_result(run_id, MacroBenchmarkResult(
            provider=provider, series=series,
            fetch_ok=score is not None, error=None if score is not None else "fetch failed",
            latency_ms=100, observation_count=10 if score is not None else 0,
            completeness_score=score, composite_score=score,
        ))


# ── /research/provider-scorecard ─────────────────────────────────────

def test_scorecard_requires_auth(client):
    assert client.get("/research/provider-scorecard").status_code == 401


def test_scorecard_reports_unavailable_domain_when_no_finished_run(client):
    r = client.get("/research/provider-scorecard", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert set(body["domains"]) == {"price", "news", "macro", "analytics"}
    for domain_summary in body["domains"].values():
        assert domain_summary["available"] is False
        assert domain_summary["providers"] == []


def test_scorecard_reflects_a_real_finished_price_run(client):
    _seed_price("run-price-1", "EURUSD", "H1", {"ctrader": 95.0, "twelve_data": 80.0})
    r = client.get("/research/provider-scorecard", headers=HDR)
    price = r.json()["domains"]["price"]
    assert price["available"] is True
    assert price["run_id"] == "run-price-1"
    providers = {p["provider"]: p for p in price["providers"]}
    assert providers["ctrader"]["mean_composite_score"] == 95.0
    assert providers["twelve_data"]["mean_composite_score"] == 80.0
    # Other domains still honestly report no finished run.
    assert r.json()["domains"]["macro"]["available"] is False


def test_scorecard_ignores_non_finished_runs(client):
    from storage import provider_benchmark
    provider_benchmark.upsert_run("run-queued", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05, status="running")
    r = client.get("/research/provider-scorecard", headers=HDR)
    assert r.json()["domains"]["price"]["available"] is False


# ── /research/best-provider ──────────────────────────────────────────

def test_best_provider_requires_auth(client):
    assert client.get("/research/best-provider", params={"domain": "price"}).status_code == 401


def test_best_provider_rejects_unknown_domain(client):
    r = client.get("/research/best-provider", params={"domain": "bogus"}, headers=HDR)
    assert r.status_code == 400
    assert "domain" in r.json()["detail"]


def test_best_provider_price_requires_symbol(client):
    r = client.get("/research/best-provider", params={"domain": "price", "timeframe": "H1"}, headers=HDR)
    assert r.status_code == 400
    assert "symbol" in r.json()["detail"]


def test_best_provider_price_requires_timeframe(client):
    r = client.get("/research/best-provider", params={"domain": "price", "symbol": "EURUSD"}, headers=HDR)
    assert r.status_code == 400
    assert "timeframe" in r.json()["detail"]


def test_best_provider_macro_requires_series_not_symbol(client):
    r = client.get("/research/best-provider", params={"domain": "macro", "symbol": "EURUSD"}, headers=HDR)
    assert r.status_code == 400
    assert "series" in r.json()["detail"]


def test_best_provider_rejects_timeframe_for_non_price_domain(client):
    r = client.get(
        "/research/best-provider", params={"domain": "macro", "series": "VIX", "timeframe": "H1"}, headers=HDR,
    )
    assert r.status_code == 400
    assert "not applicable" in r.json()["detail"]


def test_best_provider_reports_unavailable_when_no_finished_run(client):
    r = client.get(
        "/research/best-provider", params={"domain": "price", "symbol": "EURUSD", "timeframe": "H1"}, headers=HDR,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["best"] is None
    assert body["ranking"] == []
    assert "No finished" in body["note"]


def test_best_provider_reports_unavailable_when_item_never_benchmarked(client):
    _seed_price("run-price-2", "GBPUSD", "H1", {"ctrader": 90.0})
    r = client.get(
        "/research/best-provider", params={"domain": "price", "symbol": "EURUSD", "timeframe": "H1"}, headers=HDR,
    )
    body = r.json()
    assert body["available"] is False
    assert body["run_id"] == "run-price-2"  # the run was found — just no matching item in it
    assert "not part of" in body["note"]


def test_best_provider_returns_real_ranking_for_price(client):
    _seed_price("run-price-3", "EURUSD", "H1", {"ctrader": 95.0, "twelve_data": 80.0, "dukascopy": None})
    r = client.get(
        "/research/best-provider", params={"domain": "price", "symbol": "EURUSD", "timeframe": "H1"}, headers=HDR,
    )
    body = r.json()
    assert body["available"] is True
    assert body["best"]["provider"] == "ctrader"
    assert body["best"]["composite_score"] == 95.0
    assert [p["provider"] for p in body["ranking"]] == ["ctrader", "twelve_data", "dukascopy"]
    assert body["ranking"][-1]["available"] is False


def test_best_provider_price_scopes_by_timeframe_not_just_symbol(client):
    # Same symbol, two different timeframes with different scores — the
    # lookup for one timeframe must not be polluted by the other's rows.
    from backtest.price_benchmark import BenchmarkResult
    from storage import provider_benchmark

    provider_benchmark.upsert_run("run-price-4", "smoke", ["EURUSD"], ["H1", "H4"], ["ctrader"], 300, 0.05)
    provider_benchmark.set_run_status("run-price-4", "finished", started=True)
    provider_benchmark.set_run_status("run-price-4", "finished", finished=True)
    provider_benchmark.record_result("run-price-4", BenchmarkResult(
        provider="ctrader", symbol="EURUSD", timeframe="H1", fetch_ok=True, error=None,
        latency_ms=100, bars_fetched=300, completeness_score=99.0, composite_score=99.0,
    ))
    provider_benchmark.record_result("run-price-4", BenchmarkResult(
        provider="ctrader", symbol="EURUSD", timeframe="H4", fetch_ok=True, error=None,
        latency_ms=100, bars_fetched=300, completeness_score=40.0, composite_score=40.0,
    ))
    r_h1 = client.get(
        "/research/best-provider", params={"domain": "price", "symbol": "EURUSD", "timeframe": "H1"}, headers=HDR,
    ).json()
    r_h4 = client.get(
        "/research/best-provider", params={"domain": "price", "symbol": "EURUSD", "timeframe": "H4"}, headers=HDR,
    ).json()
    assert r_h1["best"]["composite_score"] == 99.0
    assert r_h4["best"]["composite_score"] == 40.0


def test_best_provider_macro_uses_series_param(client):
    _seed_macro("run-macro-1", "VIX", {"cboe": 98.0, "fred": 92.0})
    r = client.get("/research/best-provider", params={"domain": "macro", "series": "VIX"}, headers=HDR)
    body = r.json()
    assert body["available"] is True
    assert body["best"]["provider"] == "cboe"
    assert body["item"] == {"series": "VIX"}


def test_best_provider_symbol_is_uppercased(client):
    _seed_price("run-price-5", "EURUSD", "H1", {"ctrader": 88.0})
    r = client.get(
        "/research/best-provider", params={"domain": "price", "symbol": "eurusd", "timeframe": "h1"}, headers=HDR,
    )
    assert r.json()["available"] is True


def test_best_provider_all_unavailable_when_every_provider_failed(client):
    _seed_price("run-price-6", "USDJPY", "H1", {"ctrader": None, "twelve_data": None})
    r = client.get(
        "/research/best-provider", params={"domain": "price", "symbol": "USDJPY", "timeframe": "H1"}, headers=HDR,
    )
    body = r.json()
    assert body["run_id"] == "run-price-6"
    assert body["available"] is False
    assert body["best"] is None
    assert len(body["ranking"]) == 2  # both rows still reported, never dropped
