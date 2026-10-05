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

from backtest.shadow_outcome_resolver import resolve_decision_outcome
from backtest.shadow_outcome_terminality import TERMINAL_CONFIRMED, assess_terminality
from backtest.shadow_outcome_verification import verify_historical_stability
from storage.shadow_decision_snapshot import (
    get_snapshot_by_request_id,
    list_request_ids_for_hypothesis,
)
from storage.shadow_outcome_observation import list_observations_for_request

__all__ = ["evaluate_all_requests_for_hypothesis", "count_terminal_confirmed"]


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
    call per request_id. Returns one assess_terminality() result dict
    per request_id enumerated by storage.shadow_decision_snapshot.
    list_request_ids_for_hypothesis() (complete, no LIMIT), in that
    enumeration's own order.

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
        results.append(assess_terminality(latest_observation, previous_observation, latest_verification))
    return results


def count_terminal_confirmed(terminality_results: list[dict[str, Any]]) -> int:
    """PURE -- no I/O, no randomness. Counts how many of the
    caller-supplied assess_terminality() result dicts have
    terminality_state == TERMINAL_CONFIRMED. This IS n_T(H) (docs/
    SHADOW_EVIDENCE_UNIT_CLOSURE.md) when `terminality_results` is the
    WHOLE list evaluate_all_requests_for_hypothesis() returned for one
    hypothesis_id -- never a hand-filtered subset."""
    return sum(1 for result in terminality_results if result["terminality_state"] == TERMINAL_CONFIRMED)
