"""
tests/test_provider_benchmark_routes.py
--------------------------------------------
Provider Benchmark & Data Quality Lab Phase 1 — contract tests for
execution/routes/provider_benchmark.py, matching tests/test_missions.py's
established fixtures/conventions (client/HDR, the _FakeProc pattern that
avoids spawning a real subprocess for argv-shape assertions).
"""
from __future__ import annotations

import json
import os
import time

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

_VALID_BODY = {"profile": "smoke", "symbols": ["EURUSD"], "timeframes": ["H1"]}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


class _FakeProc:
    def __init__(self, argv, **kwargs):
        _FakeProc.captured_argv = argv
        self.stdout = iter([])
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _FakeBlockingProc:
    def __init__(self, argv, **kwargs):
        _FakeBlockingProc.captured_argv = argv
        self.returncode = None
        self.killed = False

    @property
    def stdout(self):
        import queue

        def _gen():
            q = queue.Queue()
            while not self.killed:
                try:
                    yield q.get(timeout=0.05)
                except queue.Empty:
                    continue
        return _gen()

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9


def _wait_for_terminal(client, run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/research/provider-benchmark/{run_id}", headers=HDR).json()
        job_status = body.get("job_status")
        if job_status not in ("queued", "running"):
            return body
        time.sleep(0.05)
    return body


# ── Auth ────────────────────────────────────────────────────────────

def test_create_requires_auth(client):
    assert client.post("/research/provider-benchmark", json=_VALID_BODY).status_code == 401


def test_list_requires_auth(client):
    assert client.get("/research/provider-benchmark").status_code == 401


def test_status_requires_auth(client):
    assert client.get("/research/provider-benchmark/does-not-exist").status_code == 401


def test_results_requires_auth(client):
    assert client.get("/research/provider-benchmark/does-not-exist/results").status_code == 401


def test_cancel_requires_auth(client):
    assert client.post("/research/provider-benchmark/does-not-exist/cancel").status_code == 401


# ── Validation ──────────────────────────────────────────────────────

def test_create_rejects_unknown_profile(client):
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "profile": "not_a_profile"}, headers=HDR)
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_create_rejects_unknown_symbol(client):
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "symbols": ["ZZZFAKE"]}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown symbol" in r.json()["detail"]


def test_create_rejects_out_of_scope_asset_class(client, monkeypatch):
    # equity/etf/energy moved in-scope 2026-08-10 — no real symbol in
    # config/symbols.yaml is out of scope anymore, so this shrinks the
    # scope set just for this test to keep exercising the actual
    # rejection code path (still real, defensive code: it protects
    # against a future asset_class added to symbols.yaml without a
    # matching _IN_SCOPE_ASSET_CLASSES update). The import inside
    # provider_benchmark_create is `from backtest.price_benchmark import
    # _IN_SCOPE_ASSET_CLASSES` executed at call time, so patching the
    # module attribute here is picked up by the next request.
    import backtest.price_benchmark as pb
    monkeypatch.setattr(pb, "_IN_SCOPE_ASSET_CLASSES", {"fx_major", "fx_minor", "metals", "crypto", "indices"})
    # AAPL is asset_class=equity in config/symbols.yaml — outside the
    # shrunk scope above.
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "symbols": ["AAPL"]}, headers=HDR)
    assert r.status_code == 400
    assert "outside Phase 1" in r.json()["detail"]


def test_create_rejects_empty_symbols_list(client):
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "symbols": []}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_unknown_timeframe(client):
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "timeframes": ["W1"]}, headers=HDR)
    assert r.status_code == 400
    assert "timeframe" in r.json()["detail"].lower()


def test_create_rejects_empty_providers_list(client):
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "providers": []}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_outputsize_out_of_bounds(client):
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "outputsize": 1}, headers=HDR)
    assert r.status_code == 400
    r2 = client.post("/research/provider-benchmark", json={**_VALID_BODY, "outputsize": 999_999}, headers=HDR)
    assert r2.status_code == 400


def test_create_rejects_bad_tolerance_pct(client):
    r = client.post("/research/provider-benchmark", json={**_VALID_BODY, "tolerance_pct": 0.0}, headers=HDR)
    assert r.status_code == 400
    r2 = client.post("/research/provider-benchmark", json={**_VALID_BODY, "tolerance_pct": 100.0}, headers=HDR)
    assert r2.status_code == 400


def test_create_allows_null_symbols_timeframes_providers(client, monkeypatch):
    """Omitting symbols/timeframes/providers is valid — the engine
    resolves the profile's own defaults (see backtest/price_benchmark.py's
    run_benchmark)."""
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/provider-benchmark", json={"profile": "smoke"}, headers=HDR)
    assert r.status_code == 200, r.text


# ── argv building ──────────────────────────────────────────────────

def test_create_builds_expected_argv_and_returns_run_id(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    r = client.post("/research/provider-benchmark", json=_VALID_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "run_id" in body and len(body["run_id"]) > 0

    _wait_for_terminal(client, body["run_id"])
    argv = _FakeProc.captured_argv
    assert "backtest.price_benchmark" in argv
    assert "--run-id" in argv and body["run_id"] in argv
    assert "--profile" in argv and "smoke" in argv
    assert "--symbols" in argv and "EURUSD" in argv
    assert "--timeframes" in argv and "H1" in argv


def test_create_omits_optional_flags_when_absent(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/provider-benchmark", json={"profile": "smoke"}, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    argv = _FakeProc.captured_argv
    assert "--symbols" not in argv
    assert "--timeframes" not in argv
    assert "--providers" not in argv
    assert "--outputsize" not in argv


def test_create_includes_providers_and_outputsize_when_given(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    body = {**_VALID_BODY, "providers": ["ctrader", "twelve_data"], "outputsize": 400}
    r = client.post("/research/provider-benchmark", json=body, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    argv = _FakeProc.captured_argv
    assert "--providers" in argv
    idx = argv.index("--providers")
    assert argv[idx + 1] == "ctrader"
    assert "--outputsize" in argv and "400" in argv


# ── list / status / results / cancel ──────────────────────────────

def test_list_shows_created_run(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/provider-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)

    listing = client.get("/research/provider-benchmark", headers=HDR)
    assert listing.status_code == 200
    ids = [row["job_id"] for row in listing.json()["runs"]]
    assert run_id in ids


def test_status_404_when_unknown(client):
    r = client.get("/research/provider-benchmark/does-not-exist-at-all", headers=HDR)
    assert r.status_code == 404


def test_status_returns_progress_and_job_status(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/provider-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    body = _wait_for_terminal(client, run_id)
    assert "progress" in body
    assert set(body["progress"]) == {"total_results", "fetch_ok", "fetch_failed"}


def test_results_empty_before_any_recorded(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/provider-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    results = client.get(f"/research/provider-benchmark/{run_id}/results", headers=HDR)
    assert results.status_code == 200
    assert results.json()["results"] == []


def test_cancel_404_when_unknown(client):
    r = client.post("/research/provider-benchmark/does-not-exist/cancel", headers=HDR)
    assert r.status_code == 404


# ── history (Phase 1c) ──────────────────────────────────────────────

def test_history_requires_auth(client):
    assert client.get("/research/provider-benchmark/history").status_code == 401


def test_history_rejects_bad_limit(client):
    r = client.get("/research/provider-benchmark/history?limit=0", headers=HDR)
    assert r.status_code == 400
    r2 = client.get("/research/provider-benchmark/history?limit=999", headers=HDR)
    assert r2.status_code == 400


def test_history_empty_when_no_finished_runs(client):
    r = client.get("/research/provider-benchmark/history", headers=HDR)
    assert r.status_code == 200
    assert r.json() == {"history": []}


def test_history_reflects_a_real_finished_run(client, monkeypatch):
    from storage import provider_benchmark as sb
    from dataclasses import dataclass, field

    @dataclass(frozen=True)
    class _R:
        provider: str = "ctrader"
        symbol: str = "EURUSD"
        timeframe: str = "H1"
        fetch_ok: bool = True
        error: str | None = None
        latency_ms: int | None = 120
        bars_fetched: int = 300
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
        composite_score: float | None = 96.0
        evidence_series: list = field(default_factory=list)

    sb.upsert_run("hist-run-1", "smoke", ["EURUSD"], ["H1"], None, 300, 0.05)
    sb.record_result("hist-run-1", _R())
    sb.set_run_status("hist-run-1", "finished", finished=True)

    r = client.get("/research/provider-benchmark/history", headers=HDR)
    assert r.status_code == 200
    history = r.json()["history"]
    assert len(history) == 1
    assert history[0]["run_id"] == "hist-run-1"
    assert history[0]["provider"] == "ctrader"
    assert history[0]["mean_composite_score"] == 96.0


def test_cancel_running_job(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeBlockingProc)

    r = client.post("/research/provider-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        status = client.get(f"/research/provider-benchmark/{run_id}", headers=HDR).json()
        if status.get("job_status") == "running":
            break
        time.sleep(0.02)

    cancel = client.post(f"/research/provider-benchmark/{run_id}/cancel", headers=HDR)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
