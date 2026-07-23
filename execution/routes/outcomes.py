"""
execution/routes/outcomes.py
-------------------------------
Trade outcome tracking: shadow book (counterfactual gate ledger),
system metrics, data-confidence, broker reconciliation snapshot,
execution-quality (TCA), meta-analysis, and the outcomes read/close
endpoints. Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException

from execution.api_core import _check_auth, _get_config, logger

router = APIRouter()


@router.get("/shadow-book")
async def shadow_book_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Per-gate counterfactual ledger: what the rejected signals would have
    done (storage/shadow_book.py). avg_r < 0 = the gate saves losses;
    avg_r > 0 = the gate rejects profit. The audit's calibration input."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.shadow_book import gate_ledger
        return gate_ledger()
    except Exception as exc:
        logger.error(f"shadow-book failed: {exc}")
        raise HTTPException(status_code=503, detail="Shadow book unavailable.")


@router.get("/metrics")
async def metrics_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
):
    """Prometheus-text metrics (execution/metrics.py): scheduler heartbeat
    age, D1 up/latency, decision/fill counters, schema version. Auth-gated
    like every other data endpoint — point the scraper at it with the
    X-API-Key header. render_metrics() never raises: a D1 outage yields
    iatis_d1_up 0, not a 500."""
    _check_auth(x_api_key, iatis_session)
    from fastapi.responses import PlainTextResponse
    from execution.metrics import render_metrics
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


@router.get("/data-confidence")
async def data_confidence_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Cross-provider data-confidence history (core/data_confidence.py).
    Reads the stored check table — never triggers provider fetches, so
    the dashboard can poll it freely. Monitoring only, never a gate."""
    _check_auth(x_api_key, iatis_session)
    try:
        from core.data_confidence import recent_checks
        return recent_checks()
    except Exception as exc:
        logger.error(f"data-confidence failed: {exc}")
        raise HTTPException(status_code=503, detail="Data-confidence history unavailable.")


@router.get("/reconciliation")
async def reconciliation_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Last stored broker-vs-internal reconciliation result. Read-only by
    design: this process must NEVER run reconcile() itself — that would
    open a second cTrader session and evict the scheduler's (single
    session slot per account). The scheduler stores after every tick."""
    _check_auth(x_api_key, iatis_session)
    try:
        from execution.reconciliation import last_result
        result = last_result()
        return result or {"status": "none", "reason": "no reconciliation stored yet"}
    except Exception as exc:
        logger.error(f"reconciliation read failed: {exc}")
        raise HTTPException(status_code=503, detail="Reconciliation history unavailable.")


@router.get("/execution-quality")
async def execution_quality_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """TCA report (storage/execution_quality.py): realized slippage per
    symbol/session vs the backtest's slippage_pips assumption. Adverse-
    positive, in backtest pip units. Real broker fills only."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.execution_quality import summary
        return summary()
    except Exception as exc:
        logger.error(f"execution-quality failed: {exc}")
        raise HTTPException(status_code=503, detail="Execution-quality ledger unavailable.")


@router.get("/meta-analysis")
async def meta_analysis(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Phase 4.2: Meta-Analysis Dashboard.

    Returns:
    - Confidence calibration (score bucket → actual win rate)
    - Regime performance matrix (TRENDING/RANGING/VOLATILE → WR/PF)
    - Trade frequency per symbol per year
    - Dynamic weight suggestions
    """
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.calibration import (
            calibration_from_db, regime_performance_matrix, suggested_dynamic_weights
        )
        from storage.engine_tracker import engine_stats, neutral_rate_by_engine
        config = _get_config()
        weights = config.get("confluence", {}).get("weights", {})

        calibration = calibration_from_db()
        regime_matrix = regime_performance_matrix()
        engine_performance = engine_stats(min_votes=5)
        neutral_rates = neutral_rate_by_engine()
        dynamic_weights = suggested_dynamic_weights(weights)

        return {
            "calibration": {
                "data": calibration,
                "note": "Requires live/paper trade outcomes. Empty until trades close.",
                "bucket_count": len(calibration),
            },
            "regime_matrix": {
                "data": regime_matrix,
                "note": "WR/PF/expectancy per market regime.",
            },
            "engine_performance": {
                "data": engine_performance,
                "neutral_rates": neutral_rates,
            },
            "dynamic_weights": dynamic_weights,
        }
    except Exception as exc:
        logger.error(f"Meta-analysis error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/outcomes")
async def get_outcomes(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
    limit: int = 20,
) -> dict[str, Any]:
    """Get recent signals and performance summary for outcome tracking."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.outcome_tracker import recent_signals, performance_summary, get_open_signals
        return {
            "summary": performance_summary(),
            "open_signals": get_open_signals(),
            "recent": recent_signals(limit=limit),
        }
    except Exception as exc:
        logger.error(f"Outcomes error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.post("/outcomes/{signal_id}/close")
async def close_outcome(
    signal_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
    exit_price: float = 0.0,
    outcome: str = "win",
    notes: str = "",
) -> dict[str, Any]:
    """Record the outcome of a trade. outcome: win/loss/breakeven"""
    _check_auth(x_api_key, iatis_session)
    from storage.outcome_tracker import close_signal
    success = close_signal(signal_id, exit_price, outcome, notes=notes)
    from storage.audit_log import log_action
    log_action("close_outcome", x_api_key=x_api_key, session_id=iatis_session,
               success=success, detail=f"{signal_id} -> {outcome}")
    return {"success": success, "signal_id": signal_id, "outcome": outcome}
