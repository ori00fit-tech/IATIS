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

import json
import os
import sys
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


def test_health_full_reports_exposure_estimate(client):
    body = client.get("/health/full", headers=HDR).json()
    assert "exposure_estimate" in body
    est = body["exposure_estimate"]
    assert {"open_positions", "estimated_pct", "max_exposure_pct", "utilization_pct", "note"}.issubset(est.keys())
    assert est["max_exposure_pct"] == 5.0  # config.yaml risk.max_exposure = 0.05


def test_health_full_exposure_estimate_scales_with_open_signals(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.decision_log.DEFAULT_LOG_PATH", tmp_path / "decisions.jsonl")
    from storage.outcome_tracker import log_signal

    report = {
        "symbol": "EURUSD", "final_verdict": "EXECUTE",
        "entry_price": 1.0, "stop_loss": 0.99, "take_profit": 1.02,
        "confluence": {"score": 70.0, "vote": {"winning_bias": "BULLISH"}},
        "regime": {"state": "TRENDING"}, "news": {"news_risk_score": 5.0},
        "engine_outputs": [],
    }
    log_signal(report)

    body = client.get("/health/full", headers=HDR).json()
    est = body["exposure_estimate"]
    assert est["open_positions"] >= 1
    # risk_per_trade_max = 0.01 -> 1% per open position, upper-bound estimate
    assert est["estimated_pct"] == pytest.approx(est["open_positions"] * 1.0, abs=0.01)


def test_health_full_reports_swap_and_load_average(client):
    body = client.get("/health/full", headers=HDR).json()
    assert {"cpu_pct", "ram_pct", "disk_pct", "swap_pct", "load_1m", "load_5m", "load_15m", "uptime_hours"}.issubset(
        body["system"].keys()
    )


def test_health_full_reports_real_service_status(client):
    body = client.get("/health/full", headers=HDR).json()
    assert "services" in body
    assert set(body["services"].keys()) == {"api", "scheduler", "watchdog", "backup", "d1_backup"}
    for entry in body["services"].values():
        assert {"status", "kind", "healthy"}.issubset(entry.keys())
        assert entry["kind"] in ("daemon", "timer")


def test_systemd_service_status_uses_fixed_argv_never_shell(monkeypatch):
    import execution.api_server as m

    captured_argvs = []

    class _FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(argv, **kwargs):
        captured_argvs.append(argv)
        assert kwargs.get("shell", False) is False
        return _FakeResult("active\n" if argv[-1] == "iatis-api" else "inactive\n")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = m._systemd_service_status()

    assert result["api"]["status"] == "active"
    assert result["scheduler"]["status"] == "inactive"
    assert all(argv[0] == "systemctl" and argv[1] == "is-active" for argv in captured_argvs)


def test_systemd_service_status_handles_missing_systemctl(monkeypatch):
    import execution.api_server as m

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("systemctl not found")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = m._systemd_service_status()
    assert all(v["status"] == "unavailable" for v in result.values())


def test_systemd_service_status_timer_inactive_is_healthy_daemon_inactive_is_not(monkeypatch):
    # watchdog/backup/d1_backup are .timer-triggered oneshots — "inactive"
    # between scheduled runs is normal, not a fault. api/scheduler are
    # long-running daemons where "inactive" means actually down.
    import execution.api_server as m

    class _FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(argv, **kwargs):
        return _FakeResult("inactive\n")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = m._systemd_service_status()

    assert result["api"]["kind"] == "daemon" and result["api"]["healthy"] is False
    assert result["scheduler"]["kind"] == "daemon" and result["scheduler"]["healthy"] is False
    assert result["watchdog"]["kind"] == "timer" and result["watchdog"]["healthy"] is True
    assert result["backup"]["kind"] == "timer" and result["backup"]["healthy"] is True
    assert result["d1_backup"]["kind"] == "timer" and result["d1_backup"]["healthy"] is True


def test_systemd_service_status_failed_timer_is_unhealthy(monkeypatch):
    import execution.api_server as m

    class _FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(argv, **kwargs):
        return _FakeResult("failed\n")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = m._systemd_service_status()
    assert result["backup"]["healthy"] is False


def test_engine_stats_includes_attribution(client):
    r = client.get("/engine-stats", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "attribution" in body
    assert {"window_seconds", "total_closed_trades", "matched_trades", "engines"}.issubset(body["attribution"].keys())


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


# ---------------------------------------------------------------------------
# File Explorer (module 11) — read-only, path-confined, secret-denylisted.
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.py").write_text("print('hello')\n")
    (tmp_path / "README.md").write_text("# Project\nSome content with NEEDLE inside.\n")
    (tmp_path / ".env").write_text("CTRADER_ACCESS_TOKEN=shhh\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n")
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "sessions.json").write_text('{"abc": 123}')
    (tmp_path / "dashboard").mkdir()
    # A real filename from this repo — must NOT be caught by the "token"
    # denylist word (it's design tokens, not an auth token).
    (tmp_path / "dashboard" / "tokens.css").write_text(":root { --accent: #fff; }\n")
    monkeypatch.setattr("execution.api_server._REPO_ROOT", tmp_path)
    return tmp_path


def test_files_tree_requires_auth(client):
    assert client.get("/files/tree").status_code == 401


def test_files_tree_excludes_denylisted_entries(client, fake_repo):
    r = client.get("/files/tree", headers=HDR)
    assert r.status_code == 200, r.text
    names = {e["name"] for e in r.json()["entries"]}
    assert "README.md" in names and "scripts" in names
    assert ".env" not in names and ".git" not in names


def test_files_tree_excludes_sessions_file(client, fake_repo):
    r = client.get("/files/tree", params={"path": "storage"}, headers=HDR)
    assert r.status_code == 200, r.text
    names = {e["name"] for e in r.json()["entries"]}
    assert "sessions.json" not in names


def test_files_tree_rejects_traversal(client, fake_repo):
    r = client.get("/files/tree", params={"path": "../../etc"}, headers=HDR)
    assert r.status_code == 400


def test_files_tree_rejects_bare_parent_traversal(client, fake_repo):
    r = client.get("/files/tree", params={"path": ".."}, headers=HDR)
    assert r.status_code == 400


def test_files_tree_missing_path_404s(client, fake_repo):
    r = client.get("/files/tree", params={"path": "does/not/exist"}, headers=HDR)
    assert r.status_code == 404


def test_files_read_returns_content(client, fake_repo):
    r = client.get("/files/read", params={"path": "README.md"}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "NEEDLE" in body["content"]
    assert body["binary"] is False


def test_files_read_denies_env_file(client, fake_repo):
    r = client.get("/files/read", params={"path": ".env"}, headers=HDR)
    assert r.status_code == 403


def test_files_read_denies_git_internals(client, fake_repo):
    r = client.get("/files/read", params={"path": ".git/config"}, headers=HDR)
    assert r.status_code == 403


def test_files_read_denies_sessions_file(client, fake_repo):
    r = client.get("/files/read", params={"path": "storage/sessions.json"}, headers=HDR)
    assert r.status_code == 403


def test_files_read_allows_tokens_css_false_positive_check(client, fake_repo):
    r = client.get("/files/read", params={"path": "dashboard/tokens.css"}, headers=HDR)
    assert r.status_code == 200, r.text
    assert "--accent" in r.json()["content"]


def test_files_read_rejects_traversal(client, fake_repo):
    r = client.get("/files/read", params={"path": "../outside.txt"}, headers=HDR)
    assert r.status_code == 400


def test_files_download_requires_auth(client):
    assert client.get("/files/download", params={"path": "README.md"}).status_code == 401


def test_files_download_denies_secret_path(client, fake_repo):
    r = client.get("/files/download", params={"path": ".env"}, headers=HDR)
    assert r.status_code == 403


def test_files_download_serves_allowed_file(client, fake_repo):
    r = client.get("/files/download", params={"path": "README.md"}, headers=HDR)
    assert r.status_code == 200
    assert b"NEEDLE" in r.content


def test_files_search_finds_content_match_and_skips_denied(client, fake_repo):
    r = client.get("/files/search", params={"query": "NEEDLE"}, headers=HDR)
    assert r.status_code == 200, r.text
    paths = {res["path"] for res in r.json()["results"]}
    assert "README.md" in paths

    # The secret lives only inside .env, which must never be scanned.
    r2 = client.get("/files/search", params={"query": "shhh"}, headers=HDR)
    assert r2.json()["results"] == []


def test_files_search_finds_filename_match(client, fake_repo):
    r = client.get("/files/search", params={"query": "run.py"}, headers=HDR)
    assert r.status_code == 200, r.text
    assert any(res["path"] == "scripts/run.py" for res in r.json()["results"])


def test_files_diff_uses_fixed_argv_never_shell(client, fake_repo, monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        import subprocess as _sp
        return _sp.CompletedProcess(argv, 0, stdout="diff --git a/README.md b/README.md\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    r = client.get("/files/diff", params={"path": "README.md"}, headers=HDR)
    assert r.status_code == 200, r.text
    assert captured["argv"] == ["git", "diff", "--no-color", "HEAD", "--", "README.md"]
    assert captured["kwargs"].get("shell", False) is False
    assert r.json()["has_changes"] is True


def test_files_diff_denies_secret_path(client, fake_repo):
    r = client.get("/files/diff", params={"path": ".env"}, headers=HDR)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Shared helpers extracted for Alert Center reuse (module 14) — verify the
# extraction didn't change behavior, independent of the routes that call them.
# ---------------------------------------------------------------------------

def test_scheduler_status_unknown_without_any_log(tmp_path, monkeypatch):
    import execution.api_server as m
    monkeypatch.chdir(tmp_path)
    assert m._scheduler_status()["status"] == "unknown"


def test_scheduler_status_running_from_log_file(tmp_path, monkeypatch):
    import execution.api_server as m
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "system.log").write_text("2026-07-11 10:00:00 | INFO | Run complete | 3 EXECUTE\n")
    monkeypatch.chdir(tmp_path)
    status = m._scheduler_status()
    assert status["status"] == "running"
    assert status["last_execute_count"] == 3


def test_load_manifests_reads_research_results(tmp_path, monkeypatch):
    import json as _json
    import execution.api_server as m

    (tmp_path / "research" / "results").mkdir(parents=True)
    manifest = {
        "kind": "test_kind", "generated_at": "2026-07-11T00:00:00+00:00",
        "reproducible": False, "git": {"commit": "abcdef1234", "dirty": True},
        "params": {}, "datasets": [], "results": {},
    }
    (tmp_path / "research" / "results" / "foo_manifest.json").write_text(_json.dumps(manifest))
    monkeypatch.chdir(tmp_path)

    manifests = m._load_manifests()
    assert len(manifests) == 1
    assert manifests[0]["reproducible"] is False
    assert manifests[0]["git_commit"] == "abcdef12"


def test_forward_rule_alerts_silent_with_few_trades():
    from storage.outcome_tracker import log_signal, close_signal
    import execution.api_server as m

    report = {
        "symbol": "EURUSD", "final_verdict": "EXECUTE",
        "entry_price": 1.0, "stop_loss": 0.99, "take_profit": 1.02,
        "confluence": {"score": 70.0, "vote": {"winning_bias": "BULLISH"}},
        "regime": {"state": "TRENDING"}, "news": {"news_risk_score": 5.0},
        "engine_outputs": [],
    }
    sid = log_signal(report)
    close_signal(sid, 1.02, "win")

    # One closed trade is far below every pre-registered min_n (40/100) and
    # below the 80% early-warning threshold too — no alert should fire yet.
    assert m._forward_rule_alerts() == []


# ---------------------------------------------------------------------------
# Forward Demo (module 6) — pre-registered D001/D002 rule progress.
# ---------------------------------------------------------------------------

def test_forward_rule_progress_covers_both_registered_rules():
    import execution.api_server as m
    from storage.outcome_tracker import _init_db

    _init_db()  # outcomes table exists in prod before this is ever called
    progress = m._forward_rule_progress()
    ids = {p["rule_id"] for p in progress}
    assert ids == {"D001_fx_cut", "D002_carrier_confirmation"}
    for p in progress:
        assert {"statement", "bucket", "metric", "current_value", "op", "threshold",
                "n", "min_n", "progress_pct", "sufficient_n", "triggered", "action"}.issubset(p.keys())
        assert p["n"] == 0  # no outcomes in the fake D1 for this test
        assert p["sufficient_n"] is False
        assert p["triggered"] is False


def test_forward_review_requires_auth(client):
    assert client.get("/forward-review").status_code == 401


def test_forward_review_contract(client):
    from storage.outcome_tracker import _init_db

    _init_db()  # outcomes table exists in prod before this is ever called
    r = client.get("/forward-review", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"checked_at", "rules"}.issubset(body.keys())
    assert {p["rule_id"] for p in body["rules"]} == {"D001_fx_cut", "D002_carrier_confirmation"}


def test_outcomes_includes_profit_factor(client):
    body = client.get("/outcomes", headers=HDR).json()
    assert "profit_factor" in body["summary"]
    assert "avg_r_multiple" in body["summary"]


def _assert_strict_json(text: str) -> None:
    """A browser's fetch().json() uses strict JSON.parse, which rejects the
    bare Infinity/-Infinity/NaN tokens Python's json.dumps emits by default
    for those float values — pytest's/requests' own json() is lenient and
    would never catch this. Parse under the strict rule instead.
    """
    def reject(token):
        raise AssertionError(f"Response contains a non-standard JSON token: {token!r} in {text[:300]}")
    json.loads(text, parse_constant=reject)


def test_outcomes_response_is_strict_json_even_with_infinite_profit_factor(client):
    # Zero losing trades → profit_factor is mathematically infinite. This
    # must round-trip as a JSON string sentinel, not a raw Infinity token
    # (see storage/outcome_tracker.py's performance_summary).
    from storage.outcome_tracker import log_signal, close_signal

    report = {
        "symbol": "EURUSD", "final_verdict": "EXECUTE",
        "entry_price": 1.0850, "stop_loss": 1.0920, "take_profit": 1.0640,
        "confluence": {"score": 70.0, "vote": {"winning_bias": "BEARISH"}},
        "regime": {"state": "TRENDING"}, "news": {"news_risk_score": 5.0},
        "engine_outputs": [],
    }
    sid = log_signal(report)
    close_signal(sid, 1.0640, "win")

    r = client.get("/outcomes", headers=HDR)
    _assert_strict_json(r.text)
    assert r.json()["summary"]["profit_factor"] == "Infinity"


def test_forward_review_response_is_strict_json(client):
    from storage.outcome_tracker import _init_db

    _init_db()
    r = client.get("/forward-review", headers=HDR)
    _assert_strict_json(r.text)


def test_alerts_response_is_strict_json(client):
    _assert_strict_json(client.get("/alerts", headers=HDR).text)


# ---------------------------------------------------------------------------
# Research Integrity (module 9) — leakage guard, survivorship checker,
# manifest validator. Read-only, no network calls.
# ---------------------------------------------------------------------------

def test_research_integrity_requires_auth(client):
    assert client.get("/research/integrity").status_code == 401


def test_research_integrity_contract(client):
    r = client.get("/research/integrity", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"checked_at", "overall", "checks"}.issubset(body.keys())
    assert body["overall"] in ("PASS", "WARNING", "FAIL", "ERROR")
    assert {"leakage_guard", "survivorship", "manifest_validator"}.issubset(body["checks"].keys())
    for check in body["checks"].values():
        assert check["status"] in ("PASS", "WARNING", "FAIL", "ERROR")


def test_leakage_guard_report_scans_real_research_scripts():
    import execution.api_server as m

    report = m._leakage_guard_report()
    assert report["status"] in ("PASS", "WARNING")
    assert report["files_scanned"] > 0


def test_leakage_guard_report_advisory_only_never_fails():
    # A hard invariant of research/guards/static_scan.py's own design: it
    # is CLEAN or WARNINGS_FOUND, never a blocking verdict. The wrapper
    # here must not invent a FAIL state that contradicts that.
    import execution.api_server as m

    report = m._leakage_guard_report()
    assert report["status"] != "FAIL"


def test_manifest_validator_matches_load_manifests():
    import execution.api_server as m

    report = m._manifest_validator_report()
    manifests = m._load_manifests()
    assert report["total"] == len(manifests)
    non_repro = sum(1 for x in manifests if x.get("reproducible") is False)
    assert report["reproducible_count"] == len(manifests) - non_repro
    assert report["status"] == ("WARNING" if non_repro else "PASS")


def test_survivorship_report_shape():
    import execution.api_server as m

    report = m._survivorship_report()
    assert report["status"] in ("PASS", "WARNING", "FAIL")
    assert "symbol_evidence" in report and "selection_disclosure" in report


# ---------------------------------------------------------------------------
# Reports (module 10) — on-demand snapshots, Markdown or JSON.
# ---------------------------------------------------------------------------

REPORT_KINDS = ["research", "manifest_summary", "system", "provider", "forward", "data_quality"]


def test_reports_requires_auth(client):
    assert client.get("/reports/research").status_code == 401


def test_reports_unknown_kind_404s(client):
    r = client.get("/reports/not-a-real-kind", headers=HDR)
    assert r.status_code == 404


@pytest.mark.parametrize("kind", REPORT_KINDS)
def test_reports_markdown_format(client, kind):
    from storage.outcome_tracker import _init_db

    _init_db()  # outcomes table exists in prod before "forward" is ever requested
    r = client.get(f"/reports/{kind}", headers=HDR)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/markdown")
    assert f'filename="iatis_{kind}_report.md"' in r.headers["content-disposition"]
    assert r.text.startswith("#")


@pytest.mark.parametrize("kind", REPORT_KINDS)
def test_reports_json_format(client, kind):
    from storage.outcome_tracker import _init_db

    _init_db()
    r = client.get(f"/reports/{kind}", params={"format": "json"}, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"kind", "title", "generated_at", "data"}.issubset(body.keys())
    assert body["kind"] == kind
    assert isinstance(body["data"], dict)


def test_reports_forward_json_is_strict_json(client):
    # forward-review's rules can contain the "Infinity" sentinel — make
    # sure wrapping it in a report doesn't reintroduce a raw Infinity token.
    from storage.outcome_tracker import _init_db

    _init_db()
    r = client.get("/reports/forward", params={"format": "json"}, headers=HDR)
    _assert_strict_json(r.text)


# ---------------------------------------------------------------------------
# Experiment Runner (module 5) — whitelisted jobs only, fixed argv, never
# shell=True, never user-supplied arguments. Deliberately narrow scope:
# only verify_data_integrity and forward_review are whitelisted (local/
# fast/no network); see execution/api_server.py's module docstring for why
# long-running or provider-API-spending jobs are NOT included.
# ---------------------------------------------------------------------------

def _wait_for_job(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/experiments/{job_id}", headers=HDR).json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.05)
    return body


def test_experiment_job_catalog_requires_auth(client):
    assert client.get("/experiments/jobs").status_code == 401


def test_experiment_job_catalog_is_the_narrow_whitelist(client):
    r = client.get("/experiments/jobs", headers=HDR)
    assert r.status_code == 200, r.text
    ids = {j["id"] for j in r.json()["jobs"]}
    assert ids == {"verify_data_integrity", "forward_review", "backup_d1"}


def test_experiment_job_catalog_categorizes_ops_vs_research(client):
    r = client.get("/experiments/jobs", headers=HDR)
    by_id = {j["id"]: j["category"] for j in r.json()["jobs"]}
    assert by_id["verify_data_integrity"] == "research"
    assert by_id["forward_review"] == "research"
    assert by_id["backup_d1"] == "ops"


def test_experiments_run_requires_auth(client):
    assert client.post("/experiments/run", json={"job": "verify_data_integrity"}).status_code == 401


def test_experiments_run_rejects_unknown_job(client):
    r = client.post("/experiments/run", json={"job": "rm -rf /"}, headers=HDR)
    assert r.status_code == 400


def test_experiments_status_unknown_job_404s(client):
    assert client.get("/experiments/nonexistent-job-id", headers=HDR).status_code == 404


def test_experiments_list_requires_auth(client):
    assert client.get("/experiments").status_code == 401


def test_run_job_uses_fixed_argv_never_shell(monkeypatch):
    """Unit-level guarantee on the actual subprocess call, independent of
    timing: fixed argv list, shell never True, output captured line by line."""
    import execution.api_server as m

    captured: dict = {}

    class _FakeProc:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            self.stdout = iter(["line one\n", "line two\n"])
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    monkeypatch.setattr(m, "_JOB_COMMANDS", {"fake_job": ["echo", "hi"]})

    job = m._Job("test-id", "fake_job")
    m._run_job(job)

    assert captured["argv"] == ["echo", "hi"]
    assert captured["kwargs"].get("shell", False) is False
    assert job.status == "finished"
    assert job.log_lines == ["line one", "line two"]


def test_experiments_run_and_poll_to_completion(client, monkeypatch):
    import execution.api_server as m

    monkeypatch.setattr(m, "_JOB_COMMANDS", {"echo_job": [sys.executable, "-c", "print('hello from job')"]})

    r = client.post("/experiments/run", json={"job": "echo_job"}, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("queued", "running")

    body = _wait_for_job(client, r.json()["job_id"])
    assert body["status"] == "finished", body
    assert body["returncode"] == 0
    assert any("hello from job" in line for line in body["log"])


def test_experiments_run_failed_job_reports_nonzero_returncode(client, monkeypatch):
    import execution.api_server as m

    monkeypatch.setattr(m, "_JOB_COMMANDS", {"failing_job": [sys.executable, "-c", "import sys; sys.exit(1)"]})

    r = client.post("/experiments/run", json={"job": "failing_job"}, headers=HDR)
    body = _wait_for_job(client, r.json()["job_id"])
    assert body["status"] == "failed", body
    assert body["returncode"] == 1


def test_experiments_run_rejects_duplicate_concurrent_run(client, monkeypatch):
    import execution.api_server as m

    monkeypatch.setattr(m, "_JOB_COMMANDS", {"slow_job": [sys.executable, "-c", "import time; time.sleep(1)"]})

    r1 = client.post("/experiments/run", json={"job": "slow_job"}, headers=HDR)
    assert r1.status_code == 200, r1.text

    r2 = client.post("/experiments/run", json={"job": "slow_job"}, headers=HDR)
    assert r2.status_code == 409

    _wait_for_job(client, r1.json()["job_id"])  # drain before the test ends


def test_experiments_list_includes_started_jobs(client, monkeypatch):
    import execution.api_server as m

    monkeypatch.setattr(m, "_JOB_COMMANDS", {"list_test_job": [sys.executable, "-c", "print('x')"]})
    r = client.post("/experiments/run", json={"job": "list_test_job"}, headers=HDR)
    job_id = r.json()["job_id"]

    body = client.get("/experiments", headers=HDR).json()
    assert any(j["job_id"] == job_id for j in body["jobs"])
    _wait_for_job(client, job_id)  # drain before the test ends


# ---------------------------------------------------------------------------
# VPS Operations (module 12) — deliberately narrow: config-cache reload
# only here; "diagnostics" reuses /health/full, "backup" reuses the
# Experiment Runner's "backup_d1" job. No service-restart endpoint exists.
# ---------------------------------------------------------------------------

def test_ops_reload_config_requires_auth(client):
    assert client.post("/ops/reload-config").status_code == 401


def test_ops_reload_config_clears_cache(client):
    import execution.api_server as m

    m._config_cache = {"stale": True}
    r = client.post("/ops/reload-config", headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True
    assert m._config_cache is None


def test_no_service_restart_endpoint_exists(client):
    # Explicit guard for the scoping decision: restarting the live
    # scheduler/API is not exposed anywhere in this API.
    for path in ("/ops/restart", "/ops/restart-api", "/ops/restart-scheduler"):
        assert client.post(path, headers=HDR).status_code == 404


# ---------------------------------------------------------------------------
# Security / audit log (module 15) — real audit trail for mutating
# actions; role-based access is a deliberately scoped-out gap (see
# execution/api_server.py's module docstring and MISSION_CONTROL_AUDIT.md).
# ---------------------------------------------------------------------------

def test_audit_log_requires_auth(client):
    assert client.get("/audit-log").status_code == 401


def test_audit_log_records_login_success_and_failure(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", tmp_path / "audit.jsonl")
    client.post("/login", json={"key": "wrong-key"})
    client.post("/login", json={"key": "test-key-123"})

    r = client.get("/audit-log", headers=HDR)
    assert r.status_code == 200, r.text
    actions = [(e["action"], e["success"]) for e in r.json()["entries"]]
    assert ("login", False) in actions
    assert ("login", True) in actions


def test_audit_log_records_experiment_run(client, tmp_path, monkeypatch):
    import execution.api_server as m

    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(m, "_JOB_COMMANDS", {"audit_test_job": [sys.executable, "-c", "print('x')"]})
    r = client.post("/experiments/run", json={"job": "audit_test_job"}, headers=HDR)
    job_id = r.json()["job_id"]
    _wait_for_job(client, job_id)

    entries = client.get("/audit-log", headers=HDR).json()["entries"]
    assert any(e["action"] == "experiment_run" and "audit_test_job" in (e["detail"] or "") for e in entries)


def test_audit_log_records_reload_config(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", tmp_path / "audit.jsonl")
    client.post("/ops/reload-config", headers=HDR)
    entries = client.get("/audit-log", headers=HDR).json()["entries"]
    assert any(e["action"] == "reload_config" for e in entries)


def test_audit_log_records_close_outcome(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", tmp_path / "audit.jsonl")
    from storage.outcome_tracker import log_signal

    report = {
        "symbol": "EURUSD", "final_verdict": "EXECUTE",
        "entry_price": 1.0, "stop_loss": 0.99, "take_profit": 1.02,
        "confluence": {"score": 70.0, "vote": {"winning_bias": "BULLISH"}},
        "regime": {"state": "TRENDING"}, "news": {"news_risk_score": 5.0},
        "engine_outputs": [],
    }
    sid = log_signal(report)
    client.post(f"/outcomes/{sid}/close", params={"exit_price": 1.02, "outcome": "win"}, headers=HDR)

    entries = client.get("/audit-log", headers=HDR).json()["entries"]
    assert any(e["action"] == "close_outcome" and sid in (e["detail"] or "") for e in entries)


def test_audit_log_never_exposes_raw_api_key(client, tmp_path, monkeypatch):
    monkeypatch.setattr("storage.audit_log.DEFAULT_LOG_PATH", tmp_path / "audit.jsonl")
    client.post("/ops/reload-config", headers=HDR)
    entries = client.get("/audit-log", headers=HDR).json()["entries"]
    assert entries
    for e in entries:
        assert "test-key-123" not in str(e)


# ---------------------------------------------------------------------------
# Alert Center (module 14) — aggregates existing signals, never a new
# data source of its own.
# ---------------------------------------------------------------------------

def test_alerts_requires_auth(client):
    assert client.get("/alerts").status_code == 401


def test_alerts_response_shape_and_severity_counts(client):
    r = client.get("/alerts", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"checked_at", "count", "by_severity", "alerts"}.issubset(body.keys())
    assert body["count"] == len(body["alerts"])
    assert sum(body["by_severity"].values()) == body["count"]
    for a in body["alerts"]:
        assert {"severity", "category", "message", "detail"}.issubset(a.keys())
        assert a["severity"] in ("error", "warning", "info")


def test_alerts_flags_missing_provider_credentials(client):
    # tests/conftest.py strips all credential env vars for every test, so
    # every provider must show up as a provider_failure warning here.
    body = client.get("/alerts", headers=HDR).json()
    provider_alerts = {a["detail"]["provider"] for a in body["alerts"] if a["category"] == "provider_failure"}
    assert {"twelve_data", "alpha_vantage", "finnhub", "ctrader"}.issubset(provider_alerts)


def test_alerts_sorted_errors_before_warnings_before_info(client):
    order = {"error": 0, "warning": 1, "info": 2}
    severities = [a["severity"] for a in client.get("/alerts", headers=HDR).json()["alerts"]]
    ranks = [order[s] for s in severities]
    assert ranks == sorted(ranks)


# ---------------------------------------------------------------------------
# Data Providers telemetry (module 2) — real usage from decisions.jsonl,
# no live pings; macro/alt source status, no fabricated latency.
# ---------------------------------------------------------------------------

def test_provider_chains_includes_recent_usage_and_macro_sources(client):
    r = client.get("/provider-chains", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "recent_usage" in body and "macro_sources" in body
    assert {"cboe", "fred", "cftc", "alternative_me"}.issubset(body["macro_sources"].keys())


def test_macro_source_status_alternative_me_honestly_unconfigured():
    import execution.api_server as m

    status = m._macro_source_status()
    assert status["alternative_me"]["configured"] is False
    assert "not integrated" in status["alternative_me"]["note"]


def test_macro_source_status_keyless_sources_are_configured_without_env_vars(monkeypatch):
    import execution.api_server as m

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    status = m._macro_source_status()
    assert status["cboe"]["configured"] is True
    assert status["cftc"]["configured"] is True
    # FRED itself is keyless-capable but its "configured" flag tracks the
    # optional API key specifically (a caveat in its own note).
    assert status["fred"]["configured"] is False


def test_provider_usage_aggregates_from_decision_log(tmp_path, monkeypatch):
    import execution.api_server as m
    from storage.decision_log import log_decision

    path = tmp_path / "decisions.jsonl"
    monkeypatch.setattr("storage.decision_log.DEFAULT_LOG_PATH", path)

    log_decision({"symbol": "EURUSD", "final_verdict": "EXECUTE", "data_providers": {"H4": "ccxt", "D1": "ccxt"}}, path=path)
    log_decision({"symbol": "XAUUSD", "final_verdict": "NO_TRADE", "data_providers": {"H4": "yahoo_finance"}}, path=path)

    usage = m._provider_usage_from_decisions()
    assert usage["ccxt"]["count"] == 2
    assert usage["ccxt"]["timeframes"] == ["D1", "H4"]
    assert usage["yahoo_finance"]["count"] == 1
    assert usage["ccxt"]["last_used_at"] is not None


# ---------------------------------------------------------------------------
# Research Center drill-down (module 4) — GET /research/{hypothesis_id}.
# Uses the real registry.json shipped in this repo, same pattern as
# test_research_layer.py's edge-gate tests against real hypothesis data.
# ---------------------------------------------------------------------------

def test_research_hypothesis_detail_requires_auth(client):
    assert client.get("/research/H015").status_code == 401


def test_research_hypothesis_detail_unknown_id_404s(client):
    r = client.get("/research/H999_DOES_NOT_EXIST", headers=HDR)
    assert r.status_code == 404


def test_research_hypothesis_detail_route_does_not_shadow_literal_routes(client):
    # /research/{hypothesis_id} is registered after /research/manifests
    # and /research/integrity — both literal routes must still resolve
    # to themselves, not be captured as hypothesis_id="manifests"/"integrity".
    assert client.get("/research/manifests", headers=HDR).status_code == 200
    assert client.get("/research/integrity", headers=HDR).status_code == 200


def test_research_hypothesis_detail_finds_exact_manifest_link(client):
    # H015's registry entry declares manifest="results/engine_subset_search_20260709_manifest.json",
    # which really exists in research/results/ in this repo.
    r = client.get("/research/H015", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "H015"
    assert body["hypothesis"]["status"] == "RESOLVED"
    exact_files = {m["file"] for m in body["manifests"]["exact"]}
    assert "engine_subset_search_20260709_manifest.json" in exact_files


def test_research_hypothesis_detail_includes_result_files_with_existence_check(client):
    r = client.get("/research/H015", headers=HDR)
    body = r.json()
    assert body["result_files"]
    assert all({"path", "exists"}.issubset(rf.keys()) for rf in body["result_files"])


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
