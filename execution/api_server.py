"""
execution/api_server.py
---------------------------
FastAPI HTTP server — Phase 2. Security-hardened.

Security measures applied (see IATIS_Security_Audit.md):
- P0: Auth fail-closed (API_SERVER_KEY required)
- P0: Dashboard requires auth
- P0: All HTML output escaped via html.escape()
- P1: Symbol input validation (strict regex)
- P1: Constant-time key comparison (hmac.compare_digest)
- P1: Generic error messages (details logged only)
- P2: Swagger disabled in production (ENV=production)
- P3: SQLite/cache file permissions set at init
- P3: Config cache thread-safe (threading.Lock)
"""

from __future__ import annotations

import asyncio
import hmac
import html
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
load_dotenv()

try:
    from fastapi import FastAPI, Header, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel
except ImportError as exc:
    raise ImportError("Run: pip install fastapi uvicorn") from exc

from utils.helpers import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Symbol validation — strict allowlist
# ---------------------------------------------------------------------------
_SYMBOL_RE = re.compile(r'^[A-Z]{2,6}(/[A-Z]{2,6})?$')

def _validate_symbol(symbol: str) -> str:
    """Validate and normalize symbol. Raises HTTPException on invalid input."""
    clean = symbol.upper().strip()
    if not _SYMBOL_RE.match(clean):
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Use EURUSD or EUR/USD (letters only, 2-6 chars each)."
        )
    if len(clean) > 14:
        raise HTTPException(status_code=400, detail="Symbol too long.")
    return clean

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
_ENV = os.environ.get("ENV", "production").lower()
_docs_url = "/docs" if _ENV == "development" else None

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup/shutdown lifecycle handler."""
    # Startup
    from pathlib import Path
    for path in ["storage/decisions.db", "storage/engine_tracker.db"]:
        p = Path(path)
        if p.exists():
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass
    for path in ["storage/td_cache"]:
        p = Path(path)
        if p.is_dir():
            try:
                os.chmod(p, 0o700)
            except Exception:
                pass
    logger.info(
        f"IATIS API started "
        f"(ENV={_ENV}, auth={'required' if os.environ.get('API_SERVER_KEY') else 'dev-mode'})"
    )
    yield
    # Shutdown (nothing needed)


app = FastAPI(
    title="IATIS API",
    description="Institutional Adaptive Trading Intelligence System",
    version="0.3.0",
    docs_url=_docs_url,
    redoc_url=None,
    lifespan=lifespan,
)

_executor = ThreadPoolExecutor(max_workers=4)
_config_cache: dict | None = None
_config_lock = threading.Lock()

# Telegram error cooldown (issue #11 — flood protection)
_error_cooldown: dict[str, float] = {}
_COOLDOWN_SECONDS = 1800  # 30 minutes per symbol

def _get_config() -> dict:
    """Thread-safe config cache (issue #14)."""
    global _config_cache
    with _config_lock:
        if _config_cache is None:
            _config_cache = load_config()
            api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
            if api_key:
                _config_cache["data"]["twelve_data_api_key"] = api_key
    return _config_cache


def _check_auth(x_api_key: str | None) -> None:
    """Fail-closed auth — error if key not configured (issue #3).
    Constant-time comparison to prevent timing attacks (issue #6).
    """
    required = os.environ.get("API_SERVER_KEY")
    if not required:
        # In development mode, skip auth. In production, fail-closed.
        if _ENV == "development":
            return
        raise HTTPException(
            status_code=500,
            detail="API_SERVER_KEY not configured on server."
        )
    if not hmac.compare_digest(x_api_key or "", required):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")


def _h(value: Any) -> str:
    """HTML-escape any value for safe dashboard rendering (issue #1)."""
    return html.escape(str(value) if value is not None else "")


def _set_file_permissions(path) -> None:
    """Restrict file to owner read/write only (issue #9, #10)."""
    try:
        from pathlib import Path
        p = Path(path)
        if p.exists():
            os.chmod(p, 0o600)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class AnalyzeRequest(BaseModel):
    source: str = "twelve_data"
    bars: int = 500
    timeframes: list[str] = ["M15", "H1", "H4", "D1"]


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
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
    }


@app.post("/analyze/{symbol}")
async def analyze(
    symbol: str,
    req: AnalyzeRequest = AnalyzeRequest(),
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Run full pipeline for one symbol. Symbol: EURUSD or EUR/USD."""
    _check_auth(x_api_key)
    clean_symbol = _validate_symbol(symbol)  # issue #2

    td_symbol = clean_symbol if "/" in clean_symbol else (
        f"{clean_symbol[:3]}/{clean_symbol[3:]}" if len(clean_symbol) == 6 else clean_symbol
    )
    internal_symbol = clean_symbol.replace("/", "")

    config = dict(_get_config())
    config["data"] = dict(config["data"])
    config["data"].update({
        "source": req.source,
        "symbol": internal_symbol,
        "twelve_data_symbol": td_symbol,
        "bars_to_load": req.bars,
        "timeframes": req.timeframes,
    })
    config["telegram"] = {"enabled": False}

    loop = asyncio.get_event_loop()
    try:
        from main import run_pipeline
        start = time.monotonic()
        report = await loop.run_in_executor(_executor, run_pipeline, config)
        report["processing_time_sec"] = round(time.monotonic() - start, 3)
        return JSONResponse(content=report)
    except Exception as exc:
        logger.error(f"Pipeline error for {internal_symbol}: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal processing error.")  # issue #7


@app.get("/decisions")
async def decisions(
    limit: int = Query(default=20, ge=1, le=200),
    verdict: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key)
    try:
        from storage.decision_log import read_decisions, summarize_decisions
        all_d = read_decisions()
        if verdict:
            all_d = [d for d in all_d if d.get("final_verdict") == verdict.upper()]
        return {
            "total_in_log": len(all_d),
            "returned": len(all_d[-limit:]),
            "summary": summarize_decisions(),
            "decisions": list(reversed(all_d[-limit:])),
        }
    except Exception as exc:
        logger.error(f"Decisions error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@app.get("/budget")
async def budget(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_api_key)
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


@app.get("/stats")
async def stats(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_api_key)
    try:
        from storage.decision_db import summary, regime_performance
        return {"summary": summary(), "regime_performance": regime_performance()}
    except Exception as exc:
        logger.error(f"Stats error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@app.get("/dashboard")
async def dashboard(x_api_key: str | None = Header(default=None)):
    """HTML dashboard - requires auth. All values HTML-escaped."""
    _check_auth(x_api_key)

    try:
        from storage.decision_db import summary as db_summary, regime_performance, recent
        s = db_summary()
        regime_perf = regime_performance()
        last_decisions = recent(limit=5)
    except Exception as exc:
        logger.error(f"Dashboard DB error: {exc}", exc_info=True)
        return HTMLResponse("<pre>Dashboard unavailable</pre>", status_code=500)

    config = _get_config()
    version = _h(config.get("system", {}).get("version", "?"))

    try:
        from core.twelve_data_client import RateLimiter, MAX_REQUESTS_PER_DAY
        credits = RateLimiter().remaining_today()
        credit_pct = int(credits / MAX_REQUESTS_PER_DAY * 100)
        credit_color = "green" if credit_pct > 50 else "orange" if credit_pct > 20 else "red"
    except Exception:
        credits, credit_pct, credit_color = "?", 0, "gray"

    decisions_html = ""
    for d in last_decisions:
        v = _h(d.get("verdict", "?"))
        color = "#00cc44" if v == "EXECUTE" else "#ff4444"
        decisions_html += (
            f"<tr>"
            f"<td>{_h(d.get('ts',''))[:19]}</td>"
            f"<td>{_h(d.get('symbol','?'))}</td>"
            f"<td style='color:{color};font-weight:bold'>{v}</td>"
            f"<td>{_h(d.get('regime','?'))}</td>"
            f"<td>{_h(d.get('cf_score') or '--')}</td>"
            f"<td style='font-size:0.8em'>{_h((d.get('fail_reason') or d.get('summary',''))[:80])}</td>"
            f"</tr>"
        )

    regime_html = ""
    for r in regime_perf:
        total = r.get("total", 1)
        execs = r.get("executes", 0)
        pct = int(execs / total * 100) if total else 0
        regime_html += (
            f"<tr><td>{_h(r['regime'])}</td>"
            f"<td>{_h(total)}</td>"
            f"<td>{_h(execs)} ({pct}%)</td>"
            f"<td>{_h(r.get('avg_cf_score','?'))}</td></tr>"
        )

    reasons_html = "".join(
        f"<tr><td>{_h(r['reason'][:80])}</td><td>{_h(r['count'])}</td></tr>"
        for r in s.get("top_no_trade_reasons", [])[:5]
    ) or "<tr><td colspan=2>No data yet</td></tr>"

    backtest_html = ""
    try:
        import json as _json
        from pathlib import Path as _Path
        for f in sorted(_Path("storage").glob("backtest_*.json"))[:5]:
            try:
                d = _json.loads(f.read_text())
                m = d.get("metrics", {})
                pf = float(m.get("profit_factor", 0))
                wr = float(m.get("win_rate", 0))
                ret = float(m.get("total_return_pct", 0))
                color = "#3fb950" if pf > 1.5 else "#d29922"
                backtest_html += (
                    f"<tr><td>{_h(d.get('symbol','?'))}</td>"
                    f"<td>{_h(d.get('period','?'))[:30]}</td>"
                    f"<td>{_h(m.get('trades_closed','?'))}</td>"
                    f"<td>{wr:.1%}</td>"
                    f"<td style='color:{color}'>{pf:.2f}</td>"
                    f"<td>{ret:.1%}</td>"
                    f"<td>{_h(m.get('max_drawdown_pct','?'))}</td></tr>"
                )
            except Exception:
                continue
    except Exception:
        pass

    backtest_section = ""
    if backtest_html:
        backtest_section = (
            "<h2>Backtest Results</h2><table>"
            "<tr><th>Symbol</th><th>Period</th><th>Trades</th>"
            "<th>WR</th><th>PF</th><th>Return</th><th>Max DD</th></tr>"
            + backtest_html + "</table>"
        )

    st = (
        "body{font-family:monospace;background:#0d1117;color:#c9d1d9;margin:20px}"
        "h1{color:#58a6ff}h2{color:#79c0ff;border-bottom:1px solid #30363d;padding-bottom:4px}"
        ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}"
        ".card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 24px;min-width:140px}"
        ".card .val{font-size:2em;font-weight:bold;color:#58a6ff}"
        ".card .lbl{color:#8b949e;font-size:0.85em}"
        "table{width:100%;border-collapse:collapse;margin:8px 0}"
        "th{background:#161b22;color:#8b949e;text-align:left;padding:8px;font-size:0.85em}"
        "td{padding:8px;border-bottom:1px solid #21262d;font-size:0.9em}"
        "tr:hover td{background:#161b22}"
    )

    html_content = (
        "<!DOCTYPE html><html><head>"
        "<meta charset=\'utf-8\'>"
        "<meta name=\'viewport\' content=\'width=device-width,initial-scale=1\'>"
        "<title>IATIS Dashboard</title>"
        "<meta http-equiv=\'refresh\' content=\'60\'>"
        f"<style>{st}</style>"
        "</head><body>"
        f"<h1>IATIS Dashboard <span style=\'font-size:0.5em;color:#8b949e\'>v{version}</span></h1>"
        "<p style=\'color:#8b949e\'>Auto-refreshes every 60s</p>"
        "<h2>System Status</h2>"
        "<div class=\'cards\'>"
        f"<div class=\'card\'><div class=\'val\'>{_h(s.get('total',0))}</div><div class=\'lbl\'>Total</div></div>"
        f"<div class=\'card\'><div class=\'val\' style=\'color:#3fb950\'>{_h(s.get('execute',0))}</div><div class=\'lbl\'>EXECUTE</div></div>"
        f"<div class=\'card\'><div class=\'val\' style=\'color:#f85149\'>{_h(s.get('no_trade',0))}</div><div class=\'lbl\'>NO_TRADE</div></div>"
        f"<div class=\'card\'><div class=\'val\' style=\'color:{credit_color}\'>{_h(credits)}</div><div class=\'lbl\'>API Credits</div></div>"
        "</div>"
        + backtest_section
        + "<h2>Top NO_TRADE Reasons</h2><table><tr><th>Reason</th><th>Count</th></tr>"
        + reasons_html + "</table>"
        + "<h2>Regime Performance</h2><table><tr><th>Regime</th><th>Total</th><th>EXECUTE</th><th>Avg Score</th></tr>"
        + (regime_html or "<tr><td colspan=4>No data yet</td></tr>") + "</table>"
        + "<h2>Last 5 Decisions</h2><table>"
        + "<tr><th>Time</th><th>Symbol</th><th>Verdict</th><th>Regime</th><th>Score</th><th>Reason</th></tr>"
        + (decisions_html or "<tr><td colspan=6>No decisions yet</td></tr>") + "</table>"
        + "<p style=\'color:#8b949e;font-size:0.8em;margin-top:32px\'>"
        + "<a href=\'/budget\' style=\'color:#58a6ff\'>Budget</a> | "
        + "<a href=\'/stats\' style=\'color:#58a6ff\'>Stats</a> | "
        + "<a href=\'/engine-stats\' style=\'color:#58a6ff\'>Engine Stats</a> | "
        + "<a href=\'/backtest-results\' style=\'color:#58a6ff\'>Backtest Results</a>"
        + "</p></body></html>"
    )
    return HTMLResponse(html_content)

@app.get("/engine-stats")
async def engine_stats_endpoint(
    symbol: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Per-engine performance statistics and suggested weight adjustments."""
    _check_auth(x_api_key)
    try:
        from storage.engine_tracker import engine_stats, neutral_rate_by_engine, suggested_weights
        config = _get_config()
        current_weights = config.get("confluence", {}).get("weights", {})

        stats = engine_stats(min_votes=5, symbol=symbol)
        neutral = neutral_rate_by_engine()
        suggested = suggested_weights(current_weights)

        return {
            "engine_stats": stats,
            "neutral_rates": neutral,
            "current_weights": current_weights,
            "suggested_weights": suggested,
            "note": "Suggested weights need 20+ votes per engine to be reliable. Review before applying."
        }
    except Exception as exc:
        logger.error(f"Engine stats error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@app.get("/backtest-results")
async def backtest_results(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """List all saved backtest result files with key metrics."""
    _check_auth(x_api_key)
    import json
    from pathlib import Path
    results = []
    storage = Path("storage")
    for f in sorted(storage.glob("backtest_*.json")):
        try:
            data = json.loads(f.read_text())
            results.append({
                "file": f.name,
                "symbol": data.get("symbol"),
                "period": data.get("period"),
                "metrics": data.get("metrics", {}),
            })
        except Exception:
            continue
    return {"count": len(results), "results": results}
