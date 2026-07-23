"""
execution/routes/logs.py
---------------------------
Live Logs (Mission Control module 13) — read-only journalctl/log-file
tail, whitelist-only sources, fixed-argv subprocess calls (never
shell=True). Part of the execution/api_server.py split (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query

from execution.api_core import _check_auth
from execution.api_shared_helpers import _LOG_UNITS

router = APIRouter()


@router.get("/logs/sources")
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


@router.get("/logs")
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
