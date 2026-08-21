"""
execution/routes/post_trade.py
----------------------------------
Unified Post-Trade Control / Incident Register — the 6 read/write
endpoints over storage/post_trade_incidents.py, following exactly the
same auth/audit-log pattern as execution/routes/kill_switch.py.

Never calls execution.reconciliation.reconcile() (only last_result(), via
execution/post_trade_monitor.py's post_trade_summary()) — the API process
must never open a second cTrader session, matching execution/routes/
outcomes.py's own documented reconciliation-endpoint constraint. Every
state-changing action (acknowledge/resolve/waive) is written to the
existing audit log, actor="dashboard", the same convention every other
control-plane route in this codebase already uses.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException

from execution.api_core import _check_auth, logger

router = APIRouter()


@router.get("/post-trade/summary")
async def post_trade_summary_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Read-only dashboard snapshot: incident counts + current
    reconciliation/kill-switch/execution-quality state. Never scans,
    never writes — safe to poll on every page load."""
    _check_auth(x_api_key, iatis_session)
    try:
        from execution.post_trade_monitor import post_trade_summary
        return post_trade_summary()
    except Exception as exc:
        logger.error(f"post-trade summary failed: {exc}")
        raise HTTPException(status_code=503, detail="Post-trade summary unavailable.")


@router.get("/post-trade/incidents")
async def list_incidents_endpoint(
    status: str | None = None,
    severity: str | None = None,
    control_type: str | None = None,
    limit: int = 200,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.post_trade_incidents import list_incidents
        rows = list_incidents(status=status, severity=severity, control_type=control_type, limit=limit)
        return {"incidents": rows, "count": len(rows)}
    except Exception as exc:
        logger.error(f"post-trade incident list failed: {exc}")
        raise HTTPException(status_code=503, detail="Incident list unavailable.")


@router.get("/post-trade/incidents/{incident_id}")
async def get_incident_endpoint(
    incident_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    from storage.post_trade_incidents import get_incident
    row = get_incident(incident_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown incident_id.")
    return row


@router.post("/post-trade/incidents/{incident_id}/acknowledge")
async def acknowledge_incident_endpoint(
    incident_id: str,
    body: dict[str, Any] | None = None,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    note = str((body or {}).get("note", "") or "").strip() or None

    from storage.audit_log import log_action
    from storage.post_trade_incidents import acknowledge_incident

    try:
        row = acknowledge_incident(incident_id, actor="dashboard", note=note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log_action(
        "post_trade_incident.acknowledge", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"{incident_id}: {note or '(no note)'}",
    )
    return row


@router.post("/post-trade/incidents/{incident_id}/resolve")
async def resolve_incident_endpoint(
    incident_id: str,
    body: dict[str, Any],
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required to resolve an incident.")
    evidence = body.get("evidence")

    from storage.audit_log import log_action
    from storage.post_trade_incidents import resolve_incident

    try:
        row = resolve_incident(incident_id, actor="dashboard", reason=reason, evidence=evidence)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log_action(
        "post_trade_incident.resolve", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"{incident_id}: {reason}",
    )
    return row


@router.post("/post-trade/incidents/{incident_id}/waive")
async def waive_incident_endpoint(
    incident_id: str,
    body: dict[str, Any],
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _check_auth(x_api_key, iatis_session)
    reason = str(body.get("reason", "")).strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required to waive an incident.")

    from storage.audit_log import log_action
    from storage.post_trade_incidents import waive_incident

    try:
        row = waive_incident(incident_id, actor="dashboard", reason=reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log_action(
        "post_trade_incident.waive", x_api_key=x_api_key, session_id=iatis_session,
        detail=f"{incident_id}: {reason}",
    )
    return row
