"""
integrations/ctrader/oauth.py
-------------------------------
cTrader OAuth 2.0: authorize-URL construction and the two token-endpoint
exchanges (authorization_code, refresh_token). Plain HTTPS only — this
module never touches a live cTrader trading session (that stays entirely
inside execution/ctrader_client.py) and never imports ctrader_open_api/
twisted, so it works even where the optional cTrader install
(requirements-ctrader.txt, split out 2026-07-14) isn't present.

refresh_tokens()/write_env_var() are the same logic
scripts/ctrader_refresh_access_token.py has used as an operator-run CLI —
relocated here so the CLI script and the new automatic in-process refresh
(token_manager.py, execution/ctrader_client.py) share one implementation
instead of two copies drifting apart.

SECURITY: client_secret, the authorization code, access_token, and
refresh_token must never be logged in full, never returned in any HTTP
response body, and never sent to the frontend. Every function here takes/
returns them as plain values for the CALLER (a server-side route or the
scheduler process) to persist — nothing in this module writes to an HTTP
response.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"  # matches scripts/ctrader_refresh_access_token.py's existing constant


class CTraderOAuthError(RuntimeError):
    """Raised on any failure exchanging/refreshing a token. Never carries
    the client_secret in its message. Subclasses RuntimeError so existing
    callers (scripts/ctrader_refresh_access_token.py's prior contract)
    that catch RuntimeError keep working unchanged."""


def build_authorize_url(client_id: str, redirect_uri: str, state: str, scope: str = "trading") -> str:
    """cTrader's documented authorization-code consent-screen URL."""
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "product": "web", "state": state}
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token_request(params: dict) -> dict:
    try:
        resp = requests.get(TOKEN_URL, params=params, timeout=30)
    except requests.RequestException as exc:
        raise CTraderOAuthError(f"Request failed: {exc}") from exc
    if resp.status_code != 200:
        raise CTraderOAuthError(f"Token endpoint returned {resp.status_code}: {resp.text[:500]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise CTraderOAuthError(f"Non-JSON response: {resp.text[:500]}") from exc
    if not data.get("accessToken"):
        raise CTraderOAuthError(f"Response missing accessToken: {data}")
    return data


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """The ~1-minute authorization code -> {accessToken, refreshToken,
    expiresIn}. Must be called immediately by the server on
    GET /ctrader/callback — the code (and the resulting tokens) must
    never be handed to the frontend."""
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    })


def refresh_tokens(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Calls cTrader's token endpoint and returns the parsed JSON payload.
    Raises CTraderOAuthError with a readable message on any failure —
    never a bare requests/JSON exception."""
    return _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    })


def write_env_var(env_path: Path, key: str, value: str) -> None:
    """In-place update of one KEY=value line in a .env file, preserving
    every other line verbatim (including comments/ordering). Appends the
    key at the end if it wasn't already present. Anchored regex so
    CTRADER_ACCESS_TOKEN never clobbers CTRADER_ACCESS_TOKEN_EXPIRY."""
    lines = env_path.read_text().splitlines(keepends=True)
    pattern = re.compile(rf"^{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}\n"
            break
    else:
        lines.append(f"{key}={value}\n")
    env_path.write_text("".join(lines))
