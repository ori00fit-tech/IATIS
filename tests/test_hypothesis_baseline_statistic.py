"""tests/test_hypothesis_baseline_statistic.py -- tests for backtest/
hypothesis_baseline_statistic.py (Canonical Baseline Statistic Design
Gate): the 8 locked fail-fast guards (NO_CANONICAL_CELL, CELL_NOT_FOUND,
SOURCE_HYPOTHESIS_MISMATCH, NO_STAGE_B_VALIDATION, STAGE_B_NOT_CONFIRMED,
VALIDATION_RECORD_NOT_FOUND, BY_EXIT_REASON_MISSING, ZERO_DENOMINATOR),
the tp_sl_win_rate computation itself, and independence from storage/
execution/scheduler logic and from the SHADOW/terminality chain."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from backtest import hypothesis_baseline_statistic as hbs
from backtest.mission_validator import SAME_SYMBOL_CONFIRMED

HID = "CONFLUENCE-HYPOTHESIS-x"


def _promotion(**overrides) -> dict:
    base = {
        "promotion_id": "PROMOTION-1", "hypothesis_id": HID,
        "mission_id": "MISSION-1", "cell_id": "CELL-1", "symbol": "XAUUSD",
        "decision": "PROMOTED", "decision_reason": "Stage B confirmed same-symbol.",
    }
    base.update(overrides)
    return base


def _cell(**overrides) -> dict:
    base = {
        "cell_id": "CELL-1", "source_hypothesis_id": HID, "symbol": "XAUUSD",
        "stage_b_validation_id": "VALIDATION-1", "stage_b_verdict": SAME_SYMBOL_CONFIRMED,
    }
    base.update(overrides)
    return base


def _by_exit_reason(tp=0, tp_gap=0, sl=0, sl_gap=0, forced_close=0) -> dict:
    out = {}
    if tp:
        out["TP"] = {"trades": tp, "wins": tp}
    if tp_gap:
        out["TP_GAP"] = {"trades": tp_gap, "wins": tp_gap}
    if sl:
        out["SL"] = {"trades": sl, "wins": 0}
    if sl_gap:
        out["SL_GAP"] = {"trades": sl_gap, "wins": 0}
    if forced_close:
        out["FORCED_CLOSE"] = {"trades": forced_close, "wins": 0}
    return out


def _validation_result(symbol="XAUUSD", by_exit_reason=None, metrics_json_override=None) -> dict:
    if metrics_json_override is not None:
        return {"symbol": symbol, "metrics_json": metrics_json_override}
    metrics = {"win_rate": 50.0}
    if by_exit_reason is not None:
        metrics["by_exit_reason"] = by_exit_reason
    return {"symbol": symbol, "metrics_json": json.dumps(metrics)}


# --- 1. NO_CANONICAL_CELL ---------------------------------------------------


def test_no_promotion_exists_is_no_canonical_cell():
    result = hbs.evaluate_canonical_baseline_statistic(HID, [], None, [])
    assert result == {
        "hypothesis_id": HID, "divergence_statistic_valid": False,
        "tp_sl_win_rate": None, "failure_reason": hbs.NO_CANONICAL_CELL,
    }


def test_ambiguous_promotions_is_no_canonical_cell():
    promotions = [
        _promotion(promotion_id="PROMOTION-1", cell_id="CELL-1"),
        _promotion(promotion_id="PROMOTION-2", cell_id="CELL-2", mission_id="MISSION-2"),
    ]
    result = hbs.evaluate_canonical_baseline_statistic(HID, promotions, None, [])
    assert result["failure_reason"] == hbs.NO_CANONICAL_CELL
    assert result["divergence_statistic_valid"] is False


# --- 1.5 CELL_NOT_FOUND ------------------------------------------------------


def test_resolved_cell_id_but_no_cell_supplied_is_cell_not_found():
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], None, [])
    assert result["failure_reason"] == hbs.CELL_NOT_FOUND


# --- 2. SOURCE_HYPOTHESIS_MISMATCH ------------------------------------------


def test_cell_source_hypothesis_mismatch_is_hard_stop_no_fallback():
    cell = _cell(source_hypothesis_id="CONFLUENCE-HYPOTHESIS-DIFFERENT")
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, [])
    assert result["failure_reason"] == hbs.SOURCE_HYPOTHESIS_MISMATCH


# --- 3. NO_STAGE_B_VALIDATION ------------------------------------------------


def test_missing_stage_b_validation_id():
    cell = _cell(stage_b_validation_id=None)
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, [])
    assert result["failure_reason"] == hbs.NO_STAGE_B_VALIDATION


# --- 4. STAGE_B_NOT_CONFIRMED ------------------------------------------------


def test_stage_b_verdict_not_confirmed():
    cell = _cell(stage_b_verdict="SAME_SYMBOL_NOT_CONFIRMED")
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, [])
    assert result["failure_reason"] == hbs.STAGE_B_NOT_CONFIRMED


# --- 5/6. VALIDATION_RECORD_NOT_FOUND ----------------------------------------


def test_no_validation_result_for_cell_symbol():
    cell = _cell(symbol="XAUUSD")
    validation_results = [_validation_result(symbol="EURUSD", by_exit_reason=_by_exit_reason(tp=10, sl=5))]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["failure_reason"] == hbs.VALIDATION_RECORD_NOT_FOUND


def test_symbol_match_happens_inside_function_not_preselected_by_caller():
    """The caller passes the WHOLE unfiltered validation_results list --
    the function itself finds the row matching cell['symbol']."""
    cell = _cell(symbol="XAUUSD")
    validation_results = [
        _validation_result(symbol="EURUSD", by_exit_reason=_by_exit_reason(tp=1, sl=1)),
        _validation_result(symbol="XAUUSD", by_exit_reason=_by_exit_reason(tp=10, sl=5)),
        _validation_result(symbol="BTCUSD", by_exit_reason=_by_exit_reason(tp=2, sl=2)),
    ]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["divergence_statistic_valid"] is True
    assert result["tp_sl_win_rate"] == pytest.approx(10 / 15)


# --- 7. BY_EXIT_REASON_MISSING -----------------------------------------------


def test_metrics_json_none_is_by_exit_reason_missing():
    cell = _cell()
    validation_results = [_validation_result(metrics_json_override=None)]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["failure_reason"] == hbs.BY_EXIT_REASON_MISSING


def test_metrics_json_malformed_is_by_exit_reason_missing():
    cell = _cell()
    validation_results = [_validation_result(metrics_json_override="{not valid json")]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["failure_reason"] == hbs.BY_EXIT_REASON_MISSING


def test_metrics_json_without_by_exit_reason_key():
    cell = _cell()
    validation_results = [_validation_result(by_exit_reason=None)]  # win_rate only, no by_exit_reason
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["failure_reason"] == hbs.BY_EXIT_REASON_MISSING


def test_by_exit_reason_present_but_empty_dict():
    cell = _cell()
    validation_results = [_validation_result(by_exit_reason={})]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["failure_reason"] == hbs.BY_EXIT_REASON_MISSING


# --- 8. ZERO_DENOMINATOR -----------------------------------------------------


def test_all_forced_close_is_zero_denominator_not_fabricated_zero():
    """Locked: FORCED_CLOSE never counts as a loss, and an all-FORCED_
    CLOSE sample must never produce tp_sl_win_rate=0.0."""
    cell = _cell()
    validation_results = [_validation_result(by_exit_reason=_by_exit_reason(forced_close=12))]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["failure_reason"] == hbs.ZERO_DENOMINATOR
    assert result["divergence_statistic_valid"] is False
    assert result["tp_sl_win_rate"] is None


def test_no_exit_reason_keys_at_all_is_zero_denominator():
    cell = _cell()
    validation_results = [_validation_result(by_exit_reason={"SOMETHING_UNEXPECTED": {"trades": 5, "wins": 1}})]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result["failure_reason"] == hbs.ZERO_DENOMINATOR


# --- success path -------------------------------------------------------------


def test_valid_statistic_combines_tp_and_tp_gap_vs_sl_and_sl_gap():
    cell = _cell()
    validation_results = [_validation_result(by_exit_reason=_by_exit_reason(
        tp=6, tp_gap=2, sl=1, sl_gap=1, forced_close=3,
    ))]
    result = hbs.evaluate_canonical_baseline_statistic(HID, [_promotion()], cell, validation_results)
    assert result == {
        "hypothesis_id": HID, "divergence_statistic_valid": True,
        "tp_sl_win_rate": pytest.approx(8 / 10), "failure_reason": None,
    }


def test_repeated_reevaluation_of_same_cell_still_resolves():
    """Multiple PROMOTED rows for the same cell_id (re-evaluated ledger
    growth) are not ambiguous -- mirrors resolve_canonical_baseline()'s
    own locked rule, reused verbatim here."""
    promotions = [
        _promotion(promotion_id="PROMOTION-1", cell_id="CELL-1"),
        _promotion(promotion_id="PROMOTION-2", cell_id="CELL-1"),
    ]
    cell = _cell()
    validation_results = [_validation_result(by_exit_reason=_by_exit_reason(tp=10, sl=10))]
    result = hbs.evaluate_canonical_baseline_statistic(HID, promotions, cell, validation_results)
    assert result["divergence_statistic_valid"] is True
    assert result["tp_sl_win_rate"] == pytest.approx(0.5)


# --- structural: no coupling with storage/execution/scheduler/SHADOW --------


def _source_without_docstrings() -> str:
    source = inspect.getsource(hbs)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_storage_execution_scheduler_main_or_promotion_gate_import():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "from storage",
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from backtest.execution_attribution", "import backtest.execution_attribution",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"hypothesis_baseline_statistic unexpectedly references {pattern!r}"


def test_no_coupling_with_shadow_terminality_or_revision_chain():
    body = _source_without_docstrings()
    assert "shadow_outcome_terminality" not in body
    assert "shadow_outcome_revision" not in body


def test_never_queries_promotions_cell_or_validation_results_itself():
    """Pure function discipline: the caller already has all three inputs
    in hand -- this module never fetches any of them."""
    body = _source_without_docstrings()
    assert "list_promotions_for_hypothesis" not in body
    assert "get_cell(" not in body
    assert "validation_results(" not in body
