"""
backtest/shadow_outcome_evidence.py
---------------------------------------
Hypothesis Discovery Engine -- Membership Integration: the shared
evaluation primitive (operator's own locked Design Gate, 2026-10,
"Membership Integration Design", decision C -- "fasl al-hisab 'an
al-taghlif" / separate computation from packaging).

NAMING NOTE (operator's own locked correction, same Design Gate): this
module was originally written to the filename `backtest/shadow_evidence.py`,
which collided with an already-committed, unrelated module of that exact
name (Phase 15B: SHADOW Evidence Record, commit 677ce63 -- an ancestor of
this branch's HEAD, building `build_shadow_evidence_record()` over
backtest.shadow_observation.compute_shadow_observed_profile(), an entirely
different concern: SHADOW's own decision/verdict frequency, with a
permanent, structural `divergence_assessable=False`). That collision
overwrote Phase 15B's committed file in the working tree; it was restored
from HEAD byte-for-byte, and THIS module was moved to its own,
non-colliding name, `shadow_outcome_evidence.py` -- naming it after the
observed/baseline OUTCOME evidence it actually composes (mirrors the
existing backtest.shadow_outcome_aggregate / shadow_outcome_terminality /
shadow_outcome_resolver / shadow_outcome_verification family).
backtest.shadow_evidence (Phase 15B) and this module remain, and will
always remain, two entirely separate, non-overlapping modules.

THE PROBLEM, precisely (operator's own locked Gate 0 finding): backtest.
shadow_record.build_shadow_record() used to COMBINE three things in one
function body -- the observed-side evaluation (a live network call, via
backtest.shadow_outcome_aggregate.evaluate_all_requests_for_hypothesis()),
the baseline-side evaluation (backtest.hypothesis_baseline_statistic.
evaluate_canonical_baseline_statistic()), and the Phase-13 record
packaging (the three-conjunct `completed` formula). backtest.
shadow_divergence_membership.record_family_membership_if_attempted()
needs the SAME raw evidence (n_t, tp_count, sl_count, p_value,
baseline_p, request_ids) that build_shadow_record() computed internally
but never exposed, PLUS a canonical hypothesis_fingerprint/
research_code_commit neither function sourced. Re-running the evaluation
a second time to get at that evidence (REJECTED option A) would duplicate
the live network call; copying build_shadow_record()'s formula into a
second module (REJECTED option B) would create a second, driftable
source of truth.

THIS MODULE is the resolution (option C): the ONE place that performs the
observed-side evaluation, the baseline-side composition, and the
canonical-identity resolution -- computed ONCE per call -- returning a
single evidence dict that backtest.shadow_record.compose_shadow_record()
and the Membership Integration orchestrator (backtest.
shadow_integration, a separate, later module) each consume independently,
reading only the fields each actually needs. This module performs NO
packaging of its own (no `completed`, no `diverged_catastrophically`) --
that stays owned by backtest.shadow_record, by this Design Gate's own
locked decision (point 3: "completed" is Phase-13 policy vocabulary, not
evidence, and is never pre-baked here).

CANONICAL IDENTITY RESOLUTION (operator's own locked rule, point 4/5):
    promotions -> resolve_canonical_baseline() -> canonical cell_id
                -> PROMOTED rows matching that cell_id
                -> require hypothesis_fingerprint/research_code_commit
                   AGREEMENT across every matching row
                -> CANONICAL_IDENTITY_RESOLVED (agreed values), or
                   NO_CANONICAL_CELL (resolve_canonical_baseline() did not
                   reach CANONICAL_CELL_RESOLVED), or
                   IDENTITY_MISMATCH (one canonical cell_id, but its
                   PROMOTED rows disagree on fingerprint/commit -- never
                   silently resolved by picking the first/last row).
This reuses backtest.hypothesis_baseline.resolve_canonical_baseline() and
backtest.hypothesis_promotion.PROMOTED verbatim, never re-implements
either. A promotion dict whose own hypothesis_id does not match the
hypothesis_id argument still raises backtest.hypothesis_baseline.
HypothesisBaselineError, propagated completely unchanged -- the same
fail-fast, no-isolation precedent every module in this engine follows.

IDENTITY IS INDEPENDENT OF COMPLETION (operator's own locked rule, point
4): NO_CANONICAL_CELL/IDENTITY_MISMATCH never affect n_t, tp_count,
sl_count, p_value, or divergence_statistic_valid -- those are computed
unconditionally from the observed/baseline sides regardless of whether
canonical identity resolves. Only a future caller that wants to WRITE a
membership row needs identity; backtest.shadow_record.
compose_shadow_record() never reads the identity fields at all.

EVIDENCE != POLICY CONSTANT (operator's own locked correction): this
module's evidence dict never contains DIVERGENCE_VERDICT_COMPUTED -- that
is a hardcoded policy constant owned by backtest.shadow_record, not an
observation this module could ever compute per-hypothesis.

REQUEST_IDS SCOPE (this module's own documented interpretation, not a
separately re-opened Design Gate): the request_ids returned are exactly
those among `terminality_results` whose terminality_state is
TERMINAL_CONFIRMED -- the SAME population tp_count/sl_count/p_value are
computed over (backtest.shadow_outcome_aggregate's own locked population
guarantee). Never the full, unfiltered enumeration -- a membership row's
request_ids_json must trace exactly the evidence that produced its own
p_value_at_entry, nothing broader.

NON-NEGOTIABLE (operator's own locked scope boundary): this module never
writes to storage, never imports backtest.promotion_gate, backtest.
policy_health, backtest.execution_attribution, execution.authorization,
execution.trade_executor, storage.outcome_tracker, storage.shadow_book,
scheduler.py, or main.py. It never changes compute_catastrophic_
divergence_p_value(), evaluate_canonical_baseline_statistic(), or
resolve_canonical_baseline() -- it only composes their existing, unchanged
outputs. No try/except anywhere in this module -- every exception from
every call it makes propagates completely unhandled, by design.
"""
from __future__ import annotations

from typing import Any

from backtest.hypothesis_baseline import CANONICAL_CELL_RESOLVED, resolve_canonical_baseline
from backtest.hypothesis_baseline_statistic import evaluate_canonical_baseline_statistic
from backtest.hypothesis_promotion import PROMOTED
from backtest.shadow_outcome_aggregate import (
    compute_catastrophic_divergence_p_value,
    count_terminal_confirmed,
    count_terminal_confirmed_by_outcome,
    evaluate_all_requests_for_hypothesis,
)
from backtest.shadow_outcome_terminality import TERMINAL_CONFIRMED

CANONICAL_IDENTITY_RESOLVED = "CANONICAL_IDENTITY_RESOLVED"
NO_CANONICAL_CELL = "NO_CANONICAL_CELL"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"

__all__ = [
    "CANONICAL_IDENTITY_RESOLVED", "NO_CANONICAL_CELL", "IDENTITY_MISMATCH",
    "evaluate_shadow_evidence",
]


def _resolve_canonical_identity(
    hypothesis_id: str, promotions: list[dict[str, Any]],
) -> tuple[str, str | None, str | None]:
    """Returns (identity_state, hypothesis_fingerprint, research_code_commit).
    The latter two are non-None iff identity_state ==
    CANONICAL_IDENTITY_RESOLVED. No invented tie-break: a genuine
    disagreement among the canonical cell_id's own PROMOTED rows is
    reported as IDENTITY_MISMATCH, never resolved by picking one."""
    resolution = resolve_canonical_baseline(hypothesis_id, promotions)
    if resolution["canonicity_state"] != CANONICAL_CELL_RESOLVED:
        return NO_CANONICAL_CELL, None, None

    cell_id = resolution["cell_id"]
    matching = [
        promotion for promotion in promotions
        if promotion["decision"] == PROMOTED and promotion["cell_id"] == cell_id
    ]
    fingerprints = {promotion["hypothesis_fingerprint"] for promotion in matching}
    commits = {promotion.get("research_code_commit") for promotion in matching}
    if len(fingerprints) != 1 or len(commits) != 1:
        return IDENTITY_MISMATCH, None, None

    return CANONICAL_IDENTITY_RESOLVED, next(iter(fingerprints)), next(iter(commits))


def evaluate_shadow_evidence(
    hypothesis_id: str,
    *,
    base_config: dict[str, Any],
    promotions: list[dict[str, Any]],
    cell: dict[str, Any] | None,
    validation_results: list[dict[str, Any]],
    api_key: str | None = None,
) -> dict[str, Any]:
    """The SOLE entry point. Evaluates the observed side (I/O: storage
    reads + a live network call, via backtest.shadow_outcome_aggregate.
    evaluate_all_requests_for_hypothesis() -- called exactly ONCE), the
    baseline side (caller-supplied `promotions`/`cell`/`validation_results`,
    via backtest.hypothesis_baseline_statistic.
    evaluate_canonical_baseline_statistic() -- never fetched here), and
    canonical-identity resolution (from the SAME `promotions` list, no
    extra caller input).

    Returns exactly:
        {hypothesis_id, n_t, tp_count, sl_count, p_value,
         divergence_statistic_valid, baseline_p, baseline_failure_reason,
         request_ids, canonical_identity_state, hypothesis_fingerprint,
         research_code_commit}

    FAIL-FAST: any exception from the observed-side evaluation, or from
    resolve_canonical_baseline()'s own HypothesisBaselineError, propagates
    completely unchanged. No partial or fabricated evidence is ever
    returned."""
    terminality_results = evaluate_all_requests_for_hypothesis(
        hypothesis_id, base_config=base_config, api_key=api_key,
    )
    n_t = count_terminal_confirmed(terminality_results)
    counts = count_terminal_confirmed_by_outcome(terminality_results)
    request_ids = [
        result["request_id"] for result in terminality_results
        if result["terminality_state"] == TERMINAL_CONFIRMED
    ]

    baseline = evaluate_canonical_baseline_statistic(hypothesis_id, promotions, cell, validation_results)
    p_value = compute_catastrophic_divergence_p_value(terminality_results, baseline["tp_sl_win_rate"])

    identity_state, hypothesis_fingerprint, research_code_commit = _resolve_canonical_identity(
        hypothesis_id, promotions,
    )

    return {
        "hypothesis_id": hypothesis_id,
        "n_t": n_t,
        "tp_count": counts["tp_count"],
        "sl_count": counts["sl_count"],
        "p_value": p_value,
        "divergence_statistic_valid": baseline["divergence_statistic_valid"],
        "baseline_p": baseline["tp_sl_win_rate"],
        "baseline_failure_reason": baseline["failure_reason"],
        "request_ids": request_ids,
        "canonical_identity_state": identity_state,
        "hypothesis_fingerprint": hypothesis_fingerprint,
        "research_code_commit": research_code_commit,
    }
