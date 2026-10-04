"""tests/test_hypothesis_baseline.py -- tests for backtest/
hypothesis_baseline.py (Expected-Baseline Canonicity Design Gate): the
locked CANONICAL_CELL_RESOLVED / NO_PROMOTION_EXISTS /
MULTIPLE_PROMOTIONS_AMBIGUOUS classification, the PROMOTED-only filter
(NOT_PROMOTED/BLOCKED rows never count), the repeated-re-evaluation-is-
not-ambiguity rule, the no-invented-tie-break rule, and independence
from storage/execution/scheduler logic."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import hypothesis_baseline as hb


def _promotion(**overrides) -> dict:
    base = {
        "promotion_id": "PROMOTION-aaaa1111", "hypothesis_id": "CONFLUENCE-HYPOTHESIS-x",
        "mission_id": "MISSION-1", "cell_id": "CELL-1", "symbol": "XAUUSD",
        "decision": "PROMOTED", "decision_reason": "Stage B confirmed same-symbol.",
    }
    base.update(overrides)
    return base


# --- NO_PROMOTION_EXISTS -----------------------------------------------------


def test_empty_promotions_list_is_no_promotion_exists():
    result = hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", [])
    assert result["canonicity_state"] == hb.NO_PROMOTION_EXISTS
    assert result["cell_id"] is None
    assert result["conflicting_cell_ids"] is None


def test_only_not_promoted_and_blocked_rows_is_no_promotion_exists():
    """A Promotion record existing is not the same fact as the hypothesis
    having been promoted -- NOT_PROMOTED/BLOCKED rows must never count
    toward resolving a baseline."""
    promotions = [
        _promotion(promotion_id="PROMOTION-1", mission_id="MISSION-1", cell_id="CELL-1",
                   decision="BLOCKED", decision_reason="Stage B still VALIDATING."),
        _promotion(promotion_id="PROMOTION-2", mission_id="MISSION-1", cell_id="CELL-1",
                   decision="NOT_PROMOTED", decision_reason="Stage B did not confirm."),
    ]
    result = hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", promotions)
    assert result["canonicity_state"] == hb.NO_PROMOTION_EXISTS
    assert result["cell_id"] is None


# --- CANONICAL_CELL_RESOLVED --------------------------------------------------


def test_single_promoted_record_resolves_canonically():
    promotions = [_promotion()]
    result = hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", promotions)
    assert result["canonicity_state"] == hb.CANONICAL_CELL_RESOLVED
    assert result["cell_id"] == "CELL-1"
    assert result["conflicting_cell_ids"] is None


def test_repeated_reevaluation_of_the_same_cell_is_not_ambiguous():
    """The same (hypothesis, mission, cell) triple can legitimately
    produce several PROMOTED rows over time (re-evaluated as the
    forensic ledger grows) -- that is NOT ambiguity, since they all
    agree on the same cell_id."""
    promotions = [
        _promotion(promotion_id="PROMOTION-1", cell_id="CELL-1", decision="PROMOTED"),
        _promotion(promotion_id="PROMOTION-2", cell_id="CELL-1", decision="PROMOTED"),
        _promotion(promotion_id="PROMOTION-3", cell_id="CELL-1", decision="PROMOTED"),
    ]
    result = hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", promotions)
    assert result["canonicity_state"] == hb.CANONICAL_CELL_RESOLVED
    assert result["cell_id"] == "CELL-1"


def test_promoted_cell_resolved_even_alongside_not_promoted_and_blocked_rows():
    promotions = [
        _promotion(promotion_id="PROMOTION-1", cell_id="CELL-1", decision="BLOCKED"),
        _promotion(promotion_id="PROMOTION-2", cell_id="CELL-1", decision="PROMOTED"),
        _promotion(promotion_id="PROMOTION-3", mission_id="MISSION-2", cell_id="CELL-2",
                   decision="NOT_PROMOTED"),
    ]
    result = hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", promotions)
    assert result["canonicity_state"] == hb.CANONICAL_CELL_RESOLVED
    assert result["cell_id"] == "CELL-1"


# --- MULTIPLE_PROMOTIONS_AMBIGUOUS -------------------------------------------


def test_two_distinct_promoted_cells_is_ambiguous_no_tie_break():
    promotions = [
        _promotion(promotion_id="PROMOTION-1", mission_id="MISSION-1", cell_id="CELL-1",
                   decision="PROMOTED"),
        _promotion(promotion_id="PROMOTION-2", mission_id="MISSION-2", cell_id="CELL-2",
                   decision="PROMOTED"),
    ]
    result = hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", promotions)
    assert result["canonicity_state"] == hb.MULTIPLE_PROMOTIONS_AMBIGUOUS
    assert result["cell_id"] is None  # no invented tie-break, ever
    assert result["conflicting_cell_ids"] == ["CELL-1", "CELL-2"]


def test_three_distinct_promoted_cells_reports_all_conflicting_ids():
    promotions = [
        _promotion(promotion_id=f"PROMOTION-{i}", mission_id=f"MISSION-{i}", cell_id=f"CELL-{i}",
                   decision="PROMOTED")
        for i in (3, 1, 2)  # deliberately out of order -- result must still be sorted
    ]
    result = hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", promotions)
    assert result["canonicity_state"] == hb.MULTIPLE_PROMOTIONS_AMBIGUOUS
    assert result["conflicting_cell_ids"] == ["CELL-1", "CELL-2", "CELL-3"]


# --- structural misuse -------------------------------------------------------


def test_hypothesis_id_mismatch_in_supplied_list_raises():
    promotions = [_promotion(hypothesis_id="CONFLUENCE-HYPOTHESIS-DIFFERENT")]
    with pytest.raises(hb.HypothesisBaselineError, match="does not match"):
        hb.resolve_canonical_baseline("CONFLUENCE-HYPOTHESIS-x", promotions)


# --- structural: no coupling with storage/execution/scheduler --------------


def _source_without_docstrings() -> str:
    source = inspect.getsource(hb)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_storage_execution_scheduler_or_main_import():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "from storage",
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"hypothesis_baseline unexpectedly references {pattern!r}"


def test_never_queries_promotions_itself():
    """Pure function discipline: the caller already has the promotions
    list in hand -- this module never fetches it."""
    body = _source_without_docstrings()
    assert "list_promotions_for_hypothesis" not in body
