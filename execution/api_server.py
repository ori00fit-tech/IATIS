"""
execution/api_server.py
---------------------------
Phase 2: FastAPI HTTP server for the IATIS pipeline.

Endpoints:
    GET  /health            — liveness check, returns version + credits remaining
    POST /analyze/{symbol}  — runs full pipeline for one symbol, returns report
    GET  /decisions         — last N decisions from the No-Trade Database
    GET  /budget            — Twelve Data API credits status

Run:
    uvicorn execution.api_server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
load_dotenv()

try:
    from fastapi import FastAPI, Header, HTTPException, Query
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
except ImportError as exc:
    raise ImportError("Run: pip install fastapi uvicorn") from exc

from utils.helpers import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="IATIS API",
    description="Institutional Adaptive Trading Intelligence System",
    version="0.2.0",
    docs_url="/docs",
    redoc_url=None,
)

_executor = ThreadPoolExecutor(max_workers=4)
_config_cache: dict | None = None


def _get_config() -> dict:
    global _config_cache
    if _config_cache is None:
        _config_cache = load_config()
        api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
        if api_key:
            _config_cache["data"]["twelve_data_api_key"] = api_key
    return _config_cache


def _check_auth(x_api_key: str | None) -> None:
    required = os.environ.get("API_SERVER_KEY", "")
    if required and x_api_key != required:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


class AnalyzeRequest(BaseModel):
    source: str = "twelve_data"
    bars: int = 500
    timeframes: list[str] = ["M15", "H1", "H4", "D1"]


@app.get("/health")
async def health() -> dict[str, Any]:
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
    _check_auth(x_api_key)

    td_symbol = symbol if "/" in symbol else (
        f"{symbol[:3]}/{symbol[3:]}" if len(symbol) == 6 else symbol
    )
    clean_symbol = td_symbol.replace("/", "")

    config = dict(_get_config())
    config["data"] = dict(config["data"])
    config["data"]["source"] = req.source
    config["data"]["symbol"] = clean_symbol
    config["data"]["twelve_data_symbol"] = td_symbol
    config["data"]["bars_to_load"] = req.bars
    config["data"]["timeframes"] = req.timeframes
    config["telegram"] = {"enabled": False}

    loop = asyncio.get_event_loop()
    try:
        from main import run_pipeline
        start = time.monotonic()
        report = await loop.run_in_executor(_executor, run_pipeline, config)
        report["processing_time_sec"] = round(time.monotonic() - start, 3)
        return JSONResponse(content=report)
    except Exception as exc:
        logger.error(f"Pipeline error for {symbol}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/decisions")
async def decisions(
    limit: int = Query(default=20, ge=1, le=200),
    verdict: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key)
    from storage.decision_log import read_decisions, summarize_decisions
    all_d = read_decisions()
    if verdict:
        all_d = [d for d in all_d if d.get("final_verdict") == verdict.upper()]
    recent = list(reversed(all_d[-limit:]))
    return {
        "total_in_log": len(all_d),
        "returned": len(recent),
        "summary": summarize_decisions(),
        "decisions": recent,
    }


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
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/stats")
async def stats(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Decision analytics from SQLite — regime performance, engine breakdown,
    top NO_TRADE reasons."""
    _check_auth(x_api_key)
    try:
        from storage.decision_db import summary, regime_performance
        return {
            "summary": summary(),
            "regime_performance": regime_performance(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/dashboard", response_class=None)
async def dashboard(x_api_key: str | None = Header(default=None)):
    """Simple HTML dashboard — system health at a glance."""
    from fastapi.responses import HTMLResponse
    from storage.decision_db import summary, regime_performance, recent

    try:
        s = summary()
        regime_perf = regime_performance()
        last_decisions = recent(limit=5)
    except Exception as exc:
        return HTMLResponse(f"<pre>DB error: {exc}</pre>", status_code=500)

    config = _get_config()
    version = config.get("system", {}).get("version", "?")

    try:
        from core.twelve_data_client import RateLimiter, MAX_REQUESTS_PER_DAY
        credits = RateLimiter().remaining_today()
        credit_pct = int(credits / MAX_REQUESTS_PER_DAY * 100)
        credit_color = "green" if credit_pct > 50 else "orange" if credit_pct > 20 else "red"
    except Exception:
        credits, credit_pct, credit_color = "?", 0, "gray"

    decisions_html = ""
    for d in last_decisions:
        v = d.get("verdict", "?")
        color = "#00cc44" if v == "EXECUTE" else "#ff4444"
        decisions_html += f"""
        <tr>
            <td>{d.get('ts','')[:19]}</td>
            <td>{d.get('symbol','?')}</td>
            <td style="color:{color};font-weight:bold">{v}</td>
            <td>{d.get('regime','?')}</td>
            <td>{d.get('cf_score') or '—'}</td>
            <td style="font-size:0.8em;max-width:300px">{(d.get('fail_reason') or d.get('summary',''))[:80]}</td>
        </tr>"""

    regime_html = ""
    for r in regime_perf:
        total = r.get('total', 1)
        execs = r.get('executes', 0)
        pct = int(execs / total * 100) if total else 0
        regime_html += f"<tr><td>{r['regime']}</td><td>{total}</td><td>{execs} ({pct}%)</td><td>{r.get('avg_cf_score','?')}</td></tr>"

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IATIS Dashboard</title>
<meta http-equiv="refresh" content="60">
<style>
  body{{font-family:monospace;background:#0d1117;color:#c9d1d9;margin:20px;}}
  h1{{color:#58a6ff;}} h2{{color:#79c0ff;border-bottom:1px solid #30363d;padding-bottom:4px;}}
  .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0;}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 24px;min-width:140px;}}
  .card .val{{font-size:2em;font-weight:bold;color:#58a6ff;}}
  .card .lbl{{color:#8b949e;font-size:0.85em;}}
  table{{width:100%;border-collapse:collapse;margin:8px 0;}}
  th{{background:#161b22;color:#8b949e;text-align:left;padding:8px;font-size:0.85em;}}
  td{{padding:8px;border-bottom:1px solid #21262d;font-size:0.9em;}}
  tr:hover td{{background:#161b22;}}
  .ok{{color:#3fb950;}} .warn{{color:#d29922;}} .badge{{
    display:inline-block;padding:2px 8px;border-radius:12px;font-size:0.8em;
    background:#21262d;margin:2px;}}
</style>
</head>
<body>
<h1>🤖 IATIS Dashboard <span style="font-size:0.5em;color:#8b949e">v{version}</span></h1>
<p style="color:#8b949e">Auto-refreshes every 60s</p>

<h2>System Status</h2>
<div class="cards">
  <div class="card"><div class="val">{s.get('total',0)}</div><div class="lbl">Total Decisions</div></div>
  <div class="card"><div class="val" style="color:#3fb950">{s.get('execute',0)}</div><div class="lbl">EXECUTE</div></div>
  <div class="card"><div class="val" style="color:#f85149">{s.get('no_trade',0)}</div><div class="lbl">NO_TRADE</div></div>
  <div class="card"><div class="val" style="color:{credit_color}">{credits}</div><div class="lbl">API Credits Left</div></div>
</div>

<h2>Top NO_TRADE Reasons</h2>
<table>
<tr><th>Reason</th><th>Count</th></tr>
{"".join(f"<tr><td>{r['reason'][:80]}</td><td>{r['count']}</td></tr>" for r in s.get('top_no_trade_reasons',[])[:5]) or "<tr><td colspan=2>No data yet</td></tr>"}
</table>

<h2>Regime Performance</h2>
<table>
<tr><th>Regime</th><th>Total</th><th>EXECUTE</th><th>Avg Score</th></tr>
{regime_html or "<tr><td colspan=4>No data yet</td></tr>"}
</table>

<h2>Last 5 Decisions</h2>
<table>
<tr><th>Time (UTC)</th><th>Symbol</th><th>Verdict</th><th>Regime</th><th>Score</th><th>Reason</th></tr>
{decisions_html or "<tr><td colspan=6>No decisions yet</td></tr>"}
</table>

<p style="color:#8b949e;font-size:0.8em;margin-top:32px">
  IATIS v{version} | <a href="/docs" style="color:#58a6ff">API Docs</a> |
  <a href="/budget" style="color:#58a6ff">Budget</a> |
  <a href="/stats" style="color:#58a6ff">Stats JSON</a>
</p>
</body>
</html>"""
    return HTMLResponse(html)
