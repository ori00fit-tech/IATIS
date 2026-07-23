"""
execution/routes/auth.py
---------------------------
Login/logout — session cookie issuance and invalidation.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import hmac
import os
import time

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from execution.api_core import _active_sessions, _save_sessions

router = APIRouter()


@router.post("/login")
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


@router.get("/login")
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


@router.get("/logout")
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

