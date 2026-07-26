"""
execution/routes/ai.py
-------------------------
AI briefing endpoints (weight-optimization suggestions, trade
explanations, news/macro analysis, research/daily summaries). AI is
advisory-only and provably outside the decision path — these endpoints
never feed a gate, weight, or trading decision.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Request

from execution.api_core import _check_auth, _get_config, logger

router = APIRouter()

# AI Copilot draft storage (Phase 4d, 2026-07-26) — a fixed, server-
# controlled directory nothing else in the codebase reads (edge_gate.py /
# philosophy_audit.py / forward_review.py only ever read registry.json +
# the outcomes DB). Module-level so tests can monkeypatch it instead of
# writing into the real repo tree, same pattern as research.py's _DATA_DIR.
_DRAFTS_DIR = Path("research/hypotheses/drafts")


@router.post("/ai/optimize-weights")
async def ai_optimize_weights(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
    dry_run: bool = True,
) -> dict[str, Any]:
    """AI Dynamic Weight Optimizer — uses Claude to suggest engine weights.

    Analyzes live engine performance + outcome data.
    dry_run=True: returns suggestions without applying.
    dry_run=False: applies weights to config.yaml.

    Requires 20+ closed trades in outcome_tracker for meaningful analysis.
    """
    _check_auth(x_api_key, iatis_session)
    config = _get_config()
    if not config.get("features", {}).get("ai_weight_suggestions", True):
        raise HTTPException(status_code=403, detail="ai_weight_suggestions is disabled in config.yaml's features section.")
    try:
        from ai.dynamic_weights import analyze_and_suggest_weights, apply_weights_to_config
        from storage.engine_tracker import engine_stats
        from storage.outcome_tracker import performance_summary
        from storage.calibration import regime_performance_matrix

        current_weights = config.get("confluence", {}).get("weights", {})

        stats = engine_stats(min_votes=5)
        outcomes = performance_summary()
        regime_data = regime_performance_matrix()

        result = analyze_and_suggest_weights(
            engine_stats=stats,
            outcome_summary=outcomes,
            current_weights=current_weights,
            regime_data=regime_data,
        )

        if not dry_run and result.get("status") == "success":
            applied = apply_weights_to_config(
                result["suggested_weights"],
                dry_run=False
            )
            result["applied"] = applied

        return result

    except Exception as exc:
        logger.error(f"AI weight optimization failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


# ---------------------------------------------------------------------------
# AI explanation layer (ai/ai_analyzer.py) — dashboard/report use only.
# Never called from /analyze or the scheduler; explanations are generated
# on demand for a decision that has already been made by confluence+risk.
# ---------------------------------------------------------------------------

@router.get("/ai/explain/{decision_id}")
async def ai_explain_trade(
    decision_id: int,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Natural-language explanation of a past decision, for the dashboard.

    Looks up the stored report by id (storage/decision_db.py) and asks
    AIAnalyzer to explain it — cached per decision_id, since the inputs
    for a past decision never change.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        import json as _json
        from ai.ai_analyzer import AIAnalyzer
        from storage.decision_db import _conn as db_conn

        with db_conn() as con:
            row = con.execute(
                "SELECT raw_json FROM decisions WHERE id=?", (decision_id,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Decision not found.")

        report = _json.loads(row["raw_json"])
        analyzer = AIAnalyzer(_get_config())
        return analyzer.explain_trade(report, cache_key=str(decision_id))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"AI explain-trade error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.post("/ai/explain-trade")
async def ai_explain_trade_inline(
    request: Request,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Explain a decision report the caller already has in hand.

    The dashboard's /decisions feed comes from the JSONL log
    (storage/decision_log.py) and has no integer row id to look up in
    decision_db.py's SQLite table, so GET /ai/explain/{decision_id}
    doesn't fit it — this endpoint takes the report body directly
    instead (the same shape returned by main.py's run_pipeline() /
    the `report` field of a /decisions entry). Cached by a hash of
    symbol+summary, since a past decision's inputs never change.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        import hashlib
        from ai.ai_analyzer import AIAnalyzer

        report = await request.json()
        if not isinstance(report, dict) or not report.get("symbol"):
            raise HTTPException(status_code=400, detail="Body must be a decision report with a symbol.")

        cache_key = hashlib.sha1(
            f"{report.get('symbol')}:{report.get('summary')}".encode("utf-8")
        ).hexdigest()[:16]

        analyzer = AIAnalyzer(_get_config())
        return analyzer.explain_trade(report, cache_key=cache_key)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"AI explain-trade (inline) error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/ai/news-analysis")
async def ai_news_analysis(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """AI read on today's economic calendar, for dashboard display only —
    does not affect the news blackout gate (fundamentals/news_risk.py)."""
    _check_auth(x_api_key, iatis_session)
    try:
        from ai.ai_analyzer import AIAnalyzer
        from fundamentals.news_calendar import get_calendar_today

        config = _get_config()
        symbols = [
            s["internal"] for s in config.get("data", {}).get("twelve_data_symbols", [])
            if s.get("enabled")
        ]
        try:
            events = get_calendar_today()
        except Exception as exc:
            logger.debug(f"Calendar fetch failed for AI news analysis: {exc}")
            events = []

        analyzer = AIAnalyzer(config)
        return analyzer.analyze_news(events, symbols)
    except Exception as exc:
        logger.error(f"AI news analysis error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/ai/macro-analysis")
async def ai_macro_analysis(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """AI read on macro/cross-asset context, for dashboard display only."""
    _check_auth(x_api_key, iatis_session)
    try:
        from ai.ai_analyzer import AIAnalyzer

        analyzer = AIAnalyzer(_get_config())
        return analyzer.analyze_macro()
    except Exception as exc:
        logger.error(f"AI macro analysis error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.post("/ai/research-summary")
async def ai_research_summary(
    request: Request,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Plain-English summary of the research/backtest state, for the
    Research & Backtests dashboard tab.

    Takes the stats the frontend already has in memory (from /research
    and /meta-analysis) in the request body, same pattern as
    POST /ai/explain-trade — avoids a third copy of the registry.json /
    backtest-file parsing logic already in those two endpoints.
    Expected body: {hypothesis_summary, latest_backtest, regime_matrix}.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        from ai.ai_analyzer import AIAnalyzer

        body = await request.json()
        hs = body.get("hypothesis_summary") or {}
        latest_bt = body.get("latest_backtest") or {}
        stats = {
            "total": hs.get("total"),
            "passed": hs.get("passed"),
            "failed": hs.get("failed"),
            "research": hs.get("research"),
            "avg_wr": latest_bt.get("avg_wr"),
            "avg_pf": latest_bt.get("avg_pf"),
            "regime_matrix": body.get("regime_matrix") or [],
        }

        analyzer = AIAnalyzer(_get_config())
        return analyzer.generate_research_summary(stats)
    except Exception as exc:
        logger.error(f"AI research summary error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.post("/ai/research-question")
async def ai_research_question(
    request: Request,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """AI Research Assistant (Backtesting Lab priority #5) — free-form
    Q&A grounded in whatever research/backtest context the frontend
    already has loaded (hypothesis registry, KPIs, sweep results,
    comparison table). Never a second data-fetching path: the caller
    supplies the context, this endpoint only forwards it to the model.
    Expected body: {context: object, question: string}.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        from ai.ai_analyzer import AIAnalyzer
        import json as _json

        body = await request.json()
        context = body.get("context") or {}
        question = str(body.get("question") or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is required.")
        if len(question) > 500:
            raise HTTPException(status_code=400, detail="question: at most 500 characters.")
        context_text = _json.dumps(context, indent=2, default=str)[:8000]

        analyzer = AIAnalyzer(_get_config())
        return analyzer.answer_research_question(context_text, question)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"AI research question error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/ai/daily-report")
async def ai_daily_report(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """AI-phrased daily summary from already-computed stats — AIAnalyzer
    only writes the prose, the numbers come from decision_db/outcome_tracker."""
    _check_auth(x_api_key, iatis_session)
    try:
        from ai.ai_analyzer import AIAnalyzer
        from storage.decision_db import summary as decision_summary
        from storage.outcome_tracker import performance_summary

        stats = {**decision_summary(), **performance_summary()}
        analyzer = AIAnalyzer(_get_config())
        return analyzer.generate_daily_report(stats)
    except Exception as exc:
        logger.error(f"AI daily report error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


# ---------------------------------------------------------------------------
# AI Copilot (Phase 4d, 2026-07-26) — drafts a candidate for the operator's
# NEXT research hypothesis, grounded in the real registry + CLAUDE.md's dead
# list + the hypothesis backlog. Two-step, deliberately: suggest (read-only,
# no side effects) then save-draft (a separate, explicit, human-triggered
# write) — never a single call that both generates and persists. Neither
# endpoint ever touches research/results/registry.json; the only write
# target is _DRAFTS_DIR, a brand-new directory nothing else in the codebase
# reads.
# ---------------------------------------------------------------------------

def _extract_markdown_section(text: str, heading: str) -> str | None:
    """Return the text between `heading` (e.g. "## The dead list") and the
    next "## " heading, or None if `heading` isn't found. Callers must
    degrade gracefully (omit the section) rather than crash — a doc's
    heading wording can change without this endpoint knowing.
    """
    idx = text.find(heading)
    if idx == -1:
        return None
    rest = text[idx + len(heading):]
    next_idx = rest.find("\n## ")
    section = rest[:next_idx] if next_idx != -1 else rest
    return section.strip()


def _build_copilot_context() -> dict[str, Any]:
    """Everything the suggest_next_hypothesis prompt needs: a compact
    registry summary (so the model knows what's already been tried), the
    CLAUDE.md dead list (so a suggestion never rebuilds a killed idea
    unchecked), and the newest hypothesis backlog's reserved-IDs section
    (so it steers toward already-triaged candidates). Every section
    degrades gracefully to omitted/None on a missing file or a doc-heading
    edit — this must never 500 the endpoint over documentation formatting.
    """
    from execution.routes.research import _load_registry_hypotheses

    hypotheses_raw = _load_registry_hypotheses()
    registry_summary = [
        {
            "id": h_id,
            "title": (h.get("title") or "")[:120],
            "status": h.get("status", "UNKNOWN"),
            "conclusion": (h.get("conclusion") or h.get("lesson") or "")[:200],
        }
        for h_id, h in hypotheses_raw.items()
    ]

    highest_id = 0
    for h_id in hypotheses_raw:
        m = re.match(r"^H(\d+)", h_id)
        if m:
            highest_id = max(highest_id, int(m.group(1)))

    dead_list = None
    claude_md = Path("CLAUDE.md")
    if claude_md.exists():
        try:
            dead_list = _extract_markdown_section(
                claude_md.read_text(encoding="utf-8"), "## The dead list"
            )
        except Exception as exc:
            logger.debug(f"Copilot context: could not read CLAUDE.md dead list: {exc}")

    backlog_reserved = None
    try:
        backlog_files = sorted(
            Path("research/hypotheses").glob("BACKLOG_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if backlog_files:
            backlog_reserved = _extract_markdown_section(
                backlog_files[0].read_text(encoding="utf-8"), "## Reserved registry IDs"
            )
    except Exception as exc:
        logger.debug(f"Copilot context: could not read hypothesis backlog: {exc}")

    return {
        "registry_summary": registry_summary,
        "highest_registered_id": f"H{highest_id:03d}" if highest_id else None,
        "already_killed_ideas": dead_list or "Not available — see CLAUDE.md's dead list table directly.",
        "reserved_backlog_ids": backlog_reserved or "Not available.",
    }


@router.post("/ai/suggest-hypothesis")
async def ai_suggest_hypothesis(
    request: Request,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Drafts a candidate for the operator's next research hypothesis.
    Read-only: no side effects, nothing written to disk. Optional body:
    {focus_hint: string} — free text steering the suggestion (e.g. "crypto
    order flow"), same convention as /ai/research-question's free-form
    question box. Empty/omitted hint lets the prompt default toward
    docs/PHILOSOPHY_AUDIT_2026-07.md's own named next-candidates.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        from ai.ai_analyzer import AIAnalyzer

        body = await request.json() if await request.body() else {}
        focus_hint = str(body.get("focus_hint") or "")[:300]
        context = _build_copilot_context()

        analyzer = AIAnalyzer(_get_config())
        return analyzer.suggest_next_hypothesis(context, focus_hint)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"AI suggest-hypothesis error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "untitled"


def _render_draft_markdown(body: dict[str, Any], timestamp: str) -> str:
    def _s(key: str) -> str:
        return str(body.get(key) or "").strip()

    data_required = body.get("data_required") or {}

    return f"""<!-- AI-GENERATED DRAFT — NOT REVIEWED — NOT A REGISTRATION.
     Per CLAUDE.md rule 1, this must be reviewed, refined, and manually
     registered in research/results/registry.json (with its own ID)
     before any experiment runs against it. Never copy directly. -->

# {_s("title") or "Untitled"}

**Status:** DRAFT (AI-suggested, unreviewed)
**Generated:** {timestamp}

## Statement
{_s("statement")}

## Why this might be true
{_s("why_this_might_be_true")}

## Data required
{json.dumps(data_required, indent=2)}

## Falsification criteria
{_s("falsification_criteria")}

## Distinct from prior kill
{_s("distinct_from_prior_kill")}

## Notes
{_s("notes")}
"""


@router.post("/ai/save-hypothesis-draft")
async def ai_save_hypothesis_draft(
    request: Request,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Persists a Copilot suggestion (whatever the operator has on screen)
    as a DRAFT markdown file, shaped like research/hypotheses/TEMPLATE.md
    so it can be renamed/finished into a real HXXX_*.md by hand. NEVER
    writes to research/results/registry.json — the only write target is
    _DRAFTS_DIR, a brand-new directory nothing else in the codebase reads,
    so a draft is structurally incapable of being mistaken for a real
    registration. Filename is timestamp+slug, server-constructed —
    the client never supplies a path.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        body = await request.json()
        title = str(body.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title is required.")

        slug = _slugify(title)[:60]
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        _DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        target = _DRAFTS_DIR / f"ai_draft_{ts}_{slug}.md"
        target.write_text(_render_draft_markdown(body, ts), encoding="utf-8")
        return {"file": str(target)}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"AI save-hypothesis-draft error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")

