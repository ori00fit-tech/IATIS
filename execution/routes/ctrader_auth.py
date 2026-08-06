"""
execution/routes/ctrader_auth.py
------------------------------------
cTrader OAuth 2.0 web flow: GET /ctrader/status, GET /ctrader/authorize,
GET /ctrader/callback. Part of the execution/api_server.py split.

This module NEVER opens a live cTrader trading session
(core.data_providers.get_shared_ctrader_client()) — it only exchanges an
authorization code for tokens over plain HTTPS (integrations.ctrader.oauth)
and persists them via integrations.ctrader.token_manager. The scheduler
process remains the sole owner of the live session (execution/
reconciliation.py's own docstring; execution/trade_executor.py's
_get_client()) — unchanged by this module. See
tests/test_ctrader_auth_routes.py's hard-block test for the source-scan
pinning this.
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query
from fastapi.responses import RedirectResponse

from execution.api_core import _check_auth, logger

router = APIRouter()

_STATE_TTL = 600  # an authorize -> callback round trip must complete within 10 minutes
_pending_states: dict[str, float] = {}  # state -> issued_at (mirrors execution.api_core._active_sessions' shape)


def _cleanup_states() -> None:
    now = time.time()
    for s in [s for s, ts in _pending_states.items() if now - ts > _STATE_TTL]:
        _pending_states.pop(s, None)


@router.get("/ctrader/status")
async def ctrader_status(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from integrations.ctrader.token_manager import token_status
    return token_status()


@router.get("/ctrader/authorize")
async def ctrader_authorize(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> RedirectResponse:
    _check_auth(x_api_key, iatis_session)
    client_id = os.environ.get("CTRADER_CLIENT_ID", "")
    redirect_uri = os.environ.get("CTRADER_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise HTTPException(status_code=500, detail="CTRADER_CLIENT_ID/CTRADER_REDIRECT_URI not configured — see .env.example.")
    _cleanup_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time()
    from integrations.ctrader.oauth import build_authorize_url
    url = build_authorize_url(client_id, redirect_uri, state, scope=os.environ.get("CTRADER_OAUTH_SCOPE", "trading"))
    return RedirectResponse(url=url, status_code=302)


@router.get("/ctrader/callback")
async def ctrader_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> RedirectResponse:
    # No X-API-Key check here — the BROWSER is navigating back from
    # cTrader's own domain and cannot attach that header. Two checks stand
    # in for auth: (a) the session cookie (samesite="lax", per
    # execution/routes/auth.py:51's own comment, deliberately survives
    # this cross-origin top-level redirect); (b) `state`, which only an
    # authenticated /ctrader/authorize call could have minted. Both must
    # hold.
    _check_auth(None, iatis_session)
    if error:
        logger.error(f"cTrader OAuth callback error: {error}")
        return RedirectResponse(url=f"/#/ctrader-connect?ctrader_error={error}", status_code=302)
    _cleanup_states()
    if not state or state not in _pending_states:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    _pending_states.pop(state, None)
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    from integrations.ctrader.oauth import CTraderOAuthError, exchange_code
    from integrations.ctrader.token_manager import save_initial_tokens
    try:
        tokens = exchange_code(
            os.environ.get("CTRADER_CLIENT_ID", ""),
            os.environ.get("CTRADER_CLIENT_SECRET", ""),
            code,
            os.environ.get("CTRADER_REDIRECT_URI", ""),
        )
        save_initial_tokens(tokens)
    except CTraderOAuthError as exc:
        logger.error(f"cTrader OAuth code exchange failed: {exc}")
        return RedirectResponse(url="/#/ctrader-connect?ctrader_error=exchange_failed", status_code=302)
    except Exception as exc:
        logger.error(f"cTrader OAuth code exchange failed: {exc}", exc_info=True)
        return RedirectResponse(url="/#/ctrader-connect?ctrader_error=exchange_failed", status_code=302)

    # Best-effort account discovery — logged only, never blocks the
    # redirect. Confirms integrations/ctrader/account.py works and gives
    # the operator the ctidTraderAccountId to copy into CTRADER_ACCOUNT_ID
    # if this is a first-time connect (still a manual copy — see plan's
    # Scope §non-goals, no auto-selection UI in this phase).
    try:
        from integrations.ctrader.account import discover_accounts
        accounts = discover_accounts(
            os.environ.get("CTRADER_CLIENT_ID", ""),
            os.environ.get("CTRADER_CLIENT_SECRET", ""),
            tokens["accessToken"],
            os.environ.get("CTRADER_ENVIRONMENT", "demo"),
        )
        logger.info(f"✅ cTrader OAuth connected. Linked accounts: {accounts}")
    except Exception as exc:
        logger.warning(f"cTrader OAuth connected but account discovery failed (non-fatal): {exc}")

    logger.info("✅ cTrader OAuth: new access/refresh token saved to .env — restart iatis-scheduler to activate it.")
    return RedirectResponse(url="/#/ctrader-connect?ctrader_connected=1", status_code=302)
