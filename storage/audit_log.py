"""
storage/audit_log.py
---------------------
Append-only audit trail for state-changing / control-plane dashboard
actions (Mission Control module 15).

Deliberately narrow: this audits ACTIONS — login attempts, job triggers,
config reloads, outcome mutations — not every read. A per-request access
log for every GET would be noise, not signal; this is the accountability
trail the "audit log for every action" requirement actually means.

Local JSONL, same pattern and rationale as storage/decision_log.py: an
append-only audit trail gains nothing from a queryable store, and this
project already has that precedent.

Never logs a raw credential. A leaked audit log must not itself become a
leaked credential — this project has had real secret leaks before (see
CLAUDE.md's "Secrets live in .env only" rule) — so the actor field is
always a masked identifier, never the API key or full session id.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_LOG_PATH = Path(__file__).resolve().parent / "audit_log.jsonl"


def _mask_actor(x_api_key: str | None, session_id: str | None) -> str:
    if x_api_key:
        return "api_key"
    if session_id:
        return f"session:{session_id[:8]}…"
    return "unknown"


def log_action(
    action: str,
    x_api_key: str | None = None,
    session_id: str | None = None,
    success: bool = True,
    detail: str | None = None,
    path: Path | str | None = None,
) -> None:
    """Append one audit entry. Never raises — a logging failure must
    never block the action it's recording."""
    path = Path(path) if path is not None else DEFAULT_LOG_PATH
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "actor": _mask_actor(x_api_key, session_id),
        "success": success,
        "detail": detail,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.warning(f"Audit log write failed (non-fatal): {exc}")


def read_actions(limit: int = 200, path: Path | str | None = None) -> list[dict]:
    """Most recent `limit` audit entries, newest first."""
    path = Path(path) if path is not None else DEFAULT_LOG_PATH
    if not path.exists():
        return []
    entries: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(entries[-limit:]))
