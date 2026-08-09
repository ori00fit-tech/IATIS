"""
tests/test_engine_benchmark_routes.py
------------------------------------------
Engine Benchmark — contract tests for execution/routes/engine_benchmark.py,
matching tests/test_provider_benchmark_routes.py's established
fixtures/conventions (client/HDR, the _FakeProc pattern that avoids
spawning a real subprocess for argv-shape assertions).
"""
from __future__ import annotations

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

_VALID_BODY = {"profile": "smoke", "symbols": ["EURUSD"], "engines": ["nnfx"]}


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
        body = client.get(f"/research/engine-benchmark/{run_id}", headers=HDR).json()
        job_status = body.get("job_status")
        if job_status not in ("queued", "running"):
            return body
        time.sleep(0.05)
    return body


# ── Auth ────────────────────────────────────────────────────────────

def test_create_requires_auth(client):
    assert client.post("/research/engine-benchmark", json=_VALID_BODY).status_code == 401


def test_list_requires_auth(client):
    assert client.get("/research/engine-benchmark").status_code == 401


def test_status_requires_auth(client):
    assert client.get("/research/engine-benchmark/does-not-exist").status_code == 401


def test_results_requires_auth(client):
    assert client.get("/research/engine-benchmark/does-not-exist/results").status_code == 401


def test_cancel_requires_auth(client):
    assert client.post("/research/engine-benchmark/does-not-exist/cancel").status_code == 401


# ── Validation ──────────────────────────────────────────────────────

def test_create_rejects_unknown_profile(client):
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "profile": "not_a_profile"}, headers=HDR)
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_create_rejects_unknown_symbol(client):
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "symbols": ["ZZZFAKE"]}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown symbol" in r.json()["detail"]


def test_create_rejects_empty_symbols_list(client):
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "symbols": []}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_unknown_engine(client):
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "engines": ["not_a_real_engine"]}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown engine" in r.json()["detail"]


def test_create_rejects_macro_as_an_engine(client):
    # macro has no runnable engine class in the backtest path — confirmed
    # excluded from backtesting.backtest_engine.ENGINE_KEYS.
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "engines": ["macro"]}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_empty_engines_list(client):
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "engines": []}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_malformed_start_date(client):
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "start": "not-a-date"}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_start_after_end(client):
    r = client.post("/research/engine-benchmark", json={**_VALID_BODY, "start": "2024-06-01", "end": "2024-01-01"}, headers=HDR)
    assert r.status_code == 400


def test_create_allows_null_symbols_and_engines(client, monkeypatch):
    """Omitting symbols/engines is valid — the engine resolves the
    profile's own defaults (see backtest/engine_benchmark.py's
    run_benchmark)."""
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/engine-benchmark", json={"profile": "smoke"}, headers=HDR)
    assert r.status_code == 200, r.text


# ── argv building ──────────────────────────────────────────────────

def test_create_builds_expected_argv_and_returns_run_id(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    r = client.post("/research/engine-benchmark", json=_VALID_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "run_id" in body and len(body["run_id"]) > 0

    _wait_for_terminal(client, body["run_id"])
    argv = _FakeProc.captured_argv
    assert "backtest.engine_benchmark" in argv
    assert "--run-id" in argv and body["run_id"] in argv
    assert "--profile" in argv and "smoke" in argv
    assert "--symbols" in argv and "EURUSD" in argv
    assert "--engines" in argv and "nnfx" in argv


def test_create_omits_optional_flags_when_absent(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/engine-benchmark", json={"profile": "smoke"}, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    argv = _FakeProc.captured_argv
    assert "--symbols" not in argv
    assert "--engines" not in argv
    assert "--start" not in argv
    assert "--end" not in argv


def test_create_includes_dates_when_given(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    body = {**_VALID_BODY, "start": "2023-01-01", "end": "2023-12-31"}
    r = client.post("/research/engine-benchmark", json=body, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    argv = _FakeProc.captured_argv
    assert "--start" in argv and "2023-01-01" in argv
    assert "--end" in argv and "2023-12-31" in argv


# ── list / status / results / cancel ──────────────────────────────

def test_list_shows_created_run(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/engine-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)

    listing = client.get("/research/engine-benchmark", headers=HDR)
    assert listing.status_code == 200
    ids = [row["job_id"] for row in listing.json()["runs"]]
    assert run_id in ids


def test_status_404_when_unknown(client):
    r = client.get("/research/engine-benchmark/does-not-exist-at-all", headers=HDR)
    assert r.status_code == 404


def test_status_returns_progress_and_job_status(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/engine-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    body = _wait_for_terminal(client, run_id)
    assert "progress" in body
    assert set(body["progress"]) == {"total_results", "run_ok", "run_failed"}


def test_results_empty_before_any_recorded(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/engine-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    results = client.get(f"/research/engine-benchmark/{run_id}/results", headers=HDR)
    assert results.status_code == 200
    assert results.json()["results"] == []


def test_cancel_404_when_unknown(client):
    r = client.post("/research/engine-benchmark/does-not-exist/cancel", headers=HDR)
    assert r.status_code == 404


def test_cancel_running_job(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeBlockingProc)

    r = client.post("/research/engine-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        status = client.get(f"/research/engine-benchmark/{run_id}", headers=HDR).json()
        if status.get("job_status") == "running":
            break
        time.sleep(0.02)

    cancel = client.post(f"/research/engine-benchmark/{run_id}/cancel", headers=HDR)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


# ── real end-to-end results reflected via the route ────────────────

def test_results_reflect_a_real_recorded_row(client):
    from storage import engine_benchmark as sb
    from backtest.engine_benchmark import EngineBenchmarkResult

    sb.upsert_run("results-run-1", "smoke", ["EURUSD"], ["nnfx"], None, None)
    sb.record_result(
        "results-run-1",
        EngineBenchmarkResult(
            engine="nnfx", symbol="EURUSD", run_ok=True, error=None,
            total_trades=12, win_rate=0.5, profit_factor=1.3,
            sharpe_ratio=0.4, sortino_ratio=0.6, max_drawdown=8.0,
            expectancy_r=0.1, expectancy=15.0, bars_used=500,
            data_start="2023-01-01T00:00:00", data_end="2023-06-01T00:00:00",
        ),
    )
    sb.set_run_status("results-run-1", "finished", finished=True)

    r = client.get("/research/engine-benchmark/results-run-1/results", headers=HDR)
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 1
    assert results[0]["engine"] == "nnfx"
    assert results[0]["total_trades"] == 12
