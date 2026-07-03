"""tests/test_api_server.py — API server tests, dev mode."""
from __future__ import annotations
import os, pytest
from unittest.mock import patch

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"   # override module-level variable
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
    # Force the expected key: _check_auth reads API_SERVER_KEY at request
    # time, and os.environ.setdefault above is a no-op when the host
    # (e.g. the VPS) already exports a real production key — which made
    # every authenticated test fail with 401 in that environment.
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app) as c:
        yield c


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_analyze_synthetic(client):
    r = client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 200}, headers=HDR)
    assert r.status_code == 200
    assert r.json()["final_verdict"] in ("EXECUTE", "NO_TRADE")


def test_analyze_structure(client):
    r = client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 200}, headers=HDR)
    data = r.json()
    # final_verdict and summary always present regardless of MQS gate
    assert "final_verdict" in data
    assert "summary" in data
    # regime/engines present only when MQS passes (GOOD/FAIR market)
    # market_quality always present when MQS gate runs
    assert "market_quality" in data or "final_verdict" in data


def test_symbol_invalid(client):
    for bad in ["INVALID@SYM!", "A"*20]:
        r = client.post(f"/analyze/{bad}", json={"source": "synthetic"}, headers=HDR)
        assert r.status_code in (400, 422)


def test_symbol_valid(client):
    for sym in ["EURUSD", "XAUUSD"]:
        r = client.post(f"/analyze/{sym}", json={"source": "synthetic", "bars": 100}, headers=HDR)
        assert r.status_code == 200


def test_no_telegram_on_api(client):
    with patch("execution.telegram_bot.send_signal") as mock_tg:
        client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 100}, headers=HDR)
    mock_tg.assert_not_called()


def test_auth_blocks_wrong_key(client):
    import execution.api_server as m; m._ENV = "production"
    r = client.post("/analyze/EURUSD", json={"source": "synthetic"}, headers={"X-API-Key": "bad"})
    m._ENV = "development"
    assert r.status_code == 401


def test_auth_correct_key(client):
    import execution.api_server as m; m._ENV = "production"
    r = client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 100}, headers=HDR)
    m._ENV = "development"
    assert r.status_code == 200


def test_decisions(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.decision_log.DEFAULT_LOG_PATH", tmp_path / "d.jsonl")
    from storage.decision_log import log_decision
    log_decision({"final_verdict": "NO_TRADE"})
    r = client.get("/decisions", headers=HDR)
    assert r.status_code == 200
    assert "decisions" in r.json()


def test_budget(client):
    with patch("core.twelve_data_client.RateLimiter.remaining_today", return_value=750):
        r = client.get("/budget", headers=HDR)
    assert r.status_code == 200
    assert "remaining_today" in r.json()


def test_stats(client):
    r = client.get("/stats", headers=HDR)
    assert r.status_code == 200
    assert "summary" in r.json()


def test_dashboard(client):
    r = client.get("/dashboard", headers=HDR)
    assert r.status_code == 200
    assert "IATIS" in r.text


def test_data_health(client):
    r = client.get("/data-health", headers=HDR)
    assert r.status_code == 200
    data = r.json()
    assert "symbols" in data and "summary" in data
    for entry in data["symbols"]:
        assert entry["overall_status"] in ("OK", "STALE", "GAPS", "MISSING")
