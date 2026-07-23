"""
execution/api_core.py
------------------------
Shared FastAPI app infrastructure — the `app` object, auth, config cache,
session store, and symbol validators every route module in
execution/routes/ depends on.

Extracted from execution/api_server.py 2026-07-23 (audit
docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-1): that file had grown to
3,530 lines / ~70 endpoints, flagged as a monolith across three
consecutive audits. execution/api_server.py is now the thin composition
root — it imports `app` from here, includes each router from
execution/routes/, and mounts the built dashboard SPA.

Dropped in this extraction (confirmed dead, not carried over):
`_error_cooldown`/`_COOLDOWN_SECONDS` (a duplicate of scheduler.py's own,
used copy — this one had zero readers/writers anywhere in api_server.py)
and `_set_file_permissions` (zero call sites anywhere in the repo).
"""
from __future__ import annotations

import hmac
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

try:
    from fastapi import FastAPI, HTTPException
except ImportError as exc:
    raise ImportError("Run: pip install fastapi uvicorn") from exc

from utils.helpers import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Symbol validation — strict allowlist
# ---------------------------------------------------------------------------
_SYMBOL_RE = re.compile(r'^[A-Z]{2,6}(/[A-Z]{2,6})?$')


def _validate_symbol(symbol: str) -> str:
    """Validate and normalize symbol. Raises HTTPException on invalid input."""
    clean = symbol.upper().strip()
    if not _SYMBOL_RE.match(clean):
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Use EURUSD or EUR/USD (letters only, 2-6 chars each)."
        )
    if len(clean) > 14:
        raise HTTPException(status_code=400, detail="Symbol too long.")
    return clean

# Letters-only above rejects real active symbols with digits (US30, NAS100,
# SPX500 — config/symbols.yaml) — fine for /analyze's own td_symbol slash
# heuristic (which mis-splits those names anyway), but /candles resolves
# indices correctly via the symbols table and must accept their names.
_CANDLE_SYMBOL_RE = re.compile(r'^[A-Z0-9]{2,6}(/[A-Z0-9]{2,6})?$')


def _validate_candle_symbol(symbol: str) -> str:
    clean = symbol.upper().strip()
    if not _CANDLE_SYMBOL_RE.match(clean) or len(clean) > 14:
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Use EURUSD, EUR/USD, or US30 (letters/digits, 2-6 chars each)."
        )
    return clean

# The repository root — computed HERE (not in each router module) so it's
# always two levels up from THIS file (execution/api_core.py), regardless
# of how deeply nested a given router module lives under execution/routes/.
# A router-local `Path(__file__).resolve().parent.parent` would resolve
# one level short (to execution/, not the repo root) since routers live
# one directory deeper than this file. Consumed by execution/routes/files.py
# (path confinement) and execution/routes/experiments.py (subprocess cwd).
_REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
_ENV = os.environ.get("ENV", "production").lower()
_docs_url = "/docs" if _ENV == "development" else None


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup/shutdown lifecycle handler."""
    # Startup
    for path in ["storage/sessions.json"]:
        p = Path(path)
        if p.exists():
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass
    for path in ["storage/td_cache"]:
        p = Path(path)
        if p.is_dir():
            try:
                os.chmod(p, 0o700)
            except Exception:
                pass
    logger.info(
        f"IATIS API started "
        f"(ENV={_ENV}, auth={'required' if os.environ.get('API_SERVER_KEY') else 'dev-mode'})"
    )
    yield
    # Shutdown (nothing needed)


app = FastAPI(
    title="IATIS API",
    description="Institutional Adaptive Trading Intelligence System",
    version="0.3.0",
    docs_url=_docs_url,
    redoc_url=None,
    lifespan=lifespan,
)

_executor = ThreadPoolExecutor(max_workers=4)
_config_cache: dict | None = None
_config_lock = threading.Lock()

# Session store: {session_id: created_timestamp}
# Sessions persist to disk — survive server restarts
_SESSION_TTL = 86400 * 30  # 30 days
_SESSION_FILE = Path("storage/sessions.json")


def _load_sessions() -> dict:
    try:
        if _SESSION_FILE.exists():
            import json as _sj
            data = _sj.loads(_SESSION_FILE.read_text())
            cutoff = time.time() - _SESSION_TTL
            return {k: v for k, v in data.items() if v > cutoff}
    except Exception:
        pass
    return {}


def _save_sessions(sessions: dict) -> None:
    try:
        import json as _sj
        _SESSION_FILE.parent.mkdir(exist_ok=True)
        _SESSION_FILE.write_text(_sj.dumps(sessions))
        # Session IDs are bearer-equivalent credentials (30-day TTL) — lock
        # this down the same way storage/*.db is locked down at startup.
        try:
            os.chmod(_SESSION_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass


_active_sessions: dict[str, float] = _load_sessions()


def _get_config() -> dict:
    """Thread-safe config cache (issue #14)."""
    global _config_cache
    with _config_lock:
        if _config_cache is None:
            _config_cache = load_config()
            api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
            if api_key:
                _config_cache["data"]["twelve_data_api_key"] = api_key
    return _config_cache


def _reset_config_cache() -> None:
    """Clear the in-process config cache so the next _get_config() call
    reloads config.yaml from disk (used by POST /ops/reload-config).

    A router module doing `global _config_cache; _config_cache = None`
    itself would rebind a NEW module-level name in ITS OWN namespace, not
    mutate this module's — `global` only ever refers to the current
    module's globals, never an imported one's. This function is the
    correct way for execution/routes/experiments.py to reset the cache
    that _get_config() (above) actually reads.
    """
    global _config_cache
    with _config_lock:
        _config_cache = None


def _check_auth(x_api_key: str | None, cookie_key: str | None = None) -> None:
    """Fail-closed auth — accepts X-API-Key header OR valid session cookie.

    Security properties:
    - X-API-Key header: direct key comparison (for curl/API clients)
    - Session cookie: validates session_id against _active_sessions store
      The raw API key is NEVER stored in the cookie (session rotation)
    - hmac.compare_digest: timing-attack protection
    - HttpOnly cookie: JS cannot read session_id
    """
    required = os.environ.get("API_SERVER_KEY")
    if not required:
        if _ENV == "development":
            return
        raise HTTPException(status_code=500, detail="API_SERVER_KEY not configured.")

    # Check X-API-Key header (API clients)
    if x_api_key and hmac.compare_digest(x_api_key, required):
        return

    # Check session cookie (browser)
    if cookie_key:
        now = time.time()
        expires_before = now - _SESSION_TTL
        # Clean expired sessions
        expired = [sid for sid, ts in _active_sessions.items() if ts < expires_before]
        for sid in expired:
            _active_sessions.pop(sid, None)
        # Validate session
        if cookie_key in _active_sessions:
            return

    raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")
