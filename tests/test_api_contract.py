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


# ---------------------------------------------------------------------------
# Decision Explorer filters (module 7) — /decisions query params
# ---------------------------------------------------------------------------

def _log_decision(monkeypatch, tmp_path, **report_overrides):
    from storage.decision_log import log_decision

    path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("storage.decision_log.DEFAULT_LOG_PATH", path)
    report = {
        "symbol": "EURUSD",
        "final_verdict": "NO_TRADE",
        "confluence": {"score": 40.0, "fail_reasons": ["Only 1 engine(s) agree"]},
        "engine_outputs": [{"engine": "smc", "bias": "BEARISH", "score": 40}],
        "risk": {"passed": False, "reasons": ["RR below minimum"]},
    }
    report.update(report_overrides)
    log_decision(report, path=path)
    return path


def test_decisions_filters_by_symbol(client, tmp_path, monkeypatch):
    path = _log_decision(monkeypatch, tmp_path, symbol="EURUSD")
    from storage.decision_log import log_decision
    log_decision({"symbol": "XAUUSD", "final_verdict": "NO_TRADE"}, path=path)

    r = client.get("/decisions", params={"symbol": "xauusd"}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] == 1
    assert all(d["symbol"] == "XAUUSD" for d in body["decisions"])


def test_decisions_filters_by_min_score(client, tmp_path, monkeypatch):
    _log_decision(monkeypatch, tmp_path, confluence={"score": 40.0})
    r = client.get("/decisions", params={"min_score": 60}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == 0


def test_decisions_filters_by_risk_rejected(client, tmp_path, monkeypatch):
    path = _log_decision(monkeypatch, tmp_path, risk={"passed": False, "reasons": ["RR below minimum"]})
    from storage.decision_log import log_decision
    log_decision(
        {"symbol": "EURUSD", "final_verdict": "EXECUTE", "risk": {"passed": True, "reasons": []}},
        path=path,
    )

    r = client.get("/decisions", params={"risk_rejected": True}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched"] == 1
    assert body["decisions"][0]["report"]["risk"]["passed"] is False


def test_decisions_filters_by_reason_substring(client, tmp_path, monkeypatch):
    _log_decision(monkeypatch, tmp_path, confluence={"score": 40.0, "fail_reasons": ["News blackout window"]})
    r = client.get("/decisions", params={"reason": "blackout"}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == 1


def test_decisions_requires_auth(client):
    assert client.get("/decisions").status_code == 401


# ---------------------------------------------------------------------------
# Live Logs (module 13) — whitelisted sources only, no arbitrary unit/path.
# ---------------------------------------------------------------------------

def test_log_sources_requires_auth(client):
    assert client.get("/logs/sources").status_code == 401


def test_log_sources_lists_whitelist(client):
    r = client.get("/logs/sources", headers=HDR)
    assert r.status_code == 200, r.text
    ids = {s["id"] for s in r.json()["sources"]}
    assert ids == {"system", "api", "scheduler", "watchdog", "backup", "d1_backup"}


def test_logs_requires_auth(client):
    assert client.get("/logs", params={"source": "system"}).status_code == 401


def test_logs_rejects_unknown_source(client):
    r = client.get("/logs", params={"source": "not-a-real-unit"}, headers=HDR)
    assert r.status_code == 400


def test_logs_rejects_arbitrary_source_injection_attempt(client):
    # Whitelist enforcement: a value crafted to look like a shell/journalctl
    # injection must be rejected outright, never passed through.
    r = client.get("/logs", params={"source": "api; rm -rf /"}, headers=HDR)
    assert r.status_code == 400


def test_logs_system_source_reads_file(client, tmp_path, monkeypatch):
    (tmp_path / "storage").mkdir()
    log_file = tmp_path / "storage" / "system.log"
    log_file.write_text("line one\nERROR something broke\nline three\n")
    monkeypatch.chdir(tmp_path)
    r = client.get("/logs", params={"source": "system"}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines_returned"] == 3
    assert "ERROR something broke" in body["entries"]


def test_logs_system_source_missing_file_reports_error_not_500(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = client.get("/logs", params={"source": "system"}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entries"] == []
    assert body["error"]


def test_logs_search_filters_entries(client, tmp_path, monkeypatch):
    (tmp_path / "storage").mkdir()
    log_file = tmp_path / "storage" / "system.log"
    log_file.write_text("all is well\nERROR disk full\nall is well again\n")
    monkeypatch.chdir(tmp_path)
    r = client.get("/logs", params={"source": "system", "search": "error"}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines_returned"] == 1
    assert "ERROR disk full" in body["entries"][0]


def test_logs_journal_source_never_shells_out_unsanitized(client, monkeypatch):
    # journalctl may be genuinely absent/inert in CI — assert the call
    # shape instead of real journal output: argv must be a fixed list
    # with the exact whitelisted unit name, never a user-controlled string
    # and never shell=True.
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        import subprocess as _sp
        return _sp.CompletedProcess(argv, 0, stdout="log line\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    r = client.get("/logs", params={"source": "scheduler", "lines": 50}, headers=HDR)
    assert r.status_code == 200, r.text
    assert captured["argv"] == ["journalctl", "-u", "iatis-scheduler", "-n", "50", "--no-pager", "--output=cat"]
    assert captured["kwargs"].get("shell", False) is False
    assert r.json()["entries"] == ["log line"]


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
