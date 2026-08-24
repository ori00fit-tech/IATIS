"""tests/test_hypothesis_live_request_integration.py -- end-to-end tests
for backtest.hypothesis_live_request.evaluate_live_identity_request()
(Hypothesis Discovery Engine, Phase 8C — Live Identity Request adapter),
through the REAL Phase 4-6 chain to a genuinely GRANTED CONFLUENCE
policy, with main.run_pipeline() monkeypatched (a real, network/data-
fetching pipeline run is out of scope for a unit test; the adapter's OWN
job — resolving identity, building the governed config, and gating the
result — is what's under test here, not run_pipeline() itself, which is
reused unchanged)."""
from __future__ import annotations

import pytest

from backtest import hypothesis_factory as hf
from backtest import hypothesis_live_request as hlr
from backtest import hypothesis_mission as hm
from backtest import hypothesis_policy as hpol
from backtest import hypothesis_promotion as hp
from storage import hypothesis_factory as hf_storage
from storage import kill_switch as storage_kill_switch
from storage import research_matrix as rm_storage


@pytest.fixture(autouse=True)
def _isolated_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_kill_switch, "STATE_PATH", tmp_path / "kill_switch.json")
    yield


def _bundle(**overrides) -> dict:
    base = {
        "name": "Prod4 Confluence Panel",
        "timeframes": ["H4"],
        "engines": ["smc", "price_action", "nnfx", "wyckoff"],
        "indicators": [],
        "context_filters": [],
    }
    base.update(overrides)
    return base


def _base_config() -> dict:
    return {
        "engines": {"enabled": {"smc": False}, "versions": {}},
        "data": {"symbol": "EURUSD", "timeframes": ["D1", "H4"], "twelve_data_symbols": []},
        "risk": {
            "starting_balance": 10000.0, "max_drawdown_reduce": 0.1, "max_drawdown_stop": 0.15,
            "max_exposure": 0.05, "min_risk_reward": 3.0, "risk_per_trade_max": 0.01,
            "risk_per_trade_min": 0.0025, "sl_atr_multiplier": 2.5,
            "pretrade_limits": {"enabled": True},
        },
        "confluence": {"min_score_to_trade": 60, "min_engines_agreeing": 2},
    }


def _granted_confluence_hypothesis(**overrides) -> tuple[hf.Hypothesis, dict]:
    """A real Hypothesis -> Mission -> VALIDATED+CONFIRMED Cell ->
    PROMOTED Promotion -> GRANTED Policy, through the real Phase 4-6
    pipeline, for a CONFLUENCE hypothesis (Phase 8B)."""
    h = hf.generate_confluence_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], decision_version=overrides.get("decision_version", "v1"),
        bundle=overrides.get("bundle", _bundle()), risk_presets=[overrides.get("risk_preset", "balanced")],
    )[0]
    hf_storage.record_hypotheses([h])
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    grant = hpol.grant_policy(promotion["promotion_id"], "alice", "approved for live gate testing")
    return h, grant


def _granted_single_engine_hypothesis(**overrides) -> tuple[hf.Hypothesis, dict]:
    """The SINGLE_ENGINE counterpart of _granted_confluence_hypothesis():
    a real Hypothesis -> Mission -> VALIDATED+CONFIRMED Cell -> PROMOTED
    Promotion -> GRANTED Policy, through the real Phase 4-6 pipeline, for
    an ordinary (pre-Phase-8B) single-engine hypothesis."""
    engine = overrides.get("engine", "wyckoff")
    h = hf.generate_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], engines=[engine],
        timeframes=[overrides.get("timeframe", "H4")], risk_presets=[overrides.get("risk_preset", "balanced")],
        engine_versions=overrides.get("engine_versions", {engine: ("v2",)}),
    )[0]
    hf_storage.record_hypotheses([h])
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    grant = hpol.grant_policy(promotion["promotion_id"], "alice", "approved for live gate testing")
    return h, grant


def _fake_run_pipeline(verdict: str):
    def _fn(config: dict) -> dict:
        return {"final_verdict": verdict, "seen_config": config}
    return _fn


# --- the operator's required SINGLE_ENGINE scenario (Point 1) --------------


def test_single_engine_wyckoff_v2_only_wyckoff_active_fresh_gate(monkeypatch):
    """The operator's own explicit required test: SINGLE_ENGINE / Wyckoff /
    v2 -> only Wyckoff active -> attributable identity preserved -> fresh
    Gate. Proves the SINGLE_ENGINE path runs through the exact same real
    Phase 4-6-7 chain as CONFLUENCE, with direct (never bundle-derived)
    engine attribution."""
    h, grant = _granted_single_engine_hypothesis(engine="wyckoff", timeframe="H4")
    assert h.decision_type == hf.SINGLE_ENGINE
    assert h.engine_version == "v2"

    captured = {}

    def _capturing_run_pipeline(config):
        captured.update(config)
        return {"final_verdict": "EXECUTE"}

    monkeypatch.setattr("main.run_pipeline", _capturing_run_pipeline)

    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())

    assert result["identity"]["decision_type"] == hf.SINGLE_ENGINE
    assert result["identity"]["engine"] == "wyckoff"
    assert result["identity"]["engines_for_computation"] == ["wyckoff"]
    assert captured["engines"]["enabled"] == {"wyckoff": True}
    assert result["decision"] == "PROCEED"
    assert result["gate_result"]["policy_lookup_result"] == hpol.GRANTED
    # the fresh Gate call used the SAME attributed identity that was
    # resolved -- never a bundle, never CONFLUENCE.
    assert result["gate_result"]["engine"] == "wyckoff"


# --- the positive scenario -------------------------------------------------


def test_granted_identity_with_execute_verdict_proceeds(monkeypatch):
    h, grant = _granted_confluence_hypothesis()
    monkeypatch.setattr("main.run_pipeline", _fake_run_pipeline("EXECUTE"))

    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    assert result["live_verdict"] == "EXECUTE"
    assert result["decision"] == "PROCEED"
    assert result["gate_result"]["policy_lookup_result"] == hpol.GRANTED


def test_live_no_trade_verdict_never_reaches_the_gate(monkeypatch):
    h, grant = _granted_confluence_hypothesis()
    monkeypatch.setattr("main.run_pipeline", _fake_run_pipeline("NO_TRADE"))

    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    assert result["live_verdict"] == "NO_TRADE"
    assert result["decision"] == "NO_TRADE"
    assert result["gate_result"] is None  # the Gate was never asked, not asked-and-blocked


# --- the operator's required negative scenarios -----------------------------


def test_bundle_changed_after_grant_produces_no_trade(monkeypatch):
    """A different bundle composition is a DIFFERENT governed identity
    (different fingerprint, different hypothesis_id) -- there is no
    hypothesis_id representing 'the same request but the bundle changed';
    attempting to resolve one that was never granted correctly fails at
    the Gate as NO_POLICY."""
    h, grant = _granted_confluence_hypothesis(bundle=_bundle())
    other_h = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(engines=["smc", "price_action"]),
    )[0]
    hf_storage.record_hypotheses([other_h])
    monkeypatch.setattr("main.run_pipeline", _fake_run_pipeline("EXECUTE"))

    result = hlr.evaluate_live_identity_request(other_h.hypothesis_id, _base_config())
    assert result["decision"] == "NO_TRADE"
    assert result["gate_result"]["policy_lookup_result"] == hpol.NO_POLICY


def test_decision_version_changed_produces_no_trade(monkeypatch):
    h, grant = _granted_confluence_hypothesis(decision_version="v1")
    other_h = hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v2", bundle=_bundle())[0]
    hf_storage.record_hypotheses([other_h])
    monkeypatch.setattr("main.run_pipeline", _fake_run_pipeline("EXECUTE"))

    result = hlr.evaluate_live_identity_request(other_h.hypothesis_id, _base_config())
    assert result["decision"] == "NO_TRADE"
    assert result["gate_result"]["policy_lookup_result"] == hpol.NO_POLICY


def test_risk_preset_changed_produces_no_trade(monkeypatch):
    h, grant = _granted_confluence_hypothesis(risk_preset="balanced")
    other_h = hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), risk_presets=["aggressive"])[0]
    hf_storage.record_hypotheses([other_h])
    monkeypatch.setattr("main.run_pipeline", _fake_run_pipeline("EXECUTE"))

    result = hlr.evaluate_live_identity_request(other_h.hypothesis_id, _base_config())
    assert result["decision"] == "NO_TRADE"
    assert result["gate_result"]["policy_lookup_result"] == hpol.NO_POLICY


def test_policy_revoked_between_resolution_and_gate_produces_no_trade(monkeypatch):
    """The TOCTOU case: the identity is resolved and the (fake) live
    computation runs BEFORE the revoke; the Gate call afterward reads
    Policy fresh and correctly sees REVOKED."""
    h, grant = _granted_confluence_hypothesis()

    real_run_pipeline = _fake_run_pipeline("EXECUTE")

    def _revoking_run_pipeline(config):
        hpol.revoke_policy(grant["event_id"], "bob", "revoked mid-computation for TOCTOU testing")
        return real_run_pipeline(config)

    monkeypatch.setattr("main.run_pipeline", _revoking_run_pipeline)

    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    assert result["decision"] == "NO_TRADE"
    assert result["gate_result"]["policy_lookup_result"] == hpol.REVOKED


def test_no_explicit_hypothesis_id_is_structurally_impossible():
    with pytest.raises(TypeError):
        hlr.evaluate_live_identity_request(base_config=_base_config())  # type: ignore[call-arg]


def test_asking_for_whatever_is_currently_granted_has_no_code_path():
    """There is no function anywhere in this module shaped like
    'give me a currently-granted identity' -- proven by the fact that
    evaluate_live_identity_request()'s only identity-naming parameter is
    hypothesis_id, and no other public function in this module accepts
    zero identity arguments."""
    import inspect

    for name, fn in vars(hlr).items():
        if not callable(fn) or name.startswith("_") or inspect.getmodule(fn) is not hlr:
            continue
        params = inspect.signature(fn).parameters
        identity_params = {"hypothesis_id"} & set(params)
        pure_config_helpers = {"build_governed_risk_config", "build_governed_config", "compute_preset_definition_hash"}
        assert identity_params or name in pure_config_helpers, (
            f"{name}() has no way to be told WHICH identity to act on — this would be exactly the "
            f"forbidden 'whatever is granted' shape."
        )


# --- kill switch precedence (Phase 7, unchanged, still authoritative) ------


def test_kill_switch_active_overrides_a_granted_and_executed_identity(monkeypatch):
    h, grant = _granted_confluence_hypothesis()
    storage_kill_switch.activate("emergency halt for testing", activated_by="ops")
    monkeypatch.setattr("main.run_pipeline", _fake_run_pipeline("EXECUTE"))

    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    assert result["decision"] == "NO_TRADE"
    from backtest.hypothesis_decision_gate import KILL_SWITCH_ACTIVE

    assert result["gate_result"]["kill_switch_state"] == KILL_SWITCH_ACTIVE


# --- config passed to run_pipeline is genuinely governed --------------------


def test_the_config_passed_to_run_pipeline_reflects_the_governed_identity(monkeypatch):
    h, grant = _granted_confluence_hypothesis(risk_preset="aggressive")
    captured = {}

    def _capturing_run_pipeline(config):
        captured.update(config)
        return {"final_verdict": "NO_TRADE"}

    monkeypatch.setattr("main.run_pipeline", _capturing_run_pipeline)
    hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())

    assert captured["engines"]["enabled"] == {"smc": True, "price_action": True, "nnfx": True, "wyckoff": True}
    assert captured["risk"]["sl_atr_multiplier"] == 1.5
    assert captured["risk"]["min_risk_reward"] == 1.5
    assert captured["risk"]["risk_per_trade_max"] == 0.02
