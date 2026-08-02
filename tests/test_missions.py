"""
tests/test_missions.py
--------------------------
AI Research Lab / Mission Center Phase 2 (2026-07-28) — contract tests
for execution/routes/missions.py, matching tests/test_api_contract.py's
established fixtures/conventions (client/HDR, the _FakeProc pattern that
avoids spawning a real subprocess for argv-shape assertions).
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

_VALID_BODY = {
    "symbols": ["EURUSD"],
    "sampler": "random",
    "n_trials_per_symbol": 2,
    "timeframes_choices": [["H1"]],
    "engine_set_choices": [["nnfx", "price_action"]],
}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver") as c:
        yield c


class _FakeProc:
    """Matches tests/test_api_contract.py's own fake — avoids spawning a
    real `python3 -m backtest.mission_runner` subprocess for tests that
    only assert on argv shape or job-state transitions."""

    def __init__(self, argv, **kwargs):
        _FakeProc.captured_argv = argv
        self.stdout = iter([])
        self.returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _FakeBlockingProc:
    """Never exits on its own (stdout blocks forever) — for cancel tests
    that need a job to still be "running" when cancel() is called."""

    def __init__(self, argv, **kwargs):
        _FakeBlockingProc.captured_argv = argv
        self.returncode = None
        self.killed = False

    @property
    def stdout(self):
        import queue
        # A generator that blocks forever (never yields, never raises) —
        # simulates a subprocess whose output loop never reaches EOF
        # until kill() sets the flag.
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


def _wait_for_terminal(client, mission_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/research/missions/{mission_id}", headers=HDR).json()
        job_status = body.get("job_status")
        if job_status not in ("queued", "running"):
            return body
        time.sleep(0.05)
    return body


# ── Auth ──────────────────────────────────────────────────────────────────

def test_missions_create_requires_auth(client):
    assert client.post("/research/missions", json=_VALID_BODY).status_code == 401


def test_missions_list_requires_auth(client):
    assert client.get("/research/missions").status_code == 401


def test_missions_status_requires_auth(client):
    assert client.get("/research/missions/does-not-exist").status_code == 401


def test_missions_leaderboard_requires_auth(client):
    assert client.get("/research/missions/does-not-exist/leaderboard").status_code == 401


def test_missions_cancel_requires_auth(client):
    assert client.post("/research/missions/does-not-exist/cancel").status_code == 401


# ── Validation ────────────────────────────────────────────────────────────

def test_missions_create_rejects_unknown_symbol(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "symbols": ["ZZZFAKE"]}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown symbol" in r.json()["detail"]


def test_missions_create_rejects_empty_symbols(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "symbols": []}, headers=HDR)
    assert r.status_code == 400


def test_missions_create_rejects_bad_sampler(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "sampler": "not_a_sampler"}, headers=HDR)
    assert r.status_code == 400
    assert "sampler" in r.json()["detail"]


def test_missions_create_rejects_bad_objective_metric(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "objective_metric": "made_up_metric"}, headers=HDR)
    assert r.status_code == 400


def test_missions_create_rejects_n_trials_out_of_bounds(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "n_trials_per_symbol": 0}, headers=HDR)
    assert r.status_code == 400
    r2 = client.post("/research/missions", json={**_VALID_BODY, "n_trials_per_symbol": 999_999}, headers=HDR)
    assert r2.status_code == 400


def test_missions_create_rejects_unknown_engine_in_search_space(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "engine_set_choices": [["not_a_real_engine"]]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_unknown_timeframe_in_search_space(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "timeframes_choices": [["M5"]]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_unknown_context_filter_name(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "context_filter_set_choices": [[{"name": "fear_greed", "mode": "entry_filter"}]]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_unknown_context_filter_mode(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "context_filter_set_choices": [[{"name": "direction", "mode": "bogus_mode"}]]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_unknown_engine_in_engine_variant_choices(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "engine_variant_choices": [{"not_a_real_engine": "v2"}]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_unknown_variant_for_engine(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "engine_variant_choices": [{"price_action": "v3"}]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_engine_with_no_variants(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "engine_variant_choices": [{"nnfx": "v2"}]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_risk_param_out_of_bounds(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "risk_param_ranges": {"sl_atr_multiplier": [-5.0, 999.0]}},
        headers=HDR,
    )
    assert r.status_code == 400
    assert "risk_param_ranges" in r.json()["detail"]


def test_missions_create_rejects_both_risk_forms_set(client):
    r = client.post(
        "/research/missions",
        json={
            **_VALID_BODY,
            "risk_param_ranges": {"sl_atr_multiplier": [1.0, 3.0]},
            "risk_param_grid": {"sl_atr_multiplier": [1.5, 2.0]},
        },
        headers=HDR,
    )
    assert r.status_code == 400
    assert "XOR" in r.json()["detail"]


def test_missions_create_rejects_bad_start_end_order(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "start": "2024-06-01", "end": "2024-01-01"},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_bad_name(client):
    r = client.post("/research/missions", json={**_VALID_BODY, "name": "bad;name"}, headers=HDR)
    assert r.status_code == 400


# ── Dispatch / argv ────────────────────────────────────────────────────────

def test_missions_create_builds_expected_argv_and_returns_mission_id(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "mission_id" in body and len(body["mission_id"]) > 0

    _wait_for_terminal(client, body["mission_id"])
    argv = _FakeProc.captured_argv
    assert "backtest.mission_runner" in argv
    assert "--mission-id" in argv and body["mission_id"] in argv
    assert "--symbols" in argv and "EURUSD" in argv
    assert "--sampler" in argv and "random" in argv


def test_missions_create_builds_expected_argv_with_context_filters(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    body = {
        **_VALID_BODY,
        "context_filter_set_choices": [[], [{"name": "direction", "mode": "entry_filter",
                                              "params": {"allowed": ["BULLISH"]}, "weight": 0}]],
    }
    r = client.post("/research/missions", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    mission_id = r.json()["mission_id"]

    _wait_for_terminal(client, mission_id)
    argv = _FakeProc.captured_argv
    assert "--context-filter-set-choices" in argv
    idx = argv.index("--context-filter-set-choices")
    import json as _json

    assert _json.loads(argv[idx + 1]) == body["context_filter_set_choices"]


def test_missions_create_builds_expected_argv_with_engine_variants(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    body = {
        **_VALID_BODY,
        "engine_set_choices": [["nnfx", "price_action", "wyckoff"]],
        "engine_variant_choices": [{}, {"price_action": "v2"}, {"wyckoff": "v2"}],
    }
    r = client.post("/research/missions", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    mission_id = r.json()["mission_id"]

    _wait_for_terminal(client, mission_id)
    argv = _FakeProc.captured_argv
    assert "--engine-variant-choices" in argv
    idx = argv.index("--engine-variant-choices")
    import json as _json

    assert _json.loads(argv[idx + 1]) == body["engine_variant_choices"]


def test_missions_create_builds_expected_argv_omits_nothing_when_engine_variants_default(client, monkeypatch):
    """Regression pin: --engine-variant-choices is ALWAYS present in argv
    (unconditional, matching --indicator-set-choices's own status), even
    when the request never sets engine_variant_choices explicitly —
    defaults to the all-v1 [{}] entry."""
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    mission_id = r.json()["mission_id"]

    _wait_for_terminal(client, mission_id)
    argv = _FakeProc.captured_argv
    assert "--engine-variant-choices" in argv
    idx = argv.index("--engine-variant-choices")
    import json as _json

    assert _json.loads(argv[idx + 1]) == [{}]


def test_missions_create_builds_expected_argv_with_hypothesis_bundles(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    bundles = [
        {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []},
        {"name": "NNFX + Wyckoff", "timeframes": ["H4"], "engines": ["nnfx", "wyckoff"], "indicators": [], "context_filters": []},
    ]
    body = {**_VALID_BODY, "hypothesis_bundle_choices": bundles}
    r = client.post("/research/missions", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    mission_id = r.json()["mission_id"]

    _wait_for_terminal(client, mission_id)
    argv = _FakeProc.captured_argv
    assert "--hypothesis-bundle-choices" in argv
    idx = argv.index("--hypothesis-bundle-choices")
    import json as _json

    assert _json.loads(argv[idx + 1]) == bundles


def test_missions_create_without_hypothesis_bundles_omits_the_flag(client, monkeypatch):
    # Regression guard: a request without hypothesis_bundle_choices must
    # build argv byte-identical to before this feature existed — no
    # --hypothesis-bundle-choices flag appended at all.
    monkeypatch.setattr("subprocess.Popen", _FakeProc)

    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    _wait_for_terminal(client, r.json()["mission_id"])
    assert "--hypothesis-bundle-choices" not in _FakeProc.captured_argv


def test_missions_create_rejects_hypothesis_bundle_blank_name(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "hypothesis_bundle_choices": [
            {"name": "", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []},
        ]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_create_rejects_hypothesis_bundle_duplicate_names(client):
    bundle = {"name": "Same Name", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "hypothesis_bundle_choices": [bundle, dict(bundle)]},
        headers=HDR,
    )
    assert r.status_code == 400
    assert "unique" in r.json()["detail"]


def test_missions_create_rejects_hypothesis_bundle_unknown_engine(client):
    r = client.post(
        "/research/missions",
        json={**_VALID_BODY, "hypothesis_bundle_choices": [
            {"name": "Bad", "timeframes": ["H1"], "engines": ["not_a_real_engine"], "indicators": [], "context_filters": []},
        ]},
        headers=HDR,
    )
    assert r.status_code == 400


def test_missions_list_includes_created_mission(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    mission_id = r.json()["mission_id"]
    _wait_for_terminal(client, mission_id)

    listing = client.get("/research/missions", headers=HDR)
    assert listing.status_code == 200
    ids = [m["job_id"] for m in listing.json()["missions"]]
    assert mission_id in ids


def test_missions_status_404_when_unknown(client):
    r = client.get("/research/missions/does-not-exist-at-all", headers=HDR)
    assert r.status_code == 404


def test_missions_leaderboard_returns_empty_for_unknown_mission(client):
    r = client.get("/research/missions/does-not-exist-at-all/leaderboard", headers=HDR)
    assert r.status_code == 200
    assert r.json()["trials"] == []


# ── Cancel ────────────────────────────────────────────────────────────────

def test_missions_cancel_404_when_unknown(client):
    r = client.post("/research/missions/does-not-exist-at-all/cancel", headers=HDR)
    assert r.status_code == 404


def test_missions_cancel_kills_running_job(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeBlockingProc)

    r = client.post("/research/missions", json=_VALID_BODY, headers=HDR)
    mission_id = r.json()["mission_id"]

    # Give _run_job's background thread a moment to actually reach the
    # subprocess.Popen call and flip status to "running".
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        status = client.get(f"/research/missions/{mission_id}", headers=HDR).json()
        if status.get("job_status") == "running":
            break
        time.sleep(0.02)

    cancel = client.post(f"/research/missions/{mission_id}/cancel", headers=HDR)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"


# ── Phase 3: Meta-Analysis + Multi-Stage Validation ────────────────────────

_VALID_VALIDATION_BODY = {
    "trial_number": 0,
    "trial_symbol": "EURUSD",
    "validation_symbols": ["GBPUSD", "XAUUSD"],
    # A genuine cross-symbol scenario (trial_symbol excluded from
    # validation_symbols) — explicit since SAME_SYMBOL is now the API
    # default and would reject this exact body (Forensic Audit Phase 1,
    # item D, 2026-08-02).
    "validation_mode": "CROSS_SYMBOL",
}


def _seed_mission_and_trial(mission_id: str, state: str = "COMPLETE", n_complete: int = 1):
    from backtest import mission_runner
    from backtest.optimizer import MissionSearchSpace, _ENGINES_IDX_KEY, _INDICATORS_IDX_KEY, _TF_IDX_KEY
    from storage import research_missions

    space = MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    research_missions.upsert_mission(
        mission_id=mission_id, name="test-mission", sampler="random", objective_metric="profit_factor",
        symbols=["EURUSD"], n_trials_per_symbol=n_complete, min_trades=1, seed=42,
        search_space=mission_runner._search_space_dict(space), config={}, status="finished",
    )
    for i in range(n_complete):
        research_missions.record_trial(
            mission_id=mission_id, trial_number=i, symbol="EURUSD",
            state=state if i == 0 else "COMPLETE",
            objective_value=1.2 + i * 0.01 if (state == "COMPLETE" or i > 0) else None,
            params={_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0, "sl_atr_multiplier": 2.0 + i * 0.01},
            metrics={"profit_factor": 1.2}, trades=50 if (state == "COMPLETE" or i > 0) else 0,
            error=None, started_at="t", finished_at="t",
        )


def test_missions_validate_requires_auth(client):
    assert client.post("/research/missions/does-not-exist/validate", json=_VALID_VALIDATION_BODY).status_code == 401


def test_missions_validate_404_unknown_mission(client):
    r = client.post("/research/missions/does-not-exist/validate", json=_VALID_VALIDATION_BODY, headers=HDR)
    assert r.status_code == 404


def test_missions_validate_404_unknown_trial(client):
    _seed_mission_and_trial("val-mission-a")
    r = client.post(
        "/research/missions/val-mission-a/validate",
        json={**_VALID_VALIDATION_BODY, "trial_number": 999}, headers=HDR,
    )
    assert r.status_code == 404


def test_missions_validate_rejects_non_complete_trial(client):
    _seed_mission_and_trial("val-mission-b", state="PRUNED")
    r = client.post("/research/missions/val-mission-b/validate", json=_VALID_VALIDATION_BODY, headers=HDR)
    assert r.status_code == 400
    assert "COMPLETE" in r.json()["detail"]


def test_missions_validate_rejects_single_symbol(client):
    _seed_mission_and_trial("val-mission-c")
    r = client.post(
        "/research/missions/val-mission-c/validate",
        json={**_VALID_VALIDATION_BODY, "validation_symbols": ["GBPUSD"]}, headers=HDR,
    )
    assert r.status_code == 400
    assert "at least 2" in r.json()["detail"]


def test_missions_validate_rejects_unknown_validation_symbol(client):
    _seed_mission_and_trial("val-mission-d")
    r = client.post(
        "/research/missions/val-mission-d/validate",
        json={**_VALID_VALIDATION_BODY, "validation_symbols": ["ZZZFAKE", "GBPUSD"]}, headers=HDR,
    )
    assert r.status_code == 400
    assert "Unknown symbol" in r.json()["detail"]


def test_missions_validate_rejects_too_many_validation_symbols(client):
    _seed_mission_and_trial("val-mission-e")
    r = client.post(
        "/research/missions/val-mission-e/validate",
        json={**_VALID_VALIDATION_BODY, "validation_symbols": ["EURUSD"] * 11}, headers=HDR,
    )
    assert r.status_code == 400


def test_missions_validate_rejects_bad_start_end_order(client):
    _seed_mission_and_trial("val-mission-f")
    r = client.post(
        "/research/missions/val-mission-f/validate",
        json={**_VALID_VALIDATION_BODY, "start": "2024-06-01", "end": "2024-01-01"}, headers=HDR,
    )
    assert r.status_code == 400


def test_missions_validate_rejects_multipliers_without_baseline(client):
    _seed_mission_and_trial("val-mission-g")
    r = client.post(
        "/research/missions/val-mission-g/validate",
        json={**_VALID_VALIDATION_BODY, "rb_multipliers": [0.5, 0.8]}, headers=HDR,
    )
    assert r.status_code == 400
    assert "1.0" in r.json()["detail"]


def test_missions_validate_rejects_unknown_rb_param(client):
    _seed_mission_and_trial("val-mission-h")
    r = client.post(
        "/research/missions/val-mission-h/validate",
        json={**_VALID_VALIDATION_BODY, "rb_params": ["not_a_real_param"]}, headers=HDR,
    )
    assert r.status_code == 400


# ── Validation Mode Explicitness (Forensic Audit Phase 1, item D, 2026-08-02) ──

def test_missions_validate_same_symbol_omitted_symbols_defaults_to_trial_symbol(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    _seed_mission_and_trial("val-mission-same-1")
    body = {"trial_number": 0, "trial_symbol": "EURUSD", "validation_symbols": [], "validation_mode": "SAME_SYMBOL"}
    r = client.post("/research/missions/val-mission-same-1/validate", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    # _FakeProc.captured_argv is a shared class attribute set by whichever
    # job's background thread calls subprocess.Popen — must wait for THIS
    # job to actually reach that point before reading it (matches
    # test_missions_validate_builds_expected_argv_and_returns_validation_id's
    # own established pattern below).
    _wait_for_validation_terminal(client, "val-mission-same-1", r.json()["validation_id"])
    assert "--validation-symbols" in _FakeProc.captured_argv
    idx = _FakeProc.captured_argv.index("--validation-symbols")
    assert _FakeProc.captured_argv[idx + 1] == "EURUSD"


def test_missions_validate_same_symbol_exact_match_is_accepted(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    _seed_mission_and_trial("val-mission-same-2")
    body = {"trial_number": 0, "trial_symbol": "EURUSD", "validation_symbols": ["EURUSD"], "validation_mode": "SAME_SYMBOL"}
    r = client.post("/research/missions/val-mission-same-2/validate", json=body, headers=HDR)
    assert r.status_code == 200, r.text


def test_missions_validate_same_symbol_fails_hard_on_mismatched_symbols(client, monkeypatch):
    # The literal invariant test — SAME_SYMBOL must never silently
    # substitute or widen the symbol list.
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    _seed_mission_and_trial("val-mission-same-3")
    body = {
        "trial_number": 0, "trial_symbol": "EURUSD",
        "validation_symbols": ["GBPUSD", "XAUUSD"], "validation_mode": "SAME_SYMBOL",
    }
    r = client.post("/research/missions/val-mission-same-3/validate", json=body, headers=HDR)
    assert r.status_code == 400
    assert "SAME_SYMBOL" in r.json()["detail"]


def test_missions_validate_rejects_unknown_validation_mode(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    _seed_mission_and_trial("val-mission-mode-unknown")
    body = {**_VALID_VALIDATION_BODY, "validation_mode": "NOT_A_REAL_MODE"}
    r = client.post("/research/missions/val-mission-mode-unknown/validate", json=body, headers=HDR)
    assert r.status_code == 400
    assert "validation_mode" in r.json()["detail"]


def test_missions_validate_argv_includes_validation_mode_flag(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    _seed_mission_and_trial("val-mission-mode-argv")
    r = client.post("/research/missions/val-mission-mode-argv/validate", json=_VALID_VALIDATION_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    _wait_for_validation_terminal(client, "val-mission-mode-argv", r.json()["validation_id"])
    assert "--validation-mode" in _FakeProc.captured_argv
    idx = _FakeProc.captured_argv.index("--validation-mode")
    assert _FakeProc.captured_argv[idx + 1] == "CROSS_SYMBOL"


def _wait_for_validation_terminal(client, mission_id: str, validation_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/research/missions/{mission_id}/validations/{validation_id}", headers=HDR).json()
        job_status = body.get("job_status")
        if job_status not in ("queued", "running"):
            return body
        time.sleep(0.05)
    return body


def test_missions_validate_builds_expected_argv_and_returns_validation_id(client, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    _seed_mission_and_trial("val-mission-i")

    r = client.post("/research/missions/val-mission-i/validate", json=_VALID_VALIDATION_BODY, headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "validation_id" in body and len(body["validation_id"]) > 0

    _wait_for_validation_terminal(client, "val-mission-i", body["validation_id"])
    argv = _FakeProc.captured_argv
    assert "backtest.mission_validator" in argv
    assert "--validation-id" in argv and body["validation_id"] in argv
    assert "--mission-id" in argv and "val-mission-i" in argv
    assert "--trial-number" in argv and "0" in argv
    assert "--trial-symbol" in argv and "EURUSD" in argv
    assert "--validation-symbols" in argv and "GBPUSD" in argv and "XAUUSD" in argv


def test_missions_validations_list_requires_auth(client):
    assert client.get("/research/missions/does-not-exist/validations").status_code == 401


def test_missions_validations_list_empty_for_unknown_mission(client):
    r = client.get("/research/missions/does-not-exist/validations", headers=HDR)
    assert r.status_code == 200
    assert r.json()["validations"] == []


def test_missions_validations_list_includes_created_validation(client, monkeypatch):
    # GET .../validations reads storage.research_mission_validations (D1),
    # populated by the REAL backtest/mission_validator.py subprocess as it
    # runs — _FakeProc (like _missions_list's own job-dict test) never
    # actually invokes that module, so seed the D1 row directly here,
    # exactly as run_validation()'s own first statement would.
    monkeypatch.setattr("subprocess.Popen", _FakeProc)
    _seed_mission_and_trial("val-mission-j")
    r = client.post("/research/missions/val-mission-j/validate", json=_VALID_VALIDATION_BODY, headers=HDR)
    validation_id = r.json()["validation_id"]

    from storage import research_mission_validations
    research_mission_validations.upsert_validation(
        validation_id=validation_id, mission_id="val-mission-j", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["GBPUSD", "XAUUSD"], objective_metric="profit_factor", criteria={},
    )

    listing = client.get("/research/missions/val-mission-j/validations", headers=HDR)
    assert listing.status_code == 200
    ids = [v["id"] for v in listing.json()["validations"]]
    assert validation_id in ids


def test_missions_validation_detail_requires_auth(client):
    assert client.get("/research/missions/m/validations/v").status_code == 401


def test_missions_validation_detail_404_when_unknown(client):
    r = client.get("/research/missions/m/validations/does-not-exist", headers=HDR)
    assert r.status_code == 404


def test_missions_meta_analysis_requires_auth(client):
    assert client.get("/research/missions/does-not-exist/meta-analysis").status_code == 401


def test_missions_meta_analysis_404_unknown_mission(client):
    r = client.get("/research/missions/does-not-exist/meta-analysis", headers=HDR)
    assert r.status_code == 404


def test_missions_meta_analysis_insufficient_data_shape(client):
    _seed_mission_and_trial("val-mission-k", n_complete=3)
    r = client.get("/research/missions/val-mission-k/meta-analysis", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["insufficient_data"] is True
    assert body["n_complete_trials"] == 3


def test_missions_meta_analysis_real_response_shape(client):
    _seed_mission_and_trial("val-mission-l", n_complete=25)
    r = client.get("/research/missions/val-mission-l/meta-analysis", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["insufficient_data"] is False
    assert body["mission_id"] == "val-mission-l"
    assert body["sampler"] == "random"
    assert len(body["engine_frequencies"]) > 0
    assert len(body["consensus_bands"]) == 1


def test_missions_meta_analysis_rejects_bad_query_params(client):
    _seed_mission_and_trial("val-mission-m", n_complete=25)
    r = client.get("/research/missions/val-mission-m/meta-analysis?top_fraction=0", headers=HDR)
    assert r.status_code == 400
    r2 = client.get("/research/missions/val-mission-m/meta-analysis?n_bins=0", headers=HDR)
    assert r2.status_code == 400


# ── Edge Discovery (2026-07-31) ───────────────────────────────────────────

def test_missions_meta_analysis_response_includes_new_fields(client):
    _seed_mission_and_trial("val-mission-n", n_complete=25)
    r = client.get("/research/missions/val-mission-n/meta-analysis", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert "cross_trial_consensus" in body
    assert "pooled_breakdown" in body
    assert "opportunity_candidates" in body
    assert len(body["cross_trial_consensus"]) == 16  # fixed claim family size


def test_missions_meta_analysis_response_is_valid_json_even_with_infinite_profit_factor(client):
    # Regression test for the bug this Edge Discovery change could itself
    # introduce if the json_safe() fix on the route handler were skipped:
    # a real bucket with zero losing trades has profit_factor=inf, and a
    # bare `Infinity` token is not valid strict JSON (browsers reject it,
    # even though Python's own json module round-trips it silently).
    from backtest.optimizer import MissionSearchSpace, _ENGINES_IDX_KEY, _INDICATORS_IDX_KEY, _TF_IDX_KEY
    from backtest import mission_runner
    from storage import research_missions

    mission_id = "val-mission-inf"
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),), engine_set_choices=(("nnfx",),),
        indicator_set_choices=((),), risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    research_missions.upsert_mission(
        mission_id=mission_id, name="test-mission", sampler="random", objective_metric="profit_factor",
        symbols=["EURUSD"], n_trials_per_symbol=25, min_trades=1, seed=42,
        search_space=mission_runner._search_space_dict(space), config={}, status="finished",
    )
    for i in range(25):
        research_missions.record_trial(
            mission_id=mission_id, trial_number=i, symbol="EURUSD", state="COMPLETE",
            objective_value=1.2 + i * 0.01,
            params={_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0, "sl_atr_multiplier": 2.0},
            metrics={
                "by_direction_regime_session": {
                    "BUY|RANGING|London": {
                        "trades": 20, "wins": 20, "win_rate": 100.0, "pnl": 500.0,
                        "gross_profit": 500.0, "gross_loss": 0.0, "profit_factor": float("inf"),
                    },
                },
            },
            trades=50, error=None, started_at="t", finished_at="t",
        )

    r = client.get("/research/missions/val-mission-inf/meta-analysis", headers=HDR)
    assert r.status_code == 200
    assert '"Infinity"' in r.text  # sanitized to a JSON string sentinel...
    import re
    assert not re.search(r':\s*Infinity[,}]', r.text)  # ...never a bare, invalid-JSON token
    body = r.json()
    candidate = next(c for c in body["opportunity_candidates"] if c["direction"] == "BUY")
    assert candidate["profit_factor"] == "Infinity"


# ── Feature Mining (2026-07-30) ─────────────────────────────────────────────

def test_missions_feature_mining_requires_auth(client):
    assert client.get("/research/missions/does-not-exist/feature-mining?validation_id=v").status_code == 401


def test_missions_feature_mining_404_unknown_validation(client):
    r = client.get("/research/missions/does-not-exist/feature-mining?validation_id=does-not-exist", headers=HDR)
    assert r.status_code == 404


def test_missions_feature_mining_404_validation_belongs_to_different_mission(client):
    from storage import research_mission_validations

    research_mission_validations.upsert_validation(
        validation_id="v-fm-mismatch", mission_id="val-mission-n", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD", "GBPUSD"], objective_metric="profit_factor", criteria={},
    )
    r = client.get(
        "/research/missions/some-other-mission/feature-mining?validation_id=v-fm-mismatch", headers=HDR,
    )
    assert r.status_code == 404


def test_missions_feature_mining_insufficient_data_shape(client):
    from storage import research_mission_validations

    research_mission_validations.upsert_validation(
        validation_id="v-fm-empty", mission_id="val-mission-o", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    r = client.get("/research/missions/val-mission-o/feature-mining?validation_id=v-fm-empty", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["insufficient_data"] is True
    assert body["mission_id"] == "val-mission-o"
    assert body["validation_id"] == "v-fm-empty"


def test_missions_feature_mining_real_response_shape(client):
    from storage import research_mission_validations

    research_mission_validations.upsert_validation(
        validation_id="v-fm-real", mission_id="val-mission-p", trial_number=0, trial_symbol="EURUSD",
        validation_symbols=["EURUSD"], objective_metric="profit_factor", criteria={},
    )
    fm_blob = {
        "n_trades_total": 40, "n_features_tested": 1, "bonferroni_alpha": 0.05, "insufficient_data": False,
        "associations": [{
            "feature": "regime", "feature_type": "categorical", "n_observed": 40, "overall_win_rate": 0.5,
            "insufficient_data": False, "note": "",
            "bins": [{
                "feature": "regime", "bin_label": "TRENDING", "bin_index": None, "n_trades": 40,
                "win_rate": 0.5, "mean_r": 0.1, "std_r": 1.0, "lift_win_rate": 1.0,
                "p_value": 0.9, "significance": "NOT_SIGNIFICANT",
            }],
        }],
    }
    research_mission_validations.record_validation_result(
        validation_id="v-fm-real", symbol="EURUSD", passed=True,
        metrics={}, monte_carlo={}, walk_forward={}, robustness={},
        criteria_breakdown={}, feature_mining=fm_blob, error=None, started_at="t1", finished_at="t2",
    )
    r = client.get("/research/missions/val-mission-p/feature-mining?validation_id=v-fm-real", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["insufficient_data"] is False
    assert body["mission_id"] == "val-mission-p"
    assert body["validation_id"] == "v-fm-real"
    assert body["n_trades_total"] == 40
    assert len(body["associations"]) == 1
    assert body["associations"][0]["feature"] == "regime"
