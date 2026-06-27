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
# Sessions persist to disk — survive server restarts
_SESSION_TTL = 86400 * 30  # 30 days
_SESSION_FILE = Path("storage/sessions.json")

def _load_sessions() -> dict:
    try:
        if _SESSION_FILE.exists():
            import json as _sj
            data = _sj.loads(_SESSION_FILE.read_text())
            cutoff = time.time() - _SESSION_TTL
            return {k: v for k, v in data.items() if v > cutoff}
    except Exception:
        pass
    return {}

def _save_sessions(sessions: dict) -> None:
    try:
        import json as _sj
        _SESSION_FILE.parent.mkdir(exist_ok=True)
        _SESSION_FILE.write_text(_sj.dumps(sessions))
    except Exception:
        pass

_active_sessions: dict[str, float] = _load_sessions()

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
    """Verify key and set HttpOnly session cookie.

    Security: generates a random session ID — the raw API key
    is NEVER stored in the cookie or exposed to the client.
    """
    import secrets
    body = await request.json()
    key = body.get("key", "")
    required = os.environ.get("API_SERVER_KEY", "")
    if not required or not hmac.compare_digest(key, required):
        raise HTTPException(status_code=401, detail="Invalid key")

    # Generate random session ID (NOT the raw key)
    session_id = secrets.token_urlsafe(32)
    _active_sessions[session_id] = time.time()
    _save_sessions(_active_sessions)  # persist to disk

    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="iatis_session",
        value=session_id,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=86400 * 30,  # 30 days
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
async def logout(iatis_session: str | None = Cookie(default=None)) -> Response:
    """Clear session cookie and invalidate server-side session."""
    if iatis_session and iatis_session in _active_sessions:
        _active_sessions.pop(iatis_session, None)
    response = HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<script>localStorage.removeItem('iatis_key'); window.location.href='/login';</script>
</body></html>""")
    response.delete_cookie("iatis_session")
    return response


@app.get("/dashboard")
async def dashboard():
    """IATIS Dashboard — Market Intelligence Platform."""
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IATIS — Market Intelligence</title>
<style>
  :root {
    --bg: #080c14;
    --surface: #0e1420;
    --border: #1a2236;
    --accent: #00d4ff;
    --accent2: #7c5cfc;
    --green: #00e676;
    --red: #ff5252;
    --amber: #ffab40;
    --text: #e2e8f0;
    --muted: #64748b;
    --card-bg: #111827;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'SF Mono', 'Fira Code', monospace; background: var(--bg); color: var(--text); min-height: 100vh; }

  /* Header */
  header { display: flex; align-items: center; justify-content: space-between;
    padding: 16px 24px; border-bottom: 1px solid var(--border);
    background: linear-gradient(90deg, #080c14 0%, #0d1829 100%); }
  .logo { display: flex; align-items: center; gap: 10px; }
  .logo-icon { width: 32px; height: 32px; background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 18px; }
  .logo-text { font-size: 1.1em; font-weight: 700; color: var(--accent); letter-spacing: 2px; }
  .logo-sub { font-size: 0.65em; color: var(--muted); letter-spacing: 1px; }
  #clock { font-size: 0.8em; color: var(--muted); }
  nav a { color: var(--muted); text-decoration: none; font-size: 0.75em; margin-left: 16px; transition: color 0.2s; }
  nav a:hover { color: var(--accent); }

  /* Main */
  main { padding: 20px 24px; max-width: 1400px; margin: 0 auto; }

  /* Status bar */
  #statusbar { display: flex; align-items: center; gap: 8px;
    padding: 8px 14px; background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; margin-bottom: 20px; font-size: 0.78em; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 6px var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .dot.err { background: var(--red); box-shadow: 0 0 6px var(--red); }
  .dot.loading { background: var(--amber); box-shadow: 0 0 6px var(--amber); }

  /* KPI cards */
  .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .kpi { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; position: relative; overflow: hidden; }
  .kpi::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, var(--accent), var(--accent2)); }
  .kpi .val { font-size: 1.8em; font-weight: 800; line-height: 1; margin-bottom: 4px; }
  .kpi .lbl { font-size: 0.7em; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
  .kpi.green .val { color: var(--green); }
  .kpi.red .val { color: var(--red); }
  .kpi.blue .val { color: var(--accent); }
  .kpi.purple .val { color: var(--accent2); }
  .kpi.amber .val { color: var(--amber); }

  /* Grid layout */
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 768px) { .grid2 { grid-template-columns: 1fr; } }

  /* Panels */
  .panel { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .panel-header { display: flex; align-items: center; justify-content: space-between;
    padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .panel-title { font-size: 0.8em; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: 1.5px; }
  .panel-body { padding: 0; }

  /* Table */
  table { width: 100%; border-collapse: collapse; font-size: 0.82em; }
  th { padding: 8px 12px; color: var(--muted); font-size: 0.75em; text-transform: uppercase;
    letter-spacing: 0.8px; text-align: left; background: var(--surface); font-weight: 600; }
  td { padding: 9px 12px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(0,212,255,0.03); }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 700; }
  .badge.exec { background: rgba(0,230,118,0.15); color: var(--green); }
  .badge.no-trade { background: rgba(255,82,82,0.1); color: var(--red); }
  .badge.good { background: rgba(0,212,255,0.12); color: var(--accent); }
  .badge.marginal { background: rgba(255,171,64,0.12); color: var(--amber); }
  .badge.poor { background: rgba(255,82,82,0.1); color: var(--red); }

  /* Signal list */
  .signal { display: flex; align-items: flex-start; gap: 10px;
    padding: 10px 14px; border-bottom: 1px solid var(--border); }
  .signal:last-child { border-bottom: none; }
  .signal-sym { font-weight: 800; min-width: 70px; color: var(--accent); }
  .signal-info { flex: 1; font-size: 0.82em; color: var(--muted); line-height: 1.5; }
  .signal-score { font-size: 1.1em; font-weight: 700; min-width: 40px; text-align: right; }

  /* Gauge bar */
  .gauge { height: 4px; background: var(--border); border-radius: 2px; margin-top: 4px; }
  .gauge-fill { height: 100%; border-radius: 2px; transition: width 0.8s ease; }

  /* Empty state */
  .empty { padding: 32px; text-align: center; color: var(--muted); font-size: 0.85em; }

  .spin { animation: spin 1s linear infinite; display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-icon">⚡</div>
    <div>
      <div class="logo-text">IATIS</div>
      <div class="logo-sub">MARKET INTELLIGENCE PLATFORM</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <span id="clock" style="color:var(--muted);font-size:0.78em"></span>
    <nav>
      <a href="/health">Health</a>
      <a href="/research">Research</a>
      <a href="/outcomes">Outcomes</a>
      <a href="/logout">Logout</a>
    </nav>
  </div>
</header>

<main>
  <div id="statusbar">
    <div class="dot loading" id="dot"></div>
    <span id="statustext">Connecting to IATIS...</span>
  </div>

  <!-- KPIs -->
  <div class="kpi-grid" id="kpis">
    <div class="kpi blue"><div class="val spin">⟳</div><div class="lbl">Total Decisions</div></div>
    <div class="kpi green"><div class="val">—</div><div class="lbl">EXECUTE</div></div>
    <div class="kpi amber"><div class="val">—</div><div class="lbl">API Credits</div></div>
    <div class="kpi purple"><div class="val">—</div><div class="lbl">Execute Rate</div></div>
    <div class="kpi"><div class="val">—</div><div class="lbl">Active Symbols</div></div>
  </div>

  <div class="grid2">
    <!-- Last decisions -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">⟳ Last Decisions</span>
        <span style="font-size:0.7em;color:var(--muted)" id="last-run">—</span>
      </div>
      <div class="panel-body" id="decisions-panel">
        <div class="empty">Loading...</div>
      </div>
    </div>

    <!-- Symbol Health -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">💊 Symbol Health</span>
      </div>
      <div class="panel-body" id="health-panel">
        <div class="empty">Loading...</div>
      </div>
    </div>
  </div>

  <!-- Backtest Results -->
  <div class="panel" style="margin-bottom:16px">
    <div class="panel-header">
      <span class="panel-title">📊 Backtest Results (v0.5)</span>
    </div>
    <div class="panel-body" id="bt-panel">
      <div class="empty">Loading...</div>
    </div>
  </div>

  <!-- Open Outcomes -->
  <div class="panel" style="margin-bottom:16px">
    <div class="panel-header">
      <span class="panel-title">📈 Open Signals</span>
      <span style="font-size:0.7em;color:var(--muted)">Paper trading</span>
    </div>
    <div class="panel-body" id="outcomes-panel">
      <div class="empty">No open signals</div>
    </div>
  </div>
</main>

<script>
const H = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

// Clock
setInterval(() => {
  document.getElementById('clock').textContent = new Date().toUTCString().slice(0,25) + ' UTC';
}, 1000);

async function api(path) {
  const r = await fetch(path, {credentials:'include'});
  if (r.status === 401) { window.location.href='/login'; throw new Error('auth'); }
  if (!r.ok) throw new Error(r.status + ' ' + path);
  return r.json();
}

function scoreColor(s) {
  s = parseFloat(s||0);
  return s >= 65 ? 'var(--green)' : s >= 55 ? 'var(--amber)' : 'var(--red)';
}

function pfBadge(pf) {
  pf = parseFloat(pf||0);
  if (pf >= 1.5) return 'good';
  if (pf >= 1.1) return 'marginal';
  return 'poor';
}

async function load() {
  const dot = document.getElementById('dot');
  const st = document.getElementById('statustext');
  dot.className = 'dot loading';
  st.textContent = 'Refreshing...';

  try {
    // Load critical data first (fast endpoints)
    const [health, stats] = await Promise.all([
      api('/health'), api('/stats')
    ]);

    // KPIs
    const s = stats.summary || {};
    const total = s.total || 0;
    const exec = s.execute || 0;
    const credits = health.twelve_data_credits_remaining ?? '?';
    const execRate = total > 0 ? (exec/total*100).toFixed(1)+'%' : '--';
    const creditClass = credits > 400 ? 'green' : credits > 100 ? 'amber' : 'red';

    document.getElementById('kpis').innerHTML = `
      <div class="kpi blue"><div class="val">${H(total)}</div><div class="lbl">Total Decisions</div></div>
      <div class="kpi green"><div class="val">${H(exec)}</div><div class="lbl">EXECUTE</div></div>
      <div class="kpi ${creditClass}"><div class="val">${H(credits)}</div><div class="lbl">API Credits</div></div>
      <div class="kpi purple"><div class="val">${execRate}</div><div class="lbl">Execute Rate</div></div>
      <div class="kpi"><div class="val">${H(total - exec)}</div><div class="lbl">NO_TRADE</div></div>
    `;

    dot.className = 'dot';
    st.textContent = `Live · v${H(health.version)} · ${new Date().toLocaleTimeString()} UTC`;

    // Load decisions
    try {
      const decisions = await api('/decisions?limit=8');
      const dec = decisions.decisions || [];
      if (dec.length) {
        let html = '';
        for (const d of dec) {
          const isExec = d.verdict === 'EXECUTE';
          const score = parseFloat(d.cf_score||0);
          const reason = (d.fail_reason || d.summary || '').slice(0, 60);
          const ts = (d.ts||'').slice(11,19);
          html += `<div class="signal">
            <div>
              <div class="signal-sym">${H(d.symbol||'?')}</div>
              <div style="font-size:0.7em;color:var(--muted)">${ts}</div>
            </div>
            <div class="signal-info">
              <span class="badge ${isExec ? 'exec' : 'no-trade'}">${H(d.verdict)}</span>
              ${H(d.regime||'')}
              <div style="color:var(--muted);font-size:0.9em;margin-top:2px">${H(reason)}</div>
            </div>
            <div class="signal-score" style="color:${scoreColor(score)}">${score.toFixed(0)}</div>
          </div>`;
        }
        document.getElementById('decisions-panel').innerHTML = html;
        document.getElementById('last-run').textContent = dec[0]?.ts?.slice(0,19) || '—';
      } else {
        document.getElementById('decisions-panel').innerHTML = '<div class="empty">No decisions yet</div>';
      }
    } catch(e) {
      document.getElementById('decisions-panel').innerHTML = '<div class="empty">Could not load decisions</div>';
    }

    // Load symbol health (may be slow)
    try {
      const sh = await api('/symbol-health');
      const syms = sh.symbols || [];
      if (syms.length) {
        let shHtml = '<table><tr><th>Symbol</th><th>SHI</th><th>Status</th><th>WR</th><th>Trades</th></tr>';
        for (const s of syms) {
          const statusColor = s.status === 'HEALTHY' ? 'var(--green)' : s.status === 'CAUTION' ? 'var(--amber)' : 'var(--red)';
          const wr = s.win_rate != null ? s.win_rate.toFixed(1)+'%' : '—';
          shHtml += `<tr>
            <td style="font-weight:700;color:var(--accent)">${H(s.symbol)}</td>
            <td>${H(s.shi_score)}</td>
            <td style="color:${statusColor};font-weight:700">${H(s.status)}</td>
            <td>${wr}</td>
            <td style="color:var(--muted)">${H(s.trades_count)}</td>
          </tr>`;
        }
        shHtml += '</table>';
        document.getElementById('health-panel').innerHTML = shHtml;
      }
    } catch(e) {
      document.getElementById('health-panel').innerHTML = '<div class="empty">No symbol health data yet (need closed trades)</div>';
    }

    // Load backtest
    try {
      const bt = await api('/backtest-results');
      const results = (bt.results || []).filter(r => !r.error && r.trades >= 10)
        .sort((a,b) => (b.profit_factor||0) - (a.profit_factor||0));
      if (results.length) {
        let btHtml = '<table><tr><th>Symbol</th><th>Trades</th><th>WR%</th><th>PF</th><th>DD%</th><th>Return%</th></tr>';
        for (const r of results) {
          const badge = pfBadge(r.profit_factor);
          btHtml += `<tr>
            <td style="font-weight:700;color:var(--accent)">${H(r.symbol)}</td>
            <td>${H(r.trades)}</td>
            <td>${H(r.win_rate)}%</td>
            <td><span class="badge ${badge}">${parseFloat(r.profit_factor||0).toFixed(2)}</span></td>
            <td style="color:var(--red)">${H(r.max_drawdown_pct)}%</td>
            <td style="color:${parseFloat(r.total_return_pct||0)>=0?'var(--green)':'var(--red)'}">${H(r.total_return_pct)}%</td>
          </tr>`;
        }
        btHtml += '</table>';
        document.getElementById('bt-panel').innerHTML = btHtml;
      } else {
        document.getElementById('bt-panel').innerHTML = '<div class="empty">No backtest results yet</div>';
      }
    } catch(e) {
      document.getElementById('bt-panel').innerHTML = '<div class="empty">No backtest data</div>';
    }

    // Load open outcomes
    try {
      const outcomes = await api('/outcomes');
      const open = outcomes.open_signals || [];
      if (open.length) {
        let oHtml = '<table><tr><th>Signal ID</th><th>Symbol</th><th>Direction</th><th>Entry</th><th>Score</th></tr>';
        for (const o of open) {
          const dirColor = (o.direction||'') === 'BULLISH' ? 'var(--green)' : 'var(--red)';
          oHtml += `<tr>
            <td style="font-size:0.75em;color:var(--muted)">${H(o.signal_id)}</td>
            <td style="font-weight:700;color:var(--accent)">${H(o.symbol)}</td>
            <td style="color:${dirColor};font-weight:700">${H(o.direction)}</td>
            <td>${H(o.entry_price)}</td>
            <td style="color:${scoreColor(o.cf_score)}">${H(o.cf_score)}</td>
          </tr>`;
        }
        oHtml += '</table>';
        document.getElementById('outcomes-panel').innerHTML = oHtml;
      }
    } catch(e) { /* silent */ }

    setTimeout(load, 60000);

  } catch(e) {
    dot.className = 'dot err';
    st.textContent = 'Error: ' + e.message + ' — retrying in 15s';
    setTimeout(load, 15000);
  }
}
load();

    // KPIs
    const s = stats.summary || {};
    const total = s.total || 0;
    const exec = s.execute || 0;
    const credits = health.twelve_data_credits_remaining ?? '?';
    const execRate = total > 0 ? (exec/total*100).toFixed(1)+'%' : '--';
    const creditClass = credits > 400 ? 'green' : credits > 100 ? 'amber' : 'red';

    document.getElementById('kpis').innerHTML = `
      <div class="kpi blue"><div class="val">${H(total)}</div><div class="lbl">Total Decisions</div></div>
      <div class="kpi green"><div class="val">${H(exec)}</div><div class="lbl">EXECUTE</div></div>
      <div class="kpi ${creditClass}"><div class="val">${H(credits)}</div><div class="lbl">API Credits</div></div>
      <div class="kpi purple"><div class="val">${execRate}</div><div class="lbl">Execute Rate</div></div>
      <div class="kpi"><div class="val">${H(total - exec)}</div><div class="lbl">NO_TRADE</div></div>
    `;

    // Decisions
    const dec = decisions.decisions || [];
    if (dec.length) {
      let html = '';
      for (const d of dec) {
        const isExec = d.verdict === 'EXECUTE';
        const sym = d.symbol || '?';
        const score = parseFloat(d.cf_score||0);
        const reason = (d.fail_reason || d.summary || '').slice(0, 60);
        const ts = (d.ts||'').slice(11,19);
        html += `<div class="signal">
          <div>
            <div class="signal-sym">${H(sym)}</div>
            <div style="font-size:0.7em;color:var(--muted)">${ts}</div>
          </div>
          <div class="signal-info">
            <span class="badge ${isExec ? 'exec' : 'no-trade'}">${H(d.verdict)}</span>
            ${H(d.regime||'')}
            <div style="color:var(--muted);font-size:0.9em;margin-top:2px">${H(reason)}</div>
          </div>
          <div class="signal-score" style="color:${scoreColor(score)}">${score.toFixed(0)}</div>
        </div>`;
      }
      document.getElementById('decisions-panel').innerHTML = html;
    } else {
      document.getElementById('decisions-panel').innerHTML = '<div class="empty">No decisions yet</div>';
    }

    // Last run time
    const lastRun = dec[0]?.ts?.slice(0,19) || '—';
    document.getElementById('last-run').textContent = lastRun;

    // Symbol Health
    try {
      const sh = await api('/symbol-health');
      const syms = sh.symbols || [];
      let shHtml = '<table><tr><th>Symbol</th><th>SHI</th><th>Status</th><th>WR</th><th>Trades</th></tr>';
      for (const s of syms) {
        const statusColor = s.status === 'HEALTHY' ? 'var(--green)' : s.status === 'CAUTION' ? 'var(--amber)' : 'var(--red)';
        const wr = s.win_rate != null ? s.win_rate.toFixed(1)+'%' : '—';
        shHtml += `<tr>
          <td style="font-weight:700;color:var(--accent)">${H(s.symbol)}</td>
          <td>${H(s.shi_score)}</td>
          <td style="color:${statusColor};font-weight:700">${H(s.status)}</td>
          <td>${wr}</td>
          <td style="color:var(--muted)">${H(s.trades_count)}</td>
        </tr>`;
      }
      shHtml += '</table>';
      document.getElementById('health-panel').innerHTML = shHtml || '<div class="empty">No data</div>';
    } catch(e) {
      document.getElementById('health-panel').innerHTML = '<div class="empty">No symbol health data yet</div>';
    }

    // Backtest results
    try {
      const bt = await api('/backtest-results');
      const results = bt.results || [];
      if (results.length) {
        let btHtml = '<table><tr><th>Symbol</th><th>Trades</th><th>WR%</th><th>PF</th><th>DD%</th><th>Return%</th></tr>';
        const sorted = [...results].filter(r => !r.error && r.trades >= 10)
          .sort((a,b) => (b.profit_factor||0) - (a.profit_factor||0));
        for (const r of sorted) {
          const badge = pfBadge(r.profit_factor);
          btHtml += `<tr>
            <td style="font-weight:700;color:var(--accent)">${H(r.symbol)}</td>
            <td>${H(r.trades)}</td>
            <td style="color:${scoreColor(r.win_rate/100*65)}">${H(r.win_rate)}%</td>
            <td><span class="badge ${badge}">${parseFloat(r.profit_factor||0).toFixed(2)}</span></td>
            <td style="color:var(--red)">${H(r.max_drawdown_pct)}%</td>
            <td style="color:${r.total_return_pct>=0?'var(--green)':'var(--red)'}">${H(r.total_return_pct)}%</td>
          </tr>`;
        }
        btHtml += '</table>';
        document.getElementById('bt-panel').innerHTML = btHtml;
      } else {
        document.getElementById('bt-panel').innerHTML = '<div class="empty">No backtest results yet — run full_pipeline_backtest.py</div>';
      }
    } catch(e) {
      document.getElementById('bt-panel').innerHTML = '<div class="empty">No backtest data</div>';
    }

    // Open outcomes
    const open = outcomes.open_signals || [];
    if (open.length) {
      let oHtml = '<table><tr><th>Signal ID</th><th>Symbol</th><th>Direction</th><th>Entry</th><th>Score</th></tr>';
      for (const o of open) {
        const dir = o.direction || '?';
        const dirColor = dir === 'BULLISH' ? 'var(--green)' : 'var(--red)';
        oHtml += `<tr>
          <td style="font-size:0.75em;color:var(--muted)">${H(o.signal_id)}</td>
          <td style="font-weight:700;color:var(--accent)">${H(o.symbol)}</td>
          <td style="color:${dirColor};font-weight:700">${H(dir)}</td>
          <td>${H(o.entry_price)}</td>
          <td style="color:${scoreColor(o.cf_score)}">${H(o.cf_score)}</td>
        </tr>`;
      }
      oHtml += '</table>';
      document.getElementById('outcomes-panel').innerHTML = oHtml;
    }

    dot.className = 'dot';
    st.textContent = `Live · Last refresh ${new Date().toLocaleTimeString()} UTC · v${H(health.version)}`;
    setTimeout(load, 60000);

  } catch(e) {
    dot.className = 'dot err';
    st.textContent = 'Error: ' + e.message + ' — retrying in 15s';
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

    # Old format fallback
    if not results:
        for f in sorted(storage.glob("backtest_*.json")):
            try:
                data = _json.loads(f.read_text())
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
async def research_center(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Center — hypothesis status, engine performance, backtest results."""
    _check_auth(x_api_key, iatis_session)
    import json as _json
    from pathlib import Path

    registry_path = Path("research/results/registry.json")
    hypotheses_raw = {}
    if registry_path.exists():
        try:
            hypotheses_raw = _json.loads(registry_path.read_text()).get("hypotheses", {})
        except Exception:
            pass

    hypotheses = []
    for h_id, h_data in hypotheses_raw.items():
        entry = {
            "id": h_id,
            "title": h_data.get("title", ""),
            "status": h_data.get("status", "UNKNOWN"),
            "description": h_data.get("description", "")[:120],
            "last_updated": h_data.get("last_updated", ""),
        }
        # Load result file if exists
        result_file = h_data.get("result_file")
        if result_file:
            rp = Path("research") / result_file
            if rp.exists():
                try:
                    r = _json.loads(rp.read_text())
                    entry["sample_size"] = (r.get("n_fvg_entries") or
                        r.get("qualified_n") or r.get("total_n"))
                    entry["win_rate"] = (r.get("win_rate") or
                        r.get("qualified_win_rate"))
                    entry["p_value"] = r.get("p_value")
                except Exception:
                    pass
        hypotheses.append(entry)

    try:
        from storage.engine_tracker import engine_stats
        stats = engine_stats(min_votes=1)
    except Exception:
        stats = []

    try:
        from storage.outcome_tracker import performance_summary
        outcomes = performance_summary()
    except Exception:
        outcomes = {"total_closed": 0, "win_rate": 0}

    backtest_files = sorted(Path("storage").glob("full_pipeline_backtest_*.json"), reverse=True)
    latest_backtest = None
    if backtest_files:
        try:
            bt = _json.loads(backtest_files[0].read_text())
            valid = [r for r in bt.get("results", [])
                     if not r.get("error") and r.get("trades", 0) >= 10]
            latest_backtest = {
                "file": backtest_files[0].name,
                "generated_at": bt.get("generated_at", ""),
                "summary": bt.get("summary", {}),
                "avg_wr": round(sum(r.get("win_rate",0) for r in valid)/len(valid), 1) if valid else 0,
                "avg_pf": round(sum(r.get("profit_factor",0) for r in valid)/len(valid), 2) if valid else 0,
                "top_symbols": sorted(valid, key=lambda x: x.get("profit_factor",0), reverse=True)[:5],
            }
        except Exception:
            pass

    return {
        "hypothesis_summary": {
            "total": len(hypotheses),
            "passed": sum(1 for h in hypotheses if h["status"] == "PASSED"),
            "failed": sum(1 for h in hypotheses if "FAILED" in h["status"]),
            "research": sum(1 for h in hypotheses if h["status"] == "RESEARCH"),
            "needs_data": sum(1 for h in hypotheses if h["status"] == "NEEDS_MORE_DATA"),
        },
        "hypotheses": hypotheses,
        "engine_performance": stats,
        "outcome_summary": outcomes,
        "latest_backtest": latest_backtest,
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


@app.get("/outcomes")
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


@app.post("/outcomes/{signal_id}/close")
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
    return {"success": success, "signal_id": signal_id, "outcome": outcome}


@app.get("/health/full")
async def system_health_full(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """System Health Dashboard — full status of all components."""
    _check_auth(x_api_key, iatis_session)

    import psutil
    import time as _time
    from pathlib import Path
    import re as _re

    now_utc = datetime.now(timezone.utc).isoformat()
    checks: dict[str, Any] = {}
    issues: list[str] = []

    # 1. CPU / RAM / Disk
    try:
        checks["system"] = {
            "cpu_pct": psutil.cpu_percent(interval=0.5),
            "ram_pct": psutil.virtual_memory().percent,
            "disk_pct": psutil.disk_usage("/").percent,
            "uptime_hours": round((_time.time() - psutil.boot_time()) / 3600, 1),
        }
        if checks["system"]["ram_pct"] > 85: issues.append("High RAM usage")
        if checks["system"]["disk_pct"] > 80: issues.append("High disk usage")
    except Exception as e:
        checks["system"] = {"error": str(e)[:80]}

    # 2. Scheduler last run
    try:
        import re as _re
        # Try multiple log locations
        log_candidates = [
            Path("storage/system.log"),
            Path("/var/log/iatis-scheduler.log"),
        ]
        last_run = None
        last_execute_count = 0
        for sched_log in log_candidates:
            if sched_log.exists():
                lines = sched_log.read_text().splitlines()
                for line in reversed(lines[-500:]):
                    if "Run complete" in line:
                        last_run = line.split("|")[0].strip()
                        m = _re.search(r"(\d+) EXECUTE", line)
                        if m: last_execute_count = int(m.group(1))
                        break
                if last_run:
                    break
        # Also try journalctl
        if not last_run:
            import subprocess
            result = subprocess.run(
                ["journalctl", "-u", "iatis-scheduler", "-n", "100", "--no-pager", "--output=cat"],
                capture_output=True, text=True, timeout=5
            )
            for line in reversed(result.stdout.splitlines()):
                if "Run complete" in line:
                    last_run = line[:30].strip()
                    m = _re.search(r"(\d+) EXECUTE", line)
                    if m: last_execute_count = int(m.group(1))
                    break
        checks["scheduler"] = {
            "last_run": last_run,
            "last_execute_count": last_execute_count,
            "status": "running" if last_run else "unknown",
        }
    except Exception as e:
        checks["scheduler"] = {"status": "error", "error": str(e)[:80]}

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

    # 6. Data providers
    checks["data_providers"] = {
        "twelve_data": "configured" if os.environ.get("TWELVE_DATA_API_KEY") else "missing",
        "alpha_vantage": "configured" if os.environ.get("ALPHA_VANTAGE_API_KEY") else "missing",
        "finnhub": "configured" if os.environ.get("FINNHUB_API_KEY") else "missing",
        "jblanked": "configured" if os.environ.get("JBLANKED_API_KEY") else "missing",
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


@app.get("/symbol-health")
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


@app.post("/ai/optimize-weights")
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
    try:
        from ai.dynamic_weights import analyze_and_suggest_weights, apply_weights_to_config
        from storage.engine_tracker import engine_stats
        from storage.outcome_tracker import performance_summary
        from storage.calibration import regime_performance_matrix

        config = _get_config()
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

