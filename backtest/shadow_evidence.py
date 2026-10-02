"""
backtest/shadow_evidence.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15B — SHADOW Evidence Record.

A SHADOW Evidence Record is NOT backtest.promotion_gate's own `shadow_record`
input contract (Phase 13: `{"completed": bool, "diverged_catastrophically":
bool}`) -- the two are deliberately DIFFERENT objects (operator's own locked
Phase 15B decision). evaluate_promotion_gate()'s `shadow_record.get(
"diverged_catastrophically")` is a bare truthiness check: passing anything
other than a real boolean -- a sentinel string, a tri-state marker -- would
either be silently swallowed as falsy or wrongly read as "diverged". Since
SHADOW's own architecture has no price/P&L/outcome data (confirmed directly,
Phase 15A's own Gate 0: research_live_identity_requests has no such
columns), there is no honest way to populate that boolean at all -- not
"False" (which would claim divergence was measured and found absent) and
not a fabricated truthy sentinel either.

This module therefore NEVER constructs, and NEVER feeds, backtest.
promotion_gate's `shadow_record` shape. It builds a SEPARATE, honestly-
scoped record describing only what is actually knowable: SHADOW's own
decision/verdict frequency (reused verbatim from backtest.
shadow_observation.compute_shadow_observed_profile(), Phase 15A -- never
recomputed from storage directly, never a second source of truth for the
same counts), plus an explicit, permanent `divergence_assessable=False`
with a fixed reason -- a STRUCTURAL fact about this architecture, never a
measurement outcome.

OPERATIONAL CONSEQUENCE (disclosed, not hidden): because nothing in this
engine can honestly construct Phase 13's `shadow_record` today, a hypothesis
cannot legitimately become eligible for Phase 13's LIMITED target_stage --
`evaluate_promotion_gate(target_stage=LIMITED, ...)` stays NOT_ELIGIBLE for
every real caller, since no caller may supply a fabricated `shadow_record`.
This is fail-closed by construction, exactly like every other gate in this
engine -- never a bug to work around, and never "fixed" by inventing data.

NON-NEGOTIABLE (operator's own locked Phase 15B scope boundary): this
module performs NO storage read or write of its own -- it calls only
backtest.shadow_observation.compute_shadow_observed_profile() (which is
itself the one place that reads storage.hypothesis_live_request). It never
imports backtest.promotion_gate, backtest.policy_health, backtest.
live_roster, storage.live_roster, storage.hypothesis_live_request directly,
or anything under execution/*.py, scheduler.py, or main.py. It is reachable
from nowhere outside its own tests -- WIRED=NO, PROVEN=NO, exactly like
every other dormant layer of this engine until a dedicated later phase
wires and proves it.

`window` is the SAME count-based (never time-based) semantics Phase 15A's
own compute_shadow_observed_profile() already locked -- the most recent
`window` recorded requests, nothing else.
"""
from __future__ import annotations

from typing import Any

from backtest.shadow_observation import compute_shadow_observed_profile

DIVERGENCE_ASSESSABLE = False
DIVERGENCE_NOT_ASSESSABLE_REASON = (
    "research_live_identity_requests (the sole SHADOW data source) has no price, P&L, or "
    "trade-outcome columns -- SHADOW never executes a trade, so 'catastrophic divergence' (a "
    "performance concept) has no honest source in this architecture. This is a structural fact "
    "about SHADOW's own data, not a measurement that happened to find zero divergence."
)

__all__ = [
    "ShadowEvidenceError", "DIVERGENCE_ASSESSABLE", "DIVERGENCE_NOT_ASSESSABLE_REASON",
    "build_shadow_evidence_record",
]


class ShadowEvidenceError(Exception):
    """Structural misuse only (an empty hypothesis_id, a non-positive or
    non-integer window) -- never raised for an ordinary "no SHADOW history
    yet" outcome, which is always a returned, descriptive record (every
    count at zero), never an exception."""


def build_shadow_evidence_record(hypothesis_id: str, window: int) -> dict[str, Any]:
    """The SOLE entry point. Returns EXACTLY:

        {"hypothesis_id": ..., "window": ..., "observed_profile": {...},
         "records_found": ..., "divergence_assessable": False,
         "divergence_reason": DIVERGENCE_NOT_ASSESSABLE_REASON}

    `observed_profile` is compute_shadow_observed_profile()'s own return
    dict, read verbatim -- never recomputed. `records_found` is that same
    dict's own `request_count`, surfaced at the top level so a caller never
    needs to reach into `observed_profile` to compare what was actually
    found against the `window` that was requested -- a plain fact, never a
    completeness verdict (no "window_fully_populated" or similar judgment
    is computed or returned; forming one would require a cadence/coverage
    contract this phase deliberately does not have).

    Never returns `completed`, `diverged_catastrophically`, or
    `window_fully_populated` -- see this module's own docstring for why
    each would misrepresent what is actually known."""
    if not hypothesis_id:
        raise ShadowEvidenceError("build_shadow_evidence_record: hypothesis_id is required.")
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ShadowEvidenceError(
            f"build_shadow_evidence_record: window must be a positive int, got {window!r}."
        )

    observed_profile = compute_shadow_observed_profile(hypothesis_id, window)
    return {
        "hypothesis_id": hypothesis_id,
        "window": window,
        "observed_profile": observed_profile,
        "records_found": observed_profile["request_count"],
        "divergence_assessable": DIVERGENCE_ASSESSABLE,
        "divergence_reason": DIVERGENCE_NOT_ASSESSABLE_REASON,
    }
