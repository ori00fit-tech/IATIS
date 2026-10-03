"""
backtest/shadow_outcome_revision.py
---------------------------------------
Hypothesis Discovery Engine -- Cross-Invocation Outcome Relationship
(Design Gate #3, operator's own locked contract).

SCOPE (locked, narrow): a PURE, PAIRWISE classifier only. Given two
already-fetched observation dicts for the SAME request_id (`earlier`,
`later`, ordered by `evaluated_at` -- never a whole-sequence analyzer,
never queried from storage by this module itself), it answers exactly
one question: given what Gate 0 already proved (an identical bar
sequence makes the resolver's own hit logic provably deterministic),
what KIND of relationship holds between two observed outcomes of the
SAME decision over time?

THREE STATES, by category membership, never case-by-case enumeration:

  OUTCOME_REPRODUCED -- `later`'s outcome matches `earlier`'s outcome,
      AND, when that outcome is causal (TP_HIT/SL_HIT), `later`'s
      resolved_bar_time also matches `earlier`'s. Same outcome LABEL
      with a different resolved_bar_time is NOT reproduction -- the
      concrete causal claim changed (e.g. TP_HIT@10:00 -> TP_HIT@14:00
      is OUTCOME_REVISED, never OUTCOME_REPRODUCED).

  LEGITIMATE_PROGRESSION -- `earlier`'s outcome is non-causal
      (NOT_YET_ASSESSABLE, DATA_GAP, or TIMEOUT -- none of these carry
      a resolved_bar_time, none make a specific causal claim) and
      `later` differs from it in any way. TIMEOUT is grouped here
      deliberately, NOT with TP_HIT/SL_HIT: it never receives a
      resolved_bar_time and never gets walk_ohlc captured at all (that
      capture is TP_HIT/SL_HIT-only), so there is no stronger evidence
      backing a TIMEOUT finding than backs DATA_GAP or
      NOT_YET_ASSESSABLE -- giving it stricter treatment would be
      introducing a new assumption the current evidence does not
      support (the operator's own locked reasoning).

      IMPORTANT: this is a DESCRIPTIVE label for the transition
      category, never a claim that the transition was benign or
      validated. A DATA_GAP -> TP_HIT progression could in principle
      be hiding the same resolver fetch-window drift this Design Gate
      explicitly declined to fix (see below) rather than genuine
      backfill -- this module makes no such determination either way.

  OUTCOME_REVISED -- `earlier`'s outcome is causal (TP_HIT/SL_HIT) and
      `later` differs in outcome and/or resolved_bar_time. A purely
      DESCRIPTIVE fact, with NO automatic consequence -- never a
      governance verdict, never evidence of catastrophic divergence,
      never a terminality judgment. Those all remain explicitly out of
      scope, exactly as OHLC_UNSTABLE (Design Gate #1) and
      COMPOSITION_UNSTABLE (Design Gate #2) never triggered one either.

DELIBERATELY NOT ADDRESSED HERE (operator's own locked scope boundary):

  - The resolver's own fetch-window drift (Gate 0 finding: past the
    168h horizon, `outputsize` freezes at a constant while the
    provider's "most recent N bars as of now" window keeps sliding
    forward, so a late-enough second invocation can mechanically lose
    access to the original bars regardless of data/composition
    stability). This is a characteristic of Phase 15D's own,
    already-accepted Outcome Resolution Contract's fetch mechanism --
    fixing it means changing decision-affecting fetch logic, a
    materially bigger governance question than this additive
    classifier. OBSERVED, DEFERRED, NOT FIXED HERE.
  - Any cross-reference to Design Gate #1/#2 evidence (OHLC value
    stability, walk composition stability) -- `walk_ohlc` and
    `provider_at_observation` are never persisted to the observation
    log (storage.shadow_outcome_observation's own schema has no such
    columns), so a classifier built on stored observations structurally
    cannot reach that evidence. Persisting it would be a new, separate,
    unauthorized storage change.
  - Any storage read or write of its own -- this module never imports
    storage.shadow_outcome_observation or any other storage module; the
    caller supplies both observation dicts already in hand.
  - Any threshold, n, p-value, Bonferroni correction, terminality
    judgment, catastrophic-divergence verdict, promotion, execution, or
    scheduler wiring.

`evaluated_at` ordering is checked by plain string comparison, not
datetime parsing -- reusing the exact, already-tested precedent in
backtest.shadow_outcome_observation.reduce_to_latest_observations: every
evaluated_at this phase ever produces comes from the same resolver's
own datetime.isoformat() on a timezone-aware UTC value, which sorts
lexicographically identically to chronological order.
"""
from __future__ import annotations

from typing import Any

from backtest.shadow_outcome_resolver import SL_HIT, TP_HIT

OUTCOME_REPRODUCED = "OUTCOME_REPRODUCED"
LEGITIMATE_PROGRESSION = "LEGITIMATE_PROGRESSION"
OUTCOME_REVISED = "OUTCOME_REVISED"

# The only two outcomes with a concrete causal claim (a resolved_bar_time
# and walk_ohlc evidence) -- reused verbatim from the resolver, never
# redefined. TIMEOUT/DATA_GAP/NOT_YET_ASSESSABLE are deliberately NOT in
# this set (see module docstring).
_CAUSAL_OUTCOMES = frozenset({TP_HIT, SL_HIT})

__all__ = [
    "OUTCOME_REPRODUCED", "LEGITIMATE_PROGRESSION", "OUTCOME_REVISED",
    "ShadowOutcomeRevisionError", "classify_cross_invocation_relationship",
]


class ShadowOutcomeRevisionError(Exception):
    """Structural misuse only -- a request_id mismatch between the two
    observations, or `later` not strictly later than `earlier` by
    evaluated_at. Never raised for an ordinary classification result --
    OUTCOME_REVISED is a legitimate, descriptive finding, not an
    error."""


def classify_cross_invocation_relationship(
    earlier: dict[str, Any],
    later: dict[str, Any],
) -> dict[str, Any]:
    """The SOLE entry point. `earlier` and `later` are each a WHOLE
    observation dict (as resolve_decision_outcome() returns, or as
    stored/read back from storage.shadow_outcome_observation -- never a
    hand-assembled substitute), for the SAME request_id, with `later`
    strictly later than `earlier` by evaluated_at.

    Returns exactly: {request_id, hypothesis_id, relationship,
    earlier_outcome, later_outcome, earlier_resolved_bar_time,
    later_resolved_bar_time, earlier_evaluated_at, later_evaluated_at}.

    Pure -- no storage read or write, no network call, no randomness.
    Never queries for the two observations itself; the caller already
    has them in hand.
    """
    if earlier["request_id"] != later["request_id"]:
        raise ShadowOutcomeRevisionError(
            f"classify_cross_invocation_relationship: request_id mismatch "
            f"({earlier['request_id']!r} vs {later['request_id']!r}) -- comparing two unrelated "
            f"decisions is never a valid classification."
        )

    if later["evaluated_at"] <= earlier["evaluated_at"]:
        raise ShadowOutcomeRevisionError(
            f"classify_cross_invocation_relationship: 'later' evaluated_at "
            f"({later['evaluated_at']!r}) is not strictly later than 'earlier' evaluated_at "
            f"({earlier['evaluated_at']!r}) -- the two observations are not in the order the "
            f"caller claims."
        )

    earlier_outcome = earlier["outcome"]
    later_outcome = later["outcome"]
    earlier_resolved_bar_time = earlier.get("resolved_bar_time")
    later_resolved_bar_time = later.get("resolved_bar_time")

    if earlier_outcome in _CAUSAL_OUTCOMES:
        if later_outcome == earlier_outcome and later_resolved_bar_time == earlier_resolved_bar_time:
            relationship = OUTCOME_REPRODUCED
        else:
            relationship = OUTCOME_REVISED
    elif later_outcome == earlier_outcome:
        relationship = OUTCOME_REPRODUCED
    else:
        relationship = LEGITIMATE_PROGRESSION

    return {
        "request_id": earlier["request_id"],
        "hypothesis_id": earlier.get("hypothesis_id"),
        "relationship": relationship,
        "earlier_outcome": earlier_outcome,
        "later_outcome": later_outcome,
        "earlier_resolved_bar_time": earlier_resolved_bar_time,
        "later_resolved_bar_time": later_resolved_bar_time,
        "earlier_evaluated_at": earlier["evaluated_at"],
        "later_evaluated_at": later["evaluated_at"],
    }
