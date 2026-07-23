"""
execution/routes/experience.py
---------------------------------
Experience Database (MROS), Engine Analytics, and legacy backtest-results
endpoints. Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query

from execution.api_core import _check_auth, _get_config, logger

router = APIRouter()


@router.get("/experience/summary")
async def experience_summary_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict:
    """Experience Database summary — MROS Level 1."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.experience_db import experience_summary
        return experience_summary()
    except Exception as exc:
        logger.error(f"Experience summary error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/experience/query")
async def experience_query_endpoint(
    symbol: str | None = Query(default=None),
    regime: str | None = Query(default=None),
    session: str | None = Query(default=None),
    verdict: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict:
    """Query experiences with filters."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.experience_db import query_experiences
        results = query_experiences(
            symbol=symbol, regime=regime, session=session,
            verdict=verdict, min_score=min_score, limit=limit,
        )
        return {"count": len(results), "experiences": results}
    except Exception as exc:
        logger.error(f"Experience query error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/experience/pattern")
async def experience_pattern_endpoint(
    regime: str | None = Query(default=None),
    session: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict:
    """Pattern analysis — WR for specific market conditions."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.experience_db import pattern_analysis
        filters = {}
        if regime: filters["regime"] = regime
        if session: filters["session"] = session
        if symbol: filters["symbol"] = symbol
        if min_score: filters["min_score"] = min_score
        return pattern_analysis(filters)
    except Exception as exc:
        logger.error(f"Pattern analysis error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/engine-stats")
async def engine_stats_endpoint(
    symbol: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Per-engine performance statistics and suggested weight adjustments."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.engine_tracker import engine_stats, engine_trade_attribution, neutral_rate_by_engine, suggested_weights
        config = _get_config()
        current_weights = config.get("confluence", {}).get("weights", {})

        stats = engine_stats(min_votes=5, symbol=symbol)
        neutral = neutral_rate_by_engine()
        suggested = suggested_weights(current_weights)
        attribution = engine_trade_attribution()

        return {
            "engine_stats": stats,
            "neutral_rates": neutral,
            "current_weights": current_weights,
            "suggested_weights": suggested,
            "attribution": attribution,
            "note": "Suggested weights need 20+ votes per engine to be reliable. Review before applying."
        }
    except Exception as exc:
        logger.error(f"Engine stats error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/backtest-results")
async def backtest_results(x_api_key: str | None = Header(default=None), iatis_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    """List saved backtest results — supports both old and new format."""
    _check_auth(x_api_key, iatis_session)
    import json as _json
    from pathlib import Path
    results = []
    storage = Path("storage")

    # New format: full_pipeline_backtest_YYYY-MM-DD.json
    for f in sorted(storage.glob("full_pipeline_backtest_*.json"), reverse=True)[:3]:
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            for r in data.get("results", []):
                if not r.get("error") and r.get("trades", 0) >= 10:
                    results.append({
                        "file": f.name,
                        "symbol": r.get("symbol"),
                        "period": data.get("generated_at", "")[:10],
                        "trades": r.get("trades", 0),
                        "win_rate": r.get("win_rate", 0),
                        "profit_factor": r.get("profit_factor", 0),
                        "max_drawdown_pct": r.get("max_drawdown_pct", 0),
                        "total_return_pct": r.get("total_return_pct", 0),
                        "metrics": {
                            "trades_closed": r.get("trades", 0),
                            "win_rate": r.get("win_rate", 0) / 100,
                            "profit_factor": r.get("profit_factor", 0),
                            "max_drawdown_pct": r.get("max_drawdown_pct", 0) / 100,
                            "total_return_pct": r.get("total_return_pct", 0) / 100,
                        }
                    })
        except Exception:
            continue

    # Old format fallback. backtest_engine.save() stores rate-like metrics as
    # fractions (win_rate/dd/return in 0..1); the new format above exposes them
    # as top-level percentages. Project the legacy shape onto the same top-level
    # keys so the dashboard renders one consistent row shape (it was previously
    # emitting only `metrics`, which crashed the Research & Backtests table).
    if not results:
        for f in sorted(storage.glob("backtest_*.json")):
            try:
                data = _json.loads(f.read_text())
                m = data.get("metrics", {})
                # equity_curve (a per-bar balance series) exists only in the
                # legacy backtest_engine.save() format — the pipeline runs above
                # don't persist one. Down-sample very long curves so the payload
                # stays small; the dashboard's Backtesting Charts plots it when
                # present and degrades to a metrics-only view otherwise.
                curve = data.get("equity_curve") or []
                if len(curve) > 500:
                    stride = len(curve) // 500 + 1
                    curve = curve[::stride]
                results.append({
                    "file": f.name,
                    "symbol": data.get("symbol"),
                    "period": data.get("period"),
                    "trades": m.get("trades_closed", 0),
                    "win_rate": round(m.get("win_rate", 0) * 100, 1),
                    "profit_factor": round(m.get("profit_factor", 0), 3),
                    "max_drawdown_pct": round(m.get("max_drawdown_pct", 0) * 100, 2),
                    "total_return_pct": round(m.get("total_return_pct", 0) * 100, 2),
                    "equity_curve": curve,
                    "metrics": m,
                })
            except Exception:
                continue

    return {"count": len(results), "results": results}

