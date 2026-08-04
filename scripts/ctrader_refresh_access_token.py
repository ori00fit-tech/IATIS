#!/usr/bin/env python3
"""
scripts/ctrader_refresh_access_token.py
-----------------------------------------
Exchange a saved CTRADER_REFRESH_TOKEN for a fresh CTRADER_ACCESS_TOKEN.

cTrader Open API access tokens expire (~30 days per cTrader's own docs) —
this is the live symptom `execution/ctrader_client.py` surfaces as
`CH_ACCESS_TOKEN_INVALID — Access token expired` on account auth, distinct
from the (already-fixed) ALREADY_LOGGED_IN/reconnect-storm class. No code
path in this repo refreshes the token automatically; this script is the
operator-run fix.

Uses cTrader/Spotware's own documented refresh flow (help.ctrader.com/
open-api/account-authentication/): a GET to https://openapi.ctrader.com/
apps/token with grant_type=refresh_token, refresh_token, client_id,
client_secret as query params, returning a fresh JSON payload
({accessToken, refreshToken, tokenType, expiresIn}). Per the same docs the
refresh token itself never expires on its own, but each refresh issues a
NEW one — the old refresh token should be treated as consumed, so this
script always prints (and, with --write, saves) BOTH new tokens.

Run from the IATIS project root:
    python scripts/ctrader_refresh_access_token.py            # print only, changes nothing
    python scripts/ctrader_refresh_access_token.py --write    # also updates .env in place

Requires (in .env): CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET,
CTRADER_REFRESH_TOKEN
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

import requests

TOKEN_URL = "https://openapi.ctrader.com/apps/token"


def write_env_var(env_path: Path, key: str, value: str) -> None:
    """In-place update of one KEY=value line in a .env file, preserving
    every other line verbatim (including comments/ordering). Appends the
    key at the end if it wasn't already present."""
    lines = env_path.read_text().splitlines(keepends=True)
    pattern = re.compile(rf"^{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}\n"
            break
    else:
        lines.append(f"{key}={value}\n")
    env_path.write_text("".join(lines))


def refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Calls cTrader's token endpoint and returns the parsed JSON payload.
    Raises RuntimeError with a readable message on any failure — never a
    bare requests/JSON exception, since this is meant to be run and read
    directly by an operator."""
    try:
        resp = requests.get(
            TOKEN_URL,
            params={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"Token endpoint returned {resp.status_code}: {resp.text[:500]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Non-JSON response: {resp.text[:500]}") from exc
    if not data.get("accessToken"):
        raise RuntimeError(f"Response missing accessToken: {data}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--write", action="store_true",
        help="Update CTRADER_ACCESS_TOKEN/CTRADER_REFRESH_TOKEN in .env in "
             "place (default: print only, change nothing on disk).",
    )
    args = ap.parse_args()

    client_id = os.environ.get("CTRADER_CLIENT_ID", "")
    client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "")
    refresh_token = os.environ.get("CTRADER_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        print("❌ Need CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_REFRESH_TOKEN in .env")
        return 2

    print("🔄 Exchanging CTRADER_REFRESH_TOKEN for a fresh access token...")
    try:
        data = refresh(client_id, client_secret, refresh_token)
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return 1

    new_access = data["accessToken"]
    new_refresh = data.get("refreshToken")
    expires_in = data.get("expiresIn")

    print("\n✅ New tokens issued:")
    print(f"   CTRADER_ACCESS_TOKEN={new_access}")
    if new_refresh:
        print(f"   CTRADER_REFRESH_TOKEN={new_refresh}")
    if expires_in:
        print(f"   (expires in {int(expires_in)}s ≈ {int(expires_in) / 86400:.1f} days)")

    if args.write:
        env_path = PROJECT_ROOT / ".env"
        write_env_var(env_path, "CTRADER_ACCESS_TOKEN", new_access)
        if new_refresh:
            write_env_var(env_path, "CTRADER_REFRESH_TOKEN", new_refresh)
        print(f"\n💾 Wrote new token(s) to {env_path}")
        print("   Restart the services for it to take effect (as two separate commands):")
        print("   sudo systemctl restart iatis-scheduler")
        print("   sudo systemctl restart iatis-api")
    else:
        print("\n→ Copy the token(s) above into .env, or re-run with --write to update it")
        print("  automatically, then:")
        print("   sudo systemctl restart iatis-scheduler")
        print("   sudo systemctl restart iatis-api")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
