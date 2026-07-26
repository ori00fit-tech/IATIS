"""tests/test_h101_smc_structural_bias_ab.py — the pre-registered verdict
logic, pinned to the registry text. Mirrors the H019/H023/H037 test
discipline: only the pure decision function is unit-tested here (no
market data needed); the actual A/B backtest loop needs real price data
for the full 20-symbol universe and only runs on the VPS."""
from __future__ import annotations

from research.experiments.H101_smc_structural_bias_ab import (
    DECISION,
    MIN_TOTAL_OOS_TRADES,
    SYMBOLS,
    smc_verdict,
)


def test_passes_when_positive_dpf_and_win_fraction_at_bar():
    v, checks, _ = smc_verdict(pooled_dpf=0.01, win_fraction=0.60, pooled_a_n=400)
    assert v.startswith("PASSED")
    assert all(checks.values())


def test_fails_when_dpf_not_positive_even_with_good_win_fraction():
    v, checks, _ = smc_verdict(pooled_dpf=0.0, win_fraction=0.80, pooled_a_n=400)
    assert v.startswith("FAILED")
    assert checks["1_mean_dPF>0"] is False


def test_fails_when_win_fraction_below_threshold_even_with_positive_dpf():
    v, checks, _ = smc_verdict(pooled_dpf=0.20, win_fraction=0.50, pooled_a_n=400)
    assert v.startswith("FAILED")
    assert checks["2_win_fraction>=0.60"] is False


def test_fails_when_both_negative():
    v, checks, _ = smc_verdict(pooled_dpf=-0.10, win_fraction=0.20, pooled_a_n=400)
    assert v.startswith("FAILED")
    assert checks["1_mean_dPF>0"] is False
    assert checks["2_win_fraction>=0.60"] is False


def test_insufficient_data_below_promotion_criteria_floor():
    v, _, reasons = smc_verdict(pooled_dpf=0.5, win_fraction=1.0, pooled_a_n=299)
    assert v == "INSUFFICIENT_DATA"
    assert any("299" in r for r in reasons)


def test_exactly_at_min_trades_floor_is_sufficient():
    v, _, _ = smc_verdict(pooled_dpf=0.10, win_fraction=0.70, pooled_a_n=MIN_TOTAL_OOS_TRADES)
    assert v != "INSUFFICIENT_DATA"


def test_decision_constants_match_registry_text():
    # research/results/registry.json H101 falsification_criteria: "reject
    # if the SMC-included arm's TEST-slice mean dPF is not positive with
    # OOS-win-fraction >=60%"
    assert DECISION["min_mean_dPF"] == 0.0
    assert DECISION["min_win_fraction"] == 0.60


def test_full_20_symbol_universe_no_duplicates():
    # H015 (RESOLVED): subset selection is universe-dependent noise — the
    # registered question requires the FULL universe, not a subset.
    assert len(SYMBOLS) == 20
    assert len(set(SYMBOLS)) == 20
    assert "GBPJPY" in SYMBOLS  # missing from scripts/full_pipeline_backtest.py's ACTIVE_SYMBOLS
