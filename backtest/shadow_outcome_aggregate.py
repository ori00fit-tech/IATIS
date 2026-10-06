"""
backtest/shadow_outcome_aggregate.py
---------------------------------------
Hypothesis Discovery Engine -- Observed Evidence Aggregate Composition
(operator's own locked Design Gate, built on the Observed Evidence Input
Contract and Gate 0A: Complete Request Enumeration).

TWO DISTINCT LAYERS, never merged into one function (operator's own
locked separation):

  evaluate_all_requests_for_hypothesis() -- ORCHESTRATION, impure. Owns
      every piece of I/O this phase needs: one storage read to enumerate
      request_ids (storage.shadow_decision_snapshot.
      list_request_ids_for_hypothesis() -- complete, no LIMIT, per Gate
      0A), then per request_id: a storage read for the snapshot, a LIVE
      call to resolve_decision_outcome() for `latest_observation` (never
      cached, never read from storage -- the Observed Evidence Input
      Contract's own locked rule), a storage read for stored prior
      observations, and a LIVE network call to verify_historical_
      stability() for `latest_verification`. Composes exactly one
      assess_terminality() call per request_id.

  count_terminal_confirmed() -- PURE. No I/O, no randomness. Takes the
      WHOLE list of assess_terminality() results already produced above
      and counts how many reached TERMINAL_CONFIRMED -- the same plain
      counting style as backtest.shadow_observation.
      compute_shadow_observed_profile(), reused as a precedent, not
      imported (that function counts a structurally different thing).

  compute_observed_win_rate() -- PURE. No I/O, no randomness. Observed
      Win/Loss Statistic Design (operator's own locked Design Gate,
      built on this module's own population guarantee below).

OUTCOME IS NOW CARRIED THROUGH (operator's own locked extension, this
Design Gate): each dict evaluate_all_requests_for_hypothesis() returns is
no longer bare assess_terminality() output -- it is that SAME dict,
unchanged, plus one additional key, `outcome`, copied verbatim from
`latest_observation["outcome"]`. assess_terminality()'s own contract is
NOT touched or re-implemented; this is purely an additive widening of
THIS module's own orchestration output, done here because this is the
one place that still has `latest_observation` in hand before it would
otherwise be discarded.

BAR_TIME/RESOLVED_BAR_TIME ARE NOW ALSO CARRIED THROUGH (operator's own
locked extension, Design Gate "Export Exposure Window Evidence",
2026-10): the SAME additive-widening precedent as `outcome` above, for
the SAME reason -- this is the one place that still has both `snapshot`
(carrying `bar_time`) and `latest_observation` (carrying
`resolved_bar_time`) in hand before either would otherwise be discarded.
Both are copied verbatim, unconditionally, for EVERY result regardless
of terminality_state -- no filtering happens at this layer.
`resolved_bar_time` is `None` whenever `latest_observation["outcome"]` is
not TP_HIT/SL_HIT (resolve_decision_outcome()'s own locked contract,
reused verbatim here, never re-derived). Consumers that need the
TERMINAL_CONFIRMED-only population guarantee that `resolved_bar_time` is
never None (backtest.shadow_outcome_evidence.evaluate_shadow_evidence(),
specifically) impose that filtering themselves, one layer up -- this
module makes no population claim about either new key.

POPULATION GUARANTEE (operator's own locked Gate 0 finding, reused
verbatim, not re-derived): TERMINAL_CONFIRMED is only reachable when
`latest_observation["outcome"]` is TP_HIT or SL_HIT -- verification_
attempted requires `walk_ohlc is not None`, and walk_ohlc is captured
for TP_HIT/SL_HIT only (backtest.shadow_outcome_revision's own locked
scope). So among TERMINAL_CONFIRMED results, TP_HIT_count + SL_HIT_count
== count_terminal_confirmed(...) == n_T(H) exactly -- the SAME
population backtest.hypothesis_baseline_statistic's own tp_sl_win_rate
is computed over (TP+TP_GAP vs SL+SL_GAP, FORCED_CLOSE excluded). No
population-mismatch repair is needed here, unlike the original,
rejected §9 win_rate proposal.

FAIL-CLOSED CONTRACT-DRIFT GUARD (operator's own locked decision):
compute_observed_win_rate() never silently ignores a TERMINAL_CONFIRMED
result whose `outcome` is neither TP_HIT nor SL_HIT -- structurally this
should never happen (see the population guarantee above), so if it ever
does, that is evidence of a drift between this module's own assumptions
and assess_terminality()'s/resolve_decision_outcome()'s actual behavior.
It raises ShadowOutcomeAggregateError rather than producing a statistic
over a population that silently stopped matching the locked assumption.

STATISTIC DEFINITION vs. VERDICT THRESHOLD (operator's own locked
separation, NOT reopened here): compute_observed_win_rate() returns only
the ratio itself (or None if the TERMINAL_CONFIRMED population is
empty). It never compares against 0.65 or any other threshold, never
sets DIVERGENCE_VERDICT_COMPUTED, and is never wired into backtest.
shadow_record or backtest.promotion_gate in this phase -- each is an
explicitly separate, future, independently-authorized Design Gate.

FAIL-FAST, NO ISOLATION (operator's own locked decision, adopting
backtest.shadow_observation.run_shadow_cycle()'s own existing,
independently-reached precedent verbatim): an exception raised while
evaluating ANY one request_id -- a provider fetch failure inside
resolve_decision_outcome()/verify_historical_stability(), a structural
ShadowOutcomeResolverError/ShadowOutcomeTerminalityError/
ShadowOutcomeRevisionError, anything -- propagates completely unchanged,
immediately aborting evaluation of every remaining request_id in the
same call. There is no partial result: either every request_id for this
hypothesis was evaluated, or evaluate_all_requests_for_hypothesis()
raises and callers get no list at all. Isolating one bad request from
the rest (so a single provider hiccup doesn't block the whole
hypothesis) is explicitly out of scope -- that would be retry/scheduling
policy, same boundary run_shadow_cycle() already drew for itself.

`_select_previous()`'s semantics (operator's own locked decision): the
newest row in storage.shadow_outcome_observation.
list_observations_for_request()'s own existing result (already ordered
newest-first by `seq DESC`, never re-ordered here) that does not share
`latest_observation`'s own `evaluated_at` -- nothing more. No fallback
to `seq`/`request_id`/`outcome` comparison, no new backfill/replay
semantics invented, no change to the storage API's own ordering
contract. Given no production writer exists yet for research_shadow_
outcome_observations (confirmed, Gate 0/Gate 0A), `stored` is `[]` for
every real request_id today, so `previous_observation` is always `None`
in practice -- a documented consequence, not a bug in this function.

NON-NEGOTIABLE (operator's own locked scope boundary): no storage write
of any kind anywhere in this module (no try_insert, no record_*). No
import of backtest.promotion_gate, backtest.policy_health, backtest.
hypothesis_baseline_statistic, execution.authorization, execution.
trade_executor, scheduler.py, or main.py. No try/except anywhere in this
module -- every exception from every call it makes propagates completely
unhandled, by design.
"""
from __future__ import annotations

from typing import Any

from backtest.multiple_testing import binomial_lower_tail_p_value
from backtest.shadow_outcome_resolver import SL_HIT, TP_HIT, resolve_decision_outcome
from backtest.shadow_outcome_terminality import TERMINAL_CONFIRMED, assess_terminality
from backtest.shadow_outcome_verification import verify_historical_stability
from storage.shadow_decision_snapshot import (
    get_snapshot_by_request_id,
    list_request_ids_for_hypothesis,
)
from storage.shadow_outcome_observation import list_observations_for_request

__all__ = [
    "ShadowOutcomeAggregateError",
    "evaluate_all_requests_for_hypothesis",
    "count_terminal_confirmed",
    "compute_observed_win_rate",
    "count_terminal_confirmed_by_outcome",
    "compute_catastrophic_divergence_p_value",
]


class ShadowOutcomeAggregateError(Exception):
    """Structural misuse / contract-drift guard only -- raised by
    compute_observed_win_rate() when a TERMINAL_CONFIRMED result's own
    `outcome` is neither TP_HIT nor SL_HIT. Structurally impossible per
    assess_terminality()'s own locked rules (TERMINAL_CONFIRMED requires
    walk_ohlc, captured for TP_HIT/SL_HIT only) -- if it ever happens,
    this is evidence of contract drift, never an ordinary data gap, and
    must fail closed rather than be silently ignored."""


def _select_previous(stored: list[dict[str, Any]], latest_observation: dict[str, Any]) -> dict[str, Any] | None:
    """The newest row in `stored` (already newest-first, per
    list_observations_for_request()'s own ordering, never re-sorted
    here) whose `evaluated_at` does not match `latest_observation`'s own
    -- the sole exclusion rule. `None` if no such row exists."""
    candidates = [row for row in stored if row["evaluated_at"] != latest_observation["evaluated_at"]]
    return candidates[0] if candidates else None


def evaluate_all_requests_for_hypothesis(
    hypothesis_id: str, *, base_config: dict[str, Any], api_key: str | None = None,
) -> list[dict[str, Any]]:
    """ORCHESTRATION -- impure, performs storage reads and a live network
    call per request_id. Returns one dict per request_id enumerated by
    storage.shadow_decision_snapshot.list_request_ids_for_hypothesis()
    (complete, no LIMIT), in that enumeration's own order: exactly
    assess_terminality()'s own return shape ({request_id, hypothesis_id,
    terminality_state, relationship}), PLUS three additional keys --
    `outcome` (copied verbatim from `latest_observation["outcome"]`),
    `bar_time` (copied verbatim from `snapshot["bar_time"]`), and
    `resolved_bar_time` (copied verbatim from
    `latest_observation["resolved_bar_time"]`) -- this module's own
    additive widening (see module docstring); assess_terminality()'s own
    contract is untouched. All three are carried through unconditionally
    for every result, with no filtering by terminality_state at this
    layer.

    FAIL-FAST: any exception raised while evaluating one request_id
    propagates immediately and unchanged -- no partial list is ever
    returned, no request is skipped or isolated."""
    request_ids = list_request_ids_for_hypothesis(hypothesis_id)
    results: list[dict[str, Any]] = []
    for request_id in request_ids:
        snapshot = get_snapshot_by_request_id(request_id)
        latest_observation = resolve_decision_outcome(snapshot, base_config=base_config)
        stored = list_observations_for_request(request_id)
        previous_observation = _select_previous(stored, latest_observation)
        latest_verification = verify_historical_stability(
            snapshot, latest_observation, base_config=base_config, api_key=api_key,
        )
        terminality_result = assess_terminality(latest_observation, previous_observation, latest_verification)
        results.append({
            **terminality_result,
            "outcome": latest_observation["outcome"],
            "bar_time": snapshot["bar_time"],
            "resolved_bar_time": latest_observation["resolved_bar_time"],
        })
    return results


def count_terminal_confirmed(terminality_results: list[dict[str, Any]]) -> int:
    """PURE -- no I/O, no randomness. Counts how many of the
    caller-supplied result dicts have terminality_state ==
    TERMINAL_CONFIRMED. This IS n_T(H) (docs/
    SHADOW_EVIDENCE_UNIT_CLOSURE.md) when `terminality_results` is the
    WHOLE list evaluate_all_requests_for_hypothesis() returned for one
    hypothesis_id -- never a hand-filtered subset."""
    return sum(1 for result in terminality_results if result["terminality_state"] == TERMINAL_CONFIRMED)


def compute_observed_win_rate(results: list[dict[str, Any]]) -> float | None:
    """PURE -- no I/O, no randomness, no threshold comparison.
    `results` is evaluate_all_requests_for_hypothesis()'s own return
    list (each dict carrying `outcome` alongside assess_terminality()'s
    own fields) -- never a hand-filtered subset.

    Returns TP_HIT_count / (TP_HIT_count + SL_HIT_count) over
    TERMINAL_CONFIRMED results only -- `None` if that population is
    empty (never a fabricated 0.0). TP_HIT_count + SL_HIT_count equals
    count_terminal_confirmed(results) exactly, by the population
    guarantee this module's own docstring locks: TERMINAL_CONFIRMED is
    only ever reachable for a TP_HIT/SL_HIT outcome.

    Fail-closed contract-drift guard: raises ShadowOutcomeAggregateError
    if any TERMINAL_CONFIRMED result's `outcome` is neither TP_HIT nor
    SL_HIT, rather than silently excluding it from the ratio."""
    tp = 0
    sl = 0
    for result in results:
        if result["terminality_state"] != TERMINAL_CONFIRMED:
            continue
        outcome = result["outcome"]
        if outcome == TP_HIT:
            tp += 1
        elif outcome == SL_HIT:
            sl += 1
        else:
            raise ShadowOutcomeAggregateError(
                f"compute_observed_win_rate: TERMINAL_CONFIRMED result for request_id "
                f"{result.get('request_id')!r} has outcome {outcome!r} -- only TP_HIT/SL_HIT "
                f"are structurally possible here; this indicates a contract drift between "
                f"assess_terminality() and this aggregator, not an ordinary data gap."
            )
    denominator = tp + sl
    if denominator == 0:
        return None
    return tp / denominator


def count_terminal_confirmed_by_outcome(results: list[dict[str, Any]]) -> dict[str, int]:
    """PURE -- no I/O, no randomness. Raw {tp_count, sl_count} over
    TERMINAL_CONFIRMED results only (k/n Raw-Count Extraction Design
    Gate, locked 2026-10) -- the k/n inputs backtest.multiple_testing.
    binomial_lower_tail_p_value() needs, exposed separately from any
    computed ratio (mirrors backtest.metrics.py's by_exit_reason style:
    raw counts, no derived statistic baked in).

    tp_count + sl_count == count_terminal_confirmed(results) exactly,
    by the same population guarantee compute_observed_win_rate() above
    already locks. Same fail-closed contract-drift guard: raises
    ShadowOutcomeAggregateError if any TERMINAL_CONFIRMED result's
    `outcome` is neither TP_HIT nor SL_HIT.

    Unlike compute_observed_win_rate(), an empty/zero population
    returns {"tp_count": 0, "sl_count": 0} -- never `None`. A raw count
    of zero is an honest fact; it is only a RATIO (0/0) that is
    undefined, and this function computes no ratio.

    Deliberately independent of compute_observed_win_rate() -- neither
    calls the other, by this Design Gate's own explicit scope lock,
    to avoid any behavioral/implementation churn on that already-
    accepted function."""
    tp_count = 0
    sl_count = 0
    for result in results:
        if result["terminality_state"] != TERMINAL_CONFIRMED:
            continue
        outcome = result["outcome"]
        if outcome == TP_HIT:
            tp_count += 1
        elif outcome == SL_HIT:
            sl_count += 1
        else:
            raise ShadowOutcomeAggregateError(
                f"count_terminal_confirmed_by_outcome: TERMINAL_CONFIRMED result for request_id "
                f"{result.get('request_id')!r} has outcome {outcome!r} -- only TP_HIT/SL_HIT "
                f"are structurally possible here; this indicates a contract drift between "
                f"assess_terminality() and this aggregator, not an ordinary data gap."
            )
    return {"tp_count": tp_count, "sl_count": sl_count}


def compute_catastrophic_divergence_p_value(
    results: list[dict[str, Any]], p: float | None,
) -> float | None:
    """PURE -- no I/O, no randomness, no significance classification
    (p-value Composition Design Gate, locked 2026-10). Composes
    count_terminal_confirmed_by_outcome() (k=tp_count,
    n=tp_count+sl_count) with backtest.multiple_testing.
    binomial_lower_tail_p_value().

    `p` is the caller-supplied baseline statistic (exactly
    baseline["tp_sl_win_rate"] from backtest.hypothesis_baseline_
    statistic.evaluate_canonical_baseline_statistic()) -- taken
    verbatim, never recomputed, never substituted with a fallback.

    Returns None if `p` is None (no valid baseline -- the caller's own
    divergence_statistic_valid was False) -- checked explicitly here,
    before any call to binomial_lower_tail_p_value(), since that
    function's own `0 <= p <= 1` guard cannot be evaluated against
    None. Also returns None when n < 1 (no TERMINAL_CONFIRMED evidence
    yet) -- no second check needed for that case: it already flows
    through binomial_lower_tail_p_value()'s own existing `n < 1` guard,
    the single source of truth for that behavior.

    NOT in scope here: Bonferroni/family-size correction,
    classify_significance(), diverged_catastrophically,
    DIVERGENCE_VERDICT_COMPUTED, build_shadow_record(), any writer, any
    execution/broker path."""
    if p is None:
        return None
    counts = count_terminal_confirmed_by_outcome(results)
    k = counts["tp_count"]
    n = counts["tp_count"] + counts["sl_count"]
    return binomial_lower_tail_p_value(k, n, p)
