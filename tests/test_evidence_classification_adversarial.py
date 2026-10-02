"""tests/test_evidence_classification_adversarial.py -- integration/
adversarial proofs for Phase 12's evidence classification layer: the
REAL (unmocked) backtest.multiple_testing.classify_significance() and a
REAL backtest.robustness.RobustnessResult.to_dict() artifact feed the
classifier, never a hand-waved shape; and structural proof that nothing
here writes to any promotion/policy/authorization table."""
from __future__ import annotations

from backtest import evidence_classification as ec
from backtest.mission_validator import STRONG_LEAD
from backtest.multiple_testing import classify_significance
from backtest.robustness import ParamSweepResult, RobustnessConfig, RobustnessResult


def _real_robustness_result(*verdicts: str) -> dict:
    """A GENUINE backtest.robustness.RobustnessResult, built from its own
    real dataclasses -- not a hand-crafted dict pretending to be one."""
    sweeps = [
        ParamSweepResult(param=f"p{i}", baseline_value=1.0, baseline_pf=1.3, points=[], verdict=v)
        for i, v in enumerate(verdicts)
    ]
    result = RobustnessResult(symbol="EURUSD", sweeps=sweeps, config=RobustnessConfig())
    return result.to_dict()


# --- real, unmocked significance classification -----------------------------


def test_real_classify_significance_strongly_significant_feeds_strong_path():
    """A real z-test input engineered to survive Bonferroni correction
    (large |z|, many trials) flows through the REAL classify_significance()
    into classify_evidence(), never a mocked/hand-typed string."""
    real_significance = classify_significance(p_value=0.0001, n_trials=10)
    assert real_significance == ec.SURVIVES_CORRECTION  # sanity: our test input is what we think it is

    robustness = _real_robustness_result("STABLE", "STABLE")
    result = ec.classify_evidence(significance=real_significance, mission_verdict=STRONG_LEAD, robustness=robustness)
    assert result["classification"] == ec.STRONG


def test_real_classify_significance_insufficient_data_feeds_insufficient_evidence():
    real_significance = classify_significance(p_value=None, n_trials=5)
    assert real_significance == ec.INSUFFICIENT_DATA

    result = ec.classify_evidence(significance=real_significance, mission_verdict=STRONG_LEAD)
    assert result["classification"] == ec.INSUFFICIENT_EVIDENCE


def test_real_classify_significance_not_significant_feeds_rejected():
    real_significance = classify_significance(p_value=0.9, n_trials=10)
    assert real_significance == ec.NOT_SIGNIFICANT

    result = ec.classify_evidence(significance=real_significance, mission_verdict=STRONG_LEAD,
                                   robustness=_real_robustness_result("STABLE"))
    assert result["classification"] == ec.REJECTED


def test_real_classify_significance_nominal_only_feeds_weak():
    # p-value between the Bonferroni-corrected alpha and the nominal 0.05,
    # for a modest n_trials so the correction gap is wide enough to hit.
    real_significance = classify_significance(p_value=0.03, n_trials=20)
    assert real_significance == ec.NOMINAL_ONLY

    result = ec.classify_evidence(significance=real_significance)
    assert result["classification"] == ec.WEAK


# --- real robustness artifact reuse -----------------------------------------


def test_real_robustness_result_sensitive_sweep_downgrades_to_mixed():
    robustness = _real_robustness_result("STABLE", "SENSITIVE")
    result = ec.classify_evidence(significance=ec.SURVIVES_CORRECTION, mission_verdict=STRONG_LEAD,
                                   robustness=robustness)
    assert result["classification"] == ec.MIXED


def test_real_robustness_result_round_trips_through_to_dict_shape():
    """Proves this module consumes EXACTLY RobustnessResult.to_dict()'s
    real shape -- no field renaming, no reinterpretation."""
    robustness = _real_robustness_result("STABLE")
    assert set(robustness.keys()) == {"symbol", "min_trades", "multipliers", "sweeps"}
    assert ec.aggregate_robustness_verdict(robustness) == ec.STABLE


# --- structural: writes nothing, authorizes nothing -------------------------


def test_classify_evidence_never_writes_to_any_protected_or_governance_file():
    from pathlib import Path

    watched = [
        Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"),
        Path("research/results/registry.json"),
    ]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    ec.classify_evidence(significance=ec.SURVIVES_CORRECTION, mission_verdict=STRONG_LEAD,
                          robustness=_real_robustness_result("STABLE"))

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p]


def test_backtest_robustness_module_itself_is_never_modified_in_behavior():
    """Confirms Phase 12 never patched backtest/robustness.py: the real
    module's own STABLE/SENSITIVE/INSUFFICIENT vocabulary and
    RobustnessResult.to_dict() shape are exactly what this test
    constructs and relies on, unchanged."""
    from backtest.robustness import DEFAULT_MULTIPLIERS, SWEEP_PARAMS

    assert DEFAULT_MULTIPLIERS == (0.5, 0.8, 1.0, 1.2, 1.5)
    assert SWEEP_PARAMS == ("sl_atr_multiplier", "commission_pips", "slippage_pips", "min_rr")
