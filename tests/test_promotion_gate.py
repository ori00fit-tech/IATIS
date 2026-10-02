"""tests/test_promotion_gate.py -- tests for backtest/promotion_gate.py
(Hypothesis Discovery Engine, Phase 13A/13B — Promotion Evidence Contract
+ Promotion Gate), covering the locked 13A table exactly."""
from __future__ import annotations

import inspect

import pytest

from backtest import promotion_gate as pg
from backtest.mission_validator import STRONG_LEAD, WEAK_LEAD


def _evidence(classification: str, mission_verdict: str | None = None) -> dict:
    return {"classification": classification, "reason": "test", "inputs": {
        "significance": "SURVIVES_CORRECTION", "mission_verdict": mission_verdict, "robustness": None,
    }}


_SHADOW_OK = {"completed": True, "diverged_catastrophically": False}
_SHADOW_DIVERGED = {"completed": True, "diverged_catastrophically": True}
_SHADOW_INCOMPLETE = {"completed": False, "diverged_catastrophically": False}
_LIMITED_OK = {"completed": True, "acceptable_performance": True}
_LIMITED_BAD = {"completed": True, "acceptable_performance": False}
_LIMITED_INCOMPLETE = {"completed": False, "acceptable_performance": True}


# --- SHADOW ------------------------------------------------------------


@pytest.mark.parametrize("classification", ["WEAK", "MIXED", "PROMISING", "STRONG"])
def test_shadow_eligible_for_any_positive_classification(classification):
    result = pg.evaluate_promotion_gate(
        target_stage=pg.SHADOW, evidence=_evidence(classification), cross_symbol_confirmed=False,
    )
    assert result["eligibility"] == pg.ELIGIBLE
    assert result["reasons"] == []


@pytest.mark.parametrize("classification", ["REJECTED", "INSUFFICIENT_EVIDENCE"])
def test_shadow_not_eligible_for_negative_classification(classification):
    result = pg.evaluate_promotion_gate(
        target_stage=pg.SHADOW, evidence=_evidence(classification), cross_symbol_confirmed=False,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE


def test_shadow_does_not_require_cross_symbol():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.SHADOW, evidence=_evidence("WEAK"), cross_symbol_confirmed=False,
    )
    assert result["eligibility"] == pg.ELIGIBLE


def test_shadow_eligible_result_carries_the_non_promotion_grade_note():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.SHADOW, evidence=_evidence("STRONG"), cross_symbol_confirmed=False,
    )
    assert "note" in result
    assert "not imply" in result["note"]


def test_shadow_does_not_require_a_prior_shadow_record():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.SHADOW, evidence=_evidence("WEAK"), cross_symbol_confirmed=False, shadow_record=None,
    )
    assert result["eligibility"] == pg.ELIGIBLE


# --- LIMITED -------------------------------------------------------------


def test_limited_eligible_with_everything_satisfied():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.LIMITED, evidence=_evidence("PROMISING"), cross_symbol_confirmed=True,
        shadow_record=_SHADOW_OK,
    )
    assert result["eligibility"] == pg.ELIGIBLE
    assert "note" not in result


@pytest.mark.parametrize("classification", ["WEAK", "REJECTED", "INSUFFICIENT_EVIDENCE"])
def test_limited_not_eligible_below_promising(classification):
    result = pg.evaluate_promotion_gate(
        target_stage=pg.LIMITED, evidence=_evidence(classification), cross_symbol_confirmed=True,
        shadow_record=_SHADOW_OK,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE


def test_limited_requires_cross_symbol():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.LIMITED, evidence=_evidence("STRONG"), cross_symbol_confirmed=False,
        shadow_record=_SHADOW_OK,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert any("CROSS_SYMBOL" in r for r in result["reasons"])


def test_limited_requires_a_completed_shadow_record():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.LIMITED, evidence=_evidence("STRONG"), cross_symbol_confirmed=True, shadow_record=None,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert any("SHADOW record" in r for r in result["reasons"])


def test_limited_requires_shadow_record_be_actually_completed():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.LIMITED, evidence=_evidence("STRONG"), cross_symbol_confirmed=True,
        shadow_record=_SHADOW_INCOMPLETE,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE


def test_limited_rejects_catastrophic_divergence():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.LIMITED, evidence=_evidence("STRONG"), cross_symbol_confirmed=True,
        shadow_record=_SHADOW_DIVERGED,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert any("catastrophic divergence" in r for r in result["reasons"])


# --- ACTIVE ----------------------------------------------------------------


def test_active_eligible_with_everything_satisfied():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("STRONG", mission_verdict=STRONG_LEAD),
        cross_symbol_confirmed=True, limited_review=_LIMITED_OK,
    )
    assert result["eligibility"] == pg.ELIGIBLE


def test_active_requires_strong_classification():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("PROMISING", mission_verdict=STRONG_LEAD),
        cross_symbol_confirmed=True, limited_review=_LIMITED_OK,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE


def test_active_requires_strong_lead_specifically_not_just_strong_classification():
    """The operator's own explicit requirement: STRONG classification
    alone is not sufficient -- mission_verdict must be STRONG_LEAD."""
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("STRONG", mission_verdict=WEAK_LEAD),
        cross_symbol_confirmed=True, limited_review=_LIMITED_OK,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert any("STRONG_LEAD" in r for r in result["reasons"])


def test_active_requires_cross_symbol():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("STRONG", mission_verdict=STRONG_LEAD),
        cross_symbol_confirmed=False, limited_review=_LIMITED_OK,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE


def test_active_requires_a_completed_limited_review():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("STRONG", mission_verdict=STRONG_LEAD),
        cross_symbol_confirmed=True, limited_review=None,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert any("LIMITED review" in r for r in result["reasons"])


def test_active_requires_limited_review_be_actually_completed():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("STRONG", mission_verdict=STRONG_LEAD),
        cross_symbol_confirmed=True, limited_review=_LIMITED_INCOMPLETE,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE


def test_active_rejects_unacceptable_limited_performance():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("STRONG", mission_verdict=STRONG_LEAD),
        cross_symbol_confirmed=True, limited_review=_LIMITED_BAD,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert any("acceptable performance" in r for r in result["reasons"])


def test_active_accumulates_every_failing_reason_not_just_the_first():
    result = pg.evaluate_promotion_gate(
        target_stage=pg.ACTIVE, evidence=_evidence("WEAK", mission_verdict=WEAK_LEAD),
        cross_symbol_confirmed=False, limited_review=None,
    )
    assert result["eligibility"] == pg.NOT_ELIGIBLE
    assert len(result["reasons"]) == 4  # classification, cross_symbol, strong_lead, limited_review


# --- structural --------------------------------------------------------


def test_evaluate_promotion_gate_rejects_unrecognized_target_stage():
    with pytest.raises(pg.PromotionGateError, match="target_stage"):
        pg.evaluate_promotion_gate(target_stage="BOGUS", evidence=_evidence("STRONG"), cross_symbol_confirmed=True)


def _source_without_module_docstring() -> str:
    source = inspect.getsource(pg)
    return source.split('"""', 2)[-1]


def test_no_call_to_policy_registry_or_execution_authorization():
    body = _source_without_module_docstring()
    forbidden = ("activate_policy(", "revoke_policy(", "execution.authorization", "from execution import authorization")
    for pattern in forbidden:
        assert pattern not in body, f"promotion_gate unexpectedly references {pattern!r}"


def test_no_storage_import_or_database_write():
    body = _source_without_module_docstring()
    forbidden = ("from storage", "import storage", "d1_client")
    for pattern in forbidden:
        assert pattern not in body, f"promotion_gate unexpectedly references {pattern!r}"


def test_no_new_lifecycle_state_invented():
    public_names = [n for n in dir(pg) if not n.startswith("_")]
    forbidden_substrings = ("paused", "downgraded")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"promotion_gate unexpectedly exposes {name!r}"


def test_nothing_outside_tests_imports_promotion_gate_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "promotion_gate" not in text, f"{path} unexpectedly references promotion_gate"


def test_is_deterministic_same_inputs_same_output():
    kwargs = dict(
        target_stage=pg.ACTIVE, evidence=_evidence("STRONG", mission_verdict=STRONG_LEAD),
        cross_symbol_confirmed=True, limited_review=_LIMITED_OK,
    )
    assert pg.evaluate_promotion_gate(**kwargs) == pg.evaluate_promotion_gate(**kwargs)
