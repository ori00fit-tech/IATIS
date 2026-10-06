"""tests/test_shadow_outcome_evidence.py -- tests for backtest/
shadow_outcome_evidence.py (the shared evaluation primitive, Membership
Integration Design Gate, locked 2026-10, decision C): a SINGLE
evaluate_all_requests_for_hypothesis() call composed with the baseline
statistic and canonical-identity resolution, request_ids scoped to
TERMINAL_CONFIRMED only, and the three identity states
(CANONICAL_IDENTITY_RESOLVED / NO_CANONICAL_CELL / IDENTITY_MISMATCH)
with no invented tie-break.

NAMING NOTE: this module/test pair was originally written as
backtest/shadow_evidence.py / tests/test_shadow_evidence.py, which
collided with an already-committed, unrelated Phase 15B module of that
exact name. Moved here to avoid that collision -- see backtest.
shadow_outcome_evidence's own module docstring."""
from __future__ import annotations

from unittest.mock import patch

from backtest import shadow_outcome_evidence as se

HID = "CONFLUENCE-HYPOTHESIS-x"


def _terminality_result(request_id: str, terminality_state: str, outcome: str | None = None) -> dict:
    return {
        "request_id": request_id, "hypothesis_id": HID,
        "terminality_state": terminality_state, "relationship": "UNCHANGED",
        "outcome": outcome,
    }


def _promotion(cell_id: str, decision: str, fingerprint: str, commit: str | None, promotion_id: str) -> dict:
    return {
        "promotion_id": promotion_id, "hypothesis_id": HID, "cell_id": cell_id,
        "decision": decision, "hypothesis_fingerprint": fingerprint, "research_code_commit": commit,
    }


def _baseline(valid: bool, tp_sl_win_rate: float | None, failure_reason: str | None) -> dict:
    return {
        "hypothesis_id": HID, "divergence_statistic_valid": valid,
        "tp_sl_win_rate": tp_sl_win_rate, "failure_reason": failure_reason,
    }


TERMINALITY_RESULTS = [
    _terminality_result("R1", "TERMINAL_CONFIRMED", "TP_HIT"),
    _terminality_result("R2", "TERMINAL_CONFIRMED", "TP_HIT"),
    _terminality_result("R3", "TERMINAL_CONFIRMED", "SL_HIT"),
    _terminality_result("R4", "AWAITING_OUTCOME", None),
]


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_observed_side_evaluated_exactly_once(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = TERMINALITY_RESULTS
    mock_baseline.return_value = _baseline(True, 0.65, None)

    se.evaluate_shadow_evidence(
        HID, base_config={"k": "v"}, promotions=[], cell=None, validation_results=[], api_key="secret",
    )
    mock_evaluate_requests.assert_called_once_with(HID, base_config={"k": "v"}, api_key="secret")


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_n_t_tp_sl_counts_and_p_value_composed_from_observed_side(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = TERMINALITY_RESULTS
    mock_baseline.return_value = _baseline(True, 0.65, None)

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert evidence["n_t"] == 3
    assert evidence["tp_count"] == 2
    assert evidence["sl_count"] == 1
    assert evidence["p_value"] is not None  # real binomial_lower_tail_p_value(k=2, n=3, p=0.65)
    assert evidence["divergence_statistic_valid"] is True
    assert evidence["baseline_p"] == 0.65
    assert evidence["baseline_failure_reason"] is None


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_request_ids_scoped_to_terminal_confirmed_only(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = TERMINALITY_RESULTS
    mock_baseline.return_value = _baseline(True, 0.65, None)

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert sorted(evidence["request_ids"]) == ["R1", "R2", "R3"]  # R4 (AWAITING_OUTCOME) excluded


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_p_value_none_when_baseline_invalid(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = TERMINALITY_RESULTS
    mock_baseline.return_value = _baseline(False, None, "NO_STAGE_B_VALIDATION")

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert evidence["p_value"] is None
    assert evidence["baseline_p"] is None
    assert evidence["baseline_failure_reason"] == "NO_STAGE_B_VALIDATION"


# ---------- canonical identity resolution ----------------------------------


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_identity_resolved_single_promoted_cell(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = []
    mock_baseline.return_value = _baseline(False, None, "NO_CANONICAL_CELL")
    promotions = [_promotion("C1", "PROMOTED", "FP-1", "commit-1", "PROMO-1")]

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=promotions, cell=None, validation_results=[],
    )
    assert evidence["canonical_identity_state"] == se.CANONICAL_IDENTITY_RESOLVED
    assert evidence["hypothesis_fingerprint"] == "FP-1"
    assert evidence["research_code_commit"] == "commit-1"


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_identity_resolved_agrees_across_multiple_promoted_rows_same_cell(mock_evaluate_requests, mock_baseline):
    """Several PROMOTED rows for the SAME cell_id (re-evaluated as the
    forensic ledger grows) that agree on fingerprint/commit -- not
    ambiguity, resolves cleanly."""
    mock_evaluate_requests.return_value = []
    mock_baseline.return_value = _baseline(False, None, "NO_CANONICAL_CELL")
    promotions = [
        _promotion("C1", "PROMOTED", "FP-1", "commit-1", "PROMO-1"),
        _promotion("C1", "PROMOTED", "FP-1", "commit-1", "PROMO-2"),
        _promotion("C1", "BLOCKED", "FP-1", "commit-1", "PROMO-0"),
    ]

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=promotions, cell=None, validation_results=[],
    )
    assert evidence["canonical_identity_state"] == se.CANONICAL_IDENTITY_RESOLVED
    assert evidence["hypothesis_fingerprint"] == "FP-1"
    assert evidence["research_code_commit"] == "commit-1"


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_identity_no_canonical_cell_when_no_promotion_exists(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = []
    mock_baseline.return_value = _baseline(False, None, "NO_CANONICAL_CELL")

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert evidence["canonical_identity_state"] == se.NO_CANONICAL_CELL
    assert evidence["hypothesis_fingerprint"] is None
    assert evidence["research_code_commit"] is None


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_identity_no_canonical_cell_when_multiple_distinct_cells_promoted(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = []
    mock_baseline.return_value = _baseline(False, None, "NO_CANONICAL_CELL")
    promotions = [
        _promotion("C1", "PROMOTED", "FP-1", "commit-1", "PROMO-1"),
        _promotion("C2", "PROMOTED", "FP-2", "commit-2", "PROMO-2"),
    ]

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=promotions, cell=None, validation_results=[],
    )
    assert evidence["canonical_identity_state"] == se.NO_CANONICAL_CELL
    assert evidence["hypothesis_fingerprint"] is None
    assert evidence["research_code_commit"] is None


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_identity_mismatch_when_fingerprints_disagree_for_same_canonical_cell(mock_evaluate_requests, mock_baseline):
    """Never a silently-picked first/last row -- a genuine disagreement
    among the SAME canonical cell_id's own PROMOTED rows is reported,
    not resolved."""
    mock_evaluate_requests.return_value = []
    mock_baseline.return_value = _baseline(False, None, "NO_CANONICAL_CELL")
    promotions = [
        _promotion("C1", "PROMOTED", "FP-1", "commit-1", "PROMO-1"),
        _promotion("C1", "PROMOTED", "FP-DIFFERENT", "commit-1", "PROMO-2"),
    ]

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=promotions, cell=None, validation_results=[],
    )
    assert evidence["canonical_identity_state"] == se.IDENTITY_MISMATCH
    assert evidence["hypothesis_fingerprint"] is None
    assert evidence["research_code_commit"] is None


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_identity_mismatch_when_commits_disagree_for_same_canonical_cell(mock_evaluate_requests, mock_baseline):
    mock_evaluate_requests.return_value = []
    mock_baseline.return_value = _baseline(False, None, "NO_CANONICAL_CELL")
    promotions = [
        _promotion("C1", "PROMOTED", "FP-1", "commit-1", "PROMO-1"),
        _promotion("C1", "PROMOTED", "FP-1", "commit-DIFFERENT", "PROMO-2"),
    ]

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=promotions, cell=None, validation_results=[],
    )
    assert evidence["canonical_identity_state"] == se.IDENTITY_MISMATCH


@patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic")
@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_identity_independent_of_completion_fields(mock_evaluate_requests, mock_baseline):
    """Operator's own locked rule: NO_CANONICAL_CELL/IDENTITY_MISMATCH
    never affect n_t/tp_count/sl_count/p_value/divergence_statistic_valid."""
    mock_evaluate_requests.return_value = TERMINALITY_RESULTS
    mock_baseline.return_value = _baseline(True, 0.65, None)

    evidence = se.evaluate_shadow_evidence(
        HID, base_config={}, promotions=[], cell=None, validation_results=[],
    )
    assert evidence["canonical_identity_state"] == se.NO_CANONICAL_CELL
    assert evidence["n_t"] == 3
    assert evidence["divergence_statistic_valid"] is True


@patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis")
def test_observed_side_failure_propagates_uncaught(mock_evaluate_requests):
    mock_evaluate_requests.side_effect = RuntimeError("provider down")
    import pytest
    with pytest.raises(RuntimeError, match="provider down"):
        se.evaluate_shadow_evidence(HID, base_config={}, promotions=[], cell=None, validation_results=[])


def test_evidence_dict_has_exactly_the_locked_fields():
    with patch("backtest.shadow_outcome_evidence.evaluate_all_requests_for_hypothesis", return_value=[]), \
         patch("backtest.shadow_outcome_evidence.evaluate_canonical_baseline_statistic",
               return_value=_baseline(False, None, "NO_CANONICAL_CELL")):
        evidence = se.evaluate_shadow_evidence(
            HID, base_config={}, promotions=[], cell=None, validation_results=[],
        )
    assert set(evidence.keys()) == {
        "hypothesis_id", "n_t", "tp_count", "sl_count", "p_value",
        "divergence_statistic_valid", "baseline_p", "baseline_failure_reason",
        "request_ids", "canonical_identity_state", "hypothesis_fingerprint", "research_code_commit",
    }


# ---------- structural: no scope creep --------------------------------------


def _source_without_docstrings() -> str:
    import inspect
    import re
    source = inspect.getsource(se)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_module_has_no_try_except():
    body = _source_without_docstrings()
    assert "try:" not in body
    assert "except" not in body


def test_module_never_writes_to_storage():
    body = _source_without_docstrings()
    forbidden = ("try_insert", "record_", "update_cell", "INSERT INTO", "UPDATE ")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_evidence unexpectedly references {pattern!r}"


def test_no_policy_constants_or_downstream_consumers_referenced():
    body = _source_without_docstrings()
    forbidden = (
        "DIVERGENCE_VERDICT_COMPUTED", "compose_shadow_record", "build_shadow_record",
        "n_eff", "k_eff", "classify_significance", "bonferroni_alpha",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_evidence unexpectedly references {pattern!r}"
