"""tests/test_shadow_evidence_assembly.py -- tests for backtest/
shadow_evidence_assembly.py (Evidence Assembly, Design Gate locked
2026-10, "Observe Operational Composition"): canonical-cell reuse (NO_
CANONICAL_CELL/CELL_NOT_FOUND/SOURCE_HYPOTHESIS_MISMATCH, imported
verbatim from backtest.hypothesis_baseline_statistic), significance via
stage_a_p_value + family params, mission_verdict via stage_b_verdict,
robustness always None, MISSING_FAMILY_PARAMS, and fail-fast
propagation from resolve_canonical_baseline()."""
from __future__ import annotations

import pytest

from backtest import shadow_evidence_assembly as sea
from backtest.evidence_classification import INSUFFICIENT_EVIDENCE, PROMISING, REJECTED, WEAK
from backtest.hypothesis_baseline import HypothesisBaselineError
from backtest.hypothesis_promotion import BLOCKED, PROMOTED

HID = "CONFLUENCE-HYPOTHESIS-x"


def _promotion(cell_id: str, decision: str, promotion_id: str) -> dict:
    return {"promotion_id": promotion_id, "hypothesis_id": HID, "cell_id": cell_id, "decision": decision}


def _cell(cell_id="C1", source_hypothesis_id=HID, stage_a_p_value=0.001, stage_b_verdict=None) -> dict:
    return {
        "cell_id": cell_id, "source_hypothesis_id": source_hypothesis_id,
        "stage_a_p_value": stage_a_p_value, "stage_b_verdict": stage_b_verdict,
    }


def _family(planned_n=10, family_alpha=0.05) -> dict:
    return {"planned_n": planned_n, "family_alpha": family_alpha}


PROMOTIONS_SINGLE = [_promotion("C1", PROMOTED, "PROMO-1")]


def test_assembled_with_survives_correction_falls_through_to_promising():
    """p=0.001, n=10, alpha=0.05 -> bonferroni_alpha=0.005 -> SURVIVES_CORRECTION.
    mission_verdict=None, robustness=None -> R4/R5 can't match -> PROMISING (R6)."""
    result = sea.assemble_shadow_evidence(
        HID, PROMOTIONS_SINGLE, _cell(stage_a_p_value=0.001), _family(),
    )
    assert result["assembly_state"] == sea.ASSEMBLED
    assert result["evidence"]["classification"] == PROMISING


def test_assembled_nominal_only_caps_at_weak():
    """p=0.03 < family_alpha=0.05 but not < bonferroni_alpha=0.005 -> NOMINAL_ONLY -> WEAK."""
    result = sea.assemble_shadow_evidence(
        HID, PROMOTIONS_SINGLE, _cell(stage_a_p_value=0.03), _family(),
    )
    assert result["assembly_state"] == sea.ASSEMBLED
    assert result["evidence"]["classification"] == WEAK


def test_assembled_not_significant_rejected():
    result = sea.assemble_shadow_evidence(
        HID, PROMOTIONS_SINGLE, _cell(stage_a_p_value=0.9), _family(),
    )
    assert result["assembly_state"] == sea.ASSEMBLED
    assert result["evidence"]["classification"] == REJECTED


def test_assembled_none_stage_a_p_value_is_insufficient_evidence_not_a_guard():
    """stage_a_p_value=None is NOT a MISSING_FAMILY_PARAMS-style assembly
    failure -- classify_significance() already turns it into
    INSUFFICIENT_DATA, which classify_evidence() turns into
    INSUFFICIENT_EVIDENCE. Assembly itself still succeeds."""
    result = sea.assemble_shadow_evidence(
        HID, PROMOTIONS_SINGLE, _cell(stage_a_p_value=None), _family(),
    )
    assert result["assembly_state"] == sea.ASSEMBLED
    assert result["evidence"]["classification"] == INSUFFICIENT_EVIDENCE


def test_mission_verdict_read_verbatim_from_stage_b_verdict():
    result = sea.assemble_shadow_evidence(
        HID, PROMOTIONS_SINGLE, _cell(stage_a_p_value=0.001, stage_b_verdict="SOME_VERDICT"), _family(),
    )
    assert result["evidence"]["inputs"]["mission_verdict"] == "SOME_VERDICT"


def test_robustness_is_always_none():
    result = sea.assemble_shadow_evidence(
        HID, PROMOTIONS_SINGLE, _cell(stage_a_p_value=0.001), _family(),
    )
    assert result["evidence"]["inputs"]["robustness"] is None


# ---------- fail-closed guards -----------------------------------------


def test_no_canonical_cell_when_no_promotion_exists():
    result = sea.assemble_shadow_evidence(HID, [], _cell(), _family())
    assert result == {"assembly_state": sea.NO_CANONICAL_CELL, "evidence": None}


def test_no_canonical_cell_when_multiple_distinct_cells_promoted():
    promotions = [
        _promotion("C1", PROMOTED, "PROMO-1"),
        _promotion("C2", PROMOTED, "PROMO-2"),
    ]
    result = sea.assemble_shadow_evidence(HID, promotions, _cell(), _family())
    assert result == {"assembly_state": sea.NO_CANONICAL_CELL, "evidence": None}


def test_no_canonical_cell_ignores_non_promoted_decisions():
    promotions = [_promotion("C1", BLOCKED, "PROMO-1")]
    result = sea.assemble_shadow_evidence(HID, promotions, _cell(), _family())
    assert result == {"assembly_state": sea.NO_CANONICAL_CELL, "evidence": None}


def test_cell_not_found_when_cell_is_none():
    result = sea.assemble_shadow_evidence(HID, PROMOTIONS_SINGLE, None, _family())
    assert result == {"assembly_state": sea.CELL_NOT_FOUND, "evidence": None}


def test_source_hypothesis_mismatch():
    result = sea.assemble_shadow_evidence(
        HID, PROMOTIONS_SINGLE, _cell(source_hypothesis_id="OTHER-HYPOTHESIS"), _family(),
    )
    assert result == {"assembly_state": sea.SOURCE_HYPOTHESIS_MISMATCH, "evidence": None}


def test_missing_family_params_when_family_is_none():
    result = sea.assemble_shadow_evidence(HID, PROMOTIONS_SINGLE, _cell(), None)
    assert result == {"assembly_state": sea.MISSING_FAMILY_PARAMS, "evidence": None}


def test_missing_family_params_when_planned_n_absent():
    result = sea.assemble_shadow_evidence(HID, PROMOTIONS_SINGLE, _cell(), {"family_alpha": 0.05})
    assert result == {"assembly_state": sea.MISSING_FAMILY_PARAMS, "evidence": None}


def test_missing_family_params_when_family_alpha_absent():
    result = sea.assemble_shadow_evidence(HID, PROMOTIONS_SINGLE, _cell(), {"planned_n": 10})
    assert result == {"assembly_state": sea.MISSING_FAMILY_PARAMS, "evidence": None}


def test_structural_misuse_propagates_uncaught():
    """A promotion whose own hypothesis_id does not match the argument
    raises HypothesisBaselineError from resolve_canonical_baseline() --
    propagated completely unchanged, never caught here."""
    mismatched = [{"promotion_id": "P1", "hypothesis_id": "WRONG-HID", "cell_id": "C1", "decision": PROMOTED}]
    with pytest.raises(HypothesisBaselineError):
        sea.assemble_shadow_evidence(HID, mismatched, _cell(), _family())


# ---------- structural: no scope creep --------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(sea)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_is_isolated_from_io_and_downstream_layers():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "promotion_gate", "shadow_record", "shadow_integration",
        "shadow_divergence_membership", "shadow_effective_sample", "shadow_observe_orchestrator",
        "execution.authorization", "execution.trade_executor", "scheduler", "main.py",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_evidence_assembly unexpectedly references {pattern!r}"
