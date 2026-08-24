"""tests/test_hypothesis_decision_gate_storage.py -- full integration
tests for backtest.hypothesis_decision_gate.evaluate_live_decision() +
storage/hypothesis_decision_gate.py, against REAL storage.kill_switch
(file-backed, isolated per test via a tmp STATE_PATH) and a REAL Phase
4/5/6 chain through to a genuinely GRANTED policy (Hypothesis Discovery
Engine, Phase 7 — Live Decision Gate, BUILT/TESTED/NOT WIRED)."""
from __future__ import annotations

import pytest

from backtest import hypothesis_decision_gate as gate
from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from backtest import hypothesis_policy as hpol
from backtest import hypothesis_promotion as hp
from storage import hypothesis_decision_gate as gate_storage
from storage import hypothesis_factory as hf_storage
from storage import kill_switch as storage_kill_switch
from storage import research_matrix as rm_storage


@pytest.fixture(autouse=True)
def _isolated_kill_switch(monkeypatch, tmp_path):
    """Every test in this file gets its own kill_switch.json — never the
    real repo file, and never shared across tests."""
    monkeypatch.setattr(storage_kill_switch, "STATE_PATH", tmp_path / "kill_switch.json")
    yield


def _persist_hypothesis(**overrides) -> hf.Hypothesis:
    symbol = overrides.get("symbol", "EURUSD")
    engine = overrides.get("engine", "price_action")
    hyps = hf.generate_hypotheses(
        symbols=[symbol], engines=[engine], timeframes=[overrides.get("timeframe", "H1")],
        risk_presets=[overrides.get("risk_preset", "balanced")],
        engine_versions={engine: (overrides.get("engine_version", "v2"),)},
    )
    h = hyps[0]
    hf_storage.record_hypotheses([h])
    return h


def _granted_identity(**overrides) -> tuple[hf.Hypothesis, dict]:
    """A real Hypothesis -> Mission -> VALIDATED+CONFIRMED Cell ->
    PROMOTED Promotion -> GRANTED Policy, through the real Phase 4-6
    pipeline end to end."""
    h = _persist_hypothesis(**overrides)
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    grant = hpol.grant_policy(promotion["promotion_id"], "alice", "approved for live gate testing")
    return h, grant


def _identity_of(h: hf.Hypothesis) -> dict:
    return {"symbol": h.symbol, "engine": h.engine, "engine_version": h.engine_version, "timeframe": h.timeframe, "risk_preset": h.risk_preset}


# --- the four failure-matrix rows this gate actually decides ---------------


def test_no_policy_is_no_trade():
    result = gate.evaluate_live_decision("EURUSD", "price_action", "v2", "H1", "balanced")
    assert result["decision"] == gate.NO_TRADE
    assert result["policy_lookup_result"] == hpol.NO_POLICY
    assert result["kill_switch_state"] == gate.KILL_SWITCH_INACTIVE


def test_revoked_is_no_trade():
    h, grant = _granted_identity()
    hpol.revoke_policy(grant["event_id"], "bob", "revoked for testing")

    result = gate.evaluate_live_decision(**_identity_of(h))
    assert result["decision"] == gate.NO_TRADE
    assert result["policy_lookup_result"] == hpol.REVOKED


def test_granted_and_kill_switch_inactive_is_proceed():
    h, grant = _granted_identity()

    result = gate.evaluate_live_decision(**_identity_of(h))
    assert result["decision"] == gate.PROCEED
    assert result["policy_lookup_result"] == hpol.GRANTED
    assert result["policy_event_id"] == grant["event_id"]


def test_kill_switch_active_overrides_a_granted_policy():
    h, grant = _granted_identity()
    storage_kill_switch.activate("emergency halt for testing", activated_by="ops")

    result = gate.evaluate_live_decision(**_identity_of(h))
    assert result["decision"] == gate.NO_TRADE
    assert result["kill_switch_state"] == gate.KILL_SWITCH_ACTIVE
    # policy was STILL looked up and recorded -- kill switch wins, but the
    # audit trail is not truncated.
    assert result["policy_lookup_result"] == hpol.GRANTED


def test_kill_switch_inactive_does_not_rescue_a_revoked_policy():
    h, grant = _granted_identity()
    hpol.revoke_policy(grant["event_id"], "bob", "revoked for testing")
    assert storage_kill_switch.is_active() is False

    result = gate.evaluate_live_decision(**_identity_of(h))
    assert result["decision"] == gate.NO_TRADE
    assert result["policy_lookup_result"] == hpol.REVOKED


def test_kill_switch_state_unreadable_fails_closed(tmp_path):
    """A corrupted kill_switch.json must block, exactly like storage.
    kill_switch.get_state()'s own documented fail-closed behavior --
    this gate never weakens that guarantee."""
    h, grant = _granted_identity()
    (tmp_path / "kill_switch.json").write_text("not valid json {{{")

    result = gate.evaluate_live_decision(**_identity_of(h))
    assert result["decision"] == gate.NO_TRADE
    assert result["kill_switch_state"] == gate.KILL_SWITCH_ACTIVE


# --- exact identity, no leakage ---------------------------------------------


def test_a_different_engine_version_is_no_policy_not_granted():
    h, grant = _granted_identity(engine_version="v2")
    result = gate.evaluate_live_decision(h.symbol, h.engine, "v1", h.timeframe, h.risk_preset)
    assert result["decision"] == gate.NO_TRADE
    assert result["policy_lookup_result"] == hpol.NO_POLICY


# --- independence / TOCTOU (operator's strengthened §7) --------------------


def test_each_decision_is_independent_no_authorization_carried_forward():
    h, grant = _granted_identity()

    decision_1 = gate.evaluate_live_decision(**_identity_of(h))
    assert decision_1["decision"] == gate.PROCEED

    hpol.revoke_policy(grant["event_id"], "bob", "operator judgment call")

    decision_2 = gate.evaluate_live_decision(**_identity_of(h))
    assert decision_2["decision"] == gate.NO_TRADE

    assert decision_1["decision_id"] != decision_2["decision_id"]
    history = gate_storage.list_decisions_for_identity(**_identity_of(h))
    assert len(history) == 2  # both independently persisted, neither overwritten


def test_repeated_identical_calls_are_not_deduplicated():
    """Unlike Phase 4/5/6's idempotent ledgers, every live decision call
    is its own real observation -- calling the SAME identity twice with
    NOTHING having changed still produces TWO rows."""
    h, grant = _granted_identity()

    gate.evaluate_live_decision(**_identity_of(h))
    gate.evaluate_live_decision(**_identity_of(h))

    history = gate_storage.list_decisions_for_identity(**_identity_of(h))
    assert len(history) == 2
    assert len({d["decision_id"] for d in history}) == 2


# --- audit trail completeness -----------------------------------------------


def test_audit_trail_is_fully_populated_for_a_granted_decision():
    h, grant = _granted_identity()
    result = gate.evaluate_live_decision(**_identity_of(h))

    assert result["policy_event_id"] == grant["event_id"]
    assert result["policy_seq"] is not None
    assert result["promotion_id"] is not None
    assert result["mission_id"] is not None
    assert result["hypothesis_id"] == h.hypothesis_id
    assert result["research_code_commit"] == "commit-A"


def test_audit_trail_explicitly_records_null_not_omitted_for_no_policy():
    """A NO_POLICY decision still produces a real, queryable row -- the
    absence of a policy is itself recorded, not a missing log line."""
    result = gate.evaluate_live_decision("EURUSD", "price_action", "v2", "H1", "balanced")
    fetched = gate_storage.get_decision(result["decision_id"])

    assert fetched is not None
    assert fetched["policy_lookup_result"] == hpol.NO_POLICY
    assert fetched["policy_event_id"] is None
    assert fetched["policy_seq"] is None
    assert fetched["promotion_id"] is None
    assert fetched["mission_id"] is None
    assert fetched["hypothesis_id"] is None


def test_risk_and_pretrade_verdict_columns_are_always_null_reserved_fields():
    h, grant = _granted_identity()
    result = gate.evaluate_live_decision(**_identity_of(h))
    assert result["risk_verdict"] is None
    assert result["pretrade_limits_verdict"] is None


# --- no mutation of anything upstream ---------------------------------------


def test_evaluate_live_decision_never_mutates_promotion_policy_or_cell():
    h, grant = _granted_identity()
    from storage import hypothesis_policy as hpol_storage
    from storage import hypothesis_promotion as hp_storage

    before_promotion = hp_storage.get_promotion(grant["promotion_id"])
    before_policy_event = hpol_storage.get_policy_event(grant["event_id"])
    before_cell = rm_storage.get_cell(before_promotion["cell_id"])

    gate.evaluate_live_decision(**_identity_of(h))

    assert hp_storage.get_promotion(grant["promotion_id"]) == before_promotion
    assert hpol_storage.get_policy_event(grant["event_id"]) == before_policy_event
    assert rm_storage.get_cell(before_promotion["cell_id"]) == before_cell


# --- indexes -----------------------------------------------------------------


def test_live_decisions_table_indexes_exist(fake_d1):
    gate_storage.list_recent_decisions()  # triggers _init(con)
    idx_names = {r[0] for r in fake_d1.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert {"idx_rld_identity", "idx_rld_decision", "idx_rld_policy_event"} <= idx_names
