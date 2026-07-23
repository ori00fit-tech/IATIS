"""
execution/routes/journal.py
------------------------------
Trade Journal (storage/journal.py) — the detailed, filterable view over the
paper-trading outcomes ledger. Read-only over trade economics; the single
write surface is the operator annotation (notes/tags), audit-logged.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, Response

from execution.api_core import _check_auth, logger

router = APIRouter()




# ---------------------------------------------------------------------------
# Trade Journal (storage/journal.py) — the detailed, filterable view over the
# paper-trading outcomes ledger. Read-only over trade economics; the single
# write surface is the operator annotation (notes/tags), audit-logged.
# ---------------------------------------------------------------------------

@router.get("/journal")
async def journal_list(
    symbol: str | None = Query(default=None),
    outcome: str | None = Query(default=None, description="win / loss / breakeven / open"),
    direction: str | None = Query(default=None, description="BUY / SELL (BULLISH/BEARISH accepted)"),
    regime: str | None = Query(default=None, description="TRENDING / RANGING / VOLATILE"),
    date_from: str | None = Query(default=None, description="ISO date/timestamp, inclusive lower bound"),
    date_to: str | None = Query(default=None, description="ISO date/timestamp, inclusive upper bound"),
    search: str | None = Query(default=None, description="Substring over notes / signal_id"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Filterable, paginated trade journal (newest first)."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.journal import list_trades
        return list_trades(
            symbol=symbol, outcome=outcome, direction=direction, regime=regime,
            date_from=date_from, date_to=date_to, search=search,
            limit=limit, offset=offset,
        )
    except Exception as exc:
        logger.error(f"Journal list error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/journal/stats")
async def journal_stats_endpoint(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Aggregate journal statistics: R-based equity curve, streaks, and
    per-symbol/regime/direction breakdowns — all recomputed from prices."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.journal import journal_stats
        return journal_stats()
    except Exception as exc:
        logger.error(f"Journal stats error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/journal/export")
async def journal_export(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> Response:
    """The whole journal as CSV for offline analysis."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.journal import export_csv
        csv_text = export_csv()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return Response(
            content=csv_text,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=iatis_journal_{stamp}.csv"},
        )
    except Exception as exc:
        logger.error(f"Journal export error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")


@router.get("/journal/{signal_id}")
async def journal_detail(
    signal_id: str,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Full journal entry for one signal: prices, engine votes at signal
    time, realized R, planned RR, duration, annotations."""
    _check_auth(x_api_key, iatis_session)
    try:
        from storage.journal import trade_detail
        detail = trade_detail(signal_id)
    except Exception as exc:
        logger.error(f"Journal detail error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")
    if detail is None:
        raise HTTPException(status_code=404, detail="Unknown signal_id.")
    return detail


@router.post("/journal/{signal_id}/annotate")
async def journal_annotate(
    signal_id: str,
    request: Request,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Attach operator notes/tags to a trade. Annotation only — never read
    by any gate, weight, or measurement."""
    _check_auth(x_api_key, iatis_session)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required.")
    notes = body.get("notes")
    tags = body.get("tags")
    if notes is None and tags is None:
        raise HTTPException(status_code=400, detail="Provide 'notes' and/or 'tags'.")
    if tags is not None and not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="'tags' must be a list of strings.")
    try:
        from storage.journal import annotate
        found, applied = annotate(signal_id, notes=notes, tags=tags)
    except Exception as exc:
        logger.error(f"Journal annotate error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error.")
    from storage.audit_log import log_action
    log_action("journal_annotate", x_api_key=x_api_key, session_id=iatis_session,
               success=found and applied, detail=signal_id)
    if not found:
        raise HTTPException(status_code=404, detail="Unknown signal_id.")
    if not applied:
        # Found the row but nothing was actually written (e.g. tags were
        # requested but the tags-column migration hasn't run and no notes
        # were given) — must not report success:true for a no-op.
        raise HTTPException(
            status_code=409,
            detail="Nothing was persisted — tags require migration 3 (storage.migrations) and no notes were provided.",
        )
    return {"success": True, "signal_id": signal_id}

