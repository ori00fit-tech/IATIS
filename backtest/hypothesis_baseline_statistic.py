"""
backtest/hypothesis_baseline_statistic.py
---------------------------------------
Hypothesis Discovery Engine -- Canonical Baseline Statistic (operator's
own locked Design Gate, following the Canonical Baseline Resolution gate
below it in the chain).

THE PROBLEM, precisely: `resolve_canonical_baseline()` (backtest/
hypothesis_baseline.py) resolves WHICH cell_id is canonical for a
PROMOTED hypothesis, but explicitly defers extracting any metric from
that cell as its own, separate, future step (its own docstring, lines
49-54). This module is that step -- but ONLY as far as computing
`tp_sl_win_rate` from `BacktestMetrics.by_exit_reason` (backtest/
metrics.py), restricted to the TP_HIT/SL_HIT-equivalent population that
SHADOW's TERMINAL_CONFIRMED evidence unit can actually prove (docs/
SHADOW_EVIDENCE_UNIT_CLOSURE.md).

CANONICAL BASELINE SOURCE (operator's own locked decision): a PROMOTED
hypothesis's canonical baseline is its Stage B (same-symbol,
out-of-sample) validation metrics -- `research_mission_validation_
results.metrics_json` for the row matching the canonical cell's own
`symbol`, reached via that cell's `stage_b_validation_id`. `stage_a_
metrics_json` (a single, small, in-sample Optuna trial) is NEVER treated
as canonical, regardless of availability -- it is the provisional
screening-stage evidence, not the evidence that actually backs a
PROMOTED decision.

EIGHT GUARDS (locked, fail-fast, in this exact order -- each is its own
distinct `failure_reason`, never collapsed into a different one):
  1. NO_CANONICAL_CELL            -- resolve_canonical_baseline() did not
                                      reach CANONICAL_CELL_RESOLVED.
  1.5 CELL_NOT_FOUND              -- a cell_id was resolved, but the
                                      caller-supplied `cell` is None (a
                                      storage inconsistency, never
                                      silently treated as "no cell_id").
  2. SOURCE_HYPOTHESIS_MISMATCH   -- defensive re-check: `cell["source_
                                      hypothesis_id"] != hypothesis_id`.
                                      Not a fallback to a different cell
                                      -- a hard stop.
  3. NO_STAGE_B_VALIDATION        -- `cell.get("stage_b_validation_id")`
                                      is absent.
  4. STAGE_B_NOT_CONFIRMED        -- `cell.get("stage_b_verdict") !=
                                      SAME_SYMBOL_CONFIRMED`. This is the
                                      SOLE check for Stage B confirmation
                                      -- no second lookup into storage.
                                      research_mission_validations'
                                      own `status`/`overall_verdict` is
                                      ever performed here (operator's own
                                      locked decision: avoid a third
                                      table).
  5/6. VALIDATION_RECORD_NOT_FOUND -- no row in the caller-supplied
                                      `validation_results` list has
                                      `symbol == cell["symbol"]`. The
                                      symbol match happens INSIDE this
                                      function, never pre-filtered by the
                                      caller (operator's own locked
                                      decision) -- `(validation_id,
                                      symbol)` is the table's own PRIMARY
                                      KEY, so at most one match is ever
                                      structurally possible.
  7. BY_EXIT_REASON_MISSING       -- the matched row's `metrics_json` is
                                      None/empty/unparseable, OR parses
                                      but has no `by_exit_reason` key, OR
                                      `by_exit_reason` is itself empty.
                                      Never defaulted to an empty dict
                                      and read as "zero everywhere".
  8. ZERO_DENOMINATOR             -- `TP.trades + TP_GAP.trades +
                                      SL.trades + SL_GAP.trades == 0`
                                      (e.g. every trade in that Stage B
                                      run was FORCED_CLOSE). `tp_sl_win_
                                      rate` is never fabricated as 0.0 in
                                      this case, and FORCED_CLOSE is
                                      never counted as a loss.

Any guard failure => {"divergence_statistic_valid": False,
"tp_sl_win_rate": None, "failure_reason": <one of the above>}. No
fallback path, no retroactive reconstruction of missing historical data,
no re-running any backtest/validation from here.

MECHANISM (locked): a pure function over CALLER-SUPPLIED inputs only --
no storage read of its own. The caller already has (or has fetched)
`promotions` (storage.hypothesis_promotion.list_promotions_for_
hypothesis()), `cell` (storage.research_matrix.get_cell(), or None),
and `validation_results` (storage.research_mission_validations.
validation_results(), the WHOLE unfiltered list for that validation_id --
never a hand-filtered subset, since this function itself does the
symbol match).

NON-NEGOTIABLE (operator's own locked scope boundary): this module never
imports backtest.promotion_gate, backtest.policy_health, backtest.
execution_attribution, execution.authorization, execution.trade_executor,
storage.outcome_tracker, storage.shadow_book, scheduler.py, or main.py.
It never imports any storage module itself. It reuses resolve_canonical_
baseline() and SAME_SYMBOL_CONFIRMED verbatim, never re-implements
either. It makes no connection whatsoever to backtest.shadow_outcome_
terminality or backtest.shadow_outcome_revision -- this module computes
only the BASELINE side; the observed (SHADOW) side's own n_T/completed
gate (docs/SHADOW_EVIDENCE_UNIT_CLOSURE.md) is wholly separate and this
module makes no reference to it.
"""
from __future__ import annotations

import json
from typing import Any

from backtest.hypothesis_baseline import CANONICAL_CELL_RESOLVED, resolve_canonical_baseline
from backtest.mission_validator import SAME_SYMBOL_CONFIRMED

NO_CANONICAL_CELL = "NO_CANONICAL_CELL"
CELL_NOT_FOUND = "CELL_NOT_FOUND"
SOURCE_HYPOTHESIS_MISMATCH = "SOURCE_HYPOTHESIS_MISMATCH"
NO_STAGE_B_VALIDATION = "NO_STAGE_B_VALIDATION"
STAGE_B_NOT_CONFIRMED = "STAGE_B_NOT_CONFIRMED"
VALIDATION_RECORD_NOT_FOUND = "VALIDATION_RECORD_NOT_FOUND"
BY_EXIT_REASON_MISSING = "BY_EXIT_REASON_MISSING"
ZERO_DENOMINATOR = "ZERO_DENOMINATOR"

__all__ = [
    "NO_CANONICAL_CELL", "CELL_NOT_FOUND", "SOURCE_HYPOTHESIS_MISMATCH",
    "NO_STAGE_B_VALIDATION", "STAGE_B_NOT_CONFIRMED", "VALIDATION_RECORD_NOT_FOUND",
    "BY_EXIT_REASON_MISSING", "ZERO_DENOMINATOR",
    "evaluate_canonical_baseline_statistic",
]

# The only two exit_reason literals (backtesting/backtest_engine.py) that
# represent a TP_HIT-equivalent close -- reused verbatim from the locked
# by_exit_reason contract (backtest/metrics.py), never redefined.
_TP_KEYS = ("TP", "TP_GAP")
# The only two that represent an SL_HIT-equivalent close. FORCED_CLOSE is
# deliberately excluded from both -- it never enters this statistic.
_SL_KEYS = ("SL", "SL_GAP")


def _invalid(hypothesis_id: str, failure_reason: str) -> dict[str, Any]:
    return {
        "hypothesis_id": hypothesis_id,
        "divergence_statistic_valid": False,
        "tp_sl_win_rate": None,
        "failure_reason": failure_reason,
    }


def evaluate_canonical_baseline_statistic(
    hypothesis_id: str,
    promotions: list[dict[str, Any]],
    cell: dict[str, Any] | None,
    validation_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """The SOLE entry point. `promotions` is the WHOLE list of Promotion
    records for this hypothesis_id (as storage.hypothesis_promotion.
    list_promotions_for_hypothesis() returns). `cell` is storage.
    research_matrix.get_cell() applied to whatever cell_id resolve_
    canonical_baseline() resolves from `promotions` -- or None if the
    caller never looked it up (e.g. no canonical cell resolved) or the
    lookup itself returned nothing. `validation_results` is the WHOLE,
    unfiltered list storage.research_mission_validations.validation_
    results() returns for the cell's own `stage_b_validation_id` -- never
    a hand-filtered subset.

    Returns exactly: {hypothesis_id, divergence_statistic_valid,
    tp_sl_win_rate, failure_reason}. `tp_sl_win_rate` is non-None iff
    `divergence_statistic_valid` is True; `failure_reason` is non-None
    iff it is False.

    Pure -- no storage read or write, no network call, no randomness, no
    fallback path, no retroactive reconstruction of missing evidence.
    """
    baseline = resolve_canonical_baseline(hypothesis_id, promotions)
    if baseline["canonicity_state"] != CANONICAL_CELL_RESOLVED:
        return _invalid(hypothesis_id, NO_CANONICAL_CELL)

    if cell is None:
        return _invalid(hypothesis_id, CELL_NOT_FOUND)

    if cell.get("source_hypothesis_id") != hypothesis_id:
        return _invalid(hypothesis_id, SOURCE_HYPOTHESIS_MISMATCH)

    if not cell.get("stage_b_validation_id"):
        return _invalid(hypothesis_id, NO_STAGE_B_VALIDATION)

    if cell.get("stage_b_verdict") != SAME_SYMBOL_CONFIRMED:
        return _invalid(hypothesis_id, STAGE_B_NOT_CONFIRMED)

    symbol = cell.get("symbol")
    matching = [row for row in validation_results if row.get("symbol") == symbol]
    if not matching:
        return _invalid(hypothesis_id, VALIDATION_RECORD_NOT_FOUND)
    validation_result = matching[0]

    raw_metrics_json = validation_result.get("metrics_json")
    if not raw_metrics_json:
        return _invalid(hypothesis_id, BY_EXIT_REASON_MISSING)
    try:
        metrics = json.loads(raw_metrics_json)
    except (TypeError, ValueError):
        return _invalid(hypothesis_id, BY_EXIT_REASON_MISSING)

    by_exit_reason = metrics.get("by_exit_reason") if isinstance(metrics, dict) else None
    if not by_exit_reason:
        return _invalid(hypothesis_id, BY_EXIT_REASON_MISSING)

    def _trades(key: str) -> int:
        bucket = by_exit_reason.get(key)
        return bucket.get("trades", 0) if isinstance(bucket, dict) else 0

    tp_trades = sum(_trades(k) for k in _TP_KEYS)
    sl_trades = sum(_trades(k) for k in _SL_KEYS)
    denominator = tp_trades + sl_trades
    if denominator <= 0:
        return _invalid(hypothesis_id, ZERO_DENOMINATOR)

    return {
        "hypothesis_id": hypothesis_id,
        "divergence_statistic_valid": True,
        "tp_sl_win_rate": tp_trades / denominator,
        "failure_reason": None,
    }
