"""
tests/test_macro_benchmark_routes.py
----------------------------------------
Provider Benchmark & Data Quality Lab Phase 3 — contract tests for
execution/routes/macro_benchmark.py, matching tests/
test_news_benchmark_routes.py's established fixtures/conventions.
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

_VALID_BODY = {"profile": "smoke", "series": ["VIX"]}


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
        body = client.get(f"/research/macro-benchmark/{run_id}", headers=HDR).json()
        job_status = body.get("job_status")
        if job_status not in ("queued", "running"):
            return body
        time.sleep(0.05)
    return body


# ── Auth ────────────────────────────────────────────────────────────

def test_create_requires_auth(client):
    assert client.post("/research/macro-benchmark", json=_VALID_BODY).status_code == 401


def test_list_requires_auth(client):
    assert client.get("/research/macro-benchmark").status_code == 401


def test_status_requires_auth(client):
    assert client.get("/research/macro-benchmark/does-not-exist").status_code == 401


def test_results_requires_auth(client):
    assert client.get("/research/macro-benchmark/does-not-exist/results").status_code == 401


def test_cancel_requires_auth(client):
    assert client.post("/research/macro-benchmark/does-not-exist/cancel").status_code == 401


# ── Validation ──────────────────────────────────────────────────────

def test_create_rejects_unknown_profile(client):
    r = client.post("/research/macro-benchmark", json={**_VALID_BODY, "profile": "not_a_profile"}, headers=HDR)
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_create_rejects_empty_series_list(client):
    r = client.post("/research/macro-benchmark", json={**_VALID_BODY, "series": []}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_unknown_series(client):
    r = client.post("/research/macro-benchmark", json={**_VALID_BODY, "series": ["NOT_A_SERIES"]}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown series" in r.json()["detail"]


def test_create_rejects_empty_providers_list(client):
    r = client.post("/research/macro-benchmark", json={**_VALID_BODY, "providers": []}, headers=HDR)
    assert r.status_code == 400


def test_create_rejects_unknown_provider(client):
    r = client.post("/research/macro-benchmark", json={**_VALID_BODY, "providers": ["not_a_provider"]}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown provider" in r.json()["detail"]


def test_create_rejects_months_out_of_bounds(client):
    r = client.post("/research/macro-benchmark", json={**_VALID_BODY, "months": 0}, headers=HDR)
    assert r.status_code == 400
    r2 = client.post("/research/macro-benchmark", json={**_VALID_BODY, "months": 999}, headers=HDR)
    assert r2.status_code == 400


def test_create_rejects_tolerance_pct_out_of_bounds(client):
    r = client.post("/research/macro-benchmark", json={**_VALID_BODY, "tolerance_pct": 0.0}, headers=HDR)
    assert r.status_code == 400
    r2 = client.post("/research/macro-benchmark", json={**_VALID_BODY, "tolerance_pct": 999.0}, headers=HDR)
    assert r2.status_code == 400


def test_create_allows_null_series_and_providers(client, monkeypatch):
    """Omitting series/providers is valid — the engine resolves the
    profile's own default series list and each series' own native
    provider set (see backtest/macro_benchmark.py's run_benchmark)."""
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/macro-benchmark", json={"profile": "smoke"}, headers=HDR)
    assert r.status_code == 200, r.text


# ── argv building ──────────────────────────────────────────────────

def test_create_builds_expected_argv_and_returns_run_id(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    r = client.post("/research/macro-benchmark", json=_VALID_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "run_id" in body and len(body["run_id"]) > 0

    _wait_for_terminal(client, body["run_id"])
    argv = _FakeProc.captured_argv
    assert "backtest.macro_benchmark" in argv
    assert "--run-id" in argv and body["run_id"] in argv
    assert "--profile" in argv and "smoke" in argv
    assert "--series" in argv and "VIX" in argv
    assert "--tolerance-pct" in argv  # always present, default 1.0


def test_create_omits_optional_flags_when_absent(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/macro-benchmark", json={"profile": "smoke"}, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    argv = _FakeProc.captured_argv
    assert "--series" not in argv
    assert "--providers" not in argv
    assert "--months" not in argv


def test_create_includes_providers_and_months_when_given(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    body = {**_VALID_BODY, "providers": ["fred"], "months": 6}
    r = client.post("/research/macro-benchmark", json=body, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    argv = _FakeProc.captured_argv
    assert "--providers" in argv
    idx = argv.index("--providers")
    assert argv[idx + 1] == "fred"
    assert "--months" in argv and "6" in argv


# ── list / status / results / cancel ──────────────────────────────

def test_list_shows_created_run(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/macro-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)

    listing = client.get("/research/macro-benchmark", headers=HDR)
    assert listing.status_code == 200
    ids = [row["job_id"] for row in listing.json()["runs"]]
    assert run_id in ids


def test_status_404_when_unknown(client):
    r = client.get("/research/macro-benchmark/does-not-exist-at-all", headers=HDR)
    assert r.status_code == 404


def test_status_returns_progress_and_job_status(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/macro-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    body = _wait_for_terminal(client, run_id)
    assert "progress" in body
    assert set(body["progress"]) == {"total_results", "fetch_ok", "fetch_failed"}


def test_results_empty_before_any_recorded(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/macro-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]
    _wait_for_terminal(client, run_id)
    results = client.get(f"/research/macro-benchmark/{run_id}/results", headers=HDR)
    assert results.status_code == 200
    assert results.json()["results"] == []


def test_cancel_404_when_unknown(client):
    r = client.post("/research/macro-benchmark/does-not-exist/cancel", headers=HDR)
    assert r.status_code == 404


def test_cancel_running_job(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeBlockingProc)

    r = client.post("/research/macro-benchmark", json=_VALID_BODY, headers=HDR)
    run_id = r.json()["run_id"]

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        status = client.get(f"/research/macro-benchmark/{run_id}", headers=HDR).json()
        if status.get("job_status") == "running":
            break
        time.sleep(0.02)

    cancel = client.post(f"/research/macro-benchmark/{run_id}/cancel", headers=HDR)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
