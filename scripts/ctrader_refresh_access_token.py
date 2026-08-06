#!/usr/bin/env python3
"""
scripts/ctrader_refresh_access_token.py
-----------------------------------------
Exchange a saved CTRADER_REFRESH_TOKEN for a fresh CTRADER_ACCESS_TOKEN.

cTrader Open API access tokens expire (~30 days per cTrader's own docs) —
this is the live symptom `execution/ctrader_client.py` surfaces as
`CH_ACCESS_TOKEN_INVALID — Access token expired` on account auth. As of
the cTrader OAuth web-flow rebuild, `execution/ctrader_client.py` also
refreshes automatically on that exact rejection, and `scheduler.py`
refreshes proactively before expiry — this script remains the manual,
operator-run alternative (e.g. to force a refresh outside those paths, or
when running the CLI is more convenient than the browser flow at
`/ctrader/authorize`). Thin wrapper over integrations/ctrader/oauth.py,
which both this script and the automatic paths share.

Run from the IATIS project root:
    python scripts/ctrader_refresh_access_token.py            # print only, changes nothing
    python scripts/ctrader_refresh_access_token.py --write    # also updates .env in place

Requires (in .env): CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET,
CTRADER_REFRESH_TOKEN
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from integrations.ctrader.oauth import CTraderOAuthError, TOKEN_URL, refresh_tokens as refresh, write_env_var


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--write", action="store_true",
        help="Update CTRADER_ACCESS_TOKEN/CTRADER_REFRESH_TOKEN/"
             "CTRADER_ACCESS_TOKEN_EXPIRY in .env in place "
             "(default: print only, change nothing on disk).",
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
    except CTraderOAuthError as exc:
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
        if expires_in:
            write_env_var(env_path, "CTRADER_ACCESS_TOKEN_EXPIRY", str(time.time() + float(expires_in)))
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
