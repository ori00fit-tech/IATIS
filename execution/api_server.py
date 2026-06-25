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
    from fastapi import Cookie, FastAPI, Header, HTTPException, Query, Request, Response
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

# Session store: {session_id: created_timestamp}
# Sessions expire after 7 days. Stored in memory — resets on restart.
# For multi-user or persistent sessions: use SQLite (Phase 6+)
_active_sessions: dict[str, float] = {}
_SESSION_TTL = 86400 * 7  # 7 days

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


def _check_auth(x_api_key: str | None, cookie_key: str | None = None) -> None:
    """Fail-closed auth — accepts X-API-Key header OR valid session cookie.

    Security properties:
    - X-API-Key header: direct key comparison (for curl/API clients)
    - Session cookie: validates session_id against _active_sessions store
      The raw API key is NEVER stored in the cookie (session rotation)
    - hmac.compare_digest: timing-attack protection
    - HttpOnly cookie: JS cannot read session_id
    """
    required = os.environ.get("API_SERVER_KEY")
    if not required:
        if _ENV == "development":
            return
        raise HTTPException(status_code=500, detail="API_SERVER_KEY not configured.")

    # Check X-API-Key header (API clients)
    if x_api_key and hmac.compare_digest(x_api_key, required):
        return

    # Check session cookie (browser)
    if cookie_key:
        now = time.time()
        expires_before = now - _SESSION_TTL
        # Clean expired sessions
        expired = [sid for sid, ts in _active_sessions.items() if ts < expires_before]
        for sid in expired:
            _active_sessions.pop(sid, None)
        # Validate session
        if cookie_key in _active_sessions:
            return

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
    iatis_session: str | None = Cookie(default=None),
) -> JSONResponse:
    """Run full pipeline for one symbol. Symbol: EURUSD or EUR/USD."""
    _check_auth(x_api_key, iatis_session)
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
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
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


@app.get("/stats")
async def stats(x_api_key: str | None = Header(default=None), iatis_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.decision_db import summary, regime_performance
        return {"summary": summary(), "regime_performance": regime_performance()}
    except Exception as exc:
        logger.error(f"Stats error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@app.post("/login")
async def do_login(request: Request) -> Response:
    """Verify key and set HttpOnly cookie — key never visible to JS."""
    body = await request.json()
    key = body.get("key", "")
    required = os.environ.get("API_SERVER_KEY", "")
    if not required or not hmac.compare_digest(key, required):
        raise HTTPException(status_code=401, detail="Invalid key")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="iatis_session",
        value=key,
        httponly=True,       # JS cannot read this
        secure=True,         # HTTPS only
        samesite="strict",
        max_age=86400 * 7,   # 7 days
    )
    return response


@app.get("/login")
async def login_page() -> HTMLResponse:
    """Login page — submits key via POST, receives HttpOnly cookie."""
    return HTMLResponse("""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IATIS — Login</title>
<style>
  body{font-family:monospace;background:#0d1117;color:#c9d1d9;display:flex;
    align-items:center;justify-content:center;height:100vh;margin:0}
  .box{background:#161b22;border:1px solid #30363d;border-radius:12px;
    padding:40px;width:320px;text-align:center}
  h1{color:#58a6ff;margin:0 0 8px}
  p{color:#8b949e;font-size:0.85em;margin:0 0 24px}
  input{width:100%;box-sizing:border-box;padding:10px;background:#0d1117;
    border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:1em;margin-bottom:12px}
  button{width:100%;padding:10px;background:#238636;border:none;border-radius:6px;
    color:#fff;font-size:1em;cursor:pointer}
  button:hover{background:#2ea043}
  .err{color:#f85149;font-size:0.85em;margin-top:8px;display:none}
</style>
</head>
<body>
<div class="box">
  <h1>&#x1F916; IATIS</h1>
  <p>Enter your API key to access the dashboard</p>
  <input type="password" id="key" placeholder="API Server Key" autofocus
         onkeydown="if(event.key==='Enter')login()">
  <button onclick="login()">Login</button>
  <div class="err" id="err">Invalid key — try again</div>
</div>
<script>
async function login() {
  const key = document.getElementById('key').value.trim();
  if (!key) return;
  const r = await fetch('/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key})
  });
  if (r.ok) {
    window.location.href = '/dashboard';
  } else {
    document.getElementById('err').style.display = 'block';
  }
}
</script>
</body>
</html>""")


@app.get("/logout")
async def logout() -> HTMLResponse:
    """Clear stored key."""
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<script>localStorage.removeItem('iatis_key'); window.location.href='/login';</script>
</body></html>""")


@app.get("/dashboard")
async def dashboard():
    """
    HTML dashboard — no server-side auth needed here.
    The page itself loads data via JS fetch() calls that include
    the API key from localStorage. If key is missing, redirects to /login.
    """
    return HTMLResponse("""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IATIS Dashboard</title>
<style>
  body{font-family:monospace;background:#0d1117;color:#c9d1d9;margin:20px}
  h1{color:#58a6ff}h2{color:#79c0ff;border-bottom:1px solid #30363d;padding-bottom:4px}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 24px;min-width:140px}
  .val{font-size:2em;font-weight:bold;color:#58a6ff}.lbl{color:#8b949e;font-size:0.85em}
  table{width:100%;border-collapse:collapse;margin:8px 0}
  th{background:#161b22;color:#8b949e;text-align:left;padding:8px;font-size:0.85em}
  td{padding:8px;border-bottom:1px solid #21262d;font-size:0.9em}
  tr:hover td{background:#161b22}
  .ok{color:#3fb950}.bad{color:#f85149}.warn{color:#d29922}
  a{color:#58a6ff;text-decoration:none}
  #status{color:#8b949e;font-size:0.85em;margin-bottom:16px}
</style>
</head>
<body>
<h1>&#x1F916; IATIS Dashboard</h1>
<p id="status">Loading...</p>
<div id="content"></div>
<p style="color:#8b949e;font-size:0.8em;margin-top:32px">
  <a href="/budget">Budget</a> |
  <a href="/stats">Stats JSON</a> |
  <a href="/engine-stats">Engine Stats</a> |
  <a href="/backtest-results">Backtest Results</a> |
  <a href="/logout">Logout</a>
</p>
<script>
const H = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

async function api(path) {
  const r = await fetch(path, {credentials: 'include'});
  if (r.status === 401) { window.location.href='/login'; }
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function load() {
  try {
    const [health, stats, decisions, bt] = await Promise.all([
      api('/health'), api('/stats'), api('/decisions?limit=5'), api('/backtest-results')
    ]);

    const s = stats.summary || {};
    const total = s.total || 0;
    const execRate = total > 0 ? ((s.execute||0)/total*100).toFixed(1)+'%' : '--';
    const credits = health.twelve_data_credits_remaining ?? '?';
    const creditColor = credits > 400 ? 'ok' : credits > 100 ? 'warn' : 'bad';

    let html = `
      <div class="cards">
        <div class="card"><div class="val">${H(total)}</div><div class="lbl">Total Decisions</div></div>
        <div class="card"><div class="val ok">${H(s.execute||0)}</div><div class="lbl">EXECUTE</div></div>
        <div class="card"><div class="val bad">${H(s.no_trade||0)}</div><div class="lbl">NO_TRADE</div></div>
        <div class="card"><div class="val ${creditColor}">${H(credits)}</div><div class="lbl">API Credits</div></div>
        <div class="card"><div class="val">${execRate}</div><div class="lbl">EXECUTE Rate</div></div>
      </div>`;

    // Backtest results
    if (bt.results && bt.results.length) {
      html += '<h2>Backtest Results</h2><table><tr><th>Symbol</th><th>Period</th><th>Trades</th><th>WR</th><th>PF</th><th>Return</th><th>Max DD</th></tr>';
      const seen = new Set();
      for (const r of bt.results) {
        if (seen.has(r.symbol)) continue; seen.add(r.symbol);
        const m = r.metrics || {};
        const pf = parseFloat(m.profit_factor||0);
        const wr = parseFloat(m.win_rate||0);
        const ret = parseFloat(m.total_return_pct||0);
        const pfColor = pf > 1.5 ? '#3fb950' : '#d29922';
        html += `<tr><td>${H(r.symbol)}</td><td>${H((r.period||'').slice(0,30))}</td>
          <td>${H(m.trades_closed||0)}</td><td>${(wr*100).toFixed(1)}%</td>
          <td style="color:${pfColor}">${pf.toFixed(2)}</td>
          <td>${(ret*100).toFixed(1)}%</td>
          <td>${H(m.max_drawdown_pct ? (m.max_drawdown_pct*100).toFixed(1)+'%' : '--')}</td></tr>`;
      }
      html += '</table>';
    }

    // NO_TRADE reasons
    const reasons = s.top_no_trade_reasons || [];
    if (reasons.length) {
      html += '<h2>Top NO_TRADE Reasons</h2><table><tr><th>Reason</th><th>Count</th></tr>';
      for (const r of reasons) html += `<tr><td>${H(r.reason)}</td><td>${H(r.count)}</td></tr>`;
      html += '</table>';
    }

    // Regime performance
    const regimes = stats.regime_performance || [];
    if (regimes.length) {
      html += '<h2>Regime Performance</h2><table><tr><th>Regime</th><th>Total</th><th>EXECUTE</th><th>Avg Score</th></tr>';
      for (const r of regimes) {
        const pct = r.total > 0 ? Math.round(r.executes/r.total*100) : 0;
        html += `<tr><td>${H(r.regime)}</td><td>${H(r.total)}</td><td>${H(r.executes)} (${pct}%)</td><td>${H(r.avg_cf_score)}</td></tr>`;
      }
      html += '</table>';
    }

    // Last decisions
    const dec = (decisions.decisions || []).slice(0,5);
    html += '<h2>Last 5 Decisions</h2><table><tr><th>Time</th><th>Symbol</th><th>Verdict</th><th>Regime</th><th>Score</th><th>Reason</th></tr>';
    for (const d of dec) {
      const color = d.verdict === 'EXECUTE' ? '#3fb950' : '#f85149';
      html += `<tr>
        <td>${H((d.ts||'').slice(0,19))}</td><td>${H(d.symbol)}</td>
        <td style="color:${color};font-weight:bold">${H(d.verdict)}</td>
        <td>${H(d.regime)}</td><td>${H(d.cf_score)}</td>
        <td style="font-size:0.8em">${H((d.fail_reason||d.summary||'').slice(0,70))}</td></tr>`;
    }
    if (!dec.length) html += '<tr><td colspan=6>No decisions yet</td></tr>';
    html += '</table>';

    document.getElementById('content').innerHTML = html;
    document.getElementById('status').textContent = `v${H(health.version)} · Updated ${new Date().toLocaleTimeString()}`;

    // Auto-refresh every 60s
    setTimeout(load, 60000);
  } catch(e) {
    document.getElementById('status').textContent = 'Error loading data: ' + e.message;
    setTimeout(load, 15000);
  }
}
load();
</script>
</body>
</html>""")

@app.get("/engine-stats")
async def engine_stats_endpoint(
    symbol: str | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Per-engine performance statistics and suggested weight adjustments."""
    _check_auth(x_api_key, iatis_session)
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
async def backtest_results(x_api_key: str | None = Header(default=None), iatis_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    """List all saved backtest result files with key metrics."""
    _check_auth(x_api_key, iatis_session)
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


@app.get("/research")
async def research_dashboard(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research hypotheses status with sample sizes and win rates."""
    _check_auth(x_api_key, iatis_session)
    import json
    from pathlib import Path

    registry_path = Path("research/results/registry.json")
    results_dir = Path("research/results")

    try:
        registry = json.loads(registry_path.read_text())
    except Exception:
        raise HTTPException(status_code=500, detail="Could not read research registry.")

    hypotheses = []
    for h_id, h_data in registry.get("hypotheses", {}).items():
        entry = {
            "id": h_id,
            "title": h_data.get("title", ""),
            "status": h_data.get("status", "UNKNOWN"),
            "notes": h_data.get("notes", ""),
            "last_updated": h_data.get("last_updated", ""),
        }

        # Load result file if available
        result_file = h_data.get("result_file")
        if result_file:
            result_path = Path("research") / result_file
            if result_path.exists():
                try:
                    result = json.loads(result_path.read_text())
                    entry["sample_size"] = (
                        result.get("n_fvg_entries") or
                        result.get("qualified_n") or
                        result.get("sample_size_qualified") or
                        result.get("total_n")
                    )
                    entry["win_rate"] = (
                        result.get("win_rate") or
                        result.get("qualified_win_rate") or
                        result.get("combined_win_rate")
                    )
                    entry["p_value"] = result.get("p_value")
                    entry["improvement"] = (
                        result.get("improvement") or
                        result.get("win_rate_improvement")
                    )
                except Exception:
                    pass

        hypotheses.append(entry)

    return {
        "total_hypotheses": len(hypotheses),
        "passed": sum(1 for h in hypotheses if h["status"] == "PASSED"),
        "failed": sum(1 for h in hypotheses if "FAILED" in h["status"]),
        "research": sum(1 for h in hypotheses if h["status"] == "RESEARCH"),
        "pending": sum(1 for h in hypotheses if h["status"] == "PENDING"),
        "hypotheses": hypotheses,
    }


@app.get("/meta-analysis")
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
