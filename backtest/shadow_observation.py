"""
backtest/shadow_observation.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15A — SHADOW Observation Cycle.

Wires the already-mature, dormant Phase 8C producer (backtest.
hypothesis_live_request.evaluate_live_identity_request()) into a real,
periodic SHADOW observation cycle -- NOT a new validation engine, NOT a
new decision mechanism. run_shadow_cycle() is a one-shot, deterministic
orchestration over a caller-supplied list of entries; it contains NO
clock/sleep/timing/scheduling logic of its own. An external scheduler
(out of this phase's scope) decides WHEN to call it and how often -- a
governed identity's own `timeframe` is metadata an external trigger
COULD use to decide cadence, never logic embedded here.

ONE-WAY DEPENDENCY DIRECTION (operator's own locked requirement):

    promotion_gate result -> live_roster -> shadow_observation

This module imports backtest.live_roster (its own public contract only --
check_current_eligibility()) and backtest.hypothesis_live_request (the
unmodified Phase 8C producer). It NEVER imports backtest.promotion_gate,
backtest.policy_health, or anything under execution/*.py directly -- only
already-computed, public-contract dicts cross those boundaries.

SHADOW OBSERVED PROFILE SCOPE (operator's own locked correction, reached
after direct code verification that research_live_identity_requests has
NO price/P&L/outcome columns of any kind -- SHADOW never executes a
trade, so there is no win/loss, no P&L, and no entry/exit state to
observe):

    compute_shadow_observed_profile() returns EXACTLY:
        {"request_count": ..., "execute_verdict_count": ...,
         "proceed_count": ..., "no_trade_count": ...}

    It NEVER returns (and this module never computes) trade_count,
    win_rate, profit_factor, max_drawdown, or execution_slippage -- there
    is no honest source for any of them from decision/verdict records
    alone. `request_count` is deliberately NOT named `trade_count`: a
    SHADOW request is a decision OBSERVATION, never a trade, and
    `live_verdict == "EXECUTE"` here means only "the pipeline's own
    verdict", never "an order was executed". This module never passes
    its own output to backtest.policy_health.assess_policy_health() --
    that module's 5-field contract is structurally incompatible with
    this 4-field one, and no fallback value (0, None, or a borrowed
    "expected" value) is ever substituted for a missing field to force
    that compatibility. Reconnecting SHADOW to Phase 14 would require a
    separate virtual-trade/outcome-simulation layer (entry reference,
    exit/reference horizon, simulated P&L, a costs/slippage model) that
    does not exist yet and is explicitly out of this phase's scope.

    `window` is a COUNT of the most recent recorded requests to consider
    (passed straight through to storage.hypothesis_live_request.
    list_live_identity_requests_for_hypothesis()'s own `limit` parameter)
    -- NOT a time-based window; that storage function has no
    timestamp-range query, and this phase does not add one.

A failed fresh eligibility check for one roster entry SKIPS that entry
for THIS cycle only -- this module never calls backtest.live_roster.
remove_from_roster() or storage.live_roster.try_remove() as a side
effect. Auto-removal lifecycle policy is explicitly deferred.

A structural error raised by evaluate_live_identity_request() (for
example research.edge_gate.EdgeNotProvenError) is NEVER caught or
absorbed here -- it propagates immediately out of run_shadow_cycle(),
ending that call before any later entries in the same list are
processed. Isolating failures between entries (so one hypothesis's
structural problem doesn't block the rest) would be retry/scheduling
policy -- explicitly out of this phase's scope.
"""
from __future__ import annotations

from typing import Any

from backtest.hypothesis_live_request import evaluate_live_identity_request
from backtest.live_roster import check_current_eligibility
from storage.hypothesis_live_request import list_live_identity_requests_for_hypothesis


def run_shadow_cycle(*, entries: list[dict[str, Any]], base_config: dict[str, Any]) -> list[dict[str, Any]]:
    """`entries` is a caller-supplied list, each shaped:

        {"roster_entry": <storage.live_roster row>,
         "fresh_promotion_gate_result": <evaluate_promotion_gate() return dict>}

    This function never enumerates storage.live_roster.list_active_
    entries() itself, and never calls evaluate_promotion_gate() itself --
    both are the caller's own job, done fresh immediately before each
    call to this function.

    Returns one result dict per entry, in the same order:

        {"hypothesis_id": ..., "roster_entry_id": ..., "observed": bool,
         "reason": str | None, "live_identity_request": dict | None}

    `observed=False` means this cycle's fresh eligibility check failed
    (`reason` explains why); the roster entry itself is left completely
    untouched. `observed=True` means evaluate_live_identity_request() was
    called exactly once for this hypothesis_id, unmodified, with
    `base_config` passed straight through."""
    results: list[dict[str, Any]] = []
    for entry in entries:
        roster_entry = entry["roster_entry"]
        hypothesis_id = roster_entry["hypothesis_id"]
        eligibility = check_current_eligibility(
            roster_entry=roster_entry, fresh_promotion_gate_result=entry["fresh_promotion_gate_result"],
        )
        if not eligibility["eligible_this_cycle"]:
            results.append({
                "hypothesis_id": hypothesis_id, "roster_entry_id": roster_entry["roster_entry_id"],
                "observed": False, "reason": eligibility["reason"], "live_identity_request": None,
            })
            continue

        live_identity_request = evaluate_live_identity_request(hypothesis_id, base_config)
        results.append({
            "hypothesis_id": hypothesis_id, "roster_entry_id": roster_entry["roster_entry_id"],
            "observed": True, "reason": None, "live_identity_request": live_identity_request,
        })
    return results


def compute_shadow_observed_profile(hypothesis_id: str, window: int) -> dict[str, Any]:
    """See this module's own docstring for the full, locked rationale.
    Reads storage.hypothesis_live_request.
    list_live_identity_requests_for_hypothesis() -- the SAME Phase 8C
    ledger run_shadow_cycle() itself writes to via evaluate_live_
    identity_request() -- and classifies the most recent `window`
    records by their own `live_verdict`/`decision` columns, nothing
    else."""
    records = list_live_identity_requests_for_hypothesis(hypothesis_id, limit=window)
    return {
        "request_count": len(records),
        "execute_verdict_count": sum(1 for r in records if r.get("live_verdict") == "EXECUTE"),
        "proceed_count": sum(1 for r in records if r.get("decision") == "PROCEED"),
        "no_trade_count": sum(1 for r in records if r.get("decision") == "NO_TRADE"),
    }
