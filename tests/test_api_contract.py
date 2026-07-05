"""
tests/test_api_contract.py — contract tests for the dashboard data
endpoints (audit item H7).

execution/api_server.py sat at 36% coverage: auth and a handful of
routes were tested, but the ~15 dashboard data endpoints the Command
Center actually renders from were not. Each test here pins the two
things a frontend depends on: (1) the endpoint requires auth, and
(2) the response body has the agreed top-level shape — against the
fake in-memory D1 the whole suite runs on (tests/conftest.py).
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
    # https base_url: the session cookie is set with secure=True, so the
    # test client must speak "https" for the cookie jar to send it back.
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _seed_execute_signal(symbol="EURUSD"):
    """One EXECUTE decision + outcome-tracker signal in the fake D1."""
    from storage.outcome_tracker import log_signal

    report = {
        "symbol": symbol,
        "final_verdict": "EXECUTE",
        "entry_price": 1.0850,
        "stop_loss": 1.0920,
        "take_profit": 1.0640,
        "confluence": {"score": 72.0, "vote": {"winning_bias": "BEARISH"}},
        "regime": {"state": "TRENDING"},
        "news": {"news_risk_score": 5.0},
        "engine_outputs": [
            {"engine": "SMC", "bias": "BEARISH", "score": 52},
            {"engine": "NNFX", "bias": "BEARISH", "score": 65},
        ],
    }
    return log_signal(report)


# ---------------------------------------------------------------------------
# Every dashboard data endpoint: 401 without auth, 200 + agreed shape with.
# ---------------------------------------------------------------------------

DATA_ENDPOINTS = [
    ("/experience/summary", None),
    ("/experience/query", {"count", "experiences"}),
    ("/experience/pattern", None),
    ("/engine-stats", {"engine_stats", "neutral_rates", "current_weights", "suggested_weights"}),
    ("/outcomes", {"summary", "open_signals", "recent"}),
    ("/backtest-results", {"count", "results"}),
    ("/symbol-health", {"total", "healthy", "caution", "paused", "symbols"}),
]


@pytest.mark.parametrize("path,_", DATA_ENDPOINTS)
def test_data_endpoints_require_auth(client, path, _):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path,required_keys", DATA_ENDPOINTS)
def test_data_endpoints_contract_on_empty_db(client, path, required_keys):
    r = client.get(path, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict)
    if required_keys:
        assert required_keys.issubset(body.keys()), f"{path} missing {required_keys - set(body.keys())}"


def test_health_full_reports_system_and_issues(client):
    assert client.get("/health/full").status_code == 401
    r = client.get("/health/full", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"status", "issues", "system"}.issubset(body.keys())
    assert body["status"] in ("ok", "degraded")


def test_meta_analysis_contract(client):
    r = client.get("/meta-analysis", headers=HDR)
    assert r.status_code == 200, r.text
    assert "calibration" in r.json()


# ---------------------------------------------------------------------------
# Outcome lifecycle through the API: seeded signal appears open, closes,
# and shows up in the performance summary.
# ---------------------------------------------------------------------------

def test_outcome_lifecycle_via_api(client):
    signal_id = _seed_execute_signal()

    body = client.get("/outcomes", headers=HDR).json()
    assert any(s["signal_id"] == signal_id for s in body["open_signals"])

    r = client.post(
        f"/outcomes/{signal_id}/close",
        params={"exit_price": 1.0640, "outcome": "win"},
        headers=HDR,
    )
    assert r.status_code == 200 and r.json()["success"] is True

    body = client.get("/outcomes", headers=HDR).json()
    assert not any(s["signal_id"] == signal_id for s in body["open_signals"])
    assert body["summary"]["wins"] >= 1


def test_close_outcome_requires_auth(client):
    assert client.post("/outcomes/whatever/close", params={"exit_price": 1.0}).status_code == 401


# ---------------------------------------------------------------------------
# Login/session-cookie flow — the only browser path into the dashboard.
# ---------------------------------------------------------------------------

def test_login_rejects_wrong_key(client):
    assert client.post("/login", json={"key": "wrong"}).status_code == 401


def test_login_sets_session_cookie_that_authenticates(client):
    r = client.post("/login", json={"key": "test-key-123"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    # The cookie must be a rotated session id, never the raw key
    assert client.cookies.get("iatis_session") not in (None, "test-key-123")

    # Cookie alone (no X-API-Key header) must authenticate
    assert client.get("/stats").status_code == 200


def test_logout_invalidates_session(client):
    client.post("/login", json={"key": "test-key-123"})
    assert client.get("/stats").status_code == 200

    client.get("/logout", follow_redirects=False)
    assert client.get("/stats").status_code == 401


# ---------------------------------------------------------------------------
# Evidence manifests endpoint + decision-TF surface (Command Center round)
# ---------------------------------------------------------------------------

def test_health_exposes_decision_timeframe(client):
    from utils.helpers import load_config

    body = client.get("/health").json()
    assert body["decision_timeframe"] == load_config()["data"]["timeframes"][0]


def test_research_manifests_requires_auth(client):
    assert client.get("/research/manifests").status_code == 401


def test_research_manifests_contract(client):
    r = client.get("/research/manifests", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"count", "manifests"}.issubset(body.keys())
    assert body["count"] == len(body["manifests"])
    # The repo ships real manifests (d1/h4 backtests) — each entry must
    # carry the fields the dashboard renders.
    for m in body["manifests"]:
        assert {"file", "kind", "generated_at", "reproducible",
                "git_commit", "datasets_count"}.issubset(m.keys())
