"""
backtest/shadow_effective_sample.py
---------------------------------------
Hypothesis Discovery Engine -- Within-Hypothesis Dependence: Effective
Sample Size (operator's own locked Design Gate, 2026-10, "n_eff/k_eff --
Design Only"). Implements the connected-components clustering method
Gate 0F confirmed as the correct structural basis (storage.shadow_
divergence_membership's own docstring, written by that earlier Design
Gate) over backtest.shadow_outcome_evidence.evaluate_shadow_evidence()'s
own `exposure_windows` field (Export Exposure Window Evidence Design
Gate, commit 5301da9) -- unchanged, not re-opened, not widened.

TWO FUNCTIONS, never merged (operator's own locked separation, mirroring
this engine's own established house style of narrow, single-concern
pure functions -- count_terminal_confirmed() vs. compute_observed_
win_rate() vs. count_terminal_confirmed_by_outcome()):

  cluster_exposure_windows() -- PURE, geometry only. Takes `exposure_windows`
      (exactly evaluate_shadow_evidence()'s own field: a list of
      {"request_id", "bar_time", "resolved_bar_time"}) and returns one
      cluster per connected component of the exposure-window-overlap
      graph. Needs no outcome at all.

  compute_effective_sample() -- PURE, outcome lookup only. Takes the
      clusters above PLUS a caller-supplied `outcomes_by_request_id`
      mapping (sourced from data the caller already has --
      backtest.shadow_outcome_aggregate.evaluate_all_requests_for_
      hypothesis()'s own per-request `outcome` field, never a new
      resolve_decision_outcome() call) and returns {"n_eff", "k_eff"}.

OVERLAP DEFINITION (operator's own locked choice, deliberately
conservative): two windows [bar_time_i, resolved_bar_time_i] and
[bar_time_j, resolved_bar_time_j] overlap iff
    bar_time_i <= resolved_bar_time_j  AND  bar_time_j <= resolved_bar_time_i
-- i.e. INCLUSIVE boundary touching counts as overlap (a window ending
at the exact instant another begins is treated as dependent, not
independent). This is a deliberate conservative choice, not a
mathematical necessity -- it biases n_eff downward (fewer, larger
clusters) whenever boundaries coincide exactly.

CONNECTED COMPONENTS ARE WELL-DEFINED REGARDLESS OF INPUT ORDER (a
property of the mathematical object itself, not an implementation
choice this module makes): which nodes end up in the same component
never depends on the order edges are discovered or processed. What this
module's own implementation DOES fix deterministically, because the
mathematical object does not fix it on its own, is: (1) the
representative chosen per cluster, (2) the order of `member_request_ids`
within a cluster, and (3) the order clusters are returned in.

REPRESENTATIVE SELECTION (operator's own locked rule, reused verbatim
from this session's own earlier correction -- never re-derived as
"statistically optimal", only as consistent with the already-locked
temporal contract): within each cluster, the member with the EARLIEST
`bar_time`; ties broken by `request_id` ascending (lexicographic).

k_eff COUNTS REPRESENTATIVES ONLY (operator's own locked rule,
explicit, not implied): k_eff is the count of clusters whose
REPRESENTATIVE's own outcome is TP_HIT. Non-representative members'
outcomes are NEVER consulted, NEVER counted, NEVER averaged -- a
cluster contributes exactly 0 or 1 to k_eff, regardless of how many
members it has or what they individually resolved to. A representative
with no entry in `outcomes_by_request_id` raises
ShadowEffectiveSampleError -- a missing lookup is never silently
dropped from k_eff's count.

THIS MODULE COMPUTES NOTHING BEYOND {n_eff, k_eff} (operator's own
locked scope boundary): no p-value, no threshold, no verdict, no
promotion, no Bonferroni/family-size correction, no classify_
significance(). binomial_lower_tail_p_value() and compute_catastrophic_
divergence_p_value() are NEITHER imported NOR modified here -- composing
n_eff/k_eff into either is a separate, future, independently-authorized
Design Gate (operator's own explicit "production wiring" boundary).

NON-NEGOTIABLE (operator's own locked scope boundary): this module
performs NO I/O of any kind -- no storage read or write, no network
call. It never imports backtest.shadow_outcome_evidence, backtest.
shadow_record, backtest.shadow_integration, or backtest.shadow_
divergence_membership (production/statistical layers stay entirely
unaware of this module's existence in this phase -- WIRED=NO). It never
imports backtest.promotion_gate, backtest.policy_health, backtest.
execution_attribution, execution.authorization, execution.trade_executor,
storage.outcome_tracker, storage.shadow_book, scheduler.py, or main.py.
No try/except anywhere in this module -- every exception from every
call it makes propagates completely unhandled, by design.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backtest.shadow_outcome_resolver import TP_HIT

__all__ = [
    "ShadowEffectiveSampleError", "cluster_exposure_windows", "compute_effective_sample",
]


class ShadowEffectiveSampleError(Exception):
    """Structural misuse only -- a cluster's own representative_request_id
    has no entry in the caller-supplied outcomes_by_request_id mapping.
    Never raised for an ordinary TP_HIT/non-TP_HIT representative outcome
    -- CONTRADICTED/SL_HIT/anything-not-TP_HIT is a legitimate finding
    (k_eff simply does not count that cluster), not an error. Only a
    genuinely missing lookup raises."""


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _windows_overlap(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> bool:
    """INCLUSIVE overlap test (operator's own locked, deliberately
    conservative choice): boundary touching counts as overlap."""
    a_start, a_end = a
    b_start, b_end = b
    return a_start <= b_end and b_start <= a_end


def cluster_exposure_windows(exposure_windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """PURE -- no I/O, no randomness. `exposure_windows` is exactly
    backtest.shadow_outcome_evidence.evaluate_shadow_evidence()'s own
    `exposure_windows` field (or structurally equivalent): a list of
    {"request_id", "bar_time", "resolved_bar_time"}, unchanged, never
    widened by this module.

    Returns one dict per connected component of the exposure-window-
    overlap graph (see module docstring for the overlap definition):
        {"representative_request_id": str, "member_request_ids": list[str]}
    `member_request_ids` is sorted ascending; the returned list of
    clusters is itself ordered by (representative's bar_time,
    representative_request_id) -- deterministic regardless of the
    input list's own order. Empty input returns an empty list."""
    if not exposure_windows:
        return []

    parsed: dict[str, tuple[datetime, datetime]] = {
        window["request_id"]: (_parse_utc(window["bar_time"]), _parse_utc(window["resolved_bar_time"]))
        for window in exposure_windows
    }
    request_ids = sorted(parsed.keys())

    parent: dict[str, str] = {request_id: request_id for request_id in request_ids}

    def _find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        root_a, root_b = _find(a), _find(b)
        if root_a != root_b:
            if root_a < root_b:
                parent[root_b] = root_a
            else:
                parent[root_a] = root_b

    for i, request_id_a in enumerate(request_ids):
        for request_id_b in request_ids[i + 1:]:
            if _windows_overlap(parsed[request_id_a], parsed[request_id_b]):
                _union(request_id_a, request_id_b)

    groups: dict[str, list[str]] = {}
    for request_id in request_ids:
        groups.setdefault(_find(request_id), []).append(request_id)

    clusters = []
    for members in groups.values():
        representative = min(members, key=lambda rid: (parsed[rid][0], rid))
        clusters.append({
            "representative_request_id": representative,
            "member_request_ids": sorted(members),
        })
    clusters.sort(key=lambda cluster: (
        parsed[cluster["representative_request_id"]][0],
        cluster["representative_request_id"],
    ))
    return clusters


def compute_effective_sample(
    clusters: list[dict[str, Any]], outcomes_by_request_id: dict[str, str],
) -> dict[str, int]:
    """PURE -- no I/O, no randomness. `clusters` is cluster_exposure_
    windows()'s own return list (or structurally equivalent).
    `outcomes_by_request_id` is a caller-supplied {request_id: outcome}
    mapping -- sourced from data the caller already has (e.g. backtest.
    shadow_outcome_aggregate.evaluate_all_requests_for_hypothesis()'s own
    per-request `outcome` field), never fetched or recomputed by this
    function.

    Returns exactly {"n_eff": int, "k_eff": int}. n_eff is simply
    len(clusters). k_eff counts ONLY each cluster's own representative
    outcome (TP_HIT) -- non-representative members' outcomes are never
    consulted. Empty `clusters` returns {"n_eff": 0, "k_eff": 0} -- a
    plain arithmetic fact, never a fabricated None.

    Fail-closed: raises ShadowEffectiveSampleError if any
    representative_request_id has no entry in outcomes_by_request_id --
    a missing lookup is never silently excluded from k_eff's count."""
    n_eff = len(clusters)
    k_eff = 0
    for cluster in clusters:
        representative = cluster["representative_request_id"]
        if representative not in outcomes_by_request_id:
            raise ShadowEffectiveSampleError(
                f"compute_effective_sample: representative_request_id {representative!r} has no "
                f"entry in outcomes_by_request_id -- missing outcome lookup, never silently "
                f"dropped from k_eff's count."
            )
        if outcomes_by_request_id[representative] == TP_HIT:
            k_eff += 1
    return {"n_eff": n_eff, "k_eff": k_eff}
