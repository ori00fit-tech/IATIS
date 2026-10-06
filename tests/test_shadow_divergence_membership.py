"""tests/test_shadow_divergence_membership.py -- tests for backtest/
shadow_divergence_membership.py (Family Membership Writer, decision
layer, Design Gate locked 2026-10): the locked "attempted" condition
(n_t>=40 AND p_value is not None), no-DB-call-on-non-attempt, and
structural independence from n_eff/k_eff/Bonferroni/verdict/
build_shadow_record()."""
from __future__ import annotations

from unittest.mock import patch

from backtest import shadow_divergence_membership as bdm

HID = "CONFLUENCE-HYPOTHESIS-x"


def _kwargs(**overrides) -> dict:
    base = dict(
        hypothesis_id=HID, n_t=40, p_value=0.0004, baseline_p=0.65,
        tp_count=28, sl_count=12, request_ids=["R1", "R2"],
        hypothesis_fingerprint="FP-x", research_code_commit="abc123",
    )
    base.update(overrides)
    return base


@patch("backtest.shadow_divergence_membership.try_insert_membership")
def test_n_t_below_threshold_returns_none_with_no_db_call(mock_insert):
    result = bdm.record_family_membership_if_attempted(**_kwargs(n_t=39))
    assert result is None
    mock_insert.assert_not_called()


@patch("backtest.shadow_divergence_membership.try_insert_membership")
def test_p_value_none_returns_none_with_no_db_call(mock_insert):
    result = bdm.record_family_membership_if_attempted(**_kwargs(p_value=None))
    assert result is None
    mock_insert.assert_not_called()


@patch("backtest.shadow_divergence_membership.try_insert_membership")
def test_both_conditions_unmet_returns_none_with_no_db_call(mock_insert):
    result = bdm.record_family_membership_if_attempted(**_kwargs(n_t=0, p_value=None))
    assert result is None
    mock_insert.assert_not_called()


@patch("backtest.shadow_divergence_membership.try_insert_membership")
def test_n_t_exactly_forty_qualifies(mock_insert):
    mock_insert.return_value = {"hypothesis_id": HID}
    result = bdm.record_family_membership_if_attempted(**_kwargs(n_t=40))
    assert result == {"hypothesis_id": HID}
    mock_insert.assert_called_once()


@patch("backtest.shadow_divergence_membership.try_insert_membership")
def test_both_conditions_met_calls_storage_with_exact_evidence(mock_insert):
    mock_insert.return_value = {"hypothesis_id": HID}
    result = bdm.record_family_membership_if_attempted(**_kwargs(n_t=52))

    mock_insert.assert_called_once_with(
        hypothesis_id=HID, hypothesis_fingerprint="FP-x", research_code_commit="abc123",
        tp_count_at_entry=28, sl_count_at_entry=12, p_value_at_entry=0.0004,
        baseline_p_at_entry=0.65, request_ids=["R1", "R2"],
    )
    assert result == {"hypothesis_id": HID}


@patch("backtest.shadow_divergence_membership.try_insert_membership")
def test_duplicate_hypothesis_propagates_none_from_storage(mock_insert):
    """storage.try_insert_membership() itself returns None on a
    duplicate hypothesis_id -- this layer must pass that through
    unchanged, never retry, never fabricate a row."""
    mock_insert.return_value = None
    result = bdm.record_family_membership_if_attempted(**_kwargs(n_t=100))
    assert result is None
    mock_insert.assert_called_once()


def test_n_min_terminal_confirmed_is_forty():
    assert bdm.N_MIN_TERMINAL_CONFIRMED == 40


# ---------- structural: no scope creep -------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(bdm)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_n_eff_k_eff_bonferroni_classification_verdict_or_build_record():
    body = _source_without_docstrings()
    forbidden = (
        "n_eff", "k_eff", "classify_significance", "bonferroni_alpha",
        "diverged_catastrophically", "DIVERGENCE_VERDICT_COMPUTED", "build_shadow_record",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_divergence_membership unexpectedly references {pattern!r}"


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body
