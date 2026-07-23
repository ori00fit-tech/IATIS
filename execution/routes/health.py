"""
execution/routes/health.py
-----------------------------
System health/status endpoints: /health (public), /budget, /stats,
/health/full (full component status), /symbol-health.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException

from execution.api_core import _check_auth, _get_config, logger
from execution.api_shared_helpers import _scheduler_status, _systemd_service_status

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    """Public health check — no auth required."""
    config = _get_config()
    credits = None
    try:
        from core.twelve_data_client import RateLimiter
        credits = RateLimiter().remaining_today()
    except Exception:
        pass
    return {
        "status": "ok",
        "version": config.get("system", {}).get("version", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "twelve_data_credits_remaining": credits,
        # First entry of data.timeframes — the TF engine votes are computed
        # on (H4-primary since 2026-07). Surfaced so the dashboard shows
        # which system is actually running.
        "decision_timeframe": (config.get("data", {}).get("timeframes") or ["H1"])[0],
    }



@router.get("/budget")
async def budget(x_api_key: str | None = Header(default=None), iatis_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    try:
        from core.twelve_data_client import RateLimiter, MAX_REQUESTS_PER_DAY
        remaining = RateLimiter().remaining_today()
        used = MAX_REQUESTS_PER_DAY - remaining
        return {
            "max_per_day": MAX_REQUESTS_PER_DAY,
            "used_today": used,
            "remaining_today": remaining,
            "percent_used": round(used / MAX_REQUESTS_PER_DAY * 100, 1),
        }
    except Exception as exc:
        logger.error(f"Budget error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/stats")
async def stats(x_api_key: str | None = Header(default=None), iatis_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.decision_db import summary, regime_performance
        return {"summary": summary(), "regime_performance": regime_performance()}
    except Exception as exc:
        logger.error(f"Stats error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")




@router.get("/health/full")
async def system_health_full(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """System Health Dashboard — full status of all components."""
    _check_auth(x_api_key, iatis_session)

    import psutil
    import time as _time
    from pathlib import Path

    now_utc = datetime.now(timezone.utc).isoformat()
    checks: dict[str, Any] = {}
    issues: list[str] = []

    # 1. CPU / RAM / Disk
    mon_cfg = _get_config().get("monitoring", {})
    ram_warn_pct = mon_cfg.get("ram_warn_pct", 85)
    disk_warn_pct = mon_cfg.get("disk_warn_pct", 80)
    try:
        swap = psutil.swap_memory()
        try:
            load1, load5, load15 = os.getloadavg()
        except (OSError, AttributeError):
            load1 = load5 = load15 = None  # not available on this platform
        checks["system"] = {
            "cpu_pct": psutil.cpu_percent(interval=0.5),
            "ram_pct": psutil.virtual_memory().percent,
            "disk_pct": psutil.disk_usage("/").percent,
            "swap_pct": swap.percent,
            "load_1m": load1, "load_5m": load5, "load_15m": load15,
            "uptime_hours": round((_time.time() - psutil.boot_time()) / 3600, 1),
        }
        if checks["system"]["ram_pct"] > ram_warn_pct: issues.append("High RAM usage")
        if checks["system"]["disk_pct"] > disk_warn_pct: issues.append("High disk usage")
        if checks["system"]["swap_pct"] > 50: issues.append("High swap usage")
    except Exception as e:
        checks["system"] = {"error": str(e)[:80]}

    # 2. Scheduler last run
    try:
        checks["scheduler"] = _scheduler_status()
    except Exception as e:
        checks["scheduler"] = {"status": "error", "error": str(e)[:80]}

    # 2b. Real per-service systemd status (module 1) — same whitelist of
    # units /logs already knows about, one `systemctl is-active` call per
    # unit (fixed argv, never shell=True). Absent/inert on hosts with no
    # systemd (e.g. this sandbox, or a dev laptop) — reported, not fatal.
    try:
        checks["services"] = _systemd_service_status()
    except Exception as e:
        checks["services"] = {"error": str(e)[:80]}

    # 3. SQLite decisions DB
    try:
        from storage.decision_db import _conn as db_conn
        with db_conn() as con:
            total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            # column is 'ts' not 'timestamp'
            recent = con.execute(
                "SELECT COUNT(*) FROM decisions WHERE ts > datetime('now','-24 hours')"
            ).fetchone()[0]
        checks["database"] = {"status": "ok", "total_decisions": total, "last_24h": recent}
    except Exception as e:
        checks["database"] = {"status": "error", "error": str(e)[:100]}

    # 4. Calendar cache
    try:
        cache_path = Path("storage/calendar_cache.json")
        if cache_path.exists():
            import json as _json
            cache_data = _json.loads(cache_path.read_text())
            checks["calendar"] = {
                "status": "ok",
                "fetched_at": cache_data.get("fetched_at", "?"),
                "event_count": cache_data.get("count", 0),
            }
        else:
            checks["calendar"] = {"status": "no_cache", "note": "Run scripts/cache_calendar.py"}
            issues.append("Calendar cache missing — run scripts/cache_calendar.py")
    except Exception as e:
        checks["calendar"] = {"status": "error", "error": str(e)[:80]}

    # 5. Outcome tracker
    try:
        from storage.outcome_tracker import performance_summary
        summary = performance_summary()
        checks["outcome_tracker"] = {
            "status": "ok",
            "total_closed": summary["total_closed"],
            "win_rate": summary["win_rate"],
            "open_signals": summary["open_signals"],
        }
    except Exception as e:
        checks["outcome_tracker"] = {"status": "error", "error": str(e)[:80]}

    # 5b. Exposure estimate — an UPPER BOUND, not the live risk-engine
    # figure. risk/portfolio_exposure.py tracks real open-position risk
    # in-memory inside the scheduler process (its own docstring: "Phase 1:
    # in-memory only"); the API server is a separate process and cannot
    # read that state. Assuming every open paper-trading signal risks
    # risk_per_trade_max (the ceiling, not the actual per-trade size,
    # which SHI's position_multiplier can reduce) gives a directionally
    # honest "how close to the cap could we be" number without claiming
    # precision this endpoint can't actually verify.
    try:
        from storage.outcome_tracker import get_open_signals
        risk_cfg = _get_config().get("risk", {})
        max_exposure = float(risk_cfg.get("max_exposure", 0.05))
        risk_per_trade_max = float(risk_cfg.get("risk_per_trade_max", 0.01))
        open_count = len(get_open_signals())
        estimated_pct = open_count * risk_per_trade_max
        checks["exposure_estimate"] = {
            "open_positions": open_count,
            "estimated_pct": round(estimated_pct * 100, 2),
            "max_exposure_pct": round(max_exposure * 100, 2),
            "utilization_pct": round(min(100.0, estimated_pct / max_exposure * 100), 1) if max_exposure > 0 else None,
            "note": ("Upper bound — assumes every open position risks risk_per_trade_max. "
                     "Not the live risk-engine figure; that state is in-memory in the "
                     "scheduler process and unreachable from the API server."),
        }
    except Exception as e:
        checks["exposure_estimate"] = {"status": "error", "error": str(e)[:80]}

    # 6. Data providers
    checks["data_providers"] = {
        "twelve_data": "configured" if os.environ.get("TWELVE_DATA_API_KEY") else "missing",
        "alpha_vantage": "configured" if os.environ.get("ALPHA_VANTAGE_API_KEY") else "missing",
        "finnhub": "configured" if os.environ.get("FINNHUB_API_KEY") else "missing",
        "economic_calendar": "forex_factory (keyless)",
        "yahoo_finance": "always_available",
    }

    # 7. cTrader
    ct_token = os.environ.get("CTRADER_ACCESS_TOKEN", "")
    checks["ctrader"] = {
        "configured": bool(ct_token and ct_token != "TOKEN_HERE"),
        "account_id": os.environ.get("CTRADER_ACCOUNT_ID", "not_set"),
        "environment": os.environ.get("CTRADER_ENVIRONMENT", "not_set"),
    }
    if not checks["ctrader"]["configured"]:
        issues.append("cTrader access token not set")

    return {
        "status": "healthy" if not issues else "degraded",
        "issues": issues,
        "checked_at": now_utc,
        **checks,
    }


@router.get("/symbol-health")
async def symbol_health_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Symbol Health Index for all active symbols."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.symbol_health import get_all_symbol_health
        config = _get_config()
        symbols_cfg = config.get("data", {}).get("twelve_data_symbols", [])
        active = [s["internal"] for s in symbols_cfg if s.get("enabled")]
        health = get_all_symbol_health(active)
        paused = [h for h in health if h["status"] == "PAUSED"]
        caution = [h for h in health if h["status"] == "CAUTION"]
        return {
            "total": len(health),
            "healthy": len(health) - len(paused) - len(caution),
            "caution": len(caution),
            "paused": len(paused),
            "symbols": health,
        }
    except Exception as exc:
        logger.error(f"Symbol health error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")
