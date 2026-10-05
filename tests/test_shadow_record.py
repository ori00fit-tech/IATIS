"""tests/test_shadow_record.py -- tests for backtest/shadow_record.py
(the Full Observed Evidence Producer, Implementation Design): the
three-conjunct `completed` formula (with DIVERGENCE_VERDICT_COMPUTED
hardcoded False), the minimal two-key record shape, the
observed/baseline I/O-boundary asymmetry, and fail-fast propagation."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backtest import shadow_record as sr


def test_divergence_verdict_computed_is_hardcoded_false():
    """Locked: this constant must never silently become True -- doing
    so honestly requires a separate, future Design Gate."""
    assert sr.DIVERGENCE_VERDICT_COMPUTED is False


def test_n_min_terminal_confirmed_is_forty():
    assert sr.N_MIN_TERMINAL_CONFIRMED == 40


@patch("backtest.shadow_record.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_record.count_terminal_confirmed")
@patch("backtest.shadow_record.evaluate_all_requests_for_hypothesis")
def test_completed_is_always_false_even_with_abundant_n_t_and_valid_baseline(
    mock_evaluate_requests, mock_count, mock_baseline,
):
    """The central guard this Design Gate exists for: n_T(H)>=40 AND a
    valid baseline statistic are NECESSARY but NOT SUFFICIENT. completed
    must stay False because DIVERGENCE_VERDICT_COMPUTED is False,
    regardless of how strong the other two conjuncts look."""
    mock_evaluate_requests.return_value = [{"terminality_state": "TERMINAL_CONFIRMED"}] * 100
    mock_count.return_value = 100  # far above the n>=40 threshold
    mock_baseline.return_value = {
        "hypothesis_id": "H", "divergence_statistic_valid": True,
        "tp_sl_win_rate": 0.7, "failure_reason": None,
    }

    result = sr.build_shadow_record(
        "H", base_config={}, promotions=[], cell={"cell_id": "C1"}, validation_results=[],
    )
    assert result == {"completed": False, "diverged_catastrophically": False}


@patch("backtest.shadow_record.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_record.count_terminal_confirmed")
@patch("backtest.shadow_record.evaluate_all_requests_for_hypothesis")
def test_completed_false_when_n_t_below_forty_even_if_baseline_valid(
    mock_evaluate_requests, mock_count, mock_baseline,
):
    mock_evaluate_requests.return_value = []
    mock_count.return_value = 39
    mock_baseline.return_value = {"divergence_statistic_valid": True}
    result = sr.build_shadow_record(
        "H", base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert result["completed"] is False


@patch("backtest.shadow_record.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_record.count_terminal_confirmed")
@patch("backtest.shadow_record.evaluate_all_requests_for_hypothesis")
def test_completed_false_when_baseline_invalid_even_if_n_t_abundant(
    mock_evaluate_requests, mock_count, mock_baseline,
):
    mock_evaluate_requests.return_value = []
    mock_count.return_value = 1000
    mock_baseline.return_value = {"divergence_statistic_valid": False}
    result = sr.build_shadow_record(
        "H", base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert result["completed"] is False


@patch("backtest.shadow_record.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_record.count_terminal_confirmed")
@patch("backtest.shadow_record.evaluate_all_requests_for_hypothesis")
def test_diverged_catastrophically_is_always_false(mock_evaluate_requests, mock_count, mock_baseline):
    mock_evaluate_requests.return_value = []
    mock_count.return_value = 0
    mock_baseline.return_value = {"divergence_statistic_valid": False}
    result = sr.build_shadow_record(
        "H", base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert result["diverged_catastrophically"] is False


@patch("backtest.shadow_record.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_record.count_terminal_confirmed")
@patch("backtest.shadow_record.evaluate_all_requests_for_hypothesis")
def test_record_shape_has_exactly_two_keys(mock_evaluate_requests, mock_count, mock_baseline):
    """Locked minimal shape -- no diagnostic fields (n_t, baseline,
    etc.) in the returned record."""
    mock_evaluate_requests.return_value = []
    mock_count.return_value = 0
    mock_baseline.return_value = {"divergence_statistic_valid": False}
    result = sr.build_shadow_record(
        "H", base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert set(result.keys()) == {"completed", "diverged_catastrophically"}


@patch("backtest.shadow_record.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_record.count_terminal_confirmed")
@patch("backtest.shadow_record.evaluate_all_requests_for_hypothesis")
def test_baseline_inputs_are_passed_through_unfetched(mock_evaluate_requests, mock_count, mock_baseline):
    """The baseline side is caller-supplied, never fetched by this
    module -- evaluate_canonical_baseline_statistic() must receive
    exactly what the caller passed, unchanged."""
    mock_evaluate_requests.return_value = []
    mock_count.return_value = 0
    mock_baseline.return_value = {"divergence_statistic_valid": False}
    promotions = [{"promotion_id": "P1"}]
    cell = {"cell_id": "C1"}
    validation_results = [{"symbol": "XAUUSD"}]

    sr.build_shadow_record(
        "H", base_config={"k": "v"}, promotions=promotions, cell=cell,
        validation_results=validation_results, api_key="secret",
    )
    mock_baseline.assert_called_once_with("H", promotions, cell, validation_results)
    mock_evaluate_requests.assert_called_once_with("H", base_config={"k": "v"}, api_key="secret")


@patch("backtest.shadow_record.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_record.evaluate_all_requests_for_hypothesis")
def test_observed_side_failure_propagates_uncaught(mock_evaluate_requests, mock_baseline):
    """Fail-fast: an exception from the observed-side evaluation must
    propagate unchanged -- no partial/fabricated record, and the
    baseline side must never even be evaluated once the observed side
    has already failed."""
    mock_evaluate_requests.side_effect = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        sr.build_shadow_record("H", base_config={}, promotions=[], cell=None, validation_results=[])
    mock_baseline.assert_not_called()


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(sr)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_never_writes_to_storage():
    body = _source_without_docstrings()
    forbidden = ("try_insert", "record_", "update_cell", "INSERT INTO", "UPDATE ")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_record unexpectedly references {pattern!r}"
