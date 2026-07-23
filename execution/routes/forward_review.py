"""
execution/routes/forward_review.py
-------------------------------------
Forward Demo (Mission Control module 6) — pre-registered D001/D002 rule
progress, read-only. Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException

from execution.api_core import _check_auth, logger
from execution.api_shared_helpers import _forward_rule_progress

router = APIRouter()


@router.get("/forward-review")
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


