"""
execution/routes/kill_switch.py
----------------------------------
Operational kill switch (RTS 6 Art.12 / PRA SS5/18 "kill functionality"):
a manual, operator-triggered halt on NEW order submission. Backed by
storage/kill_switch.py — a local file, not D1, so this control keeps
working even if the remote database is unreachable.

Scope, stated explicitly (see storage/kill_switch.py's own docstring for
the full reasoning): this blocks scheduler.py's EXECUTE branch only,
before a TradeExecutor is ever constructed. It does NOT and cannot
"cancel pending orders" — IATIS only ever places protected market orders
with SL/TP already attached, so no pending/limit order queue exists
anywhere to cancel. It does NOT force-close already-open positions —
that is a separate, materially riskier action (a real market close, not
an abstention) and is deliberately not built here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException

from execution.api_core import _check_auth

router = APIRouter()


@router.get("/risk/kill-switch")
async def kill_switch_status(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage.kill_switch import get_state

    return get_state()


@router.post("/risk/kill-switch/activate")
async def kill_switch_activate(
    body: dict[str, Any],
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required to activate the kill switch.")

    from storage.audit_log import log_action
    from storage.kill_switch import activate

    state = activate(reason=reason, activated_by="dashboard")
    log_action("kill_switch.activate", x_api_key=x_api_key, session_id=iatis_session, detail=reason)
    return state


@router.post("/risk/kill-switch/deactivate")
async def kill_switch_deactivate(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)

    from storage.audit_log import log_action
    from storage.kill_switch import deactivate, get_state

    prior = get_state()
    if not prior.get("active"):
        raise HTTPException(status_code=409, detail="Kill switch is not currently active.")

    state = deactivate(deactivated_by="dashboard")
    log_action(
        "kill_switch.deactivate", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"was active since {prior.get('activated_at')} (reason: {prior.get('reason')})",
    )
    return state
