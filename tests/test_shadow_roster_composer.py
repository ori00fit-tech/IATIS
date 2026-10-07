"""tests/test_shadow_roster_composer.py -- tests for backtest/
shadow_roster_composer.py (Roster-to-Entries Composer, Design Gate
locked 2026-10, "Observe Operational Composition"): "composer reviews,
never decides" (a fresh evaluate_promotion_gate(target_stage=SHADOW)
call per entry, never re-implemented eligibility logic), exclude-only
(never removes from roster, never persists a rejection), logging on
exclusion, and fail-fast propagation."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backtest import shadow_roster_composer as rc
from backtest.shadow_evidence_assembly import ASSEMBLED

HID_A = "CONFLUENCE-HYPOTHESIS-A"
HID_B = "CONFLUENCE-HYPOTHESIS-B"


def _roster_entry(hypothesis_id: str, roster_entry_id: str = "R1") -> dict:
    return {"hypothesis_id": hypothesis_id, "roster_entry_id": roster_entry_id, "status": "ACTIVE"}


def _assembled(evidence=None) -> dict:
    return {"assembly_state": ASSEMBLED, "evidence": evidence or {"classification": "PROMISING"}}


def _not_assembled(state="NO_CANONICAL_CELL") -> dict:
    return {"assembly_state": state, "evidence": None}


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_includes_entry_when_assembled_and_eligible(mock_gate):
    mock_gate.return_value = {"eligibility": "ELIGIBLE", "target_stage": "SHADOW", "reasons": []}
    roster_entries = [_roster_entry(HID_A)]

    result = rc.compose_shadow_observe_entries(roster_entries, evidence_resolver=lambda h: _assembled())
    assert len(result) == 1
    assert result[0]["roster_entry"] == roster_entries[0]
    assert result[0]["fresh_promotion_gate_result"] == mock_gate.return_value


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_excludes_entry_when_assembly_not_assembled_and_never_calls_gate(mock_gate):
    roster_entries = [_roster_entry(HID_A)]
    result = rc.compose_shadow_observe_entries(roster_entries, evidence_resolver=lambda h: _not_assembled())
    assert result == []
    mock_gate.assert_not_called()


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_excludes_entry_when_not_eligible(mock_gate):
    mock_gate.return_value = {"eligibility": "NOT_ELIGIBLE", "target_stage": "SHADOW", "reasons": ["too weak"]}
    roster_entries = [_roster_entry(HID_A)]
    result = rc.compose_shadow_observe_entries(roster_entries, evidence_resolver=lambda h: _assembled())
    assert result == []


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_target_stage_is_always_shadow_and_cross_symbol_confirmed_always_false(mock_gate):
    mock_gate.return_value = {"eligibility": "ELIGIBLE", "target_stage": "SHADOW", "reasons": []}
    evidence = {"classification": "PROMISING"}
    rc.compose_shadow_observe_entries([_roster_entry(HID_A)], evidence_resolver=lambda h: _assembled(evidence))
    mock_gate.assert_called_once_with(target_stage="SHADOW", evidence=evidence, cross_symbol_confirmed=False)


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_evidence_resolver_called_with_each_entrys_own_hypothesis_id(mock_gate):
    mock_gate.return_value = {"eligibility": "ELIGIBLE", "target_stage": "SHADOW", "reasons": []}
    seen = []

    def resolver(hypothesis_id):
        seen.append(hypothesis_id)
        return _assembled()

    rc.compose_shadow_observe_entries([_roster_entry(HID_A), _roster_entry(HID_B)], evidence_resolver=resolver)
    assert seen == [HID_A, HID_B]


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_mixed_batch_includes_only_eligible_assembled_entries(mock_gate):
    def resolver(hypothesis_id):
        if hypothesis_id == HID_A:
            return _assembled()
        return _not_assembled()

    def gate(*, target_stage, evidence, cross_symbol_confirmed):
        return {"eligibility": "ELIGIBLE", "target_stage": "SHADOW", "reasons": []}

    mock_gate.side_effect = gate
    roster_entries = [_roster_entry(HID_A, "R1"), _roster_entry(HID_B, "R2")]
    result = rc.compose_shadow_observe_entries(roster_entries, evidence_resolver=resolver)
    assert len(result) == 1
    assert result[0]["roster_entry"]["hypothesis_id"] == HID_A


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_exclusion_logs_hypothesis_id_and_reason_for_assembly_failure(mock_gate, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        rc.compose_shadow_observe_entries(
            [_roster_entry(HID_A)], evidence_resolver=lambda h: _not_assembled("MISSING_FAMILY_PARAMS"),
        )
    assert HID_A in caplog.text
    assert "MISSING_FAMILY_PARAMS" in caplog.text
    mock_gate.assert_not_called()


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_exclusion_logs_hypothesis_id_and_reason_for_ineligibility(mock_gate, caplog):
    import logging
    mock_gate.return_value = {"eligibility": "NOT_ELIGIBLE", "target_stage": "SHADOW", "reasons": ["too weak"]}
    with caplog.at_level(logging.INFO):
        rc.compose_shadow_observe_entries([_roster_entry(HID_A)], evidence_resolver=lambda h: _assembled())
    assert HID_A in caplog.text
    assert "too weak" in caplog.text


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_evidence_resolver_failure_propagates_uncaught(mock_gate):
    def failing_resolver(hypothesis_id):
        raise RuntimeError("storage unreachable")

    with pytest.raises(RuntimeError, match="storage unreachable"):
        rc.compose_shadow_observe_entries([_roster_entry(HID_A)], evidence_resolver=failing_resolver)
    mock_gate.assert_not_called()


@patch("backtest.shadow_roster_composer.evaluate_promotion_gate")
def test_evaluate_promotion_gate_failure_propagates_uncaught(mock_gate):
    from backtest.promotion_gate import PromotionGateError
    mock_gate.side_effect = PromotionGateError("bad target_stage")
    with pytest.raises(PromotionGateError, match="bad target_stage"):
        rc.compose_shadow_observe_entries([_roster_entry(HID_A)], evidence_resolver=lambda h: _assembled())


def test_empty_roster_entries_returns_empty_list():
    assert rc.compose_shadow_observe_entries([], evidence_resolver=lambda h: _assembled()) == []


# ---------- structural: no scope creep --------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(rc)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_never_touches_the_roster_storage_or_downstream_layers():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "live_roster", "remove_from_roster", "try_remove",
        "shadow_record", "shadow_integration", "shadow_divergence_membership",
        "shadow_effective_sample", "shadow_observe_orchestrator",
        "execution.authorization", "execution.trade_executor", "scheduler", "main.py",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_roster_composer unexpectedly references {pattern!r}"
