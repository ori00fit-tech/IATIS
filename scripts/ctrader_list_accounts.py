#!/usr/bin/env python3
"""
scripts/ctrader_list_accounts.py
--------------------------------
List every ctidTraderAccountId linked to your CTRADER_ACCESS_TOKEN, with the
environment (live/demo) each one belongs to.

Use this to find the correct value for CTRADER_ACCOUNT_ID. The number shown in
the cTrader desktop/web UI is NOT the ctidTraderAccountId — only this call gives
the internal id the Open API expects. GET /ctrader/callback (the OAuth web
flow) also runs this same discovery automatically and logs the result — this
script is the manual/CLI alternative.

Run from the IATIS project root:
    python scripts/ctrader_list_accounts.py

Requires (in .env): CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN
(CTRADER_ACCOUNT_ID is NOT needed here — that is what we are trying to discover.)
"""
from __future__ import annotations

import os
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

from integrations.ctrader.account import CTraderAccountDiscoveryError, discover_accounts


def main() -> int:
    client_id = os.environ.get("CTRADER_CLIENT_ID", "")
    client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "")
    access_token = os.environ.get("CTRADER_ACCESS_TOKEN", "")
    env = os.environ.get("CTRADER_ENVIRONMENT", "demo")

    if not (client_id and client_secret and access_token):
        print("❌ Need CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN")
        return 2

    print(f"🔌 Connecting (env={env}) to list accounts...")
    try:
        accounts = discover_accounts(client_id, client_secret, access_token, env)
    except CTraderAccountDiscoveryError as exc:
        print(f"❌ Failed: {exc}")
        return 1

    if not accounts:
        print("⚠️ Token is valid but has NO linked trader accounts.")
        return 0

    print(f"\n✅ {len(accounts)} account(s) linked to this token:\n")
    for a in accounts:
        print(f"   ctidTraderAccountId = {a['ctid_trader_account_id']}"
              f"   |  env = {'LIVE' if a['is_live'] else 'DEMO'}"
              f"   |  traderLogin (shown in UI) = {a['trader_login']}")
    print("\n→ Put the id whose env matches CTRADER_ENVIRONMENT into CTRADER_ACCOUNT_ID.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
