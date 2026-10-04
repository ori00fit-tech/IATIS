"""
backtest/shadow_outcome_terminality.py
---------------------------------------
Hypothesis Discovery Engine -- Terminality Semantics (operator's own
locked Design Gate).

NO BORROWED PRECEDENT (operator's own locked Gate 0 finding): the
real-trade "closed" concept does not rest on anything structurally
stronger than what SHADOW already has -- both ultimately read OHLC and
trust it, and the one real-trade path with genuine broker confirmation
(`execution.reconciliation.reconcile_close_signal`) explicitly refuses
to resolve win/loss. So terminality here is built entirely from the
three already-accepted, independent facts: OHLC value stability (#1),
walk composition stability (#2), and cross-invocation outcome
relationship (#3). Nothing is reused from the real-trade pipeline.

MECHANISM (locked): a pure function over CALLER-SUPPLIED inputs only --
no storage read or write, no new schema, no persistence, no wiring into
any live/scheduled path. The caller already has (or has fetched) the
observation dicts and the verification result; this module only
combines them.

SCOPE -- PAIRWISE, CURRENT-STATE-ONLY (both locked explicitly, mirroring
Design Gate #3's own "pairwise only, no sequence analyzer" rule):
`assess_terminality()` takes exactly the LATEST observation, the
IMMEDIATELY PRECEDING observation (or None), and the latest
observation's own verify_historical_stability() result (or None) -- it
never scans a decision's full history. This is a deliberate choice,
not an oversight: a decision whose history is
TP_HIT -> SL_HIT (REVISED) -> SL_HIT (REPRODUCED) can reach
TERMINAL_CONFIRMED again once it resettles -- an earlier revision is
NOT permanently disqualifying (current-state-only, not sticky).

FOUR STATES, locked:

  TERMINAL_CONFIRMED -- every bar in the latest verification's `bars`
      is OHLC_STABLE, `composition_state` is COMPOSITION_STABLE, AND
      the relationship to the immediately preceding observation is
      OUTCOME_REPRODUCED. Requires an ACTUAL re-invocation to have
      reproduced the outcome -- not just a static-data check.

  PROVISIONAL -- stable OHLC/composition evidence exists, but
      reproduction is not yet established: either there is no
      preceding observation to compare against at all (first
      observation), or the relationship is LEGITIMATE_PROGRESSION
      (the decision is still evolving), or not every bar reached
      OHLC_STABLE (e.g. a VERIFICATION_DATA_GAP bar -- unconfirmed is
      never conflated with contradicted).

  CONTRADICTED -- ANY bar is OHLC_UNSTABLE, OR composition_state is
      COMPOSITION_UNSTABLE, OR the relationship to the preceding
      observation is OUTCOME_REVISED. A named, explicit signal --
      never silently folded into PROVISIONAL. Unstable raw data alone
      is sufficient for this state even with no preceding observation
      at all; it does not require a revision to have been observed.

  NOT_YET_ASSESSABLE -- no walk evidence exists at all (the underlying
      outcome was TIMEOUT/DATA_GAP/NOT_YET_ASSESSABLE), or no
      verification was ever attempted/possible (`latest_verification`
      is None, or its own `ohlc_state`/`composition_state` is
      NOT_YET_VERIFIABLE -- which happens precisely when the baseline
      provider isn't Twelve Data, inheriting the already-locked
      Historical Targeting scope restriction unchanged).

REPRODUCTION REQUIREMENT, locked as a MINIMUM, never a count: "at least
one OUTCOME_REPRODUCED" is a structural existence check (you need SOME
reproduction for "confirmed" to mean anything), not a magnitude
threshold in the family of 40/100/300 -- and it stays exactly that; no
larger count is required or configurable here.

STILL EXPLICITLY OUT OF SCOPE (operator's own locked boundary): any
persistence/schema change, any wiring into a live/scheduled path, any
catastrophic-divergence verdict, any promotion/lifecycle consequence,
any n/threshold/p-value/Bonferroni correction, any `shadow_record`
translation, and any governance response to CONTRADICTED or any other
state -- all of it is a purely descriptive fact, same as every prior
layer in this chain.

NON-NEGOTIABLE (operator's own locked scope boundary): this module
never imports backtest.promotion_gate, backtest.policy_health,
backtest.execution_attribution, execution.authorization,
execution.trade_executor, storage.outcome_tracker, storage.shadow_book,
scheduler.py, or main.py. It never imports storage.shadow_outcome_
observation (or any storage module) -- the caller already has both
observation dicts in hand. It never calls resolve_decision_outcome() or
verify_historical_stability() itself (that would re-derive or re-fetch,
not combine already-computed facts) -- it only calls
classify_cross_invocation_relationship(), reusing Design Gate #3's own
locked comparison logic rather than re-implementing it. A
ShadowOutcomeRevisionError raised by that call (request_id mismatch,
non-strictly-later ordering) propagates unchanged -- never caught,
never converted into a terminality state.
"""
from __future__ import annotations

from typing import Any

from backtest.shadow_outcome_revision import (
    OUTCOME_REPRODUCED,
    OUTCOME_REVISED,
    classify_cross_invocation_relationship,
)
from backtest.shadow_outcome_verification import (
    COMPOSITION_STABLE,
    COMPOSITION_UNSTABLE,
    NOT_YET_VERIFIABLE,
    OHLC_STABLE,
    OHLC_UNSTABLE,
)

TERMINAL_CONFIRMED = "TERMINAL_CONFIRMED"
PROVISIONAL = "PROVISIONAL"
CONTRADICTED = "CONTRADICTED"
NOT_YET_ASSESSABLE = "NOT_YET_ASSESSABLE"

__all__ = [
    "TERMINAL_CONFIRMED", "PROVISIONAL", "CONTRADICTED", "NOT_YET_ASSESSABLE",
    "ShadowOutcomeTerminalityError", "assess_terminality",
]


class ShadowOutcomeTerminalityError(Exception):
    """Structural misuse only -- `latest_verification`'s own request_id
    does not match `latest_observation`'s. Never raised for an ordinary
    classification result; CONTRADICTED is a legitimate, descriptive
    finding, not an error. (A request_id mismatch or ordering violation
    between `previous_observation` and `latest_observation` instead
    raises backtest.shadow_outcome_revision.ShadowOutcomeRevisionError,
    propagated unchanged from classify_cross_invocation_relationship --
    never duplicated or re-wrapped here.)"""


def assess_terminality(
    latest_observation: dict[str, Any],
    previous_observation: dict[str, Any] | None,
    latest_verification: dict[str, Any] | None,
) -> dict[str, Any]:
    """The SOLE entry point. `latest_observation` and `previous_observation`
    are each a WHOLE observation dict (as resolve_decision_outcome()
    returns, or as read back from storage); `latest_verification` is the
    WHOLE dict verify_historical_stability() returns for
    `latest_observation` specifically, or None if no verification was
    ever attempted. Never a hand-assembled substitute for any of the
    three.

    Returns exactly: {request_id, hypothesis_id, terminality_state,
    relationship}, where `relationship` is whatever
    classify_cross_invocation_relationship() returned (None if
    `previous_observation` was None).

    Pure -- no storage read or write, no network call, no randomness.
    Pairwise, current-state-only: never looks past `previous_observation`
    into any earlier history.
    """
    request_id = latest_observation["request_id"]
    hypothesis_id = latest_observation.get("hypothesis_id")

    if latest_verification is not None and latest_verification.get("request_id") != request_id:
        raise ShadowOutcomeTerminalityError(
            f"assess_terminality: latest_verification's request_id "
            f"({latest_verification.get('request_id')!r}) does not match latest_observation's "
            f"({request_id!r})."
        )

    relationship = None
    if previous_observation is not None:
        relationship = classify_cross_invocation_relationship(
            previous_observation, latest_observation,
        )["relationship"]

    walk_ohlc = latest_observation.get("walk_ohlc")
    verification_attempted = (
        walk_ohlc is not None
        and latest_verification is not None
        and latest_verification.get("ohlc_state") != NOT_YET_VERIFIABLE
        and latest_verification.get("composition_state") != NOT_YET_VERIFIABLE
    )

    if not verification_attempted:
        terminality_state = NOT_YET_ASSESSABLE
    else:
        bars = latest_verification.get("bars") or []
        ohlc_any_unstable = any(bar["state"] == OHLC_UNSTABLE for bar in bars)
        ohlc_all_stable = all(bar["state"] == OHLC_STABLE for bar in bars)
        composition_unstable = latest_verification.get("composition_state") == COMPOSITION_UNSTABLE
        composition_stable = latest_verification.get("composition_state") == COMPOSITION_STABLE

        contradicted = ohlc_any_unstable or composition_unstable or relationship == OUTCOME_REVISED

        if contradicted:
            terminality_state = CONTRADICTED
        elif ohlc_all_stable and composition_stable and relationship == OUTCOME_REPRODUCED:
            terminality_state = TERMINAL_CONFIRMED
        else:
            terminality_state = PROVISIONAL

    return {
        "request_id": request_id,
        "hypothesis_id": hypothesis_id,
        "terminality_state": terminality_state,
        "relationship": relationship,
    }
