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
