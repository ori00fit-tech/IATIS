"""tests/test_h102_price_action_confluence_ab.py — the pre-registered
verdict logic, pinned to the registry text. Mirrors the H019/H023/H037/
H101 test discipline: only the pure decision function is unit-tested here
(no market data needed); the actual A/B backtest loop needs real price
data for the full 20-symbol universe and only runs on the VPS."""
from __future__ import annotations

from research.experiments.H102_price_action_confluence_ab import (
    DECISION,
    MIN_TOTAL_OOS_TRADES,
    SYMBOLS,
    price_action_verdict,
)


def test_passes_keep_engine_when_removal_hurts():
    # removal_dPF < 0 means removing price_action made things worse —
    # engine should stay (matches H015's finding it's load-bearing).
    v, checks, _ = price_action_verdict(pooled_removal_dpf=-0.10, win_fraction_removal=0.20, pooled_a_n=400)
    assert v.startswith("PASSED")
    assert checks["1_removal_dPF>=0"] is False


def test_fails_demote_when_removal_does_not_hurt_and_favored():
    # Both conditions favor removal -> reject the hypothesis (demote toward disable).
    v, checks, _ = price_action_verdict(pooled_removal_dpf=0.05, win_fraction_removal=0.70, pooled_a_n=400)
    assert v.startswith("FAILED")
    assert all(checks.values())


def test_passes_keep_engine_when_removal_dpf_ok_but_win_fraction_low():
    # removal_dPF non-negative but win-fraction doesn't clear the bar ->
    # not a clean enough case to demote; engine stays (PASSED).
    v, checks, _ = price_action_verdict(pooled_removal_dpf=0.02, win_fraction_removal=0.30, pooled_a_n=400)
    assert v.startswith("PASSED")
    assert checks["2_win_fraction_favoring_removal>=0.60"] is False


def test_boundary_exactly_zero_removal_dpf_counts_as_non_negative():
    v, checks, _ = price_action_verdict(pooled_removal_dpf=0.0, win_fraction_removal=0.60, pooled_a_n=400)
    assert checks["1_removal_dPF>=0"] is True
    assert v.startswith("FAILED")


def test_insufficient_data_below_promotion_criteria_floor():
    v, _, reasons = price_action_verdict(pooled_removal_dpf=0.5, win_fraction_removal=1.0, pooled_a_n=299)
    assert v == "INSUFFICIENT_DATA"
    assert any("299" in r for r in reasons)


def test_exactly_at_min_trades_floor_is_sufficient():
    v, _, _ = price_action_verdict(pooled_removal_dpf=-0.1, win_fraction_removal=0.1, pooled_a_n=MIN_TOTAL_OOS_TRADES)
    assert v != "INSUFFICIENT_DATA"


def test_decision_constants_match_registry_text():
    # research/results/registry.json H102 falsification_criteria: reject
    # only if removal dPF is non-negative AND OOS-win-fraction >=60%
    # favoring removal.
    assert DECISION["max_removal_dPF"] == 0.0
    assert DECISION["min_win_fraction"] == 0.60


def test_full_20_symbol_universe_matches_h101():
    from research.experiments.H101_smc_structural_bias_ab import SYMBOLS as H101_SYMBOLS
    assert SYMBOLS == H101_SYMBOLS
