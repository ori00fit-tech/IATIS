"""tests/test_policy_registry.py -- domain/structural tests for
backtest/policy_registry.py (Hypothesis Discovery Engine, Phase 10 --
Policy Registry Enforcement), through the REAL Phase 4-6 chain to a
genuinely GRANTED (and, for the TOCTOU tests, later REVOKED) Phase 6
policy event."""
from __future__ import annotations

import inspect

import pytest

from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from backtest import hypothesis_policy as hpol
from backtest import hypothesis_promotion as hp
from backtest import policy_registry as preg
from backtest.hypothesis_live_request import compute_preset_definition_hash
from storage import hypothesis_factory as hf_storage
from storage import kill_switch as storage_kill_switch
from storage import research_matrix as rm_storage


@pytest.fixture(autouse=True)
def _isolated_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_kill_switch, "STATE_PATH", tmp_path / "kill_switch.json")
    yield


def _granted_single_engine_hypothesis(**overrides) -> tuple[hf.Hypothesis, dict]:
    """A real Hypothesis -> Mission -> VALIDATED+CONFIRMED Cell ->
    PROMOTED Promotion -> GRANTED Policy, through the real Phase 4-6
    pipeline -- the SAME real GRANT a draft_policy()/validate_policy()
    call re-checks fresh, never a fabricated event."""
    h = hf.generate_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], engines=[overrides.get("engine", "wyckoff")],
        timeframes=[overrides.get("timeframe", "H4")], risk_presets=[overrides.get("risk_preset", "balanced")],
        engine_versions={overrides.get("engine", "wyckoff"): (overrides.get("engine_version", "v2"),)},
    )[0]
    hf_storage.record_hypotheses([h])
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    grant = hpol.grant_policy(promotion["promotion_id"], "alice", "approved for Policy Registry testing")
    return h, grant


def _draft_for(h: hf.Hypothesis, **overrides) -> dict:
    base = dict(
        policy_version="v1", symbol=h.symbol, timeframe=h.timeframe, regime_profile="TRENDING",
        hypothesis_id=h.hypothesis_id, engine=h.engine, engine_version=h.engine_version,
        decision_type=h.decision_type, risk_preset=h.risk_preset,
        risk_definition_hash=compute_preset_definition_hash(h.risk_preset),
    )
    base.update(overrides)
    return preg.draft_policy(**base)


# --- draft_policy -----------------------------------------------------------


def test_draft_policy_requires_an_existing_phase6_event():
    with pytest.raises(preg.PolicyRegistryError, match="no Phase 6 policy event exists"):
        preg.draft_policy(
            policy_version="v1", symbol="EURUSD", timeframe="H4", regime_profile="TRENDING",
            hypothesis_id="SINGLE-ENGINE-HYPOTHESIS-ghost", engine="wyckoff", engine_version="v2",
            decision_type="SINGLE_ENGINE", risk_preset="balanced", risk_definition_hash="hash",
        )


def test_draft_policy_succeeds_against_a_real_granted_event():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    assert row["status"] == preg.DRAFT
    assert row["policy_event_id"] == grant["event_id"]


@pytest.mark.parametrize("bad_regime", ["UNKNOWN", "SIDEWAYS", "", "trending"])
def test_draft_policy_rejects_any_regime_profile_outside_the_valid_set(bad_regime):
    h, grant = _granted_single_engine_hypothesis()
    with pytest.raises(preg.PolicyRegistryError, match="regime_profile"):
        _draft_for(h, regime_profile=bad_regime)


def test_draft_policy_rejects_invalid_decision_type():
    h, grant = _granted_single_engine_hypothesis()
    with pytest.raises(preg.PolicyRegistryError, match="decision_type"):
        _draft_for(h, decision_type="MAJORITY_VOTE")


# --- validate_policy ---------------------------------------------------------


def test_validate_policy_succeeds_when_granted():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    result = preg.validate_policy(row["policy_id"])
    assert result["status"] == preg.VALIDATED


def test_validate_policy_denies_when_grant_was_revoked_before_validation():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    hpol.revoke_policy(grant["event_id"], "bob", "revoked before validation")
    result = preg.validate_policy(row["policy_id"])
    assert result["status"] == preg.DRAFT  # denied -- stays DRAFT, never an exception


def test_validate_policy_unknown_id_raises():
    with pytest.raises(preg.PolicyRegistryError, match="unknown policy_id"):
        preg.validate_policy("POLICY-ghost")


# --- activate_policy / TOCTOU closure ----------------------------------------


def test_activate_policy_succeeds_when_still_granted():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    preg.validate_policy(row["policy_id"])
    result = preg.activate_policy(row["policy_id"])
    assert result["status"] == preg.ACTIVE


def test_activate_policy_denies_when_grant_revoked_between_validate_and_activate():
    """The operator's own required TOCTOU closure: activate_policy() must
    re-check the Phase 6 ledger FRESH, independently of validate_policy()'s
    own earlier check."""
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    preg.validate_policy(row["policy_id"])

    hpol.revoke_policy(grant["event_id"], "bob", "revoked between validate and activate")

    result = preg.activate_policy(row["policy_id"])
    assert result["status"] == preg.VALIDATED  # denied -- never reaches ACTIVE


def test_activate_policy_without_validation_first_is_a_no_op():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    result = preg.activate_policy(row["policy_id"])
    assert result["status"] == preg.DRAFT


# --- revoke_policy ------------------------------------------------------------


def test_revoke_policy_from_every_non_terminal_state():
    for stage, symbol in (("draft", "EURUSD"), ("validated", "GBPUSD"), ("active", "XAUUSD")):
        h2, grant2 = _granted_single_engine_hypothesis(symbol=symbol)
        row = _draft_for(h2)
        if stage in ("validated", "active"):
            preg.validate_policy(row["policy_id"])
        if stage == "active":
            preg.activate_policy(row["policy_id"])
        result = preg.revoke_policy(row["policy_id"], f"revoked from {stage}")
        assert result["status"] == preg.REVOKED


def test_revoke_policy_requires_a_reason():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    with pytest.raises(preg.PolicyRegistryError, match="reason"):
        preg.revoke_policy(row["policy_id"], "")


# --- resolve_eligible_policy --------------------------------------------------


def test_resolve_eligible_policy_no_policy_when_none_active():
    result = preg.resolve_eligible_policy("EURUSD", "H4", "TRENDING")
    assert result["result"] == preg.NO_POLICY
    assert result["policy"] is None


def test_resolve_eligible_policy_eligible_when_one_active():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    preg.validate_policy(row["policy_id"])
    preg.activate_policy(row["policy_id"])

    result = preg.resolve_eligible_policy(h.symbol, h.timeframe, "TRENDING")
    assert result["result"] == preg.ELIGIBLE_POLICY
    assert result["policy"]["policy_id"] == row["policy_id"]


def test_resolve_eligible_policy_unknown_regime_is_no_policy_even_with_an_active_match():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    preg.validate_policy(row["policy_id"])
    preg.activate_policy(row["policy_id"])

    result = preg.resolve_eligible_policy(h.symbol, h.timeframe, "UNKNOWN")
    assert result["result"] == preg.NO_POLICY


def test_resolve_eligible_policy_conflict_via_monkeypatched_storage(monkeypatch):
    """storage.policy_registry.find_active_policy() is structurally
    guaranteed (by the DB's own partial unique index, proven in tests/
    test_policy_registry_storage.py) to never return more than one row
    for a real scope -- this test proves ONLY the resolver's own
    classification logic for that otherwise-impossible case, via a
    monkeypatched storage read, never by actually corrupting the
    database (which the index itself prevents)."""
    monkeypatch.setattr(
        preg.storage_policy, "find_active_policy",
        lambda symbol, timeframe, regime: [{"policy_id": "POLICY-x"}, {"policy_id": "POLICY-y"}],
    )
    result = preg.resolve_eligible_policy("EURUSD", "H4", "TRENDING")
    assert result["result"] == preg.CONFLICT
    assert result["policy"] is None


# --- would_authorize_live_run --------------------------------------------------


_PROD4_CONFIGURED_ENGINES = {"smc": True, "price_action": True, "nnfx": True, "wyckoff": True}


def test_would_authorize_live_run_no_trade_without_a_policy():
    result = preg.would_authorize_live_run("EURUSD", "H4", "TRENDING", _PROD4_CONFIGURED_ENGINES)
    assert result["decision"] == "NO_TRADE"
    assert result["reason"] == preg.NO_POLICY


def test_would_authorize_live_run_proceed_to_gate_with_an_eligible_policy():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    preg.validate_policy(row["policy_id"])
    preg.activate_policy(row["policy_id"])

    result = preg.would_authorize_live_run(h.symbol, h.timeframe, "TRENDING", _PROD4_CONFIGURED_ENGINES)
    assert result["decision"] == "PROCEED_TO_GATE"
    assert result["policy"]["policy_id"] == row["policy_id"]


def test_would_authorize_live_run_conflict_is_no_trade(monkeypatch):
    monkeypatch.setattr(
        preg.storage_policy, "find_active_policy",
        lambda symbol, timeframe, regime: [{"policy_id": "POLICY-x"}, {"policy_id": "POLICY-y"}],
    )
    result = preg.would_authorize_live_run("EURUSD", "H4", "TRENDING", _PROD4_CONFIGURED_ENGINES)
    assert result["decision"] == "NO_TRADE"
    assert result["reason"] == preg.CONFLICT


# --- structural: configured engine != eligible policy -----------------------


def _source_without_module_docstring() -> str:
    source = inspect.getsource(preg)
    return source.split('"""', 2)[-1]


def test_resolver_never_reads_config_or_engines_yaml():
    body = _source_without_module_docstring()
    forbidden = ("load_config(", "config.yaml", "engines.yaml", '"enabled"', "config[\"engines\"]")
    for pattern in forbidden:
        assert pattern not in body, f"backtest.policy_registry unexpectedly references {pattern!r}"


def test_no_enumeration_of_grants_or_hypotheses_in_resolver():
    body = _source_without_module_docstring()
    forbidden = ("list_policy_events", "list_hypotheses(", "for grant in", "for hypothesis in", "for draft in")
    for pattern in forbidden:
        assert pattern not in body, f"backtest.policy_registry unexpectedly references {pattern!r}"


def test_nothing_outside_tests_imports_policy_registry_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "policy_registry" not in text, f"{path} unexpectedly references policy_registry"


def test_would_authorize_live_run_never_touches_config_or_registry_files():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    preg.would_authorize_live_run("EURUSD", "H4", "TRENDING", _PROD4_CONFIGURED_ENGINES)

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p]
