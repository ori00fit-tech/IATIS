"""
execution/routes/alerts.py
-----------------------------
Alert Center (Mission Control module 14) — aggregates signals already
available from other endpoints (scheduler status, data health, evidence
manifests, forward-rule milestones) into one feed. Not a new data source.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Header

from execution.api_core import _check_auth
from execution.api_shared_helpers import (
    _data_health_snapshot,
    _forward_rule_alerts,
    _load_manifests,
    _scheduler_status,
)

router = APIRouter()


@router.get("/alerts")
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
            if status in ("STALE", "GAPS", "STARVED", "MISSING"):
                add(
                    "error" if status in ("MISSING", "STARVED") else "warning",
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
