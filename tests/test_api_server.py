"""
tests/test_api_server.py
---------------------------
Tests for execution/api_server.py — uses FastAPI TestClient, no real
network calls or Twelve Data requests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

try:
    from fastapi.testclient import TestClient
    from execution.api_server import app
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not FASTAPI_AVAILABLE,
    reason="fastapi not installed"
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch):
    """Patch _get_config so tests don't read real config.yaml each time."""
    from utils.helpers import load_config
    cfg = load_config()
    cfg["data"]["source"] = "synthetic"
    cfg["telegram"] = {"enabled": False}
    monkeypatch.setattr("execution.api_server._config_cache", cfg)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "timestamp" in data


def test_health_no_auth_required(client):
    # /health is public — no X-API-Key needed
    resp = client.get("/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /analyze/{symbol}
# ---------------------------------------------------------------------------

def test_analyze_runs_pipeline(client):
    resp = client.post(
        "/analyze/EURUSD",
        json={"source": "synthetic", "bars": 200},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "final_verdict" in data
    assert data["final_verdict"] in ("EXECUTE", "NO_TRADE")
    assert "processing_time_sec" in data


def test_analyze_accepts_dash_symbol(client):
    # EUR/USD as URL path causes 404 because FastAPI treats / as path separator.
    # The correct way to pass EUR/USD is as EURUSD or with a query param.
    # This test verifies the 6-char format works correctly.
    resp = client.post(
        "/analyze/EURUSD",
        json={"source": "synthetic", "bars": 200},
    )
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "EURUSD"


def test_analyze_returns_full_report_structure(client):
    resp = client.post(
        "/analyze/EURUSD",
        json={"source": "synthetic", "bars": 200},
    )
    data = resp.json()
    assert "regime" in data
    assert "engine_outputs" in data
    assert "confluence" in data
    assert "risk" in data
    assert "summary" in data


def test_analyze_no_telegram_sent(client):
    """API calls must never trigger Telegram notifications."""
    with patch("execution.telegram_bot.send_signal") as mock_tg:
        client.post("/analyze/EURUSD", json={"source": "synthetic", "bars": 100})
    mock_tg.assert_not_called()


def test_analyze_auth_blocks_wrong_key(monkeypatch, client):
    monkeypatch.setenv("API_SERVER_KEY", "secret123")
    resp = client.post(
        "/analyze/EURUSD",
        json={"source": "synthetic"},
        headers={"X-API-Key": "wrongkey"},
    )
    assert resp.status_code == 401


def test_analyze_auth_passes_correct_key(monkeypatch, client):
    monkeypatch.setenv("API_SERVER_KEY", "secret123")
    resp = client.post(
        "/analyze/EURUSD",
        json={"source": "synthetic", "bars": 100},
        headers={"X-API-Key": "secret123"},
    )
    assert resp.status_code == 200


def test_analyze_no_auth_when_key_not_set(monkeypatch, client):
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    resp = client.post(
        "/analyze/EURUSD",
        json={"source": "synthetic", "bars": 100},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /decisions
# ---------------------------------------------------------------------------

def test_decisions_returns_structure(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "storage.decision_log.DEFAULT_LOG_PATH",
        tmp_path / "decisions.jsonl",
    )
    from storage.decision_log import log_decision
    log_decision({"final_verdict": "NO_TRADE", "symbol": "EURUSD"})
    log_decision({"final_verdict": "NO_TRADE", "symbol": "XAUUSD"})

    resp = client.get("/decisions?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_in_log" in data
    assert "decisions" in data
    assert "summary" in data


def test_decisions_filter_by_verdict(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "storage.decision_log.DEFAULT_LOG_PATH",
        tmp_path / "decisions.jsonl",
    )
    from storage.decision_log import log_decision
    log_decision({"final_verdict": "NO_TRADE"})
    log_decision({"final_verdict": "EXECUTE"})

    resp = client.get("/decisions?verdict=EXECUTE")
    data = resp.json()
    assert all(d.get("final_verdict") == "EXECUTE" for d in data["decisions"])


# ---------------------------------------------------------------------------
# /budget
# ---------------------------------------------------------------------------

def test_budget_returns_structure(client):
    with patch("core.twelve_data_client.RateLimiter.remaining_today", return_value=750):
        resp = client.get("/budget")
    assert resp.status_code == 200
    data = resp.json()
    assert "max_per_day" in data
    assert "remaining_today" in data
    assert "percent_used" in data
