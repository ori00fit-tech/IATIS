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

from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Request

from execution.api_core import _check_auth, _get_config, logger

router = APIRouter()


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

