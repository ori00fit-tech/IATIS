"""
integrations/ctrader/token_manager.py
----------------------------------------
get_valid_access_token() — the one function every token consumer in this
codebase should eventually call. Checks the current CTRADER_ACCESS_TOKEN's
tracked expiry (CTRADER_ACCESS_TOKEN_EXPIRY, a unix timestamp in .env),
refreshes proactively via integrations.ctrader.oauth.refresh_tokens() when
within a safety margin of expiry, and returns the current, valid token —
reading/writing os.environ + .env, never a database (secrets live in .env
only, per CLAUDE.md — this module honors that rather than opening a new
secrets-storage question).

Two entry points:
- get_valid_access_token(margin_seconds): proactive, cheap no-op when not
  near expiry (single env read + float compare, no network call). Called
  periodically from scheduler.py::run_once() and from GET /ctrader/status.
- refresh_access_token_sync(): unconditional refresh, called reactively by
  execution/ctrader_client.py's connect() the instant a
  CH_ACCESS_TOKEN_INVALID rejection proves the current token is already
  dead — margin-based logic is irrelevant there, skip straight to the
  network call.

Both funnel through _do_refresh(), the only function that mutates state:
acquires _refresh_lock (prevents two refreshes racing each other within
one process), calls oauth.refresh_tokens(), updates os.environ AND .env
(oauth.write_env_var, 3 keys), and returns the new access token. A
disk-write failure is logged, not raised — the in-memory os.environ update
already lets THIS process keep working; only a restart before the next
successful write would lose the refresh.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from integrations.ctrader import oauth

_refresh_lock = threading.Lock()

# Test/deploy seam: tests monkeypatch this to a tmp_path .env instead of
# the real project .env — mirrors the existing "monkeypatch.setattr(m,
# '_DATA_DIR', tmp_path)" seam convention used elsewhere in this repo.
_ENV_PATH: Path = Path(__file__).resolve().parent.parent.parent / ".env"


class TokenRefreshError(Exception):
    """The refresh_token itself was rejected (or credentials are missing)
    — needs a fresh browser /ctrader/authorize round trip, not another
    automatic retry."""


def _read_expiry() -> float | None:
    raw = os.environ.get("CTRADER_ACCESS_TOKEN_EXPIRY", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # malformed value — degrade to "unknown expiry", never crash a connect() over a bad timestamp


def get_valid_access_token(margin_seconds: int = 86400) -> str:
    """Proactive path. Returns the current token unchanged unless it's
    within margin_seconds of its tracked expiry (or expiry isn't tracked
    at all, e.g. a token set by hand before this feature existed — that
    case is left alone exactly like today's behavior, never force-refreshed)."""
    expiry = _read_expiry()
    if expiry is None or expiry - time.time() > margin_seconds:
        return os.environ.get("CTRADER_ACCESS_TOKEN", "")
    return refresh_access_token_sync()


def refresh_access_token_sync() -> str:
    """Reactive/unconditional path. Raises TokenRefreshError if
    CTRADER_CLIENT_ID/CTRADER_CLIENT_SECRET/CTRADER_REFRESH_TOKEN are
    missing or the token endpoint rejects the refresh_token itself —
    callers MUST catch this and fail loudly (never swallow it silently),
    since only a fresh browser /ctrader/authorize round trip can recover
    from a dead refresh_token."""
    with _refresh_lock:
        client_id = os.environ.get("CTRADER_CLIENT_ID", "")
        client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "")
        refresh_token = os.environ.get("CTRADER_REFRESH_TOKEN", "")
        if not (client_id and client_secret and refresh_token):
            raise TokenRefreshError(
                "CTRADER_CLIENT_ID/CTRADER_CLIENT_SECRET/CTRADER_REFRESH_TOKEN "
                "not fully configured — re-authorize via /ctrader/authorize."
            )
        try:
            data = oauth.refresh_tokens(client_id, client_secret, refresh_token)
        except oauth.CTraderOAuthError as exc:
            raise TokenRefreshError(str(exc)) from exc
        _persist(data)
        return os.environ["CTRADER_ACCESS_TOKEN"]


def save_initial_tokens(data: dict) -> None:
    """Called once by GET /ctrader/callback right after exchanging the
    authorization code — same persistence path as a routine refresh."""
    _persist(data)


def _persist(data: dict) -> None:
    access = data["accessToken"]
    refresh_token = data.get("refreshToken", "")
    expires_in = data.get("expiresIn")

    os.environ["CTRADER_ACCESS_TOKEN"] = access
    if refresh_token:
        os.environ["CTRADER_REFRESH_TOKEN"] = refresh_token
    expiry_str = ""
    if expires_in:
        expiry_str = str(time.time() + float(expires_in))
        os.environ["CTRADER_ACCESS_TOKEN_EXPIRY"] = expiry_str

    try:
        oauth.write_env_var(_ENV_PATH, "CTRADER_ACCESS_TOKEN", access)
        if refresh_token:
            oauth.write_env_var(_ENV_PATH, "CTRADER_REFRESH_TOKEN", refresh_token)
        if expiry_str:
            oauth.write_env_var(_ENV_PATH, "CTRADER_ACCESS_TOKEN_EXPIRY", expiry_str)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(f"cTrader token refreshed but .env write failed: {exc}")


def token_status() -> dict:
    """Non-secret summary for GET /ctrader/status — NEVER includes the
    raw access_token/refresh_token/client_secret values."""
    expiry = _read_expiry()
    expires_in_seconds = None if expiry is None else int(expiry - time.time())
    token = os.environ.get("CTRADER_ACCESS_TOKEN", "")
    return {
        "configured": bool(token and token != "TOKEN_HERE"),
        "has_refresh_token": bool(os.environ.get("CTRADER_REFRESH_TOKEN")),
        "account_id": os.environ.get("CTRADER_ACCOUNT_ID") or None,
        "environment": os.environ.get("CTRADER_ENVIRONMENT", "demo"),
        "expires_at": expiry,
        "expires_in_seconds": expires_in_seconds,
        "needs_reauthorization": bool(
            expires_in_seconds is not None
            and expires_in_seconds <= 0
            and not os.environ.get("CTRADER_REFRESH_TOKEN")
        ),
    }
