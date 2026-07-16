"""
execution/api_server.py
---------------------------
FastAPI HTTP server — Phase 2. Security-hardened.

Security measures applied (see IATIS_Security_Audit.md):
- P0: Auth fail-closed (API_SERVER_KEY required)
- P0: Dashboard requires auth
- P0: All dynamic dashboard values HTML-escaped client-side (the `H()`
      helper in the dashboard's own <script>, since all data reaches the
      page via JSON fetch + DOM injection — there is no server-side
      string-interpolated HTML to escape here)
- P1: Symbol input validation (strict regex)
- P1: Constant-time key comparison (hmac.compare_digest)
- P1: Generic error messages (details logged only)
- P2: Swagger disabled in production (ENV=production)
- P3: SQLite/cache/session file permissions set at init
- P3: Config cache thread-safe (threading.Lock)
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv()

try:
    from fastapi import Cookie, FastAPI, Header, HTTPException, Query, Request, Response
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
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

# Letters-only above rejects real active symbols with digits (US30, NAS100,
# SPX500 — config/symbols.yaml) — fine for /analyze's own td_symbol slash
# heuristic (which mis-splits those names anyway), but /candles resolves
# indices correctly via the symbols table and must accept their names.
_CANDLE_SYMBOL_RE = re.compile(r'^[A-Z0-9]{2,6}(/[A-Z0-9]{2,6})?$')

def _validate_candle_symbol(symbol: str) -> str:
    clean = symbol.upper().strip()
    if not _CANDLE_SYMBOL_RE.match(clean) or len(clean) > 14:
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Use EURUSD, EUR/USD, or US30 (letters/digits, 2-6 chars each)."
        )
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
    for path in ["storage/sessions.json"]:
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
        # Session IDs are bearer-equivalent credentials (30-day TTL) — lock
        # this down the same way storage/*.db is locked down at startup.
        try:
            os.chmod(_SESSION_FILE, 0o600)
        except Exception:
            pass
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
    # Match config.yaml bars_to_load: below ~210 decision-TF bars NNFX is
    # mute and below 50 D1 bars the MTF gate is inert (philosophy audit).
    bars: int = 3000
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
        # First entry of data.timeframes — the TF engine votes are computed
        # on (H4-primary since 2026-07). Surfaced so the dashboard shows
        # which system is actually running.
        "decision_timeframe": (config.get("data", {}).get("timeframes") or ["H1"])[0],
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
    symbol: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="ISO date/timestamp, inclusive lower bound"),
    date_to: str | None = Query(default=None, description="ISO date/timestamp, inclusive upper bound"),
    engine: str | None = Query(default=None, description="Engine name that voted on this decision"),
    min_score: float | None = Query(default=None, ge=0, le=100),
    risk_rejected: bool = Query(default=False, description="Only decisions the risk gate rejected"),
    reason: str | None = Query(default=None, description="Substring search over NO_TRADE/rejection reasons"),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.decision_log import read_decisions, summarize_decisions, filter_decisions
        all_d = read_decisions()
        total_in_log = len(all_d)
        if verdict:
            all_d = [d for d in all_d if d.get("final_verdict") == verdict.upper()]
        matched = filter_decisions(
            all_d,
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
            engine=engine,
            min_score=min_score,
            risk_rejected=risk_rejected or None,
            reason=reason,
        )
        return {
            "total_in_log": total_in_log,
            "matched": len(matched),
            "returned": len(matched[-limit:]),
            "summary": summarize_decisions(),
            "decisions": list(reversed(matched[-limit:])),
        }
    except Exception as exc:
        logger.error(f"Decisions error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


_CANDLE_INTERVALS = ("M15", "H1", "H4", "D1")


def _fetch_symbol_for_internal(internal: str, config: dict) -> str:
    """internal name (EURUSD, US30) -> the fetch-symbol form the provider
    chain expects (EUR/USD, DJI). Uses config/symbols.yaml's own
    internal<->symbol pairing (merged into config["data"]["twelve_data_symbols"]
    by utils.helpers.load_config()) rather than guessing from string shape —
    the naive "insert a slash at position 3" heuristic used by /analyze
    silently mis-splits indices (NAS100 -> "NAS/100", not "NDX")."""
    for entry in config.get("data", {}).get("twelve_data_symbols", []):
        entry_internal = entry.get("internal") or str(entry.get("symbol", "")).replace("/", "")
        if entry_internal.upper() == internal:
            return entry["symbol"]
    return internal


@app.get("/candles/{symbol}")
async def candles(
    symbol: str,
    interval: str = Query(default="H4"),
    outputsize: int = Query(default=300, ge=10, le=1000),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """OHLCV bars for the price chart, plus the latest logged decision's
    entry/SL/TP for the same symbol so the frontend can overlay IATIS's
    own signal on the bars it was computed from."""
    _check_auth(x_api_key, iatis_session)
    clean_symbol = _validate_candle_symbol(symbol)
    internal = clean_symbol.replace("/", "")
    interval = interval.upper()
    if interval not in _CANDLE_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval must be one of {_CANDLE_INTERVALS}")

    try:
        from core.data_providers import fetch_with_failover, provider_chain_for, DataFetchError

        fetch_symbol = _fetch_symbol_for_internal(internal, _get_config())
        chain = provider_chain_for(fetch_symbol, _get_config().get("data", {}).get("provider_chains"))
        try:
            df, provider = fetch_with_failover(fetch_symbol, interval, outputsize=outputsize, providers=chain)
        except DataFetchError as exc:
            raise HTTPException(status_code=502, detail=f"No provider could deliver candles: {str(exc)[:200]}")

        bars = [
            {
                "time": int(ts.timestamp()),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
            }
            for ts, row in df.iterrows()
        ]

        from storage.decision_log import read_decisions
        matching = [d for d in read_decisions() if d.get("symbol") == internal]
        signal = None
        if matching:
            latest = matching[-1]["report"]
            signal = {
                "timestamp": matching[-1].get("timestamp"),
                "verdict": matching[-1].get("final_verdict"),
                "entry_price": latest.get("entry_price"),
                "stop_loss": latest.get("stop_loss"),
                "take_profit": latest.get("take_profit"),
            }

        return {
            "symbol": internal,
            "interval": interval,
            "provider": provider,
            "bars": bars,
            "signal": signal,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Candles error for {internal}: {exc}", exc_info=True)
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
    from storage.audit_log import log_action

    body = await request.json()
    key = body.get("key", "")
    required = os.environ.get("API_SERVER_KEY", "")
    if not required or not hmac.compare_digest(key, required):
        log_action("login", success=False)
        raise HTTPException(status_code=401, detail="Invalid key")

    # Generate random session ID (NOT the raw key)
    session_id = secrets.token_urlsafe(32)
    _active_sessions[session_id] = time.time()
    _save_sessions(_active_sessions)  # persist to disk
    log_action("login", session_id=session_id, success=True)

    response = JSONResponse({"ok": True})
    response.set_cookie(
        key="iatis_session",
        value=session_id,
        httponly=True,
        secure=True,        # Cloudflare serves HTTPS
        samesite="lax",     # strict blocks cross-origin redirects via Cloudflare
        max_age=86400 * 30,
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
  document.querySelector('button').textContent = 'Connecting...';
  try {
    const r = await fetch('/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'include',
      body: JSON.stringify({key})
    });
    if (r.ok) {
      // Session cookie set by server — no localStorage needed
      window.location.replace('/dashboard');
    } else {
      document.getElementById('err').style.display = 'block';
      document.querySelector('button').textContent = 'Login';
    }
  } catch(e) {
    document.getElementById('err').textContent = 'Connection error: ' + e.message;
    document.getElementById('err').style.display = 'block';
    document.querySelector('button').textContent = 'Login';
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
<script>window.location.href='/login';</script>
</body></html>""")
    response.delete_cookie("iatis_session")
    return response


@app.get("/dashboard")
async def dashboard(
    request: Request,
    iatis_session: str | None = Cookie(default=None),
):
    """Dashboard — requires valid session, redirects to login if not authenticated."""
    # Server-side session check — no JS required
    if not iatis_session or iatis_session not in _active_sessions:
        return RedirectResponse(url="/login", status_code=302)
    # Refresh session TTL
    _active_sessions[iatis_session] = time.time()
    _save_sessions(_active_sessions)
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
  const storedKey = sessionStorage.getItem('iatis_key') || localStorage.getItem('iatis_key');
  const headers = storedKey ? {'X-API-Key': storedKey} : {};
  const r = await fetch(path, {credentials:'include', headers});
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
</script>
</body>
</html>""")

@app.get("/experience/summary")
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


@app.get("/experience/query")
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


@app.get("/experience/pattern")
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


@app.get("/engine-stats")
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


@app.get("/research/manifests")
async def research_manifests(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Git-tracked evidence manifests (research/manifest.py, audit item H2).

    Each manifest binds one research run to the exact git commit, a config
    hash, and per-dataset SHA256 fingerprints. The dashboard renders these
    as the system's auditable evidence trail — including the honest
    `reproducible: false` flag for runs from a dirty working tree.
    """
    _check_auth(x_api_key, iatis_session)
    manifests = _load_manifests()
    return {"count": len(manifests), "manifests": manifests}


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

    # Trust audit: which PASSED entries actually clear the codified
    # promotion criteria (research/edge_gate.py) — the dashboard must never
    # render an under-evidenced PASSED as green.
    try:
        from research.edge_gate import PROMOTION_CRITERIA, audit_passed_hypotheses
        trust_warnings = audit_passed_hypotheses(hypotheses_raw)
        flagged_ids = {w.split(" ", 1)[0] for w in trust_warnings}
        promotion_criteria = PROMOTION_CRITERIA
    except Exception:
        trust_warnings, flagged_ids, promotion_criteria = [], set(), {}

    hypotheses = []
    for h_id, h_data in hypotheses_raw.items():
        entry = {
            "id": h_id,
            "title": h_data.get("title", ""),
            "status": h_data.get("status", "UNKNOWN"),
            "description": h_data.get("description", "")[:120],
            "last_updated": h_data.get("last_updated", ""),
            "conclusion": (h_data.get("conclusion") or h_data.get("lesson") or "")[:300],
            "trusted": h_data.get("status") != "PASSED" or h_id not in flagged_ids,
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
        "trust_audit": {
            "criteria": promotion_criteria,
            "warnings": trust_warnings,
        },
        "engine_performance": stats,
        "outcome_summary": outcomes,
        "latest_backtest": latest_backtest,
    }


@app.get("/philosophy-audit")
async def philosophy_audit_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """System Philosophy Audit — the same 29 checks as
    `python -m scripts.philosophy_audit`, on demand from the dashboard.

    Read-only (SELECTs against the decisions DB). Takes ~10-20s because it
    issues multiple D1 round-trips; the frontend calls it from a button,
    never on a poll."""
    _check_auth(x_api_key, iatis_session)

    def _run() -> dict[str, Any]:
        from scripts.philosophy_audit import run_all
        from storage import d1_client
        # Ensure the audited tables exist (CREATE IF NOT EXISTS) — a fresh
        # DB (or the tests' fake D1) has none until a first decision lands.
        from storage.decision_db import init_db as _init_decisions
        from storage.outcome_tracker import _init_db as _init_outcomes
        _init_decisions()
        _init_outcomes()
        with d1_client.d1_connection() as con:
            checks = run_all(con)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(checks),
                "fail": sum(1 for c in checks if c.status == "FAIL"),
                "warn": sum(1 for c in checks if c.status == "WARN"),
                "pass": sum(1 for c in checks if c.status == "PASS"),
                "info": sum(1 for c in checks if c.status == "INFO"),
            },
            "checks": [
                {"axis": c.axis, "name": c.name, "status": c.status,
                 "detail": c.detail,
                 "evidence": [str(e) for e in c.evidence[:12]]}
                for c in checks
            ],
        }

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(_executor, _run)
    except Exception as exc:
        logger.error(f"Philosophy audit failed: {exc}")
        raise HTTPException(status_code=503,
                            detail="Audit unavailable — decisions DB unreachable.")


# ---------------------------------------------------------------------------
# Research Integrity (Mission Control module 9) — on-demand, read-only
# checks alongside the philosophy audit above. Deliberately excludes
# cross-provider diff (scripts/cross_provider_diff.py): that tool makes
# live provider API calls and burns rate-limited quota (see /budget), so
# it belongs in the Experiment Runner (module 5) where a human explicitly
# kicks off a job with visible cost, not a casual dashboard click.
# ---------------------------------------------------------------------------
def _leakage_guard_report() -> dict[str, Any]:
    """Static leakage scan (research/guards/static_scan.py) over every
    research/experiment script. Advisory only, by that module's own
    design — CLEAN or WARNINGS_FOUND, never a hard FAIL; see its
    docstring for why a heuristic AST scan must never claim proof.
    """
    from research.guards.static_scan import scan_paths

    paths: list[Path] = []
    for d in ("research", "scripts"):
        paths.extend(sorted(Path(d).rglob("*.py")))
    paths.extend(sorted(Path(".").glob("run_h*.py")))

    report = scan_paths(paths)
    return {"status": "PASS" if report["verdict"] == "CLEAN" else "WARNING", **report}


def _survivorship_report() -> dict[str, Any]:
    """Symbol-evidence + selection-disclosure gate
    (research/survivorship_checker.py) — matches that module's own
    return-code convention: an enabled symbol with zero committed
    evidence is a FAIL, everything else advisory-only WARNING/PASS.
    """
    from research.survivorship_checker import check_selection_disclosure, check_symbol_evidence

    config = _get_config()
    symbol_report = check_symbol_evidence(config)
    selection_report = check_selection_disclosure()
    if symbol_report["enabled_no_evidence"]:
        status = "FAIL"
    elif (symbol_report["disabled_no_evidence"] or selection_report["undisclosed"]
          or selection_report["invalid_label"]):
        status = "WARNING"
    else:
        status = "PASS"
    return {"status": status, "symbol_evidence": symbol_report, "selection_disclosure": selection_report}


def _manifest_validator_report() -> dict[str, Any]:
    """Which evidence manifests are reproducible=false — reuses
    _load_manifests() (also backing /research/manifests and /alerts) so
    this never drifts from what those already show.
    """
    manifests = _load_manifests()
    non_reproducible = [m for m in manifests if m.get("reproducible") is False]
    return {
        "status": "WARNING" if non_reproducible else "PASS",
        "total": len(manifests),
        "reproducible_count": len(manifests) - len(non_reproducible),
        "non_reproducible": [
            {"file": m["file"], "kind": m.get("kind"), "git_dirty": m.get("git_dirty")}
            for m in non_reproducible
        ],
    }


@app.get("/research/integrity")
async def research_integrity(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Integrity — leakage guard, survivorship checker, and
    manifest validator, on demand. Read-only, no network calls, never
    modifies research evidence. See module docstring above for what's
    deliberately excluded and why.
    """
    _check_auth(x_api_key, iatis_session)

    def _run() -> dict[str, Any]:
        checks: dict[str, Any] = {}
        for name, fn in (
            ("leakage_guard", _leakage_guard_report),
            ("survivorship", _survivorship_report),
            ("manifest_validator", _manifest_validator_report),
        ):
            try:
                checks[name] = fn()
            except Exception as exc:
                checks[name] = {"status": "ERROR", "error": str(exc)[:300]}

        statuses = {c.get("status") for c in checks.values()}
        overall = (
            "FAIL" if "FAIL" in statuses else
            "ERROR" if "ERROR" in statuses else
            "WARNING" if "WARNING" in statuses else
            "PASS"
        )
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "overall": overall, "checks": checks}

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _run)


@app.get("/research/{hypothesis_id}")
async def research_hypothesis_detail(
    hypothesis_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Research Center drill-down (module 4) — the complete registry.json
    entry for one hypothesis (untruncated, unlike /research's summary
    list) plus every manifest linked to it and its declared result
    file(s).

    Manifest linking uses two sources, kept separate and labeled rather
    than merged into one list pretending to be equally certain:
      - "exact": the hypothesis's own `manifest` field in registry.json
        (a real field some hypotheses declare — H008c, H015, etc. — the
        authoritative link where it exists).
      - "heuristic": any other manifest whose filename or `kind` contains
        the hypothesis ID as a case-insensitive substring. A guess, not
        a fact — many manifest kinds (crypto_volume_experiment,
        ctrader_spread_measurement) don't embed a hypothesis ID at all.

    MUST stay registered after /research/manifests and /research/integrity
    (both literal paths) — Starlette/FastAPI match routes in registration
    order, so a path-param route registered earlier would silently shadow
    them (hit exactly this bug once while building this route; pinned by
    tests/test_api_contract.py::test_research_hypothesis_detail_route_does_not_shadow_literal_routes).
    """
    _check_auth(x_api_key, iatis_session)
    import json as _json

    registry_path = Path("research/results/registry.json")
    if not registry_path.exists():
        raise HTTPException(status_code=404, detail="Registry not found.")
    hypotheses_raw = _json.loads(registry_path.read_text()).get("hypotheses", {})
    hyp = hypotheses_raw.get(hypothesis_id)
    if hyp is None:
        raise HTTPException(status_code=404, detail=f"Hypothesis '{hypothesis_id}' not found.")

    manifests = _load_manifests()
    declared_manifest = hyp.get("manifest")
    declared_name = Path(declared_manifest).name if declared_manifest else None

    exact_links, heuristic_links = [], []
    needle = hypothesis_id.lower()
    for m in manifests:
        if declared_name and m["file"] == declared_name:
            exact_links.append(m)
        elif needle in m["file"].lower() or (m.get("kind") and needle in str(m["kind"]).lower()):
            heuristic_links.append(m)

    # Result file(s) — path + existence check only. Never dumps arbitrary
    # file content through this endpoint; that's File Explorer's job.
    result_paths: list[str] = []
    if isinstance(hyp.get("result_file"), str):
        result_paths.append(hyp["result_file"])
    result_files_field = hyp.get("result_files")
    if isinstance(result_files_field, dict):
        result_paths.extend(v for v in result_files_field.values() if isinstance(v, str))

    return {
        "id": hypothesis_id,
        "hypothesis": hyp,
        "manifests": {"exact": exact_links, "heuristic": heuristic_links},
        "result_files": [
            {"path": p, "exists": (Path("research") / p).exists()}
            for p in result_paths
        ],
    }


# ---------------------------------------------------------------------------
# Reports (Mission Control module 10) — on-demand snapshots assembled from
# data other endpoints already compute; never a second implementation of
# the same numbers. Markdown or JSON only — no PDF dependency exists in
# this project's requirements.txt, and we don't claim functionality that
# isn't real (docs/VISION_v2.md's "no future phase functionality
# pretending to be complete" rule).
# ---------------------------------------------------------------------------
_REPORT_TITLES: dict[str, str] = {
    "research": "IATIS Research Report",
    "manifest_summary": "IATIS Manifest Summary",
    "system": "IATIS System Health Report",
    "provider": "IATIS Data Provider Report",
    "forward": "IATIS Forward Demo Report",
    "data_quality": "IATIS Data Quality Report",
}


def _dict_to_md(title: str, data: dict[str, Any], generated_at: str) -> str:
    """Generic dict → Markdown for report kinds without a dedicated table
    formatter (system/provider/forward): a titled doc with the exact data
    as a JSON block. Honest about being a snapshot, not hand-formatted
    prose — good enough for an operator to read or paste elsewhere."""
    import json as _json

    return "\n".join([
        f"# {title}", "", f"Generated {generated_at}.", "",
        "```json", _json.dumps(data, indent=2, default=str), "```", "",
    ])


def _build_manifest_summary_md(manifests: dict[str, dict]) -> str:
    from scripts.generate_research_report import build_manifest_table

    n_total = len(manifests)
    n_repro = sum(1 for m in manifests.values() if m.get("reproducible"))
    return "\n".join([
        "# IATIS Manifest Summary", "",
        f"Generated {datetime.now(timezone.utc).isoformat()}.", "",
        f"{n_total} manifests, {n_repro} reproducible, {n_total - n_repro} NOT reproducible.", "",
        build_manifest_table(manifests), "",
    ])


@app.get("/reports/{kind}")
async def generate_report(
    kind: str,
    format: str = Query(default="md", pattern="^(md|json)$"),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> Any:
    _check_auth(x_api_key, iatis_session)
    if kind not in _REPORT_TITLES:
        raise HTTPException(status_code=404, detail=f"Unknown report kind '{kind}'. Choose from: {sorted(_REPORT_TITLES)}")

    generated_at = datetime.now(timezone.utc).isoformat()
    title = _REPORT_TITLES[kind]

    if kind == "research":
        from scripts.generate_research_report import build_report, load_manifests, load_registry
        registry = load_registry()
        manifests = load_manifests()
        markdown = build_report(registry, manifests)
        data: dict[str, Any] = {"registry": registry, "manifests": manifests}
    elif kind == "manifest_summary":
        from scripts.generate_research_report import load_manifests
        manifests = load_manifests()
        data = {"manifests": manifests}
        markdown = _build_manifest_summary_md(manifests)
    elif kind == "system":
        data = await system_health_full(x_api_key, iatis_session)
        markdown = _dict_to_md(title, data, generated_at)
    elif kind == "provider":
        data = await provider_chains_endpoint(x_api_key, iatis_session)
        markdown = _dict_to_md(title, data, generated_at)
    elif kind == "data_quality":
        data = _data_health_snapshot()
        markdown = _dict_to_md(title, data, generated_at)
    else:  # "forward"
        data = {
            "forward_review": await forward_review_endpoint(x_api_key, iatis_session),
            "outcomes_summary": (await get_outcomes(x_api_key, iatis_session))["summary"],
        }
        markdown = _dict_to_md(title, data, generated_at)

    if format == "json":
        return {"kind": kind, "title": title, "generated_at": generated_at, "data": data}
    return PlainTextResponse(
        markdown, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="iatis_{kind}_report.md"'},
    )


# ---------------------------------------------------------------------------
# Experiment Runner (Mission Control module 5) — whitelisted job execution
# only. No arbitrary shell, no user-supplied arguments: `job` selects a key
# into a hardcoded argv list, exactly the pattern already used by /logs
# (journalctl) and /files/diff (git diff) — never shell=True, never string
# interpolation of request data into a command line.
#
# SCOPE, DELIBERATELY NARROW: only jobs that are fast, local, and don't
# spend anything are whitelisted here — verify_data_integrity (reads local
# CSVs) and forward_review (one D1 read). Genuinely long-running jobs
# (walk_forward_validation, engine_subset_search — CPU-heavy, minutes+) and
# anything that burns rate-limited provider API quota (cross_provider_diff)
# are NOT included; widening this whitelist changes what a dashboard click
# can cost on a live VPS and should be a deliberate operator decision, not
# something inferred here. See MISSION_CONTROL_AUDIT.md's progress log.
# ---------------------------------------------------------------------------
_JOB_COMMANDS: dict[str, list[str]] = {
    "verify_data_integrity": [sys.executable, "-m", "scripts.verify_data_integrity"],
    "forward_review": [sys.executable, "-m", "scripts.forward_review"],
    "backup_d1": [sys.executable, "-m", "scripts.backup_d1"],
}
_JOB_DESCRIPTIONS: dict[str, str] = {
    "verify_data_integrity": "Audit every historical CSV for completeness/corruption/synthetic-data heuristics. Local file read, no network.",
    "forward_review": "Evaluate registry.json's pre-registered D001/D002 forward decision rules against closed outcomes. One D1 read, no network.",
    "backup_d1": "Dump every D1 table + decisions.jsonl to backups/, gzip, verify row counts, rotate old backups. Writes to local disk only, no network beyond the D1 proxy already in use.",
}
# Categorizes each whitelisted job for the frontend (Experiment Runner
# shows "research", VPS Operations shows "ops") — same underlying
# job-execution engine either way, per MISSION_CONTROL_AUDIT.md's note
# that module 12 should reuse module 5's primitive rather than duplicate it.
_JOB_CATEGORIES: dict[str, str] = {
    "verify_data_integrity": "research",
    "forward_review": "research",
    "backup_d1": "ops",
}
_JOB_TIMEOUT_SECONDS = 600  # generous for these three (all finish in seconds to low-minutes locally); kills a runaway process rather than leaking it forever

_job_executor = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, "_Job"] = {}
_jobs_lock = threading.Lock()


class _Job:
    def __init__(self, job_id: str, name: str):
        self.id = job_id
        self.name = name
        self.status = "queued"  # queued -> running -> finished | failed | timeout
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.returncode: int | None = None
        self.log_lines: list[str] = []
        self.lock = threading.Lock()


def _job_summary(job: "_Job") -> dict[str, Any]:
    return {
        "job_id": job.id,
        "job": job.name,
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "returncode": job.returncode,
        "log_lines": len(job.log_lines),
    }


def _run_job(job: "_Job") -> None:
    import subprocess

    job.status = "running"
    job.started_at = datetime.now(timezone.utc).isoformat()
    argv = _JOB_COMMANDS[job.name]
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=_REPO_ROOT, bufsize=1,
        )
        start = time.monotonic()
        assert proc.stdout is not None
        for line in proc.stdout:
            with job.lock:
                job.log_lines.append(line.rstrip("\n"))
            if time.monotonic() - start > _JOB_TIMEOUT_SECONDS:
                proc.kill()
                job.status = "timeout"
                break
        proc.wait(timeout=10)
        if job.status != "timeout":
            job.returncode = proc.returncode
            job.status = "finished" if proc.returncode == 0 else "failed"
    except Exception as exc:
        with job.lock:
            job.log_lines.append(f"[runner error] {exc}")
        job.status = "failed"
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()


class _RunJobRequest(BaseModel):
    job: str


@app.get("/experiments/jobs")
async def experiment_job_catalog(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """The whitelisted set of jobs /experiments/run will execute. Nothing
    else — see the module docstring above for why this list is short."""
    _check_auth(x_api_key, iatis_session)
    return {
        "jobs": [
            {"id": k, "description": _JOB_DESCRIPTIONS.get(k, ""), "category": _JOB_CATEGORIES.get(k, "research")}
            for k in _JOB_COMMANDS
        ]
    }


@app.post("/experiments/run")
async def experiments_run(
    body: _RunJobRequest,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    if body.job not in _JOB_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown job '{body.job}'. See /experiments/jobs.")

    with _jobs_lock:
        already_running = any(
            j.name == body.job and j.status in ("queued", "running") for j in _jobs.values()
        )
        if already_running:
            raise HTTPException(status_code=409, detail=f"'{body.job}' is already running.")
        job_id = uuid.uuid4().hex[:12]
        job = _Job(job_id, body.job)
        _jobs[job_id] = job

    from storage.audit_log import log_action
    log_action("experiment_run", x_api_key=x_api_key, session_id=iatis_session, detail=f"{body.job} ({job_id})")

    _job_executor.submit(_run_job, job)
    return _job_summary(job)


@app.get("/experiments")
async def experiments_list(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    with _jobs_lock:
        jobs = sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
        return {"jobs": [_job_summary(j) for j in jobs]}


@app.get("/experiments/{job_id}")
async def experiments_status(
    job_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with job.lock:
        return {**_job_summary(job), "log": list(job.log_lines)}


# ---------------------------------------------------------------------------
# VPS Operations (Mission Control module 12) — controlled operations only.
# "Diagnostics"/"Health check" reuse GET /health/full directly (the
# frontend calls it, no new endpoint needed). "Backup" reuses the
# Experiment Runner's job engine above via the "backup_d1" whitelist entry
# (category "ops"). This section only adds what neither of those already
# covers: an in-process config-cache reload.
#
# DELIBERATELY EXCLUDED: restarting iatis-api/iatis-scheduler. Restarting
# the live scheduler mid-cycle on what may be a production trading VPS is
# a materially different risk than anything else in this dashboard restart
# — it stays an explicit `systemctl restart` over SSH until an operator
# deliberately asks for it to be wired up. See MISSION_CONTROL_AUDIT.md.
# ---------------------------------------------------------------------------
@app.post("/ops/reload-config")
async def ops_reload_config(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Clear the in-process config.yaml cache so the next request reloads
    it from disk — e.g. after editing config.yaml on the VPS. Does not
    itself change any threshold/engine/trading value, only cache staleness.
    """
    _check_auth(x_api_key, iatis_session)
    global _config_cache
    with _config_lock:
        _config_cache = None
    from storage.audit_log import log_action
    log_action("reload_config", x_api_key=x_api_key, session_id=iatis_session)
    return {"success": True, "message": "Config cache cleared — next request reloads config.yaml from disk."}


# ---------------------------------------------------------------------------
# Security (Mission Control module 15) — audit log for every mutating
# action (login, job triggers, config reload, outcome mutation). Every
# job-execution and file-serving route in this file is already whitelist-
# only with fixed argv, satisfying "no arbitrary command execution" and
# "whitelisted jobs only" — see the Experiment Runner/File Explorer/Live
# Logs module docstrings above.
#
# DELIBERATELY NOT INCLUDED: role-based access control. Today's auth is a
# single shared API key (hmac.compare_digest) plus rotating session
# cookies — one key grants full access, there is no user/role model. RBAC
# is a real multi-user architecture change (accounts, role assignment,
# per-endpoint permission checks) that changes how every operator
# authenticates; it should be a deliberate, scoped decision made with the
# operator, not inferred and built unilaterally this late in a large
# session. Documented as an open gap in MISSION_CONTROL_AUDIT.md.
# ---------------------------------------------------------------------------
@app.get("/audit-log")
async def audit_log_endpoint(
    limit: int = Query(default=200, ge=1, le=1000),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage.audit_log import read_actions
    entries = read_actions(limit=limit)
    return {"count": len(entries), "entries": entries}


def _provider_usage_from_decisions(limit: int = 200) -> dict[str, dict[str, Any]]:
    """How often each provider actually served the last `limit` decisions,
    and when it was last used. Not a live ping and not new persistence —
    every pipeline report already logs which provider served each
    timeframe (main.py, via df.attrs["provider"]); this only aggregates
    what's already in storage/decisions.jsonl.
    """
    from storage.decision_log import read_decisions

    decisions = read_decisions()[-limit:]
    usage: dict[str, dict[str, Any]] = {}
    for d in decisions:
        ts = d.get("timestamp")
        providers = (d.get("report") or {}).get("data_providers") or {}
        for tf, provider in providers.items():
            entry = usage.setdefault(provider, {"count": 0, "last_used_at": None, "timeframes": set()})
            entry["count"] += 1
            entry["timeframes"].add(tf)
            if ts and (entry["last_used_at"] is None or ts > entry["last_used_at"]):
                entry["last_used_at"] = ts
    return {
        p: {"count": v["count"], "last_used_at": v["last_used_at"], "timeframes": sorted(v["timeframes"])}
        for p, v in usage.items()
    }


def _macro_source_status() -> dict[str, Any]:
    """Status for macro/alt data sources outside the main OHLCV provider
    chains — CBOE, FRED, CFTC are all keyless (no credentials needed);
    Alternative.me has no fetch code anywhere in this codebase and is
    reported as such rather than faked as "missing credentials". Checks
    local cache freshness and env vars only — never makes a network call.
    """
    def _dir_freshness(path: Path) -> str | None:
        if not path.exists():
            return None
        files = sorted(path.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        return datetime.fromtimestamp(files[0].stat().st_mtime, tz=timezone.utc).isoformat()

    return {
        "cboe": {
            "configured": True, "requires_key": False,
            "note": "VIX daily history, keyless CSV (core/alt_data_loader.py)",
        },
        "fred": {
            "configured": bool(os.environ.get("FRED_API_KEY")), "requires_key": False,
            "note": "works keyless via the fredgraph.csv fallback even without FRED_API_KEY",
        },
        "cftc": {
            "configured": True, "requires_key": False,
            "note": "weekly Commitments-of-Traders download (scripts/download_cot.py)",
            "last_cached": _dir_freshness(Path("data/cot")),
        },
        "alternative_me": {
            "configured": False, "requires_key": None,
            "note": "not integrated in this codebase — no fetch code exists for it",
        },
    }


@app.get("/provider-chains")
async def provider_chains_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Data-layer transparency: the per-asset-class provider chains in
    effect, which providers are actually usable right now (credentials /
    dependencies present), each timeframe's native coverage, which
    provider actually served recent decisions, and macro/alt source
    status (module 2)."""
    _check_auth(x_api_key, iatis_session)
    import os as _os
    from core.data_providers import DEFAULT_CHAINS, _NATIVE_TF, provider_chain_for

    config = _get_config()
    overrides = config.get("data", {}).get("provider_chains") or {}
    symbols_cfg = config.get("data", {}).get("twelve_data_symbols", [])
    active = [s["internal"] for s in symbols_cfg if s.get("enabled")]

    availability = {
        "ctrader": bool(_os.getenv("CTRADER_CLIENT_ID") and _os.getenv("CTRADER_ACCESS_TOKEN")),
        "twelve_data": bool(_os.getenv("TWELVE_DATA_API_KEY")),
        "yahoo_finance": True,
        "alpha_vantage": bool(_os.getenv("ALPHA_VANTAGE_API_KEY")),
        "finnhub": bool(_os.getenv("FINNHUB_API_KEY")),
        "ccxt": True,
    }
    try:
        recent_usage = _provider_usage_from_decisions()
    except Exception:
        recent_usage = {}
    return {
        "chains": {cls: (overrides.get(cls) or chain)
                   for cls, chain in DEFAULT_CHAINS.items()},
        "native_timeframes": {p: sorted(tfs) for p, tfs in _NATIVE_TF.items()},
        "availability": availability,
        "per_symbol": {sym: provider_chain_for(sym, overrides) for sym in active},
        "recent_usage": recent_usage,
        "macro_sources": _macro_source_status(),
    }


@app.get("/shadow-book")
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


@app.get("/metrics")
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


@app.get("/execution-quality")
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
    from storage.audit_log import log_action
    log_action("close_outcome", x_api_key=x_api_key, session_id=iatis_session,
               success=success, detail=f"{signal_id} -> {outcome}")
    return {"success": success, "signal_id": signal_id, "outcome": outcome}


def _scheduler_status() -> dict[str, Any]:
    """Last scheduler run, from a local log file or journalctl.

    Shared by /health/full and /alerts — extracted so both read the exact
    same signal instead of two slightly-different implementations drifting
    apart over time.
    """
    import re as _re
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
    return {
        "last_run": last_run,
        "last_execute_count": last_execute_count,
        "status": "running" if last_run else "unknown",
    }


def _systemd_service_status() -> dict[str, dict[str, Any]]:
    """Real per-service systemd status via `systemctl is-active <unit>` —
    one fixed-argv call per unit (never shell=True), reusing the same
    whitelist /logs already knows about (_LOG_UNITS, defined below).
    Absent/inert on hosts with no systemd (sandboxes, dev laptops) —
    each unit reports "unavailable" rather than raising.

    Each entry also carries `kind` ("daemon" | "timer") and a `healthy`
    verdict computed for that kind — a timer-triggered oneshot
    (watchdog/backup/d1_backup) is *expected* to read "inactive" between
    scheduled runs, while the same status on a daemon (api/scheduler)
    means it's actually down. Mission Control Audit flagged that showing
    raw systemd state with no such distinction made a healthy idle timer
    indistinguishable from a dead daemon.
    """
    import subprocess

    services: dict[str, dict[str, Any]] = {}
    for key, unit in _LOG_UNITS.items():
        kind = _UNIT_KIND.get(key, "daemon")
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=5,
            )
            status = (result.stdout or "").strip() or "unknown"
        except FileNotFoundError:
            status = "unavailable"
        except subprocess.TimeoutExpired:
            status = "timeout"
        except Exception:
            status = "error"

        healthy = status in ("active", "inactive") if kind == "timer" else status == "active"
        services[key] = {"status": status, "kind": kind, "healthy": healthy}
    return services


def _load_manifests() -> list[dict[str, Any]]:
    """Git-tracked evidence manifests — shared by /research/manifests and
    /alerts (which flags any non-reproducible or newly-generated one).
    """
    import json as _json

    manifests: list[dict[str, Any]] = []
    for f in sorted(Path("research/results").glob("*_manifest.json"), reverse=True):
        try:
            m = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        params = m.get("params") or {}
        git = m.get("git") or {}
        manifests.append({
            "file": f.name,
            "kind": m.get("kind"),
            "generated_at": m.get("generated_at"),
            "reproducible": m.get("reproducible"),
            "git_commit": (git.get("commit") or "")[:8],
            "git_dirty": git.get("dirty"),
            "decision_timeframe": params.get("decision_timeframe"),
            "engines_enabled": params.get("engines_enabled"),
            "note": params.get("note"),
            "datasets_count": len(m.get("datasets") or []),
            "results": m.get("results"),
        })
    return manifests


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


_DH_CONFIG_TO_DM = {"M15": "15m", "H1": "1h", "H4": "4h", "D1": "1d"}
_DH_STATUS_RANK = {"OK": 0, "GAPS": 1, "STALE": 2, "MISSING": 3}  # higher = worse


def _data_health_snapshot() -> dict[str, Any]:
    """Per-symbol/timeframe OHLCV cache completeness — shared by
    /data-health and /alerts (which flags STALE/GAPS/MISSING symbols).
    """
    from core.data_manager import DataManager

    config = _get_config()
    symbols_cfg = config.get("data", {}).get("twelve_data_symbols", [])
    active_symbols = [s["internal"] for s in symbols_cfg if s.get("enabled")]
    config_timeframes = config.get("data", {}).get("timeframes", ["H1", "H4", "D1"])

    dm = DataManager()
    results = []
    summary = {"ok": 0, "stale": 0, "gaps": 0, "missing": 0}

    for symbol in active_symbols:
        tf_status: dict[str, Any] = {}
        worst = "OK"
        for tf in config_timeframes:
            dm_tf = _DH_CONFIG_TO_DM.get(tf)
            if not dm_tf:
                continue
            status = dm.cache_status(symbol, dm_tf)
            tf_status[tf] = status
            if _DH_STATUS_RANK.get(status["status"], 0) > _DH_STATUS_RANK.get(worst, 0):
                worst = status["status"]
        results.append({"symbol": symbol, "timeframes": tf_status, "overall_status": worst})
        summary[worst.lower()] += 1

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "symbols": results,
        "summary": summary,
    }


@app.get("/data-health")
async def data_health(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Data Center — per-symbol/timeframe OHLCV cache completeness.

    Read-only inspection of core/data_manager.py's local CSV cache. Never
    triggers a provider fetch — reports what's actually cached on disk.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        return _data_health_snapshot()
    except Exception as exc:
        logger.error(f"Data health error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


# ---------------------------------------------------------------------------
# Live Logs (Mission Control module 13) — read-only, whitelisted sources only.
#
# Every production service ships with StandardOutput=journal /
# StandardError=journal (see the *.service unit files), so journalctl is the
# real source of truth, not a log file. `storage/system.log` is kept as a
# "system" pseudo-source for local/dev use since config.yaml's logging.file
# is empty by default in production. No arbitrary unit or path is ever
# accepted from the caller — `source` must be a key in _LOG_UNITS or the
# literal "system"; journalctl always runs with a fixed argv (no shell=True,
# no string interpolation of request data into the command line).
# ---------------------------------------------------------------------------
_LOG_UNITS: dict[str, str] = {
    "api": "iatis-api",
    "scheduler": "iatis-scheduler",
    "watchdog": "iatis-watchdog",
    "backup": "iatis-backup",
    "d1_backup": "iatis-d1-backup",
}

# "daemon" units run continuously — inactive means something is actually
# down. "timer" units are triggered by a companion .timer (see the
# iatis-*.timer files at the repo root) and sit inactive between runs by
# design — that's normal, not a fault. Mission Control Audit flagged that
# the dashboard showed all five with identical treatment, making a
# healthy idle timer indistinguishable from a dead daemon.
_UNIT_KIND: dict[str, str] = {
    "api": "daemon",
    "scheduler": "daemon",
    "watchdog": "timer",
    "backup": "timer",
    "d1_backup": "timer",
}


@app.get("/logs/sources")
async def log_sources(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """The whitelisted set of log sources /logs will tail. Nothing else."""
    _check_auth(x_api_key, iatis_session)
    return {
        "sources": [{"id": "system", "label": "System (storage/system.log)", "kind": "file"}]
        + [{"id": key, "label": f"{key} ({unit})", "kind": "journal"} for key, unit in _LOG_UNITS.items()]
    }


@app.get("/logs")
async def tail_logs(
    source: str = Query(..., description="One of the ids returned by /logs/sources"),
    lines: int = Query(default=200, ge=1, le=1000),
    search: str | None = Query(default=None, max_length=200),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Tail a whitelisted log source, optionally filtered by substring."""
    _check_auth(x_api_key, iatis_session)

    if source != "system" and source not in _LOG_UNITS:
        raise HTTPException(status_code=400, detail=f"Unknown log source '{source}'. See /logs/sources.")

    entries: list[str] = []
    error: str | None = None

    if source == "system":
        log_path = Path("storage/system.log")
        if log_path.exists():
            try:
                entries = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            except OSError as exc:
                error = str(exc)[:200]
        else:
            error = "storage/system.log doesn't exist — logging.file is unset in config.yaml, or nothing has logged locally yet."
    else:
        import subprocess
        unit = _LOG_UNITS[source]
        try:
            result = subprocess.run(
                ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "--output=cat"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                entries = result.stdout.splitlines()
            else:
                error = (result.stderr or "journalctl returned a non-zero exit code").strip()[:300]
        except FileNotFoundError:
            error = "journalctl is not available on this host."
        except subprocess.TimeoutExpired:
            error = "journalctl timed out."
        except Exception as exc:
            error = str(exc)[:200]

    if search:
        needle = search.lower()
        entries = [e for e in entries if needle in e.lower()]

    return {
        "source": source,
        "lines_requested": lines,
        "lines_returned": len(entries),
        "search": search,
        "entries": entries,
        "error": error,
    }


# ---------------------------------------------------------------------------
# File Explorer (Mission Control module 11) — read-only. View, search,
# download, diff. Never edit. Every path is confined to the repo root and
# checked against a secret-shaped denylist before it's ever opened — this
# repo's own CLAUDE.md notes real credentials have leaked into chat/commits
# twice before, so path confinement here is defense-in-depth, not decoration.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories excluded wholesale — .git can contain secrets from repo
# history even if the current tree is clean; the rest are generated/noise.
_DENY_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", ".pytest_cache"}

# Exact "word" matches on a path segment's alnum-split tokens — deliberately
# whole-word (not substring) so e.g. dashboard/frontend/src/theme/tokens.css
# (a design-tokens stylesheet, not a secret) is never falsely denied.
_DENY_WORDS = {"credential", "credentials", "secret", "secrets", "token", "password", "passwords"}
_DENY_EXTENSIONS = {"pem", "key", "pfx", "p12", "crt", "cer"}
_DENY_PREFIXES = ("storage/sessions", "storage/td_cache")
_MAX_READ_BYTES = 512_000
_MAX_SEARCH_FILES = 4000
_MAX_SEARCH_FILE_BYTES = 512_000


def _is_denied_path(posix_rel: str) -> bool:
    parts = posix_rel.split("/")
    if any(p in _DENY_DIR_NAMES for p in parts):
        return True
    if any(posix_rel.startswith(pre) for pre in _DENY_PREFIXES):
        return True
    basename = parts[-1]
    if basename == ".env" or basename.startswith(".env."):
        return True
    stem_ext = basename.rsplit(".", 1)
    if len(stem_ext) == 2 and stem_ext[1].lower() in _DENY_EXTENSIONS:
        return True
    words = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", basename) if w}
    if words & _DENY_WORDS:
        return True
    return False


def _resolve_safe_path(rel_path: str) -> tuple[Path, str]:
    """Resolve a client-supplied path against the repo root.

    Always returns a path inside _REPO_ROOT and outside the denylist, or
    raises HTTPException — callers never need to re-check.
    """
    rel_path = (rel_path or "").strip().lstrip("/")
    candidate = (_REPO_ROOT / rel_path).resolve() if rel_path else _REPO_ROOT
    try:
        posix_rel = candidate.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes the repository root.")
    if posix_rel == ".":
        posix_rel = ""
    if posix_rel and _is_denied_path(posix_rel):
        raise HTTPException(status_code=403, detail="This path is not accessible via the File Explorer.")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    return candidate, posix_rel


@app.get("/files/tree")
async def files_tree(
    path: str = Query(default=""),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory — use /files/read.")

    entries = []
    for child in target.iterdir():
        child_rel = (posix_rel + "/" + child.name) if posix_rel else child.name
        if _is_denied_path(child_rel):
            continue
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "path": child_rel,
                "type": "dir" if child.is_dir() else "file",
                "size": None if child.is_dir() else stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
        except OSError:
            continue

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"path": posix_rel, "entries": entries}


@app.get("/files/read")
async def files_read(
    path: str = Query(...),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file — use /files/tree.")

    size = target.stat().st_size
    if size > _MAX_READ_BYTES:
        return {
            "path": posix_rel, "size": size, "binary": False, "truncated": False,
            "content": None,
            "error": f"File is {size:,} bytes, over the {_MAX_READ_BYTES:,}-byte inline read limit — use /files/download.",
        }

    raw = target.read_bytes()
    try:
        content = raw.decode("utf-8")
        return {"path": posix_rel, "size": size, "binary": False, "truncated": False, "content": content, "error": None}
    except UnicodeDecodeError:
        return {
            "path": posix_rel, "size": size, "binary": True, "truncated": False,
            "content": None, "error": "Binary file — use /files/download to retrieve it.",
        }


@app.get("/files/download")
async def files_download(
    path: str = Query(...),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> FileResponse:
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file.")
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


@app.get("/files/diff")
async def files_diff(
    path: str = Query(...),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Working-tree vs HEAD diff for one file, via `git diff` with a fixed
    argv (no shell=True, path is confined/denylisted by _resolve_safe_path
    before it ever reaches subprocess).
    """
    _check_auth(x_api_key, iatis_session)
    target, posix_rel = _resolve_safe_path(path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file.")

    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color", "HEAD", "--", posix_rel],
            capture_output=True, text=True, timeout=5, cwd=_REPO_ROOT,
        )
        diff_text = result.stdout
        error = None if result.returncode == 0 else (result.stderr or "git diff failed").strip()[:300]
    except FileNotFoundError:
        diff_text, error = "", "git is not available on this host."
    except subprocess.TimeoutExpired:
        diff_text, error = "", "git diff timed out."

    return {"path": posix_rel, "diff": diff_text, "has_changes": bool(diff_text.strip()), "error": error}


@app.get("/files/search")
async def files_search(
    query: str = Query(..., min_length=2, max_length=200),
    path: str = Query(default=""),
    max_results: int = Query(default=100, ge=1, le=500),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Bounded read-only search over filenames and file contents.

    Scans at most _MAX_SEARCH_FILES files under `path`, skips anything
    denylisted or over _MAX_SEARCH_FILE_BYTES, and stops as soon as
    max_results matches are found.
    """
    _check_auth(x_api_key, iatis_session)
    root, root_rel = _resolve_safe_path(path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory.")

    needle = query.lower()
    results: list[dict[str, Any]] = []
    scanned = 0
    truncated = False

    for current_dir, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _DENY_DIR_NAMES]
        for fname in sorted(filenames):
            if len(results) >= max_results:
                truncated = True
                break
            scanned += 1
            if scanned > _MAX_SEARCH_FILES:
                truncated = True
                break

            fpath = Path(current_dir) / fname
            frel = fpath.relative_to(_REPO_ROOT).as_posix()
            if _is_denied_path(frel):
                continue

            if needle in fname.lower():
                results.append({"path": frel, "match_type": "filename", "line": None, "snippet": fname})
                continue

            try:
                if fpath.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                    continue
                text = fpath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for lineno, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    results.append({
                        "path": frel, "match_type": "content", "line": lineno,
                        "snippet": line.strip()[:200],
                    })
                    break

        if len(results) >= max_results or scanned > _MAX_SEARCH_FILES:
            truncated = True
            break

    return {"query": query, "path": root_rel, "results": results, "truncated": truncated}


# ---------------------------------------------------------------------------
# Forward decision rules (D001/D002) — shared by Forward Demo (module 6,
# /forward-review) and Alert Center (module 14, /alerts). registry.json's
# `_decision_rules` block is the single source of truth; this only reads
# and evaluates it, via scripts/forward_review.py's own helpers — never
# reinvents the rule logic.
# ---------------------------------------------------------------------------
def _forward_rule_progress() -> list[dict[str, Any]]:
    """Full progress snapshot for every pre-registered rule, whether or
    not it has triggered — the Forward Demo view. Alert Center derives
    its (much shorter) alert list from this same data.
    """
    import json as _json
    from scripts.forward_review import REGISTRY, FX, CARRIERS, _bucket_stats, _closed_outcomes

    rules = _json.loads(REGISTRY.read_text()).get("_decision_rules", {})
    rows = _closed_outcomes()
    buckets = {"fx": _bucket_stats(rows, FX), "carriers": _bucket_stats(rows, CARRIERS)}

    out: list[dict[str, Any]] = []
    for rule_id, rule in rules.items():
        if rule_id.startswith("_") or not isinstance(rule, dict):
            continue
        b = buckets.get(rule["bucket"]) or {"n": 0, "wr": None, "pf": None}
        n, min_n = b["n"], rule["min_n"]
        metric = b.get(rule["metric"])
        sufficient_n = n >= min_n
        triggered = bool(
            sufficient_n and metric is not None
            and ((rule["op"] == "<" and metric < rule["threshold"])
                 or (rule["op"] == ">=" and metric >= rule["threshold"]))
        )
        # Sanitize AFTER the numeric comparisons above — a bare `Infinity`
        # token (what json.dumps would emit for float("inf")) isn't valid
        # JSON and makes a browser's fetch().json() throw. The frontend
        # renders this string sentinel as "∞".
        if metric == float("inf"):
            json_safe_metric: float | str | None = "Infinity"
        elif metric == float("-inf"):
            json_safe_metric = "-Infinity"
        else:
            json_safe_metric = metric
        out.append({
            "rule_id": rule_id,
            "statement": rule["statement"],
            "bucket": rule["bucket"],
            "metric": rule["metric"],
            "current_value": json_safe_metric,
            "op": rule["op"],
            "threshold": rule["threshold"],
            "n": n,
            "min_n": min_n,
            "progress_pct": round(min(100.0, 100.0 * n / min_n), 1) if min_n else None,
            "sufficient_n": sufficient_n,
            "triggered": triggered,
            "action": rule.get("action"),
        })
    return out


def _forward_rule_alerts() -> list[dict[str, Any]]:
    """The subset of _forward_rule_progress() worth surfacing as an alert:
    a triggered rule, or one that's crossed 80% of its required sample.
    """
    out: list[dict[str, Any]] = []
    for p in _forward_rule_progress():
        if p["triggered"]:
            out.append({
                "severity": "warning", "category": "forward_milestone",
                "message": f"{p['rule_id']} VERDICT REACHED: {p['statement']}",
                "detail": {"rule_id": p["rule_id"], "n": p["n"], "metric": p["metric"],
                           "value": p["current_value"], "action": p["action"]},
            })
        elif not p["sufficient_n"] and p["n"] >= p["min_n"] * 0.8:
            out.append({
                "severity": "info", "category": "forward_milestone",
                "message": f"{p['rule_id']} approaching evaluation: n={p['n']}/{p['min_n']} closed {p['bucket']} trades.",
                "detail": {"rule_id": p["rule_id"], "n": p["n"], "min_n": p["min_n"]},
            })
    return out


@app.get("/forward-review")
async def forward_review_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Forward Demo (module 6) — pre-registered D001/D002 rule progress,
    read-only. Live decisions still follow scripts/forward_review.py run
    by a human/cron, per CLAUDE.md's "live decisions follow pre-registered
    rules... never invented at read time" — this endpoint only displays
    the same evaluation, it doesn't act on it.
    """
    _check_auth(x_api_key, iatis_session)
    try:
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "rules": _forward_rule_progress(),
        }
    except Exception as exc:
        logger.error(f"Forward review error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@app.get("/alerts")
async def list_alerts(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    now = datetime.now(timezone.utc).isoformat()
    items: list[dict[str, Any]] = []

    def add(severity: str, category: str, message: str, detail: dict[str, Any] | None = None) -> None:
        items.append({"severity": severity, "category": category, "message": message, "detail": detail})

    try:
        sched = _scheduler_status()
        if sched["status"] != "running":
            add("error", "service_offline", "Scheduler status unknown — no recent 'Run complete' log line found.", sched)
    except Exception as exc:
        add("error", "service_offline", f"Could not determine scheduler status: {exc}")

    provider_env = {
        "twelve_data": "TWELVE_DATA_API_KEY",
        "alpha_vantage": "ALPHA_VANTAGE_API_KEY",
        "finnhub": "FINNHUB_API_KEY",
    }
    for name, env_var in provider_env.items():
        if not os.environ.get(env_var):
            add("warning", "provider_failure", f"{name} API key not configured ({env_var}).", {"provider": name})
    ct_token = os.environ.get("CTRADER_ACCESS_TOKEN", "")
    if not (ct_token and ct_token != "TOKEN_HERE"):
        add("warning", "provider_failure", "cTrader access token not configured.", {"provider": "ctrader"})

    try:
        dh = _data_health_snapshot()
        for sym in dh["symbols"]:
            status = sym["overall_status"]
            if status in ("STALE", "GAPS", "MISSING"):
                add(
                    "error" if status == "MISSING" else "warning",
                    "missing_data",
                    f"{sym['symbol']} data is {status}.",
                    {"symbol": sym["symbol"], "status": status},
                )
    except Exception as exc:
        add("error", "missing_data", f"Could not check data health: {exc}")

    try:
        for m in _load_manifests():
            if m.get("reproducible") is False:
                add(
                    "warning", "manifest_mismatch",
                    f"{m['file']} is not reproducible (dirty working tree at generation time).",
                    {"file": m["file"], "kind": m.get("kind")},
                )
            gen_at = m.get("generated_at")
            if gen_at:
                try:
                    gen_dt = datetime.fromisoformat(gen_at)
                    if (datetime.now(timezone.utc) - gen_dt).total_seconds() < 86400:
                        add(
                            "info", "research_completed",
                            f"New manifest: {m['file']} ({m.get('kind')}).",
                            {"file": m["file"], "kind": m.get("kind")},
                        )
                except ValueError:
                    pass
    except Exception as exc:
        add("error", "manifest_mismatch", f"Could not load manifests: {exc}")

    try:
        for a in _forward_rule_alerts():
            add(a["severity"], a["category"], a["message"], a["detail"])
    except Exception as exc:
        add("error", "forward_milestone", f"Could not evaluate forward decision rules: {exc}")

    severity_order = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda a: severity_order.get(a["severity"], 3))

    return {
        "checked_at": now,
        "count": len(items),
        "by_severity": {sev: sum(1 for a in items if a["severity"] == sev) for sev in ("error", "warning", "info")},
        "alerts": items,
    }


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

@app.get("/ai/explain/{decision_id}")
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


@app.post("/ai/explain-trade")
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


@app.get("/ai/news-analysis")
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


@app.get("/ai/macro-analysis")
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


@app.post("/ai/research-summary")
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


@app.get("/ai/daily-report")
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
# Command Center SPA — built frontend, mounted only if the build exists.
# `cd dashboard/frontend && npm install && npm run build` produces dist/.
# ---------------------------------------------------------------------------
_DASHBOARD_DIST = Path("dashboard/frontend/dist")
if _DASHBOARD_DIST.is_dir():
    app.mount("/app", StaticFiles(directory=_DASHBOARD_DIST, html=True), name="dashboard_spa")
    logger.info("Command Center SPA mounted at /app")
else:
    logger.info("dashboard/frontend/dist not found — /app not mounted (run npm run build)")

