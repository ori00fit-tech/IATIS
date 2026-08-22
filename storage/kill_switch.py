"""
storage/kill_switch.py
------------------------
Operational kill switch — RTS 6 Art.12 / PRA SS5/18 "kill functionality"
style manual halt on new order submission.

Backed by a local file (storage/kill_switch.json), not D1: this control
must keep working even when the remote database is unreachable, and it
must never silently reset on a process restart — an activated switch
stays active until a human explicitly deactivates it. State is read fresh
from disk on every check, never cached in memory, so an activation made
by the dashboard/API is seen by scheduler.py's very next tick.

Scope (deliberate, stated not silently narrowed): this halts NEW order
submission only (scheduler.py's EXECUTE branch, right before a
TradeExecutor is ever constructed). IATIS only ever places protected
market orders with SL/TP already attached — no pending/limit order queue
exists anywhere in this codebase (execution/ctrader_client.py,
execution/oanda_client.py, execution/dukascopy_jforex_client.py all
expose no cancel-pending-order capability, because there is nothing to
cancel) — so "cancel pending orders" does not apply here. Force-closing
already-open positions is a separate, materially riskier action (a real
market close, not just an abstention from a new one) and is deliberately
NOT part of this kill switch; it would need its own dedicated, carefully
reviewed design and is not built here.

Fail-closed: a corrupted/unreadable state file is treated as ACTIVE
(blocking), never as inactive — the same fail-closed discipline
scheduler.py's own RE-F1/RE-F3 gates already apply to the correlation
filter and symbol-health check.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_PATH = Path("storage/kill_switch.json")

_DEFAULT_STATE: dict[str, Any] = {
    "active": False,
    "reason": None,
    "activated_at": None,
    "activated_by": None,
    "deactivated_at": None,
    "deactivated_by": None,
}


def get_state(path: Path | None = None) -> dict[str, Any]:
    """Always reads fresh from disk — never cached."""
    p = path or STATE_PATH
    if not p.exists():
        return dict(_DEFAULT_STATE)
    try:
        raw = json.loads(p.read_text())
        if not isinstance(raw, dict):
            raise ValueError("kill_switch.json did not contain a JSON object")
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        # A safety control must fail CLOSED on a corrupted/unreadable
        # state file, never fail open.
        return {
            **_DEFAULT_STATE,
            "active": True,
            "reason": f"kill_switch.json unreadable ({exc}) — failing closed",
        }
    return {**_DEFAULT_STATE, **raw}


def is_active(path: Path | None = None) -> bool:
    return bool(get_state(path).get("active"))


def activate(reason: str, activated_by: str = "operator", path: Path | None = None) -> dict[str, Any]:
    """Immediate halt. Never auto-clears — only deactivate() (an explicit,
    separate human action) turns it back off."""
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A kill switch activation must state a reason.")
    p = path or STATE_PATH
    state: dict[str, Any] = {
        "active": True,
        "reason": reason[:500],
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "activated_by": activated_by,
        "deactivated_at": None,
        "deactivated_by": None,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))
    return state


def deactivate(deactivated_by: str = "operator", path: Path | None = None) -> dict[str, Any]:
    """Manual-only reset. Preserves the prior reason/activated_at/
    activated_by fields in the returned state for the audit trail, only
    flips active=False and stamps who/when cleared it."""
    p = path or STATE_PATH
    state = get_state(p)
    state["active"] = False
    state["deactivated_at"] = datetime.now(timezone.utc).isoformat()
    state["deactivated_by"] = deactivated_by
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))
    return state
