"""tests/test_evidence_classification.py -- tests for backtest/evidence_
classification.py (Hypothesis Discovery Engine, Phase 12 — Evidence
Classification Layer), covering the locked R1-R7 precedence table
exactly."""
from __future__ import annotations

import inspect

import pytest

from backtest import evidence_classification as ec
from backtest.mission_validator import NO_EDGE, STRONG_LEAD, WEAK_LEAD


def _robustness(*verdicts: str) -> dict:
    return {"sweeps": [{"param": f"p{i}", "verdict": v} for i, v in enumerate(verdicts)]}


# --- aggregate_robustness_verdict -------------------------------------------


def test_aggregate_robustness_verdict_none_when_not_supplied():
    assert ec.aggregate_robustness_verdict(None) == None  # noqa: E711


def test_aggregate_robustness_verdict_none_when_no_sweeps():
    assert ec.aggregate_robustness_verdict({"sweeps": []}) is None


def test_aggregate_robustness_verdict_any_sensitive_wins():
    assert ec.aggregate_robustness_verdict(_robustness("STABLE", "SENSITIVE", "STABLE")) == ec.SENSITIVE


def test_aggregate_robustness_verdict_all_insufficient():
    assert ec.aggregate_robustness_verdict(_robustness("INSUFFICIENT", "INSUFFICIENT")) == ec.INSUFFICIENT


def test_aggregate_robustness_verdict_stable_with_some_insufficient():
    assert ec.aggregate_robustness_verdict(_robustness("STABLE", "INSUFFICIENT")) == ec.STABLE


def test_aggregate_robustness_verdict_all_stable():
    assert ec.aggregate_robustness_verdict(_robustness("STABLE", "STABLE")) == ec.STABLE


# --- R1: INSUFFICIENT_DATA -> INSUFFICIENT_EVIDENCE (sixth value, never a tier) --


def test_r1_insufficient_data_is_a_separate_sixth_value():
    result = ec.classify_evidence(significance=ec.INSUFFICIENT_DATA)
    assert result["classification"] == ec.INSUFFICIENT_EVIDENCE
    assert result["classification"] not in (ec.REJECTED, ec.WEAK, ec.MIXED, ec.PROMISING, ec.STRONG)


def test_r1_wins_even_with_strong_mission_and_stable_robustness():
    """INSUFFICIENT_DATA always wins first -- mission/robustness being
    otherwise favorable never upgrades it to a confident tier."""
    result = ec.classify_evidence(
        significance=ec.INSUFFICIENT_DATA, mission_verdict=STRONG_LEAD,
        robustness=_robustness("STABLE"),
    )
    assert result["classification"] == ec.INSUFFICIENT_EVIDENCE


# --- R2: NOT_SIGNIFICANT -> REJECTED -----------------------------------------


def test_r2_not_significant_is_rejected():
    result = ec.classify_evidence(significance=ec.NOT_SIGNIFICANT)
    assert result["classification"] == ec.REJECTED


def test_r2_wins_even_with_strong_mission_and_stable_robustness():
    result = ec.classify_evidence(
        significance=ec.NOT_SIGNIFICANT, mission_verdict=STRONG_LEAD, robustness=_robustness("STABLE"),
    )
    assert result["classification"] == ec.REJECTED


# --- R3: mission NO_EDGE -> REJECTED (only reachable past R1/R2) ------------


def test_r3_no_edge_rejects_even_a_surviving_significance():
    result = ec.classify_evidence(significance=ec.SURVIVES_CORRECTION, mission_verdict=NO_EDGE)
    assert result["classification"] == ec.REJECTED


def test_r3_no_edge_rejects_even_with_stable_robustness():
    result = ec.classify_evidence(
        significance=ec.SURVIVES_CORRECTION, mission_verdict=NO_EDGE, robustness=_robustness("STABLE"),
    )
    assert result["classification"] == ec.REJECTED


# --- R4: SURVIVES_CORRECTION + STRONG_LEAD + STABLE -> STRONG ---------------


def test_r4_full_strong_combination():
    result = ec.classify_evidence(
        significance=ec.SURVIVES_CORRECTION, mission_verdict=STRONG_LEAD, robustness=_robustness("STABLE"),
    )
    assert result["classification"] == ec.STRONG


@pytest.mark.parametrize("mission_verdict,robustness_verdicts", [
    (WEAK_LEAD, ("STABLE",)),
    (None, ("STABLE",)),
    (STRONG_LEAD, None),
])
def test_r4_requires_all_three_conditions_together(mission_verdict, robustness_verdicts):
    """Missing ANY one of the three R4 conditions must NOT produce
    STRONG -- it falls through to R5/R6 instead."""
    robustness = _robustness(*robustness_verdicts) if robustness_verdicts else None
    result = ec.classify_evidence(
        significance=ec.SURVIVES_CORRECTION, mission_verdict=mission_verdict, robustness=robustness,
    )
    assert result["classification"] != ec.STRONG


# --- R5: SURVIVES_CORRECTION + SENSITIVE -> MIXED ---------------------------


def test_r5_sensitive_robustness_downgrades_to_mixed():
    result = ec.classify_evidence(
        significance=ec.SURVIVES_CORRECTION, mission_verdict=STRONG_LEAD, robustness=_robustness("SENSITIVE"),
    )
    assert result["classification"] == ec.MIXED


def test_r5_sensitive_wins_over_missing_mission():
    result = ec.classify_evidence(significance=ec.SURVIVES_CORRECTION, robustness=_robustness("SENSITIVE"))
    assert result["classification"] == ec.MIXED


# --- R6: SURVIVES_CORRECTION alone -> PROMISING -----------------------------


def test_r6_bare_survives_correction_is_promising():
    result = ec.classify_evidence(significance=ec.SURVIVES_CORRECTION)
    assert result["classification"] == ec.PROMISING


def test_r6_survives_correction_with_weak_lead_is_promising_not_strong():
    result = ec.classify_evidence(significance=ec.SURVIVES_CORRECTION, mission_verdict=WEAK_LEAD)
    assert result["classification"] == ec.PROMISING


def test_r6_survives_correction_with_insufficient_robustness_is_promising():
    result = ec.classify_evidence(
        significance=ec.SURVIVES_CORRECTION, mission_verdict=STRONG_LEAD, robustness=_robustness("INSUFFICIENT"),
    )
    assert result["classification"] == ec.PROMISING


# --- R7: NOMINAL_ONLY -> WEAK, capped regardless of mission/robustness ------


def test_r7_nominal_only_is_weak():
    result = ec.classify_evidence(significance=ec.NOMINAL_ONLY)
    assert result["classification"] == ec.WEAK


def test_r7_nominal_only_capped_at_weak_even_with_strong_signals():
    """The operator's own locked rule: NOMINAL_ONLY can never reach
    MIXED/PROMISING/STRONG no matter how favorable mission/robustness are."""
    result = ec.classify_evidence(
        significance=ec.NOMINAL_ONLY, mission_verdict=STRONG_LEAD, robustness=_robustness("STABLE"),
    )
    assert result["classification"] == ec.WEAK


def test_r7_nominal_only_not_rejected_by_no_edge():
    """NO_EDGE (R3) only rejects when reached -- for NOMINAL_ONLY it IS
    reached (R1/R2 don't match NOMINAL_ONLY), so NO_EDGE still rejects."""
    result = ec.classify_evidence(significance=ec.NOMINAL_ONLY, mission_verdict=NO_EDGE)
    assert result["classification"] == ec.REJECTED


# --- structural / validation --------------------------------------------------


def test_classify_evidence_rejects_unrecognized_significance():
    with pytest.raises(ec.EvidenceClassificationError, match="significance"):
        ec.classify_evidence(significance="BOGUS")


def test_classify_evidence_result_echoes_its_own_inputs_transparently():
    result = ec.classify_evidence(
        significance=ec.SURVIVES_CORRECTION, mission_verdict=STRONG_LEAD, robustness=_robustness("STABLE"),
    )
    assert result["inputs"] == {
        "significance": ec.SURVIVES_CORRECTION, "mission_verdict": STRONG_LEAD, "robustness": ec.STABLE,
    }


def _source_without_module_docstring() -> str:
    source = inspect.getsource(ec)
    return source.split('"""', 2)[-1]


def test_never_calls_classify_significance_or_runs_robustness_or_validates_missions():
    body = _source_without_module_docstring()
    forbidden = ("classify_significance(", "run_robustness(", "run_robustness_suite(", "validate_mission(")
    for pattern in forbidden:
        assert pattern not in body, f"evidence_classification unexpectedly calls {pattern!r}"


def test_no_import_of_promotion_policy_authorization_or_storage():
    body = _source_without_module_docstring()
    forbidden = ("hypothesis_promotion", "policy_registry", "execution.authorization",
                 "from storage", "import storage")
    for pattern in forbidden:
        assert pattern not in body, f"evidence_classification unexpectedly references {pattern!r}"


def test_nothing_outside_tests_imports_evidence_classification_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "evidence_classification" not in text, f"{path} unexpectedly references evidence_classification"
