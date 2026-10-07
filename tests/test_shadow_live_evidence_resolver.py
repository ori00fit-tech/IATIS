"""tests/test_shadow_live_evidence_resolver.py -- tests for backtest/
shadow_live_evidence_resolver.py (Live Evidence Resolver, Design Gate
locked 2026-10, "External Scheduler/Runner"): the storage-fetch chain
(list_promotions_for_hypothesis -> resolve_canonical_baseline ->
get_cell -> get_family -> assemble_shadow_evidence), the None-cell_id
short-circuit, and fail-fast propagation from every composed call."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backtest import shadow_live_evidence_resolver as resolver_mod

HID = "CONFLUENCE-HYPOTHESIS-x"


@patch("backtest.shadow_live_evidence_resolver.assemble_shadow_evidence")
@patch("backtest.shadow_live_evidence_resolver.get_family")
@patch("backtest.shadow_live_evidence_resolver.get_cell")
@patch("backtest.shadow_live_evidence_resolver.resolve_canonical_baseline")
@patch("backtest.shadow_live_evidence_resolver.list_promotions_for_hypothesis")
def test_happy_path_wires_all_four_fetches_in_order(
    mock_promotions, mock_baseline, mock_get_cell, mock_get_family, mock_assemble,
):
    promotions = [{"promotion_id": "P1"}]
    cell = {"cell_id": "C1", "family_id": "F1"}
    family = {"planned_n": 10, "family_alpha": 0.05}
    mock_promotions.return_value = promotions
    mock_baseline.return_value = {"hypothesis_id": HID, "canonicity_state": "CANONICAL_CELL_RESOLVED", "cell_id": "C1"}
    mock_get_cell.return_value = cell
    mock_get_family.return_value = family
    mock_assemble.return_value = {"assembly_state": "ASSEMBLED", "evidence": {"classification": "PROMISING"}}

    result = resolver_mod.resolve_live_shadow_evidence(HID)

    mock_promotions.assert_called_once_with(HID)
    mock_baseline.assert_called_once_with(HID, promotions)
    mock_get_cell.assert_called_once_with("C1")
    mock_get_family.assert_called_once_with("F1")
    mock_assemble.assert_called_once_with(HID, promotions, cell, family)
    assert result == {"assembly_state": "ASSEMBLED", "evidence": {"classification": "PROMISING"}}


@patch("backtest.shadow_live_evidence_resolver.assemble_shadow_evidence")
@patch("backtest.shadow_live_evidence_resolver.get_family")
@patch("backtest.shadow_live_evidence_resolver.get_cell")
@patch("backtest.shadow_live_evidence_resolver.resolve_canonical_baseline")
@patch("backtest.shadow_live_evidence_resolver.list_promotions_for_hypothesis")
def test_no_canonical_cell_id_is_passed_through_to_get_cell_and_short_circuits_family(
    mock_promotions, mock_baseline, mock_get_cell, mock_get_family, mock_assemble,
):
    """resolve_canonical_baseline() did not resolve a cell_id -> get_cell(None)
    is still called (never special-cased), and since get_cell naturally
    returns None for that, get_family() is never called at all."""
    mock_promotions.return_value = []
    mock_baseline.return_value = {"hypothesis_id": HID, "canonicity_state": "NO_PROMOTION_EXISTS", "cell_id": None}
    mock_get_cell.return_value = None
    mock_assemble.return_value = {"assembly_state": "NO_CANONICAL_CELL", "evidence": None}

    result = resolver_mod.resolve_live_shadow_evidence(HID)

    mock_get_cell.assert_called_once_with(None)
    mock_get_family.assert_not_called()
    mock_assemble.assert_called_once_with(HID, [], None, None)
    assert result == {"assembly_state": "NO_CANONICAL_CELL", "evidence": None}


@patch("backtest.shadow_live_evidence_resolver.assemble_shadow_evidence")
@patch("backtest.shadow_live_evidence_resolver.get_family")
@patch("backtest.shadow_live_evidence_resolver.get_cell")
@patch("backtest.shadow_live_evidence_resolver.resolve_canonical_baseline")
@patch("backtest.shadow_live_evidence_resolver.list_promotions_for_hypothesis")
def test_cell_found_but_family_missing_still_reaches_assemble_with_family_none(
    mock_promotions, mock_baseline, mock_get_cell, mock_get_family, mock_assemble,
):
    mock_promotions.return_value = [{"promotion_id": "P1"}]
    mock_baseline.return_value = {"hypothesis_id": HID, "canonicity_state": "CANONICAL_CELL_RESOLVED", "cell_id": "C1"}
    mock_get_cell.return_value = {"cell_id": "C1", "family_id": "F-MISSING"}
    mock_get_family.return_value = None
    mock_assemble.return_value = {"assembly_state": "MISSING_FAMILY_PARAMS", "evidence": None}

    result = resolver_mod.resolve_live_shadow_evidence(HID)

    mock_get_family.assert_called_once_with("F-MISSING")
    assert mock_assemble.call_args.args[3] is None  # family argument
    assert result == {"assembly_state": "MISSING_FAMILY_PARAMS", "evidence": None}


@patch("backtest.shadow_live_evidence_resolver.list_promotions_for_hypothesis")
def test_promotions_fetch_failure_propagates_uncaught(mock_promotions):
    mock_promotions.side_effect = RuntimeError("storage unreachable")
    with pytest.raises(RuntimeError, match="storage unreachable"):
        resolver_mod.resolve_live_shadow_evidence(HID)


@patch("backtest.shadow_live_evidence_resolver.resolve_canonical_baseline")
@patch("backtest.shadow_live_evidence_resolver.list_promotions_for_hypothesis")
def test_canonical_baseline_failure_propagates_uncaught(mock_promotions, mock_baseline):
    from backtest.hypothesis_baseline import HypothesisBaselineError
    mock_promotions.return_value = []
    mock_baseline.side_effect = HypothesisBaselineError("mismatched hypothesis_id")
    with pytest.raises(HypothesisBaselineError, match="mismatched hypothesis_id"):
        resolver_mod.resolve_live_shadow_evidence(HID)


@patch("backtest.shadow_live_evidence_resolver.get_cell")
@patch("backtest.shadow_live_evidence_resolver.resolve_canonical_baseline")
@patch("backtest.shadow_live_evidence_resolver.list_promotions_for_hypothesis")
def test_get_cell_failure_propagates_uncaught(mock_promotions, mock_baseline, mock_get_cell):
    mock_promotions.return_value = []
    mock_baseline.return_value = {"hypothesis_id": HID, "canonicity_state": "CANONICAL_CELL_RESOLVED", "cell_id": "C1"}
    mock_get_cell.side_effect = RuntimeError("d1 unreachable")
    with pytest.raises(RuntimeError, match="d1 unreachable"):
        resolver_mod.resolve_live_shadow_evidence(HID)


# ---------- structural: no scope creep --------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(resolver_mod)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_is_isolated_from_downstream_and_roster_layers():
    body = _source_without_docstrings()
    forbidden = (
        "promotion_gate", "shadow_roster_composer", "shadow_observe_orchestrator",
        "shadow_record", "shadow_integration", "shadow_divergence_membership",
        "shadow_effective_sample", "live_roster",
        "execution.authorization", "execution.trade_executor", "scheduler", "main.py",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_live_evidence_resolver unexpectedly references {pattern!r}"
