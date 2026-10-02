"""tests/test_policy_registry_adversarial.py -- the operator's own
required adversarial proofs for Phase 10 (Policy Registry Enforcement):
concurrent activation races resolved by the real DB-level uniqueness
index (not a Python check), DB-failure fail-closed, and the two
mandatory resolver acceptance scenarios: NO_POLICY (+ configured prod4)
-> NO_TRADE, and a real ACTIVE policy (+ configured prod4) -> the
resolver returns the governed policy, never prod4."""
from __future__ import annotations

import threading

import pytest

from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from backtest import hypothesis_policy as hpol
from backtest import hypothesis_promotion as hp
from backtest import policy_registry as preg
from backtest.hypothesis_live_request import compute_preset_definition_hash
from storage import kill_switch as storage_kill_switch
from storage import hypothesis_factory as hf_storage
from storage import research_matrix as rm_storage

_PROD4_CONFIGURED_ENGINES = {"smc": True, "price_action": True, "nnfx": True, "wyckoff": True}


@pytest.fixture(autouse=True)
def _isolated_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_kill_switch, "STATE_PATH", tmp_path / "kill_switch.json")
    yield


def _granted_single_engine_hypothesis(**overrides) -> tuple[hf.Hypothesis, dict]:
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


# --- adversarial: concurrent activation, same scope, two DIFFERENT policies -


def test_concurrent_activation_of_two_policies_for_the_same_scope_exactly_one_wins():
    h1, grant1 = _granted_single_engine_hypothesis(symbol="EURUSD", engine="wyckoff", engine_version="v2")
    h2, grant2 = _granted_single_engine_hypothesis(symbol="EURUSD", engine="smc", engine_version="v1")
    row1 = _draft_for(h1, symbol="EURUSD", timeframe="H4", regime_profile="TRENDING")
    row2 = _draft_for(h2, symbol="EURUSD", timeframe="H4", regime_profile="TRENDING")
    preg.validate_policy(row1["policy_id"])
    preg.validate_policy(row2["policy_id"])

    results: list[dict] = []
    lock = threading.Lock()

    def _activate(policy_id: str):
        for _ in range(4):
            r = preg.activate_policy(policy_id)
            with lock:
                results.append(r)

    threads = [
        threading.Thread(target=_activate, args=(row1["policy_id"],)) for _ in range(4)
    ] + [
        threading.Thread(target=_activate, args=(row2["policy_id"],)) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    active_ids = {r["policy_id"] for r in results if r["status"] == preg.ACTIVE}
    assert len(active_ids) == 1, f"exactly one policy must ever reach ACTIVE for this scope, got {active_ids}"

    final1 = preg.get_policy(row1["policy_id"])
    final2 = preg.get_policy(row2["policy_id"])
    final_statuses = {final1["status"], final2["status"]}
    assert final_statuses == {preg.ACTIVE, preg.VALIDATED}, (
        "the loser must stay VALIDATED, never silently advance or crash"
    )


# --- adversarial: DB failure -> fail closed ---------------------------------


def test_database_failure_during_validate_propagates_fail_closed():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)

    def boom():
        raise RuntimeError("D1 unreachable")

    import storage.policy_registry as spr
    original = spr.d1_client.d1_connection
    spr.d1_client.d1_connection = boom
    try:
        with pytest.raises(RuntimeError, match="D1 unreachable"):
            preg.validate_policy(row["policy_id"])
    finally:
        spr.d1_client.d1_connection = original

    restored = preg.get_policy(row["policy_id"])
    assert restored["status"] == preg.DRAFT  # never silently advanced


def test_database_failure_during_activate_propagates_fail_closed():
    h, grant = _granted_single_engine_hypothesis()
    row = _draft_for(h)
    preg.validate_policy(row["policy_id"])

    def boom():
        raise RuntimeError("D1 unreachable")

    import storage.policy_registry as spr
    original = spr.d1_client.d1_connection
    spr.d1_client.d1_connection = boom
    try:
        with pytest.raises(RuntimeError, match="D1 unreachable"):
            preg.activate_policy(row["policy_id"])
    finally:
        spr.d1_client.d1_connection = original

    restored = preg.get_policy(row["policy_id"])
    assert restored["status"] == preg.VALIDATED  # never silently advanced to ACTIVE


# --- the two mandatory resolver acceptance scenarios ------------------------


def test_no_policy_plus_configured_prod4_is_no_trade():
    """Mandatory acceptance test #1 (domain-only, no scheduler.py
    touched): zero policies exist at all for this scope; prod4 looks
    fully configured and valid -- the resolver still refuses."""
    result = preg.would_authorize_live_run("EURUSD", "H4", "TRENDING", _PROD4_CONFIGURED_ENGINES)
    assert result["decision"] == "NO_TRADE"
    assert result["reason"] == preg.NO_POLICY


def test_active_policy_plus_configured_prod4_returns_the_governed_policy_never_prod4():
    """Mandatory acceptance test #2: a real ACTIVE policy exists, backed
    by a governed engine ('wyckoff' alone, decision_type SINGLE_ENGINE)
    that has NOTHING to do with the prod4 panel passed alongside it --
    the resolver's own answer carries ONLY the real policy's own
    identity, never anything derived from configured_engines."""
    h, grant = _granted_single_engine_hypothesis(symbol="EURUSD", engine="wyckoff")
    row = _draft_for(h, symbol="EURUSD", timeframe="H4", regime_profile="TRENDING")
    preg.validate_policy(row["policy_id"])
    preg.activate_policy(row["policy_id"])

    result = preg.would_authorize_live_run("EURUSD", "H4", "TRENDING", _PROD4_CONFIGURED_ENGINES)
    assert result["decision"] == "PROCEED_TO_GATE"
    assert result["policy"]["policy_id"] == row["policy_id"]
    assert result["policy"]["engine"] == "wyckoff"
    assert result["policy"]["decision_type"] == "SINGLE_ENGINE"
