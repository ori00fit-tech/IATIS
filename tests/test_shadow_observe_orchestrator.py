"""tests/test_shadow_observe_orchestrator.py -- tests for backtest/
shadow_observe_orchestrator.py (SHADOW Observe Orchestrator, Design Gate
locked 2026-10, HEAD=84e428f): the run_shadow_cycle() -> capture_
decision_snapshot() composition, the empty-entries success case, the
"cycle completed != evidence produced" distinction (no completed/success
key at all), fail-fast propagation, and structural isolation from
promotion_gate, the roster, execution, and every statistical/membership
layer built in prior Gates."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backtest import shadow_observe_orchestrator as orch


def _cycle_result(hypothesis_id="H1", roster_entry_id="R1", observed=True, reason=None, live_identity_request=None):
    return {
        "hypothesis_id": hypothesis_id, "roster_entry_id": roster_entry_id,
        "observed": observed, "reason": reason,
        "live_identity_request": live_identity_request if live_identity_request is not None else {"request_id": "REQ-1"},
    }


# ---------- empty entries -----------------------------------------------


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_empty_entries_returns_successful_zero_evidence_cycle(mock_cycle, mock_capture):
    mock_cycle.return_value = []
    result = orch.run_shadow_observe_cycle(entries=[], base_config={})
    assert result == {"entries_count": 0, "observed_count": 0, "captured_count": 0, "results": []}
    mock_capture.assert_not_called()


# ---------- composition: run_shadow_cycle() -> capture_decision_snapshot() -


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_run_shadow_cycle_called_once_with_entries_and_base_config(mock_cycle, mock_capture):
    mock_cycle.return_value = []
    entries = [{"roster_entry": {"hypothesis_id": "H1"}, "fresh_promotion_gate_result": {"eligibility": "ELIGIBLE"}}]
    orch.run_shadow_observe_cycle(entries=entries, base_config={"k": "v"})
    mock_cycle.assert_called_once_with(entries=entries, base_config={"k": "v"})


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_capture_decision_snapshot_called_for_observed_true_only(mock_cycle, mock_capture):
    lir = {"request_id": "REQ-OBSERVED"}
    mock_cycle.return_value = [
        _cycle_result(observed=True, live_identity_request=lir),
        _cycle_result(observed=False, reason="not eligible this cycle"),
    ]
    mock_capture.return_value = {"snapshot_id": "SNAP-1"}

    orch.run_shadow_observe_cycle(entries=[{}, {}], base_config={})
    mock_capture.assert_called_once_with(lir)


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_capture_decision_snapshot_not_called_when_nothing_observed(mock_cycle, mock_capture):
    mock_cycle.return_value = [_cycle_result(observed=False, reason="ineligible")]
    orch.run_shadow_observe_cycle(entries=[{}], base_config={})
    mock_capture.assert_not_called()


# ---------- decision_snapshot_captured flag / counts ------------------------


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_decision_snapshot_captured_true_when_snapshot_returned(mock_cycle, mock_capture):
    mock_cycle.return_value = [_cycle_result(observed=True)]
    mock_capture.return_value = {"snapshot_id": "SNAP-1"}

    result = orch.run_shadow_observe_cycle(entries=[{}], base_config={})
    assert result["results"][0]["decision_snapshot_captured"] is True
    assert result["captured_count"] == 1


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_decision_snapshot_captured_false_when_confluence_did_not_pass(mock_cycle, mock_capture):
    """observed=True but decision_snapshot was None (confluence did not
    pass) -- capture_decision_snapshot() itself returns None without
    writing anything; this must be reflected honestly as not captured."""
    mock_cycle.return_value = [_cycle_result(observed=True)]
    mock_capture.return_value = None

    result = orch.run_shadow_observe_cycle(entries=[{}], base_config={})
    assert result["results"][0]["decision_snapshot_captured"] is False
    assert result["captured_count"] == 0


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_decision_snapshot_captured_false_for_non_observed_entries(mock_cycle, mock_capture):
    mock_cycle.return_value = [_cycle_result(observed=False, reason="ineligible")]
    result = orch.run_shadow_observe_cycle(entries=[{}], base_config={})
    assert result["results"][0]["decision_snapshot_captured"] is False


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_counts_are_correct_across_a_mixed_batch(mock_cycle, mock_capture):
    mock_cycle.return_value = [
        _cycle_result(roster_entry_id="R1", observed=True),   # will capture
        _cycle_result(roster_entry_id="R2", observed=True),   # confluence did not pass -> None
        _cycle_result(roster_entry_id="R3", observed=False, reason="ineligible"),
    ]
    mock_capture.side_effect = [{"snapshot_id": "SNAP-1"}, None]

    result = orch.run_shadow_observe_cycle(entries=[{}, {}, {}], base_config={})
    assert result["entries_count"] == 3
    assert result["observed_count"] == 2
    assert result["captured_count"] == 1


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_entries_count_reflects_input_length_not_cycle_results_length(mock_cycle, mock_capture):
    """A defensive sanity check: entries_count is len(entries), never
    len(cycle_results) -- they are expected to match in practice, but
    this field's own definition is about the input, not the output."""
    mock_cycle.return_value = []
    result = orch.run_shadow_observe_cycle(entries=[{}, {}, {}], base_config={})
    assert result["entries_count"] == 3


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_results_preserve_original_run_shadow_cycle_fields(mock_cycle, mock_capture):
    mock_cycle.return_value = [_cycle_result(hypothesis_id="H-X", roster_entry_id="R-X", observed=True)]
    mock_capture.return_value = {"snapshot_id": "SNAP-1"}

    result = orch.run_shadow_observe_cycle(entries=[{}], base_config={})
    entry_result = result["results"][0]
    assert entry_result["hypothesis_id"] == "H-X"
    assert entry_result["roster_entry_id"] == "R-X"
    assert entry_result["observed"] is True


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_results_order_matches_cycle_results_order(mock_cycle, mock_capture):
    mock_cycle.return_value = [
        _cycle_result(roster_entry_id="R1", observed=False, reason="x"),
        _cycle_result(roster_entry_id="R2", observed=True),
        _cycle_result(roster_entry_id="R3", observed=False, reason="y"),
    ]
    mock_capture.return_value = {"snapshot_id": "SNAP-1"}

    result = orch.run_shadow_observe_cycle(entries=[{}, {}, {}], base_config={})
    assert [r["roster_entry_id"] for r in result["results"]] == ["R1", "R2", "R3"]


# ---------- fail-fast --------------------------------------------------------


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_run_shadow_cycle_failure_propagates_uncaught(mock_cycle, mock_capture):
    mock_cycle.side_effect = RuntimeError("provider down")
    with pytest.raises(RuntimeError, match="provider down"):
        orch.run_shadow_observe_cycle(entries=[{}], base_config={})
    mock_capture.assert_not_called()


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_capture_decision_snapshot_failure_propagates_uncaught(mock_cycle, mock_capture):
    from backtest.shadow_decision_snapshot import ShadowDecisionSnapshotError

    mock_cycle.return_value = [
        _cycle_result(roster_entry_id="R1", observed=True),
        _cycle_result(roster_entry_id="R2", observed=True),
    ]
    mock_capture.side_effect = ShadowDecisionSnapshotError("storage failure")
    with pytest.raises(ShadowDecisionSnapshotError, match="storage failure"):
        orch.run_shadow_observe_cycle(entries=[{}, {}], base_config={})
    # Only the first entry's capture was attempted before the failure aborted the whole cycle.
    assert mock_capture.call_count == 1


# ---------- "cycle completed" != "evidence produced" ------------------------


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_no_completed_or_success_key_in_output(mock_cycle, mock_capture):
    mock_cycle.return_value = []
    result = orch.run_shadow_observe_cycle(entries=[], base_config={})
    assert set(result.keys()) == {"entries_count", "observed_count", "captured_count", "results"}


@patch("backtest.shadow_observe_orchestrator.capture_decision_snapshot")
@patch("backtest.shadow_observe_orchestrator.run_shadow_cycle")
def test_zero_captured_count_on_a_successful_return_is_not_an_error(mock_cycle, mock_capture):
    mock_cycle.return_value = [_cycle_result(observed=False, reason="ineligible")]
    result = orch.run_shadow_observe_cycle(entries=[{}], base_config={})
    assert result["captured_count"] == 0  # a successful, honest return -- no exception was raised


# ---------- structural: observe-only isolation ------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(orch)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_is_isolated_from_promotion_roster_statistics_and_execution():
    body = _source_without_docstrings()
    forbidden = (
        "promotion_gate", "evaluate_promotion_gate",
        "storage.live_roster", "backtest.live_roster", "list_active_entries",
        "shadow_outcome_evidence", "shadow_record", "shadow_integration",
        "shadow_divergence_membership", "shadow_effective_sample",
        "execution.authorization", "execution.trade_executor", "trade_executor", "broker",
        "scheduler", "main.py",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_observe_orchestrator unexpectedly references {pattern!r}"


def test_module_performs_no_storage_io_of_its_own():
    body = _source_without_docstrings()
    forbidden = ("import storage", "d1_client", "fetch_with_failover")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_observe_orchestrator unexpectedly references {pattern!r}"
