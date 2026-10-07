"""tests/test_add_hypothesis_to_shadow_roster.py -- tests for scripts/
add_hypothesis_to_shadow_roster.py (Roster Population Tooling, Design
Gate locked 2026-10): the resolve_live_shadow_evidence() ->
evaluate_promotion_gate(SHADOW) -> add_to_roster() composition,
--dry-run never writing, an ineligible result never calling
add_to_roster(), and add_to_roster()'s own idempotent-duplicate result
passed through as exit code 1."""
from __future__ import annotations

import sys

from unittest.mock import patch

HID = "CONFLUENCE-HYPOTHESIS-x"


def _assembled(classification="PROMISING"):
    return {"assembly_state": "ASSEMBLED", "evidence": {"classification": classification}}


def _not_assembled(state="NO_CANONICAL_CELL"):
    return {"assembly_state": state, "evidence": None}


def _run_main(argv, monkeypatch):
    from scripts.add_hypothesis_to_shadow_roster import main
    monkeypatch.setattr(sys, "argv", ["add_hypothesis_to_shadow_roster.py", *argv])
    return main()


@patch("backtest.live_roster.add_to_roster")
@patch("backtest.promotion_gate.evaluate_promotion_gate")
@patch("backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence")
def test_dry_run_eligible_never_calls_add_to_roster(mock_resolve, mock_gate, mock_add, monkeypatch):
    mock_resolve.return_value = _assembled()
    mock_gate.return_value = {"eligibility": "ELIGIBLE", "target_stage": "SHADOW", "reasons": []}

    exit_code = _run_main([HID, "--added-by", "op", "--added-reason", "reason", "--dry-run"], monkeypatch)
    assert exit_code == 0
    mock_add.assert_not_called()


@patch("backtest.live_roster.add_to_roster")
@patch("backtest.promotion_gate.evaluate_promotion_gate")
@patch("backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence")
def test_dry_run_ineligible_never_calls_add_to_roster(mock_resolve, mock_gate, mock_add, monkeypatch):
    mock_resolve.return_value = _assembled()
    mock_gate.return_value = {"eligibility": "NOT_ELIGIBLE", "target_stage": "SHADOW", "reasons": ["too weak"]}

    exit_code = _run_main([HID, "--added-by", "op", "--added-reason", "reason", "--dry-run"], monkeypatch)
    assert exit_code == 0
    mock_add.assert_not_called()


@patch("backtest.live_roster.add_to_roster")
@patch("backtest.promotion_gate.evaluate_promotion_gate")
@patch("backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence")
def test_assembly_failure_returns_1_and_skips_gate_and_roster(mock_resolve, mock_gate, mock_add, monkeypatch):
    mock_resolve.return_value = _not_assembled("MISSING_FAMILY_PARAMS")

    exit_code = _run_main([HID, "--added-by", "op", "--added-reason", "reason"], monkeypatch)
    assert exit_code == 1
    mock_gate.assert_not_called()
    mock_add.assert_not_called()


@patch("backtest.live_roster.add_to_roster")
@patch("backtest.promotion_gate.evaluate_promotion_gate")
@patch("backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence")
def test_ineligible_without_dry_run_returns_1_and_skips_add_to_roster(mock_resolve, mock_gate, mock_add, monkeypatch):
    mock_resolve.return_value = _assembled()
    mock_gate.return_value = {"eligibility": "NOT_ELIGIBLE", "target_stage": "SHADOW", "reasons": ["too weak"]}

    exit_code = _run_main([HID, "--added-by", "op", "--added-reason", "reason"], monkeypatch)
    assert exit_code == 1
    mock_add.assert_not_called()


@patch("backtest.live_roster.add_to_roster")
@patch("backtest.promotion_gate.evaluate_promotion_gate")
@patch("backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence")
def test_eligible_calls_add_to_roster_with_exact_arguments(mock_resolve, mock_gate, mock_add, monkeypatch):
    mock_resolve.return_value = _assembled()
    gate_result = {"eligibility": "ELIGIBLE", "target_stage": "SHADOW", "reasons": [], "note": "..."}
    mock_gate.return_value = gate_result
    mock_add.return_value = {"added": True, "roster_entry": {"roster_entry_id": "R1"}}

    exit_code = _run_main([HID, "--added-by", "operator-x", "--added-reason", "promising evidence"], monkeypatch)

    mock_add.assert_called_once_with(
        hypothesis_id=HID, promotion_gate_result=gate_result,
        added_by="operator-x", added_reason="promising evidence",
    )
    assert exit_code == 0


@patch("backtest.live_roster.add_to_roster")
@patch("backtest.promotion_gate.evaluate_promotion_gate")
@patch("backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence")
def test_add_to_roster_duplicate_denial_returns_1(mock_resolve, mock_gate, mock_add, monkeypatch):
    """add_to_roster()'s own idempotent denial (an ACTIVE entry already
    exists) is passed through as a non-zero exit -- never silently
    treated as success."""
    mock_resolve.return_value = _assembled()
    mock_gate.return_value = {"eligibility": "ELIGIBLE", "target_stage": "SHADOW", "reasons": []}
    mock_add.return_value = {"added": False, "reason": "an ACTIVE roster entry already exists."}

    exit_code = _run_main([HID, "--added-by", "op", "--added-reason", "reason"], monkeypatch)
    assert exit_code == 1


@patch("backtest.live_roster.add_to_roster")
@patch("backtest.promotion_gate.evaluate_promotion_gate")
@patch("backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence")
def test_added_by_and_added_reason_are_required(mock_resolve, mock_gate, mock_add, monkeypatch):
    import pytest
    monkeypatch.setattr(sys, "argv", ["add_hypothesis_to_shadow_roster.py", HID])
    from scripts.add_hypothesis_to_shadow_roster import main
    with pytest.raises(SystemExit):
        main()
    mock_resolve.assert_not_called()


# ---------- structural: no scope creep --------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    import scripts.add_hypothesis_to_shadow_roster as mod
    source = inspect.getsource(mod)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_never_imports_execution_or_scheduler():
    body = _source_without_docstrings()
    forbidden = (
        "execution.authorization", "execution.trade_executor", "import scheduler", "import main",
        "shadow_record", "shadow_integration", "shadow_divergence_membership", "shadow_effective_sample",
    )
    for pattern in forbidden:
        assert pattern not in body, f"add_hypothesis_to_shadow_roster unexpectedly references {pattern!r}"
