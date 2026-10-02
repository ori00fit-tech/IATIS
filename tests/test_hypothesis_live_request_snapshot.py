"""tests/test_hypothesis_live_request_snapshot.py -- tests for the Phase
15D controlled extension of backtest.hypothesis_live_request.
evaluate_live_identity_request() (Phase 8C): the new `decision_snapshot`
return key, its all-or-nothing invariant, the T_D=bar_time (never a
capture-time substitute) contract, and -- critically -- that every
pre-existing field this function already returns is unaffected. This
file deliberately never modifies tests/test_hypothesis_live_request*.py
-- those passing completely unchanged is itself the backward-
compatibility proof the operator's own Governance Decision required."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import hypothesis_factory as hf
from backtest import hypothesis_live_request as hlr
from backtest.hypothesis_execution import HypothesisExecutionError
from storage import hypothesis_factory as hf_storage


def _bundle(**overrides) -> dict:
    base = {
        "name": "Prod4 Confluence Panel", "timeframes": ["H4"],
        "engines": ["smc", "price_action", "nnfx", "wyckoff"], "indicators": [], "context_filters": [],
    }
    base.update(overrides)
    return base


def _persist_confluence_hypothesis(**overrides) -> hf.Hypothesis:
    h = hf.generate_confluence_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], decision_version=overrides.get("decision_version", "v1"),
        bundle=overrides.get("bundle", _bundle()), risk_presets=[overrides.get("risk_preset", "balanced")],
    )[0]
    hf_storage.record_hypotheses([h])
    return h


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


def _passed_report(**overrides) -> dict:
    base = {
        "final_verdict": "NO_TRADE",
        "entry_price": 1.2345, "stop_loss": 1.2300, "take_profit": 1.2450,
        "bar_time": "2026-09-01T00:00:00+00:00",
        "confluence": {"vote": {"winning_bias": "BULLISH"}},
    }
    base.update(overrides)
    return base


# --- decision_snapshot: present with the right shape when confluence passed


def test_decision_snapshot_present_when_confluence_passed(monkeypatch):
    h = _persist_confluence_hypothesis()
    monkeypatch.setattr("main.run_pipeline", lambda config: _passed_report())

    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())

    assert result["decision_snapshot"] == {
        "bar_time": "2026-09-01T00:00:00+00:00", "side": "BUY",
        "entry_price": 1.2345, "stop_loss": 1.2300, "take_profit": 1.2450,
    }


def test_decision_snapshot_side_bearish_maps_to_sell(monkeypatch):
    h = _persist_confluence_hypothesis()
    monkeypatch.setattr(
        "main.run_pipeline",
        lambda config: _passed_report(confluence={"vote": {"winning_bias": "BEARISH"}}),
    )
    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    assert result["decision_snapshot"]["side"] == "SELL"


def test_decision_snapshot_present_for_execute_verdict_too():
    """decision_snapshot's own presence is gated on confluence passing
    (entry_price not None) -- independent of final_verdict, exactly like
    main.py's own report construction (conf.passed, not final_verdict)."""
    h = _persist_confluence_hypothesis()
    import main as main_module
    orig = main_module.run_pipeline
    main_module.run_pipeline = lambda config: _passed_report(final_verdict="EXECUTE")
    try:
        result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    finally:
        main_module.run_pipeline = orig
    assert result["decision_snapshot"] is not None
    assert result["live_verdict"] == "EXECUTE"


# --- decision_snapshot: absent when confluence did not pass ---------------


def test_decision_snapshot_none_when_confluence_did_not_pass(monkeypatch):
    h = _persist_confluence_hypothesis()
    monkeypatch.setattr(
        "main.run_pipeline",
        lambda config: {"final_verdict": "NO_TRADE", "entry_price": None, "stop_loss": None,
                         "take_profit": None, "confluence": {"vote": {}}},
    )
    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    assert result["decision_snapshot"] is None


# --- decision_snapshot: all-or-nothing, never partial ----------------------


def test_raises_when_entry_present_but_side_is_unresolvable(monkeypatch):
    h = _persist_confluence_hypothesis()
    monkeypatch.setattr(
        "main.run_pipeline",
        lambda config: _passed_report(confluence={"vote": {"winning_bias": "NEUTRAL"}}),
    )
    with pytest.raises(HypothesisExecutionError, match="winning_bias"):
        hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())


@pytest.mark.parametrize("missing_field", ["bar_time", "stop_loss", "take_profit"])
def test_raises_when_entry_present_but_a_required_field_is_missing(monkeypatch, missing_field):
    h = _persist_confluence_hypothesis()
    report = _passed_report()
    report[missing_field] = None
    monkeypatch.setattr("main.run_pipeline", lambda config: report)
    with pytest.raises(HypothesisExecutionError, match="structurally inconsistent"):
        hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())


# --- T_D = bar_time, never a wall-clock/capture-time substitute -----------


def test_bar_time_is_taken_verbatim_from_the_report_never_substituted(monkeypatch):
    h = _persist_confluence_hypothesis()
    monkeypatch.setattr(
        "main.run_pipeline", lambda config: _passed_report(bar_time="2020-01-01T00:00:00+00:00"),
    )
    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())
    assert result["decision_snapshot"]["bar_time"] == "2020-01-01T00:00:00+00:00"


# --- backward compatibility: every pre-existing field is untouched --------


def test_decision_snapshot_coexists_with_every_pre_existing_field_unchanged(monkeypatch):
    h = _persist_confluence_hypothesis()
    monkeypatch.setattr("main.run_pipeline", lambda config: _passed_report(final_verdict="NO_TRADE"))
    result = hlr.evaluate_live_identity_request(h.hypothesis_id, _base_config())

    assert result["live_verdict"] == "NO_TRADE"
    assert result["decision"] == "NO_TRADE"
    assert result["gate_result"] is None
    assert result["hypothesis_id"] == h.hypothesis_id
    assert result["request_id"].startswith("LIVE-IDENTITY-REQUEST-")
    assert result["decision_snapshot"] is not None  # coexists, no conflict with any existing field


# --- structural: Phase 8C's own non-negotiable #9 still holds -------------


def test_still_never_imports_execution_modules():
    source = inspect.getsource(hlr)
    body = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    forbidden = ("from execution", "import execution")
    for pattern in forbidden:
        assert pattern not in body, f"hypothesis_live_request unexpectedly references {pattern!r}"
