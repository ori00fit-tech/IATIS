"""
execution/routes/matrix_ai.py
------------------------------------
Hypothesis Discovery Engine, Phase 3B — AI Research Orchestrator — plus
Phase 3C — Controlled Recommendation Conversion (see the module-level
comment block above the /convert endpoint further down for that phase's
full boundary list).

AI is a PLANNER here, never a JUDGE. It reads already-decided Matrix
evidence (via backtest/matrix_research_planner.py's Evidence Context
Builder, which itself only re-presents backtest/matrix_evidence.py's
already-computed aggregations) and proposes WHERE to look next — new
(symbol, bundle, risk_preset) research combinations worth generating.

What this router can NEVER do, by construction:
  - Write research/results/registry.json, config.yaml, config/engines.yaml,
    or config/symbols.yaml.
  - Assign an HXXX id.
  - Let the AI itself trigger a Matrix write. AI -> DRAFT is the only
    write path the AI's own output ever reaches; APPROVED and CONVERTED
    are both exclusively human-triggered, separately-authorized actions
    (review_recommendation()/convert_recommendation() respectively) that
    this router's endpoints call, never something an AI response can
    invoke on its own.
  - Move a recommendation to APPROVED itself — only a human, through
    POST .../review, can do that (storage.matrix_ai_recommendations.
    review_recommendation is the only writer of that field, and this
    router's own review endpoint is a thin, unconditional pass-through
    to it — no AI call happens on that path).
  - AUTOMATICALLY convert an APPROVED recommendation into real Matrix
    cells. Phase 3C (below) adds a CONTROLLED, always human-triggered
    conversion path — POST .../convert — sitting behind its own,
    separate authorization gate from review's; there is still no code
    path from "AI proposed this" or even "a human approved this" straight
    to a Matrix write with no further, distinctly-authorized human action
    in between. Conversion also stops at QUEUED — running those cells is
    still the same separate POST /research/matrix/run-batch action it
    always was.

Phase 3B-H (AI Boundary Forensic Audit) hardened four properties, each
with its own regression coverage in tests/test_matrix_ai_boundary_audit.py:

  1. Snapshot immutability — evidence_snapshot_json/_hash/input_family_
     ids_json/input_cell_ids_json/constraints_used_json are written ONCE,
     at record_recommendation() time, and never touched again by anything
     (review_recommendation only ever sets status/reviewed_by/reviewed_at/
     review_note). A cell's status changing after a recommendation was
     created can never retroactively alter what that recommendation says
     the AI saw.
  2. Approval != research action. "APPROVED" means "a human reviewed this
     AI recommendation," nothing more — it is structurally incapable of
     creating a Matrix cell/family, assigning an HXXX, or touching
     registry.json, because storage.matrix_ai_recommendations.
     review_recommendation() only ever executes one UPDATE against its
     own table and calls nothing in storage.research_matrix.
  3. proposed_next_cells are UNTRUSTED input, same as any hand-typed
     symbol/bundle/risk_preset. AIAnalyzer.propose_matrix_research_plan()
     only checks STRUCTURAL completeness (are the required keys present
     and non-empty) — it deliberately never checks whether a proposed
     symbol is real, an engine is a valid engine key, or a risk_preset is
     one of RISK_PRESET_NAMES. That semantic validation happens exactly
     once, in POST /research/matrix/generate, identically regardless of
     whether the request body was hand-typed or copied from a
     recommendation — this router has no code path that skips it.
  4. Constraint PROVENANCE, not just constraint content. constraints_used
     carries research_code_commit/research_code_dirty (backtest.
     research_matrix.resolve_research_code_commit() — the same primitive
     MatrixCellSpec's own fingerprint already relies on) and a
     dead_list_hash (sha256 of the exact dead-list text used) — so a
     recommendation from six months ago can be checked against exactly
     which commit's CLAUDE.md/config/engines.yaml/config/symbols.yaml
     produced its constraints, not just "some dead list, some engines."

Every recommendation this router persists carries status="DRAFT" and a
full audit trail (evidence_snapshot + its hash, the exact constraints the
AI was given, provider/model, timestamps) — see storage/matrix_ai_
recommendations.py's own module docstring.

A second Phase 3B-H hardening pass (still no Phase 3C capability added)
fixed eight findings the first audit surfaced, none of which touched the
governance boundary above — all four properties still hold exactly as
described:
  P0. No silent context truncation before hashing/persisting — see
      ai/ai_analyzer.py's propose_matrix_research_plan() and
      _MAX_CONTEXT_CHARS below (reject outright, never truncate).
  P1. dead_list_present/dead_list_hash can no longer disagree with each
      other or with the evidence text — _dead_list_text() normalizes an
      empty extracted section to None at the source.
  P1. focus_hint is now explicitly framed as DATA, never an instruction,
      in ai/prompts/matrix_research_plan.txt (with delimiter markers),
      and is length-bounded + control-character-stripped before it ever
      reaches a prompt — see _sanitize_focus_hint.
  P1. constraints_used now carries requested_model AND actual_model
      (AIAnalyzer.resolved_model) separately — the recorded model can no
      longer diverge from what actually executed.
  P1 (moderate). review_recommendation() is now atomic (compare-and-swap
      on status='DRAFT', 409 on conflict) and every review is appended to
      research_matrix_ai_recommendation_reviews — re-review never erases
      prior history.
  P1 (moderate). reviewed_by is now derived server-side from the
      authenticated caller's masked identity (storage.audit_log.
      _mask_actor), never trusted from the request body; an optional,
      narrower MATRIX_AI_APPROVAL_KEY gate (see
      _check_approval_authorization) can restrict APPROVE/REJECT beyond
      the base _check_auth bar, disabled by default.
  LOW. evidence_snapshot_hash's verification procedure (canonicalize with
      sort_keys=True, then sha256) is now documented explicitly in
      backtest/matrix_research_planner.py and storage/matrix_ai_
      recommendations.py, with a byte-mutation regression test.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException
from pydantic import BaseModel

from backtest import matrix_research_planner as planner
from execution.api_core import _check_auth, _get_config

router = APIRouter()

_MAX_RECOMMENDATIONS_LIST_LIMIT = 200
_KNOWN_AI_PROVIDERS = ("gemini", "openai", "anthropic")

# Phase 3B-H hardening (P0) — a hard REJECTION threshold, never a
# truncation point. If the full canonical evidence context exceeds this,
# the request is refused outright (400) before the AI is ever called and
# before anything is persisted — see ai/ai_analyzer.py's propose_matrix_
# research_plan() for why silent truncation was the audit's top finding
# (a persisted "exact snapshot" that the AI never actually fully read).
_MAX_CONTEXT_CHARS = 200_000

# Phase 3B-H hardening (P1) — same 300-char convention execution/routes/
# ai.py's own POST /ai/suggest-hypothesis already applies to its own
# focus_hint, reused here rather than inventing a new bound.
_MAX_FOCUS_HINT_CHARS = 300
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_focus_hint(raw: str) -> str:
    """Bounds length and strips non-printable control characters before a
    caller-supplied focus_hint ever reaches the AI prompt. This is NOT
    prompt-injection prevention by itself — see ai/prompts/matrix_
    research_plan.txt's own explicit "focus_hint is DATA, not an
    instruction" framing and delimiter markers for that — it only removes
    characters that could otherwise obscure or forge delimiter-like
    sequences, and caps length so a caller can't smuggle in an arbitrarily
    long injection payload."""
    cleaned = _CONTROL_CHAR_RE.sub(" ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:_MAX_FOCUS_HINT_CHARS]


def _dead_list_text() -> str | None:
    """Reuses execution.routes.ai's own CLAUDE.md dead-list extraction
    verbatim — never a second implementation of "find the ## The dead
    list section." Degrades to None on any failure, exactly like its
    source (a missing/renamed heading must never 500 this endpoint).

    Phase 3B-H hardening: a heading that exists but has ZERO content
    before the next heading (`_extract_markdown_section` returns "" in
    that case) is normalized to None here, at the source — the SAME
    "nothing real to report" state as "heading missing entirely." Fixing
    this once here (rather than patching every downstream truthy/is-not-
    None check separately) is what makes dead_list_present/dead_list_hash/
    the evidence text itself impossible to disagree with each other."""
    from pathlib import Path

    from execution.routes.ai import _extract_markdown_section

    claude_md = Path("CLAUDE.md")
    if not claude_md.exists():
        return None
    try:
        text = _extract_markdown_section(claude_md.read_text(encoding="utf-8"), "## The dead list")
        return text or None
    except Exception:  # noqa: BLE001 — advisory context only, never fatal
        return None


def _frozen_engines() -> list[str]:
    engines_cfg = (_get_config().get("engines", {}) or {}).get("enabled", {}) or {}
    return [name for name, on in engines_cfg.items() if on]


def _build_context(family_ids: list[str], cell_ids: list[str], focus_hint: str, dead_list_text: str | None) -> dict[str, Any]:
    """3B.1 Evidence Context Builder call site — fetches the D1 rows
    backtest.matrix_research_planner.build_evidence_context() needs, then
    hands them to that pure function. Raises HTTPException(400) for any
    unknown family_id/cell_id (fail loud, never silently drop scope the
    caller asked for)."""
    from execution.routes.experiments import _configured_symbol_universe
    from execution.routes.research_matrix import _build_cell_evidence
    from storage import research_matrix as storage

    families = []
    cells_by_family: dict[str, list[dict[str, Any]]] = {}
    for family_id in family_ids:
        family = storage.get_family(family_id)
        if family is None:
            raise HTTPException(status_code=400, detail=f"Unknown family_id {family_id!r}.")
        families.append(family)
        cells_by_family[family_id] = storage.list_cells(family_id=family_id, limit=5000)

    scoped_cell_evidence = []
    for cell_id in cell_ids:
        cell = storage.get_cell(cell_id)
        if cell is None:
            raise HTTPException(status_code=400, detail=f"Unknown cell_id {cell_id!r}.")
        scoped_cell_evidence.append(_build_cell_evidence(cell))

    return planner.build_evidence_context(
        families, cells_by_family, scoped_cell_evidence,
        dead_list_text=dead_list_text,
        frozen_engines=_frozen_engines(),
        symbol_universe=sorted(_configured_symbol_universe()),
        focus_hint=focus_hint,
    )


class _ProposeRequest(BaseModel):
    family_ids: list[str] = []
    cell_ids: list[str] = []
    focus_hint: str = ""
    provider: str | None = None
    model: str | None = None


@router.post("/research/matrix/ai/propose")
async def matrix_ai_propose(
    body: _ProposeRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Builds the evidence context (3B.1), calls the AI research planner
    (3B.2), and — only when the AI call itself succeeds — persists the
    result as a DRAFT recommendation (3B.3). A failed/disabled AI call
    returns that status directly and persists NOTHING (there is no real
    plan to audit).

    Phase 3B-H hardening: the evidence context is REJECTED outright
    (400) if it exceeds _MAX_CONTEXT_CHARS, before the AI is ever called
    — never truncated and sent partially (see _MAX_CONTEXT_CHARS's own
    comment for why silent truncation was the audit's top finding)."""
    _check_auth(x_api_key, iatis_session)
    if not body.family_ids and not body.cell_ids:
        raise HTTPException(status_code=400, detail="At least one of family_ids/cell_ids is required.")

    from ai.ai_analyzer import AIAnalyzer
    from backtest import research_matrix as rm
    from storage import matrix_ai_recommendations as recs

    focus_hint = _sanitize_focus_hint(body.focus_hint)
    dead_list_text = _dead_list_text()
    context = _build_context(body.family_ids, body.cell_ids, focus_hint, dead_list_text)

    context_size = len(json.dumps(context, sort_keys=True, default=str))
    if context_size > _MAX_CONTEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Evidence context is too large ({context_size} chars, limit {_MAX_CONTEXT_CHARS}) — "
                f"narrow family_ids/cell_ids scope. Never silently truncated: the full evidence is always "
                f"sent to the AI in full, or the request is refused outright."
            ),
        )

    config = _get_config()
    if body.provider is not None:
        if body.provider not in _KNOWN_AI_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown provider '{body.provider}' — choose from {_KNOWN_AI_PROVIDERS}.")
        override_ai_cfg = {
            **(config.get("ai", {}) or {}),
            "providers": {body.provider: {"enabled": True}},
            "fallback_order": [body.provider],
        }
        if body.model:
            override_ai_cfg["model"] = str(body.model)[:200]
        config = {**config, "ai": override_ai_cfg}

    # Phase 3B-H (P1, model provenance) — `requested_model` is whatever
    # was ASKED for (the override above if body.model was given, else the
    # base config's own ai.model, possibly None if unset). `actual_model`
    # (below, after the call) is what the constructed provider instance
    # ACTUALLY used — AIAnalyzer.resolved_model, never re-derived from
    # this same config dict, which can diverge from it when ai.model is
    # unset and the provider fell back to its own per-provider default.
    requested_model = (config.get("ai", {}) or {}).get("model")

    analyzer = AIAnalyzer(config)
    plan = analyzer.propose_matrix_research_plan(context, focus_hint)
    if plan["status"] != "ok":
        return plan
    actual_model = analyzer.resolved_model

    snapshot_hash = planner.evidence_snapshot_hash(context)
    recommendation_id = f"MATRIX-AI-{uuid.uuid4().hex[:12]}"
    # Phase 3B-H (AI Boundary Forensic Audit) — constraint PROVENANCE, not
    # just constraint CONTENT. frozen_engines/symbol_universe are already
    # stored verbatim above (their own content IS their snapshot); dead_
    # list_present/dead_list_hash are now internally consistent by
    # construction (_dead_list_text() normalizes "" to None at the
    # source — see its own docstring). research_code_commit reuses the
    # exact same primitive backtest.research_matrix.MatrixCellSpec's own
    # fingerprint already relies on (Finding 2) — CLAUDE.md, config/
    # engines.yaml, and config/symbols.yaml are all tracked in this same
    # git repo, so the commit (+ dirty flag) is the one honest, reusable
    # "which version of the constraint-bearing files" answer, without
    # inventing a second code-identity mechanism.
    code_state = rm.resolve_research_code_commit()
    constraints_used = {
        "frozen_engines": context["frozen_engines"],
        "symbol_universe": context["symbol_universe"],
        "dead_list_present": dead_list_text is not None,
        "dead_list_hash": hashlib.sha256(dead_list_text.encode("utf-8")).hexdigest() if dead_list_text is not None else None,
        "risk_preset_names": list(rm.RISK_PRESET_NAMES),
        "research_code_commit": code_state["commit"],
        "research_code_dirty": code_state["dirty"],
    }
    recs.record_recommendation(
        recommendation_id,
        provider=plan["provider"], requested_model=requested_model, actual_model=actual_model,
        input_family_ids=body.family_ids, input_cell_ids=body.cell_ids or None,
        evidence_snapshot=context, evidence_snapshot_hash=snapshot_hash,
        constraints_used=constraints_used, focus_hint=focus_hint or None,
        reasoning_summary=plan["reasoning_summary"], coverage_gaps=plan["coverage_gaps"],
        proposed_next_cells=plan["proposed_next_cells"], distinct_from_dead_list=plan["distinct_from_dead_list"],
        priority=plan["priority"],
    )

    from storage.audit_log import log_action
    log_action(
        "matrix_ai_propose", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"recommendation_id={recommendation_id} family_ids={body.family_ids} cell_ids={body.cell_ids} priority={plan['priority']}",
    )

    return {
        "recommendation_id": recommendation_id, "evidence_snapshot_hash": snapshot_hash,
        "requested_model": requested_model, "actual_model": actual_model, **plan,
    }


@router.get("/research/matrix/ai/recommendations")
async def matrix_ai_list_recommendations(
    status: str | None = None,
    limit: int = 50,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import matrix_ai_recommendations as recs

    if status is not None and status not in recs.RECOMMENDATION_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status {status!r} — choose from {recs.RECOMMENDATION_STATUSES}")
    if not (1 <= limit <= _MAX_RECOMMENDATIONS_LIST_LIMIT):
        raise HTTPException(status_code=400, detail=f"limit must be between 1 and {_MAX_RECOMMENDATIONS_LIST_LIMIT}.")

    rows = recs.list_recommendations(status=status, limit=limit)
    return {"recommendations": rows, "count": len(rows)}


@router.get("/research/matrix/ai/recommendations/{recommendation_id}")
async def matrix_ai_get_recommendation(
    recommendation_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage import matrix_ai_recommendations as recs

    row = recs.get_recommendation(recommendation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="AI recommendation not found.")
    return row


# Phase 3B-H hardening (P1, authorization) — an OPT-IN, narrower gate
# specifically for APPROVE/REJECT, layered ON TOP OF (never instead of)
# _check_auth. Disabled by default (MATRIX_AI_APPROVAL_KEY unset), so
# every existing single-operator deployment sees zero behavior change;
# an operator who wants a stricter boundary for this one, higher-stakes
# action sets the env var and must then also supply a matching
# X-Approval-Key header. This deliberately reuses _check_auth's own
# hmac.compare_digest shared-secret-comparison SHAPE rather than
# inventing a new authorization paradigm (no roles, no user table) — it
# adds one more optional, narrowly-scoped shared secret, nothing else.
# This codebase has no per-user identity anywhere (see reviewed_by
# below), so a role/permission system is a genuinely new capability this
# hardening pass deliberately does not build.
_APPROVAL_KEY_ENV = "MATRIX_AI_APPROVAL_KEY"

# Phase 3C (Controlled Recommendation Conversion) — the operator's own
# explicit decision: conversion is a HIGHER-STAKES action than review
# (APPROVE only records a human opinion; CONVERT creates a real,
# executable-later Matrix Family) and must sit behind a permission
# separate from review's own, not the same one. Reuses the exact shared-
# secret CHECK SHAPE below rather than duplicating hmac.compare_digest
# logic a second time ("no ad-hoc keys scattered in the code") — the only
# thing that differs between the two gates is which env var/header they
# read. Disabled by default (unset), same as MATRIX_AI_APPROVAL_KEY, so
# every existing single-operator deployment sees zero behavior change
# until an operator opts in.
_CONVERSION_KEY_ENV = "MATRIX_AI_CONVERSION_KEY"


def _check_shared_secret_gate(value: str | None, *, env_var: str, header_name: str) -> None:
    import hmac

    required = os.environ.get(env_var)
    if not required:
        return  # not configured -- today's exact, unchanged behavior
    if not value or not hmac.compare_digest(value, required):
        raise HTTPException(status_code=403, detail=f"Missing or invalid {header_name} for this action.")


def _check_approval_authorization(x_approval_key: str | None) -> None:
    _check_shared_secret_gate(x_approval_key, env_var=_APPROVAL_KEY_ENV, header_name="X-Approval-Key")


def _check_conversion_authorization(x_conversion_key: str | None) -> None:
    _check_shared_secret_gate(x_conversion_key, env_var=_CONVERSION_KEY_ENV, header_name="X-Conversion-Key")


class _ReviewRequest(BaseModel):
    status: str
    review_note: str = ""


@router.post("/research/matrix/ai/recommendations/{recommendation_id}/review")
async def matrix_ai_review_recommendation(
    recommendation_id: str,
    body: _ReviewRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
    x_approval_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """3B.4 Human approval. The ONLY endpoint that can ever move a
    recommendation off DRAFT — status must be exactly APPROVED or
    REJECTED, never anything from the Matrix cell vocabulary. This never
    generates, claims, or touches a single research_matrix_cells row;
    approving a recommendation only records that a human reviewed it and
    thinks it's worth acting on — converting `proposed_next_cells` into
    a real family is still a separate, manual POST /research/matrix/
    generate call the operator makes themselves.

    Phase 3B-H hardening:
    - `reviewed_by` is derived SERVER-SIDE from the authenticated
      caller's own masked identity (storage.audit_log._mask_actor,
      reused verbatim — the same masking already applied to every
      audit_log entry), never trusted from the request body. This
      codebase has no per-user identity beyond "holds the shared
      X-API-Key" or "holds a valid session cookie" (both callers using
      the API key are indistinguishable from each other — an honest
      limit of this system's existing auth model, not something this
      endpoint can fabricate around); a caller who wants to leave
      human-readable context can still do so via review_note, which is
      never treated as an identity claim.
    - the transition is atomic (`UPDATE ... WHERE status='DRAFT'`,
      compare-and-swap) — a second review attempt on an already-
      APPROVED/REJECTED recommendation gets 409 Conflict, never a
      silent overwrite.
    - every review action is appended to research_matrix_ai_
      recommendation_reviews (storage.matrix_ai_recommendations.
      list_recommendation_reviews) — re-reviewing never erases the
      history of a prior review.
    """
    _check_auth(x_api_key, iatis_session)
    _check_approval_authorization(x_approval_key)
    from storage import matrix_ai_recommendations as recs
    from storage.audit_log import _mask_actor

    reviewed_by = _mask_actor(x_api_key, iatis_session)

    try:
        recs.review_recommendation(
            recommendation_id, status=body.status, reviewed_by=reviewed_by,
            review_note=body.review_note or None,
        )
    except ValueError as exc:
        message = str(exc)
        if "unknown recommendation_id" in message:
            status_code = 404
        elif "already reviewed" in message or "no longer DRAFT" in message:
            status_code = 409
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=message)

    from storage.audit_log import log_action
    log_action(
        "matrix_ai_review", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"recommendation_id={recommendation_id} status={body.status} reviewed_by={reviewed_by}",
    )

    return recs.get_recommendation(recommendation_id)


@router.get("/research/matrix/ai/recommendations/{recommendation_id}/reviews")
async def matrix_ai_list_recommendation_reviews(
    recommendation_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 3B-H hardening — the full, append-only review history for
    one recommendation (every review action, including ones superseded
    by a later re-review), distinct from the recommendation's own row
    which only ever carries the LATEST review's summary."""
    _check_auth(x_api_key, iatis_session)
    from storage import matrix_ai_recommendations as recs

    if recs.get_recommendation(recommendation_id) is None:
        raise HTTPException(status_code=404, detail="AI recommendation not found.")

    reviews = recs.list_recommendation_reviews(recommendation_id)
    return {"reviews": reviews, "count": len(reviews)}


# =============================================================================
# Phase 3C — Controlled Recommendation Conversion
# =============================================================================
#
# The ONE new capability this phase adds: a human can turn an APPROVED
# recommendation into a real, QUEUED Matrix Family, through the SAME
# validation a hand-typed POST /research/matrix/generate request would
# hit. This is deliberately NOT "AI -> Approved -> Execute":
#
#   AI recommendation -> DRAFT -> (human) APPROVED -> (authorized human)
#   CONVERTED -> QUEUED Matrix Cells -> (separate human action) RUNNING
#
# Non-negotiable boundaries (operator's own explicit GO conditions):
#   1. No path from the AI analyzer itself to any Matrix write function --
#      conversion is triggered ONLY by an explicit, separately-authorized
#      human action, never automatically on APPROVED.
#   2. No research/results/registry.json write, no HXXX id, ever.
#   3. proposed_next_cells is revalidated as UNTRUSTED input against the
#      CURRENT symbol/engine universe -- never trusted because "the AI
#      already checked" or "a human already approved the text."
#   4. No silent dropping of an invalid symbol/engine -- always a hard
#      409/400, never a partial conversion.
#   5. Research-code-commit drift between proposal and conversion is
#      informational only, never a blocker.
#   6. Conversion stops at QUEUED -- POST /research/matrix/run-batch
#      remains the same separate, human-triggered step it always was.
#   7. One recommendation -> at most one family, never bulk.
#   8. Conversion sits behind its OWN authorization gate
#      (_check_conversion_authorization), separate from review's
#      (_check_approval_authorization) -- reusing the same shared-secret
#      CHECK SHAPE, never the same key.
_MAX_CELLS_PER_CONVERSION = 5_000  # mirrors execution.routes.research_matrix._MAX_CELLS_PER_GENERATE


def _proposed_cells_to_matrix_specs(proposed_next_cells: Any, *, research_code_commit: str | None) -> list[Any]:
    """Untrusted input -> list[backtest.research_matrix.MatrixCellSpec],
    revalidated against the CURRENT universe -- never a looser path than
    a hand-typed POST /research/matrix/generate request would go through.
    Reuses execution.routes.research_matrix's own validate_symbols_
    against_universe()/validate_risk_presets_against_known() verbatim
    (the exact same functions matrix_generate() itself calls), plus an
    engine-name check proposed_next_cells has no equivalent for
    elsewhere today (ai.ai_analyzer.AIAnalyzer only checks STRUCTURAL
    completeness -- required keys present and non-empty when stringified
    -- never that a symbol is real, an engine is a valid engine key, or
    timeframes/engines are actually lists of strings; see this module's
    own docstring, property 3). Every failure is a plain HTTPException
    (400) naming the offending index -- never a silent drop, never a
    partial conversion.

    Deliberately does NOT call execution.routes.research_matrix.
    generate_matrix_cells()'s cartesian-product path: each proposed cell
    already fully specifies its own (symbol, bundle, risk_preset)
    combination -- treating them as independent symbols/bundles/
    risk_presets axes to cross-product would silently generate MANY more
    cells than were actually proposed and approved."""
    from backtest.research_matrix import MatrixCellSpec
    from backtesting.backtest_engine import ENGINE_KEYS
    from execution.routes.research_matrix import (
        _BundleSpec,
        validate_risk_presets_against_known,
        validate_symbols_against_universe,
    )

    if not isinstance(proposed_next_cells, list) or not proposed_next_cells:
        raise HTTPException(status_code=400, detail="proposed_next_cells is empty or malformed — nothing to convert.")
    if len(proposed_next_cells) > _MAX_CELLS_PER_CONVERSION:
        raise HTTPException(
            status_code=400,
            detail=f"proposed_next_cells has {len(proposed_next_cells)} entries, over the {_MAX_CELLS_PER_CONVERSION} cap per conversion.",
        )

    specs = []
    for i, raw in enumerate(proposed_next_cells):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail=f"proposed_next_cells[{i}] is not an object.")

        symbol = raw.get("symbol")
        risk_preset = raw.get("risk_preset")
        if not isinstance(symbol, str) or not symbol.strip():
            raise HTTPException(status_code=400, detail=f"proposed_next_cells[{i}].symbol is missing or not a string.")
        if not isinstance(risk_preset, str) or not risk_preset.strip():
            raise HTTPException(status_code=400, detail=f"proposed_next_cells[{i}].risk_preset is missing or not a string.")

        try:
            bundle_spec = _BundleSpec(
                name=raw.get("bundle_name") or "", timeframes=raw.get("timeframes") or [], engines=raw.get("engines") or [],
            )
        except Exception as exc:  # pydantic.ValidationError — kept broad, message is user-safe either way
            raise HTTPException(status_code=400, detail=f"proposed_next_cells[{i}] has a malformed bundle: {exc}")

        symbol = validate_symbols_against_universe([symbol])[0]
        validate_risk_presets_against_known([risk_preset])
        unknown_engines = sorted(set(bundle_spec.engines) - set(ENGINE_KEYS))
        if unknown_engines:
            raise HTTPException(
                status_code=400,
                detail=f"proposed_next_cells[{i}] references unknown engine(s) {unknown_engines} — choose from {list(ENGINE_KEYS)}",
            )

        specs.append(MatrixCellSpec(
            symbol=symbol, bundle=bundle_spec.model_dump(), risk_preset=risk_preset,
            confluence_overrides=None, engine_variants=None,
            data_provider=None, research_code_commit=research_code_commit,
        ))
    return specs


def _convert_recommendation_core(recommendation_id: str, *, converted_by: str | None) -> dict[str, Any]:
    """The conversion's actual business logic, called only by the route
    below (kept separate so the route stays a thin auth+audit-log
    wrapper, matching every other endpoint in this router).

    Idempotent under crash recovery: if a PRIOR attempt already created a
    family for this recommendation_id but crashed before the final CAS+
    audit-insert (research_matrix_ai_recommendations still says APPROVED),
    this call finds that family via storage.research_matrix.
    get_family_by_source_recommendation() and calls upsert_cells() again
    with the SAME (deterministically-recomputed, since proposed_next_
    cells_json is immutable) cell specs — upsert_cells()'s own per-
    fingerprint dedup reports every one of them as `duplicate`, which
    is itself the "does the cell set correspond" verification: if it
    DIDN'T correspond, upsert_cells() would try to insert genuinely new
    fingerprints and hit its own planned_n-closure guard, raising
    ValueError — surfaced here as a loud 500, never silently reconciled.
    """
    from backtest.research_matrix import resolve_research_code_commit
    from storage import matrix_ai_recommendations as recs
    from storage import research_matrix as storage
    from storage.d1_client import D1Error

    row = recs.get_recommendation(recommendation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="AI recommendation not found.")
    if row["status"] != recs.APPROVED:
        raise HTTPException(
            status_code=409,
            detail=f"Recommendation {recommendation_id!r} is {row['status']!r}, not APPROVED — only an APPROVED "
                   f"recommendation may be converted, and only once.",
        )

    proposed_next_cells_json = row["proposed_next_cells_json"]
    proposed_next_cells_hash = hashlib.sha256(proposed_next_cells_json.encode("utf-8")).hexdigest()
    proposed_next_cells = json.loads(proposed_next_cells_json)

    try:
        constraints_used = json.loads(row["constraints_used_json"])
    except Exception:  # noqa: BLE001 — provenance is best-effort informational, never fatal here
        constraints_used = {}
    proposed_at_commit = constraints_used.get("research_code_commit")

    code_state = resolve_research_code_commit()
    converted_at_commit = code_state["commit"]

    cell_specs = _proposed_cells_to_matrix_specs(proposed_next_cells, research_code_commit=converted_at_commit)

    existing_family = storage.get_family_by_source_recommendation(recommendation_id)
    if existing_family is not None:
        family_id = existing_family["family_id"]
        try:
            result = storage.upsert_cells(cell_specs, family_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"An orphaned family {family_id!r} already exists for recommendation {recommendation_id!r} "
                    f"but its cell set does not match this recommendation's own proposed_next_cells ({exc}) — "
                    f"refusing to silently reconcile. Manual investigation required."
                ),
            )
    else:
        family_id = uuid.uuid4().hex[:12]
        symbols_json = json.dumps(sorted({c.symbol for c in cell_specs}))
        try:
            storage.upsert_family(
                family_id, planned_n=len(cell_specs), family_alpha=0.05,
                symbols_json=symbols_json, source_recommendation_id=recommendation_id,
            )
        except D1Error as exc:
            # UNIQUE(source_recommendation_id) — a concurrent conversion
            # attempt for this SAME recommendation_id won the race.
            raise HTTPException(
                status_code=409,
                detail=f"Recommendation {recommendation_id!r} is already being (or has already been) converted "
                       f"by a concurrent request ({exc}).",
            )
        result = storage.upsert_cells(cell_specs, family_id)

    try:
        recs.convert_recommendation(
            recommendation_id, family_id=family_id,
            proposed_next_cells_hash=proposed_next_cells_hash, cells_considered=len(cell_specs),
            proposed_at_commit=proposed_at_commit, converted_at_commit=converted_at_commit,
            converted_by=converted_by,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "cannot be converted" in message else (404 if "unknown recommendation_id" in message else 400)
        raise HTTPException(status_code=status_code, detail=message)

    return {
        "recommendation_id": recommendation_id, "family_id": family_id,
        "planned_n": len(cell_specs), "cells_considered": len(cell_specs),
        "commit_drift": proposed_at_commit != converted_at_commit,
        "proposed_at_commit": proposed_at_commit, "converted_at_commit": converted_at_commit,
        **result,
    }


@router.post("/research/matrix/ai/recommendations/{recommendation_id}/convert")
async def matrix_ai_convert_recommendation(
    recommendation_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
    x_conversion_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """3C — Controlled Recommendation Conversion. Takes NO request body:
    conversion always acts on the recommendation's own already-persisted,
    already-immutable proposed_next_cells — a caller cannot supply
    alternate cell data through this endpoint, only trigger the
    conversion of what a human already reviewed and approved.

    Behind its OWN, separate authorization gate
    (_check_conversion_authorization / MATRIX_AI_CONVERSION_KEY) — an
    operator who can APPROVE is not automatically able to CONVERT unless
    they also hold the (separately-configured, also opt-in and disabled
    by default) conversion key. `converted_by` is derived server-side
    from the authenticated caller's own masked identity
    (storage.audit_log._mask_actor), exactly like reviewed_by — never
    trusted from a request body.

    Structurally incapable of writing research/results/registry.json,
    config.yaml, config/engines.yaml, or config/symbols.yaml, and never
    assigns an HXXX id — see the module-level comment block above this
    endpoint for the full boundary list. Stops at QUEUED: the resulting
    family's cells are exactly as "ephemeral research" as any hand-typed
    POST /research/matrix/generate family — POST /research/matrix/
    run-batch remains a separate, human-triggered action."""
    _check_auth(x_api_key, iatis_session)
    _check_conversion_authorization(x_conversion_key)
    from storage.audit_log import _mask_actor, log_action

    converted_by = _mask_actor(x_api_key, iatis_session)
    result = _convert_recommendation_core(recommendation_id, converted_by=converted_by)

    log_action(
        "matrix_ai_convert", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"recommendation_id={recommendation_id} family_id={result['family_id']} "
               f"cells_considered={result['cells_considered']} commit_drift={result['commit_drift']} "
               f"converted_by={converted_by}",
    )
    return result


@router.get("/research/matrix/ai/recommendations/{recommendation_id}/conversion")
async def matrix_ai_get_conversion(
    recommendation_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Read-only — the conversion-audit record for one recommendation, if
    it has been converted. Lets a caller confirm a conversion actually
    completed (e.g. after a 409 on a naive retry) without re-deriving
    anything from the recommendation row itself."""
    _check_auth(x_api_key, iatis_session)
    from storage import matrix_ai_recommendations as recs

    if recs.get_recommendation(recommendation_id) is None:
        raise HTTPException(status_code=404, detail="AI recommendation not found.")
    conversion = recs.get_conversion(recommendation_id)
    if conversion is None:
        raise HTTPException(status_code=404, detail="This recommendation has not been converted.")
    return conversion
