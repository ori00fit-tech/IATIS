"""
backtest/hypothesis_promotion.py
---------------------------------
Hypothesis Discovery Engine, Phase 5 — Symbol-Scoped Promotion Gate.

Answers exactly one question, for exactly one already-bound (Hypothesis,
Mission, Matrix Cell) identity triple: "does this evidence package pass
the governance gate for THIS symbol?" It is a CONSUMER of governance
state, never a producer of it — this module contains no statistical
test, no p-value computation, no Bonferroni correction (bonferroni_alpha/
apply_matrix_wide_correction/classify_significance are never called or
re-implemented here), and no new significance threshold of any kind. The
verdict this module reads is exactly the one already computed and
persisted by backtest.matrix_orchestrator's Stage A screen -> Matrix
Family correction -> Stage B pipeline (storage.research_matrix's own
`status`/`stage_a_p_value`/`stage_b_verdict` columns) — never recomputed,
never re-derived from a raw p-value.

NON-NEGOTIABLE (operator's own explicit Phase 5 principle): "Symbol-
scoped" describes where a PROMOTED decision may be used as authorization
(this exact hypothesis, this exact cell, this exact symbol) — it never
means the family-level statistical correction is recomputed within one
symbol. A cell's family-wide-corrected verdict is family-scoped by
construction (Bonferroni was applied against the family's own fixed
planned_n) and stays that way; this module only decides whether that
ALREADY-FIXED verdict, plus Stage B (when the pipeline produced one),
together clear the bar to be treated as promotion-eligible for the one
symbol this specific cell/hypothesis is actually about.

Fail-closed, structural (reports/forensic/13_CONFIRMED_BUGS.md, BUG-001 —
"scope must never be assumed, only verified"): evaluate_promotion() re-
derives and cross-checks the FULL identity chain (hypothesis -> its
Mission binding -> the bound cell -> the cell's own family/symbol/
fingerprint) on every call, and raises backtest.hypothesis_execution.
HypothesisExecutionError — refusing to produce or persist ANY decision,
never a partial/degraded one — the moment any link disagrees: an unknown
id, a hypothesis not actually bound into the named mission, a cell_id
that does not match that binding, a symbol mismatch between the cell and
the hypothesis, or the Mission binding's own recorded hypothesis_
fingerprint disagreeing with the hypothesis's current stored fingerprint.
This is the direct structural analogue of BUG-001's fix (same-symbol
scope must be verified, never inferred) applied one link further down
this engine's own identity chain.

Decision vocabulary is deliberately three-valued, never a boolean:

  PROMOTED     — every required governance stage reached a real,
                 confirming terminal outcome for this exact cell.
  NOT_PROMOTED — governance evidence exists, was evaluated by the
                 existing pipeline, and did not pass (e.g. Matrix Family
                 correction rejected it, or Stage B did not confirm).
  BLOCKED      — no valid decision can be made yet: evidence is missing,
                 incomplete, still in progress, or the pipeline recorded
                 an error/no-data outcome (INSUFFICIENT_DATA/FAILED).
                 BLOCKED means "not yet decidable," never "no."

Missing/incomplete evidence NEVER defaults to a pass — see _decide()'s
own per-status mapping and this module's own tests for every fail-closed
case enumerated by the operator (missing corrected verdict, missing
required Stage B, raw-vs-corrected substitution, cross-symbol reuse,
partial promotion, exception-as-promotion).

Reuses, never rebuilds: backtest.hypothesis_execution.HypothesisExecutionError
(the SAME exception class Phases 3/4 already raise for identity-chain
violations — no new exception type invented here), backtest.mission_
validator.SAME_SYMBOL_CONFIRMED, and every status string backtest.
research_matrix already defines (QUEUED/RUNNING/SCREENED/CANDIDATE/
VALIDATING/VALIDATED/REJECTED/INSUFFICIENT_DATA/FAILED). Adds zero new
Cartesian-product, fingerprinting, Stage A/Stage B, ranking, or optimizer
logic. Persists nothing itself when called as evaluate_promotion() (pure
read + decide); record_promotion() is the one function that persists,
via storage.hypothesis_promotion (a brand-new, independent table — this
module never mutates research_matrix_cells or any other existing table).

Explicitly out of scope for this module (operator's own Phase 5 list): no
change to Stage A, p-values, or Bonferroni; no symbol-local re-correction;
no re-running discovery; no ranking or "best hypothesis" selection; no
optimizer; no risk/exposure change; no Symbol Policy Registry; no live
execution authorization; no config.yaml/config/engines.yaml/config/
symbols.yaml changes; no new PF/n-trade/win-rate thresholds (existing
frozen policy only — CLAUDE.md's thresholds-frozen-until-shadow-book rule
is unaffected, never overridden here). PROMOTED is not execution — it
only means "this evidence package cleared the governance gate for this
symbol," nothing more.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backtest.hypothesis_execution import HypothesisExecutionError
from backtest.mission_validator import SAME_SYMBOL_CONFIRMED
from backtest.research_matrix import (
    CANDIDATE,
    FAILED,
    INSUFFICIENT_DATA,
    QUEUED,
    REJECTED,
    RUNNING,
    SCREENED,
    VALIDATED,
    VALIDATING,
)

PROMOTED = "PROMOTED"
NOT_PROMOTED = "NOT_PROMOTED"
BLOCKED = "BLOCKED"

# Every non-terminal-for-promotion-purposes status: governance evidence for
# this cell has not yet reached a state this gate can decide from. Listed
# explicitly (rather than "anything not VALIDATED/REJECTED") so a future
# new status added to research_matrix's own vocabulary fails closed by
# default -- see _decide()'s own final fallback branch.
_INCOMPLETE_STATUSES = (QUEUED, RUNNING, SCREENED, CANDIDATE, VALIDATING)
_NO_EVIDENCE_STATUSES = (INSUFFICIENT_DATA, FAILED)


def _decide(cell: dict[str, Any]) -> tuple[str, str]:
    """Pure mapping from an ALREADY-COMPUTED cell's own governance state to
    a promotion decision. No p-value math, no correction, no re-derivation
    of anything the Stage A/Matrix Family/Stage B pipeline already
    decided -- this function only reads `status` (and `stage_b_verdict`
    when status is VALIDATED) and classifies."""
    status = cell["status"]
    if status == VALIDATED:
        verdict = cell.get("stage_b_verdict")
        if verdict == SAME_SYMBOL_CONFIRMED:
            return PROMOTED, (
                f"Matrix cell survived Stage A screening and the Matrix Family "
                f"multiple-testing correction, and Stage B confirmed "
                f"(stage_b_verdict={verdict!r})."
            )
        # Structurally should never happen -- backtest.matrix_orchestrator.
        # _run_stage_b_cell() only ever sets VALIDATED when the verdict IS
        # SAME_SYMBOL_CONFIRMED (every other verdict is stored as REJECTED).
        # Treated as a fail-closed evidence-integrity blocker, not silently
        # promoted and not silently rejected -- this state means something
        # about the pipeline's own invariant broke, which is exactly the
        # kind of "error/exception never becomes a promotion" case the
        # operator's guardrail F requires.
        return BLOCKED, (
            f"Matrix cell status=VALIDATED but stage_b_verdict={verdict!r} is not "
            f"{SAME_SYMBOL_CONFIRMED!r} -- this violates the existing orchestrator's own "
            f"invariant (VALIDATED implies a confirmed verdict); treated as an "
            f"evidence-integrity blocker, never silently promoted."
        )
    if status == REJECTED:
        return NOT_PROMOTED, (
            f"Matrix cell status=REJECTED (rejection_reason={cell.get('rejection_reason')!r}) "
            f"-- already evaluated by the existing Stage A / Matrix Family correction / "
            f"Stage B pipeline and did not pass; this is a real, decided outcome, not "
            f"missing evidence."
        )
    if status in _NO_EVIDENCE_STATUSES:
        return BLOCKED, (
            f"Matrix cell status={status} -- no valid governance evidence exists for this "
            f"cell (rejection_reason={cell.get('rejection_reason')!r}); missing or invalid "
            f"evidence is never treated as a pass."
        )
    if status in _INCOMPLETE_STATUSES:
        return BLOCKED, (
            f"Matrix cell status={status} -- governance state has not yet reached a "
            f"terminal outcome (Stage A / Matrix Family correction / Stage B is still "
            f"pending or in progress for this cell); promotion cannot be decided from "
            f"incomplete evidence."
        )
    return BLOCKED, f"Matrix cell status={status!r} is not a recognized governance state -- fail closed."


def evaluate_promotion(hypothesis_id: str, mission_id: str, cell_id: str) -> dict[str, Any]:
    """Re-derives and cross-checks the FULL identity chain for this exact
    (hypothesis, mission, cell) triple, then applies _decide() to the
    cell's own CURRENT, already-computed governance state. Pure read —
    persists nothing. Every identity check below raises
    HypothesisExecutionError (never returns a degraded/partial result) the
    instant it fails, so a caller can never receive a decision for a
    triple whose identity does not actually hold together."""
    from storage import hypothesis_factory as storage_hypothesis_factory
    from storage import hypothesis_mission as storage_hypothesis_mission
    from storage import research_matrix as storage_research_matrix

    hypothesis = storage_hypothesis_factory.get_hypothesis(hypothesis_id)
    if hypothesis is None:
        raise HypothesisExecutionError(f"evaluate_promotion: unknown hypothesis_id {hypothesis_id!r}.")

    mission = storage_hypothesis_mission.get_mission(mission_id)
    if mission is None:
        raise HypothesisExecutionError(f"evaluate_promotion: unknown mission_id {mission_id!r}.")

    bindings = storage_hypothesis_mission.list_mission_bindings(mission_id)
    binding = next((b for b in bindings if b["hypothesis_id"] == hypothesis_id), None)
    if binding is None:
        raise HypothesisExecutionError(
            f"evaluate_promotion: hypothesis_id {hypothesis_id!r} is not bound into mission "
            f"{mission_id!r} — refusing to evaluate a promotion for a hypothesis/mission pair "
            f"that was never actually linked (reports/forensic/13_CONFIRMED_BUGS.md, BUG-001: "
            f"scope must never be assumed, only verified)."
        )
    if binding["cell_id"] != cell_id:
        raise HypothesisExecutionError(
            f"evaluate_promotion: cell_id {cell_id!r} does not match the cell "
            f"{binding['cell_id']!r} this hypothesis/mission binding actually resolved to — "
            f"refusing to evaluate a promotion against a mismatched cell."
        )

    cell = storage_research_matrix.get_cell(cell_id)
    if cell is None:
        raise HypothesisExecutionError(f"evaluate_promotion: unknown cell_id {cell_id!r}.")
    if cell["family_id"] != mission["family_id"]:
        raise HypothesisExecutionError(
            f"evaluate_promotion: cell {cell_id!r} belongs to family {cell['family_id']!r}, not "
            f"mission {mission_id!r}'s own family {mission['family_id']!r}."
        )
    if cell.get("source_hypothesis_id") != hypothesis_id:
        raise HypothesisExecutionError(
            f"evaluate_promotion: cell {cell_id!r}'s own source_hypothesis_id "
            f"{cell.get('source_hypothesis_id')!r} does not match {hypothesis_id!r}."
        )
    if cell["symbol"] != hypothesis["symbol"]:
        raise HypothesisExecutionError(
            f"evaluate_promotion: symbol scope mismatch — cell {cell_id!r} is for symbol "
            f"{cell['symbol']!r} but hypothesis {hypothesis_id!r} is for symbol "
            f"{hypothesis['symbol']!r} (reports/forensic/13_CONFIRMED_BUGS.md, BUG-001: "
            f"evidence scope must match the entity being promoted, never assumed)."
        )
    if binding["hypothesis_fingerprint"] != hypothesis["matrix_cell_fingerprint"]:
        raise HypothesisExecutionError(
            f"evaluate_promotion: hypothesis {hypothesis_id!r}'s stored fingerprint "
            f"{hypothesis['matrix_cell_fingerprint']!r} does not match the fingerprint "
            f"{binding['hypothesis_fingerprint']!r} recorded on its own Mission binding."
        )
    # Deliberately NOT comparing cell["fingerprint"] to hypothesis["matrix_
    # cell_fingerprint"] directly -- they are legitimately DIFFERENT values
    # by design (backtest.hypothesis_factory's own docstring: a Hypothesis's
    # fingerprint is code/commit/provider-agnostic, while build_execution_
    # request() stamps the executed cell's fingerprint WITH the research
    # code commit -- exactly Phase 3's own "same hypothesis + different
    # commit -> a different, non-colliding cell fingerprint" guarantee).
    # That derivation was already verified once, atomically, when record_
    # mission() called build_execution_request() to create this exact
    # binding -- the checks above (the binding really does link this
    # hypothesis_id to this cell_id, and its own recorded hypothesis_
    # fingerprint really does match the hypothesis's current stored one)
    # are what re-verifies that link now, without re-deriving a fingerprint
    # this function has no way to reproduce (Mission bookkeeping does not
    # persist the data_provider argument record_mission() was called with).

    decision, decision_reason = _decide(cell)

    governance_snapshot = {
        "cell_status": cell["status"],
        "stage_a_p_value": cell.get("stage_a_p_value"),
        "stage_b_verdict": cell.get("stage_b_verdict"),
        "rejection_reason": cell.get("rejection_reason"),
        "family_id": cell["family_id"],
        "cell_fingerprint": cell["fingerprint"],
        "hypothesis_fingerprint": hypothesis["matrix_cell_fingerprint"],
    }

    return {
        "hypothesis_id": hypothesis_id,
        "mission_id": mission_id,
        "cell_id": cell_id,
        "symbol": cell["symbol"],
        "decision": decision,
        "decision_reason": decision_reason,
        "governance_snapshot": governance_snapshot,
        "research_code_commit": mission.get("research_code_commit"),
        "data_snapshot_id": mission.get("data_snapshot_id"),
    }


def compute_promotion_id(
    hypothesis_id: str, mission_id: str, cell_id: str, governance_snapshot: dict[str, Any],
) -> str:
    """Deterministic identity for one promotion DECISION — the same
    identity triple, evaluated against the SAME governance snapshot,
    always produces the same promotion_id (idempotent re-evaluation, the
    same discipline as compute_mission_id()/single_engine_identity()
    elsewhere in this engine). The governance snapshot is part of the
    hash deliberately: this is an append-only forensic ledger, not a
    mutable per-triple row — if the underlying cell's governance state
    later changes (e.g. CANDIDATE -> VALIDATED as Stage B completes), a
    genuinely NEW, coexisting promotion_id is produced, never an
    overwrite of the earlier (correctly incomplete, at the time) decision."""
    payload = "|".join([hypothesis_id, mission_id, cell_id, json.dumps(governance_snapshot, sort_keys=True)])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"PROMOTION-{digest}"


def record_promotion(
    hypothesis_id: str, mission_id: str, cell_id: str, *, created_by: str | None = None,
) -> dict[str, Any]:
    """The SOLE way to persist a promotion decision. Always re-evaluates
    via evaluate_promotion() first (never trusts a caller-supplied
    decision) — the identity chain and governance-state re-verification
    happen on every call, no exceptions. Idempotent: re-recording the
    same triple against unchanged governance state returns the EXISTING
    row (`created`: False); a real change in the cell's governance state
    since the last recording produces a new, coexisting promotion record,
    never an update to the old one (append-only, matching every other
    forensic table in this engine)."""
    from storage import hypothesis_promotion as storage_hypothesis_promotion

    evaluation = evaluate_promotion(hypothesis_id, mission_id, cell_id)
    promotion_id = compute_promotion_id(
        hypothesis_id, mission_id, cell_id, evaluation["governance_snapshot"],
    )

    existing = storage_hypothesis_promotion.get_promotion(promotion_id)
    if existing is not None:
        return dict(existing, created=False)

    storage_hypothesis_promotion.persist_promotion(
        promotion_id=promotion_id,
        hypothesis_id=hypothesis_id,
        hypothesis_fingerprint=evaluation["governance_snapshot"]["hypothesis_fingerprint"],
        mission_id=mission_id,
        cell_id=cell_id,
        symbol=evaluation["symbol"],
        decision=evaluation["decision"],
        decision_reason=evaluation["decision_reason"],
        governance_snapshot=evaluation["governance_snapshot"],
        research_code_commit=evaluation["research_code_commit"],
        data_snapshot_id=evaluation["data_snapshot_id"],
        created_by=created_by,
    )
    record = storage_hypothesis_promotion.get_promotion(promotion_id)
    return dict(record, created=True)
