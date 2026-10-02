"""tests/test_shadow_observation.py -- tests for backtest/
shadow_observation.py (Hypothesis Discovery Engine, Phase 15A — SHADOW
Observation Cycle): run_shadow_cycle()'s own "skip this cycle only, never
mutate the Roster" discipline, the unmodified wiring into backtest.
hypothesis_live_request.evaluate_live_identity_request(), the locked
4-field SHADOW observed profile (decision/verdict frequency only -- never
performance statistics), and structural independence from promotion_gate/
policy_health/execution internals."""
from __future__ import annotations

import inspect

import pytest

from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from backtest import hypothesis_policy as hpol
from backtest import hypothesis_promotion as hp
from backtest import live_roster as lr
from backtest import shadow_observation as so
from backtest.hypothesis_execution import HypothesisExecutionError
from backtest.promotion_gate import ELIGIBLE, NOT_ELIGIBLE, SHADOW
from storage import hypothesis_factory as hf_storage
from storage import hypothesis_live_request as storage_live_request
from storage import kill_switch as storage_kill_switch
from storage import research_matrix as rm_storage


@pytest.fixture(autouse=True)
def _isolated_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_kill_switch, "STATE_PATH", tmp_path / "kill_switch.json")
    yield


def _shadow_eligible(**overrides) -> dict:
    base = {"eligibility": ELIGIBLE, "target_stage": SHADOW, "reasons": []}
    base.update(overrides)
    return base


def _bundle(**overrides) -> dict:
    base = {
        "name": "Prod4 Confluence Panel", "timeframes": ["H4"],
        "engines": ["smc", "price_action", "nnfx", "wyckoff"], "indicators": [], "context_filters": [],
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


def _granted_confluence_hypothesis(**overrides) -> hf.Hypothesis:
    """A real Hypothesis -> Mission -> VALIDATED+CONFIRMED Cell ->
    PROMOTED Promotion -> GRANTED Policy, through the real Phase 4-6
    pipeline -- the same fixture shape test_hypothesis_live_request_
    integration.py already established, reused here so run_shadow_
    cycle()'s own wiring into evaluate_live_identity_request() is proven
    against the real chain, not a stub."""
    h = hf.generate_confluence_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], decision_version=overrides.get("decision_version", "v1"),
        bundle=overrides.get("bundle", _bundle()), risk_presets=[overrides.get("risk_preset", "balanced")],
    )[0]
    hf_storage.record_hypotheses([h])
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    promotion = hp.record_promotion(h.hypothesis_id, result["mission_id"], cell_id)
    hpol.grant_policy(promotion["promotion_id"], "alice", "approved for SHADOW cycle testing")
    return h


def _roster_entry_for(hypothesis_id: str) -> dict:
    added = lr.add_to_roster(
        hypothesis_id=hypothesis_id, promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="SHADOW-eligible per fresh gate",
    )
    return added["roster_entry"]


# --- run_shadow_cycle: eligible entries are observed -----------------------


def test_run_shadow_cycle_calls_evaluate_live_identity_request_for_eligible_entries(monkeypatch):
    h = _granted_confluence_hypothesis()
    roster_entry = _roster_entry_for(h.hypothesis_id)
    monkeypatch.setattr("main.run_pipeline", lambda config: {"final_verdict": "EXECUTE"})

    results = so.run_shadow_cycle(
        entries=[{"roster_entry": roster_entry, "fresh_promotion_gate_result": _shadow_eligible()}],
        base_config=_base_config(),
    )

    assert len(results) == 1
    assert results[0]["observed"] is True
    assert results[0]["hypothesis_id"] == h.hypothesis_id
    assert results[0]["live_identity_request"]["live_verdict"] == "EXECUTE"
    # a real record was persisted via the unmodified Phase 8C producer
    history = storage_live_request.list_live_identity_requests_for_hypothesis(h.hypothesis_id)
    assert len(history) == 1


def test_run_shadow_cycle_never_mutates_the_roster_for_an_observed_entry(monkeypatch):
    h = _granted_confluence_hypothesis()
    roster_entry = _roster_entry_for(h.hypothesis_id)
    monkeypatch.setattr("main.run_pipeline", lambda config: {"final_verdict": "NO_TRADE"})

    so.run_shadow_cycle(
        entries=[{"roster_entry": roster_entry, "fresh_promotion_gate_result": _shadow_eligible()}],
        base_config=_base_config(),
    )

    from storage import live_roster as storage_roster
    unchanged = storage_roster.get_roster_entry(roster_entry["roster_entry_id"])
    assert unchanged["status"] == storage_roster.ACTIVE


# --- run_shadow_cycle: ineligible entries are skipped, roster untouched ----


def test_run_shadow_cycle_skips_entries_that_fail_the_fresh_eligibility_check():
    h = _granted_confluence_hypothesis()
    roster_entry = _roster_entry_for(h.hypothesis_id)
    stale_gate_result = {"eligibility": NOT_ELIGIBLE, "target_stage": SHADOW, "reasons": ["evidence degraded"]}

    results = so.run_shadow_cycle(
        entries=[{"roster_entry": roster_entry, "fresh_promotion_gate_result": stale_gate_result}],
        base_config=_base_config(),
    )

    assert results[0]["observed"] is False
    assert "degraded" in results[0]["reason"]
    assert results[0]["live_identity_request"] is None
    # no record was ever written for a skipped entry
    assert storage_live_request.list_live_identity_requests_for_hypothesis(h.hypothesis_id) == []


def test_run_shadow_cycle_skips_removed_roster_entries_without_reactivating_them():
    h = _granted_confluence_hypothesis()
    roster_entry = _roster_entry_for(h.hypothesis_id)
    removed = lr.remove_from_roster(
        roster_entry_id=roster_entry["roster_entry_id"], removed_by="bob", removed_reason="gone",
    )

    results = so.run_shadow_cycle(
        entries=[{"roster_entry": removed, "fresh_promotion_gate_result": _shadow_eligible()}],
        base_config=_base_config(),
    )

    assert results[0]["observed"] is False
    from storage import live_roster as storage_roster
    still_removed = storage_roster.get_roster_entry(roster_entry["roster_entry_id"])
    assert still_removed["status"] == storage_roster.REMOVED  # never reactivated


# --- run_shadow_cycle: ordering, multiple entries, propagation -------------


def test_run_shadow_cycle_returns_one_result_per_entry_in_order(monkeypatch):
    h1 = _granted_confluence_hypothesis(symbol="EURUSD")
    h2 = _granted_confluence_hypothesis(symbol="GBPUSD")
    entry1 = {"roster_entry": _roster_entry_for(h1.hypothesis_id), "fresh_promotion_gate_result": _shadow_eligible()}
    entry2 = {"roster_entry": _roster_entry_for(h2.hypothesis_id),
              "fresh_promotion_gate_result": {"eligibility": NOT_ELIGIBLE, "target_stage": SHADOW, "reasons": ["x"]}}
    monkeypatch.setattr("main.run_pipeline", lambda config: {"final_verdict": "NO_TRADE"})

    results = so.run_shadow_cycle(entries=[entry1, entry2], base_config=_base_config())
    assert [r["hypothesis_id"] for r in results] == [h1.hypothesis_id, h2.hypothesis_id]
    assert results[0]["observed"] is True
    assert results[1]["observed"] is False


def test_run_shadow_cycle_propagates_structural_errors_unmodified():
    """evaluate_live_identity_request() raises HypothesisExecutionError
    for an unknown hypothesis_id -- a structural problem, never silently
    absorbed into an ordinary {"observed": False} outcome."""
    ghost_roster_entry = _roster_entry_for("CONFLUENCE-HYPOTHESIS-ghost-does-not-exist")
    with pytest.raises(HypothesisExecutionError, match="unknown hypothesis_id"):
        so.run_shadow_cycle(
            entries=[{"roster_entry": ghost_roster_entry, "fresh_promotion_gate_result": _shadow_eligible()}],
            base_config=_base_config(),
        )


# --- compute_shadow_observed_profile: the locked 4-field contract ----------


def _seed_request(hypothesis_id: str, *, live_verdict: str | None, decision: str) -> None:
    storage_live_request.record_live_identity_request(
        hypothesis_id=hypothesis_id, decision_type="SINGLE_ENGINE", symbol="EURUSD", engine="wyckoff",
        engine_version="v2", timeframe="H4", risk_preset="balanced", preset_definition_hash="hash",
        risk_parameters_used_json="{}", decision=decision, decision_reason="seeded for test",
        live_verdict=live_verdict,
    )


def test_compute_shadow_observed_profile_returns_exactly_the_locked_four_fields():
    _seed_request("H-profile-1", live_verdict="EXECUTE", decision="PROCEED")
    profile = so.compute_shadow_observed_profile("H-profile-1", window=10)
    assert set(profile) == {"request_count", "execute_verdict_count", "proceed_count", "no_trade_count"}


def test_compute_shadow_observed_profile_counts_correctly():
    hyp = "H-profile-2"
    _seed_request(hyp, live_verdict="EXECUTE", decision="PROCEED")
    _seed_request(hyp, live_verdict="EXECUTE", decision="NO_TRADE")  # EXECUTE verdict, but gate blocked it
    _seed_request(hyp, live_verdict="NO_TRADE", decision="NO_TRADE")

    profile = so.compute_shadow_observed_profile(hyp, window=10)
    assert profile["request_count"] == 3
    assert profile["execute_verdict_count"] == 2
    assert profile["proceed_count"] == 1
    assert profile["no_trade_count"] == 2


def test_compute_shadow_observed_profile_returns_zeros_for_no_history():
    profile = so.compute_shadow_observed_profile("H-profile-never-requested", window=10)
    assert profile == {
        "request_count": 0, "execute_verdict_count": 0, "proceed_count": 0, "no_trade_count": 0,
    }


def test_compute_shadow_observed_profile_window_is_a_count_not_a_time_range():
    hyp = "H-profile-window"
    for _ in range(5):
        _seed_request(hyp, live_verdict="EXECUTE", decision="PROCEED")
    profile = so.compute_shadow_observed_profile(hyp, window=3)
    assert profile["request_count"] == 3  # only the 3 most recent, never all 5


def test_compute_shadow_observed_profile_never_returns_performance_fields():
    _seed_request("H-profile-neg", live_verdict="EXECUTE", decision="PROCEED")
    profile = so.compute_shadow_observed_profile("H-profile-neg", window=10)
    forbidden = ("trade_count", "win_rate", "profit_factor", "max_drawdown", "execution_slippage")
    for field in forbidden:
        assert field not in profile


# --- structural: dependency direction + no fallback/forbidden coupling -----


def _source_without_module_docstring() -> str:
    """Strips EVERY triple-quoted string (module AND per-function
    docstrings) before scanning -- several of this module's own
    docstrings legitimately explain, in prose, a call it must never make
    (e.g. 'never calls evaluate_promotion_gate() itself'), which would
    otherwise trip a bare-substring structural check against the prose
    itself rather than real code."""
    import re
    source = inspect.getsource(so)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_promotion_gate_policy_health_or_execution_import():
    body = _source_without_module_docstring()
    forbidden = (
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from execution", "import execution",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_observation unexpectedly references {pattern!r}"


def test_never_calls_remove_from_roster_or_try_remove():
    body = _source_without_module_docstring()
    assert "remove_from_roster(" not in body
    assert "try_remove(" not in body


def test_never_calls_assess_policy_health():
    body = _source_without_module_docstring()
    assert "assess_policy_health(" not in body


def test_never_enumerates_the_roster_or_calls_evaluate_promotion_gate_itself():
    body = _source_without_module_docstring()
    assert "list_active_entries(" not in body
    assert "evaluate_promotion_gate(" not in body


def test_no_clock_sleep_or_scheduling_logic():
    body = _source_without_module_docstring()
    forbidden = ("time.sleep", "import time", "import schedule", "datetime.now(", "cron")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_observation unexpectedly references {pattern!r}"
