"""
execution/routes/data_providers.py
-------------------------------------
Data provider chain status: which provider served each recent decision,
per-symbol failover chains, and macro data source status.
Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException

from execution.api_core import _check_auth, _get_config, logger
from execution.api_shared_helpers import _data_health_snapshot

router = APIRouter()


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


@router.get("/provider-chains")
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

    # yahoo_finance deliberately omitted (2026-07-17): removed as an untrusted
    # feed — out of every price chain and replaced by CBOE/FRED in the macro
    # layer — so it is no longer advertised as an available provider.
    availability = {
        "ctrader": bool(_os.getenv("CTRADER_CLIENT_ID") and _os.getenv("CTRADER_ACCESS_TOKEN")),
        "twelve_data": bool(_os.getenv("TWELVE_DATA_API_KEY")),
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




@router.get("/data-health")
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
