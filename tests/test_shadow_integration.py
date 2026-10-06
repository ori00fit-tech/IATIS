"""tests/test_shadow_integration.py -- tests for backtest/
shadow_integration.py (the Membership Integration orchestrator,
operator's own locked Design Gate, 2026-10, decision C): ONE
evaluate_shadow_evidence() call feeding BOTH compose_shadow_record()
(always) and record_family_membership_if_attempted() (only when the
four-condition precondition holds), with no duplicated network
evaluation and no copied completion formula."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backtest import shadow_integration as si

HID = "CONFLUENCE-HYPOTHESIS-x"


def _evidence(**overrides) -> dict:
    base = dict(
        hypothesis_id=HID, n_t=52, tp_count=28, sl_count=12, p_value=0.0004,
        divergence_statistic_valid=True, baseline_p=0.65, baseline_failure_reason=None,
        request_ids=["R1", "R2"], canonical_identity_state="CANONICAL_IDENTITY_RESOLVED",
        hypothesis_fingerprint="FP-x", research_code_commit="abc123",
    )
    base.update(overrides)
    return base


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_evaluate_shadow_evidence_called_exactly_once(mock_evaluate, mock_membership):
    mock_evaluate.return_value = _evidence()
    mock_membership.return_value = {"hypothesis_id": HID}

    si.evaluate_and_record_shadow(
        HID, base_config={"k": "v"}, promotions=[{"p": 1}], cell={"c": 1},
        validation_results=[{"v": 1}], api_key="secret",
    )
    mock_evaluate.assert_called_once_with(
        HID, base_config={"k": "v"}, promotions=[{"p": 1}], cell={"c": 1},
        validation_results=[{"v": 1}], api_key="secret",
    )


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_shadow_record_is_always_returned(mock_evaluate, mock_membership):
    mock_evaluate.return_value = _evidence(n_t=100, divergence_statistic_valid=True)
    mock_membership.return_value = {"hypothesis_id": HID}

    result = si.evaluate_and_record_shadow(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    # DIVERGENCE_VERDICT_COMPUTED is hardcoded False -> completed is always False today
    assert result["shadow_record"] == {"completed": False, "diverged_catastrophically": False}


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_membership_called_when_all_four_conditions_met(mock_evaluate, mock_membership):
    mock_evaluate.return_value = _evidence()
    mock_membership.return_value = {"hypothesis_id": HID}

    result = si.evaluate_and_record_shadow(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    mock_membership.assert_called_once_with(
        HID, n_t=52, p_value=0.0004, baseline_p=0.65, tp_count=28, sl_count=12,
        request_ids=["R1", "R2"], hypothesis_fingerprint="FP-x", research_code_commit="abc123",
    )
    assert result["membership"] == {"hypothesis_id": HID}


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_membership_not_called_when_identity_not_resolved(mock_evaluate, mock_membership):
    mock_evaluate.return_value = _evidence(
        canonical_identity_state="IDENTITY_MISMATCH", hypothesis_fingerprint=None, research_code_commit=None,
    )
    result = si.evaluate_and_record_shadow(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    mock_membership.assert_not_called()
    assert result["membership"] is None
    # shadow_record is unaffected by the identity failure
    assert result["shadow_record"] == {"completed": False, "diverged_catastrophically": False}


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_membership_not_called_when_no_canonical_cell(mock_evaluate, mock_membership):
    mock_evaluate.return_value = _evidence(
        canonical_identity_state="NO_CANONICAL_CELL", hypothesis_fingerprint=None, research_code_commit=None,
    )
    si.evaluate_and_record_shadow(HID, base_config={}, promotions=[], cell=None, validation_results=[])
    mock_membership.assert_not_called()


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_membership_not_called_when_n_t_below_forty(mock_evaluate, mock_membership):
    mock_evaluate.return_value = _evidence(n_t=39)
    si.evaluate_and_record_shadow(HID, base_config={}, promotions=[], cell=None, validation_results=[])
    mock_membership.assert_not_called()


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_membership_not_called_when_p_value_none(mock_evaluate, mock_membership):
    mock_evaluate.return_value = _evidence(p_value=None)
    si.evaluate_and_record_shadow(HID, base_config={}, promotions=[], cell=None, validation_results=[])
    mock_membership.assert_not_called()


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_membership_not_called_when_baseline_p_none(mock_evaluate, mock_membership):
    """The operator's own explicit fourth condition -- never relied on
    as an implication of p_value is not None, checked independently."""
    mock_evaluate.return_value = _evidence(baseline_p=None)
    si.evaluate_and_record_shadow(HID, base_config={}, promotions=[], cell=None, validation_results=[])
    mock_membership.assert_not_called()


@patch("backtest.shadow_integration.record_family_membership_if_attempted")
@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_membership_none_propagated_on_duplicate_hypothesis(mock_evaluate, mock_membership):
    """storage.try_insert_membership() denies a duplicate hypothesis_id
    -- this layer must pass that None through unchanged, never retry."""
    mock_evaluate.return_value = _evidence()
    mock_membership.return_value = None
    result = si.evaluate_and_record_shadow(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    mock_membership.assert_called_once()
    assert result["membership"] is None


@patch("backtest.shadow_integration.evaluate_shadow_evidence")
def test_evidence_failure_propagates_uncaught(mock_evaluate):
    mock_evaluate.side_effect = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        si.evaluate_and_record_shadow(HID, base_config={}, promotions=[], cell=None, validation_results=[])


def test_return_shape_has_exactly_two_keys():
    with patch("backtest.shadow_integration.evaluate_shadow_evidence", return_value=_evidence()), \
         patch("backtest.shadow_integration.record_family_membership_if_attempted", return_value=None):
        result = si.evaluate_and_record_shadow(
            HID, base_config={}, promotions=[], cell=None, validation_results=[],
        )
    assert set(result.keys()) == {"shadow_record", "membership"}


# ---------- structural: no scope creep --------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(si)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_never_imports_storage_or_out_of_scope_modules():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "promotion_gate", "policy_health", "execution_attribution",
        "execution.authorization", "trade_executor", "scheduler", "main.py",
        "n_eff", "k_eff", "classify_significance", "bonferroni_alpha",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_integration unexpectedly references {pattern!r}"
