"""
execution/routes/matrix_ai.py
------------------------------------
Hypothesis Discovery Engine, Phase 3B — AI Research Orchestrator.

AI is a PLANNER here, never a JUDGE. It reads already-decided Matrix
evidence (via backtest/matrix_research_planner.py's Evidence Context
Builder, which itself only re-presents backtest/matrix_evidence.py's
already-computed aggregations) and proposes WHERE to look next — new
(symbol, bundle, risk_preset) research combinations worth generating.

What this router can NEVER do, by construction:
  - Write research/results/registry.json, config.yaml, config/engines.yaml,
    or config/symbols.yaml.
  - Assign an HXXX id.
  - Change a research_matrix_cells row's status (nothing here calls
    storage.research_matrix.update_cell/upsert_cells/claim_*).
  - Move a recommendation to APPROVED itself — only a human, through
    POST .../review, can do that (storage.matrix_ai_recommendations.
    review_recommendation is the only writer of that field, and this
    router's own review endpoint is a thin, unconditional pass-through
    to it — no AI call happens on that path).
  - Auto-convert an APPROVED recommendation into real Matrix cells.
    That conversion is deliberately NOT built here (it would be Phase 3C,
    "Controlled Recommendation Conversion" — a genuinely new capability
    boundary, never bundled into this advisory-only phase) — an operator
    who approves a recommendation still has to read its
    `proposed_next_cells` and submit them through the existing, unchanged
    POST /research/matrix/generate themselves.

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


def _check_approval_authorization(x_approval_key: str | None) -> None:
    import hmac

    required = os.environ.get(_APPROVAL_KEY_ENV)
    if not required:
        return  # not configured -- today's exact, unchanged behavior
    if not x_approval_key or not hmac.compare_digest(x_approval_key, required):
        raise HTTPException(status_code=403, detail="Missing or invalid X-Approval-Key for this review action.")


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
