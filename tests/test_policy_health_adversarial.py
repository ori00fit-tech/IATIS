"""tests/test_policy_health_adversarial.py -- adversarial/scenario proofs
for Phase 14 (Policy Health Classification): boundary exactness,
improvement-never-flags asymmetry, the roadmap's own dramatic frequency-
explosion example, and structural independence from Phase 12/13's own
modules (Phase 14 is a fully separate governance contract, not an
extension of either)."""
from __future__ import annotations

import inspect

from backtest import policy_health as ph


def _expected(**overrides) -> dict:
    base = {
        "trade_count": 20, "profit_factor": 1.5, "max_drawdown": 0.10,
        "win_rate": 0.55, "execution_slippage": 0.5,
    }
    base.update(overrides)
    return base


# --- boundary exactness: comparisons are strict, not inclusive -------------


def test_profit_factor_exactly_at_soft_boundary_is_not_yet_flagged():
    """observed == expected * 0.85 exactly -- the locked contract uses
    strict '<', so the boundary value itself is still clean."""
    expected = _expected(profit_factor=1.0)
    result = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(profit_factor=0.85))
    assert result["soft_fields"] == [] and result["hard_fields"] == []


def test_profit_factor_just_below_soft_boundary_is_flagged():
    expected = _expected(profit_factor=1.0)
    result = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(profit_factor=0.8499))
    assert result["soft_fields"] == ["profit_factor"]


def test_max_drawdown_exactly_at_hard_boundary_is_only_soft():
    expected = _expected(max_drawdown=0.10)
    result = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(max_drawdown=0.15))
    assert result["hard_fields"] == [] and result["soft_fields"] == ["max_drawdown"]


# --- improvement never flags anything (asymmetry proof) --------------------


def test_profit_factor_and_win_rate_far_above_expected_never_flag():
    result = ph.compare_profiles(
        expected_profile=_expected(), observed_profile=_expected(profit_factor=5.0, win_rate=0.95),
    )
    assert result["hard_fields"] == [] and result["soft_fields"] == []


def test_drawdown_and_slippage_far_below_expected_never_flag():
    result = ph.compare_profiles(
        expected_profile=_expected(), observed_profile=_expected(max_drawdown=0.01, execution_slippage=0.01),
    )
    assert result["hard_fields"] == [] and result["soft_fields"] == []


def test_fully_improved_snapshot_is_healthy():
    result = ph.assess_policy_health(
        expected_profile=_expected(),
        observed_profile=_expected(profit_factor=3.0, win_rate=0.80, max_drawdown=0.02,
                                    execution_slippage=0.1, trade_count=20),
    )
    assert result["status"] == ph.HEALTHY


# --- the roadmap's own dramatic example: 8 -> 40 trades/month --------------


def test_frequency_explosion_8_to_40_is_hard_deviation():
    expected = _expected(trade_count=8)
    result = ph.compare_profiles(expected_profile=expected, observed_profile=_expected(trade_count=40))
    assert "trade_count" in result["hard_fields"]


def test_frequency_collapse_is_symmetric_to_explosion():
    expected = _expected(trade_count=40)
    exploded_equivalent_collapse = ph.compare_profiles(
        expected_profile=expected, observed_profile=_expected(trade_count=5),
    )
    assert "trade_count" in exploded_equivalent_collapse["hard_fields"]


# --- all five fields hard simultaneously -> PAUSED with full reasons -------


def test_catastrophic_snapshot_all_five_fields_hard():
    observed = _expected(profit_factor=0.5, win_rate=0.1, max_drawdown=0.5, execution_slippage=2.0, trade_count=100)
    result = ph.assess_policy_health(expected_profile=_expected(), observed_profile=observed)
    assert result["status"] == ph.PAUSED
    assert len(result["hard_fields"]) == 5
    assert len(result["reasons"]) == 5


# --- compare_profiles() and assess_policy_health() compose consistently ----


def test_assess_policy_health_is_built_directly_on_compare_profiles():
    expected, observed = _expected(), _expected(profit_factor=0.9, win_rate=0.30)
    comparison = ph.compare_profiles(expected_profile=expected, observed_profile=observed)
    health = ph.assess_policy_health(expected_profile=expected, observed_profile=observed)
    assert health["hard_fields"] == comparison["hard_fields"]
    assert health["soft_fields"] == comparison["soft_fields"]
    assert health["reasons"] == comparison["reasons"]


# --- structural: no coupling with Phase 12/13's own modules -----------------


def test_no_coupling_with_evidence_classification_or_promotion_modules():
    """Phase 14 is a fully separate governance contract -- it must never
    import backtest.evidence_classification, backtest.promotion_gate, or
    backtest.promotion_monitor; conflating 'statistical evidence degraded'
    (Phase 13C) with 'live behavior deviated from expectation' (Phase 14)
    would blur two deliberately distinct concerns."""
    source = inspect.getsource(ph)
    body = source.split('"""', 2)[-1]
    forbidden = ("evidence_classification", "promotion_gate", "promotion_monitor", "classify_evidence(",
                 "evaluate_promotion_gate(", "assess_evidence_staleness(")
    for pattern in forbidden:
        assert pattern not in body, f"policy_health unexpectedly references {pattern!r}"


def test_module_exposes_no_persistence_or_pause_action():
    public_names = [n for n in dir(ph) if not n.startswith("_")]
    forbidden_substrings = ("revoke", "activate", "persist", "save", "write")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"policy_health unexpectedly exposes {name!r}"
