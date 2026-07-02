#!/usr/bin/env python3
"""
scripts/ctrader_list_accounts.py
--------------------------------
List every ctidTraderAccountId linked to your CTRADER_ACCESS_TOKEN, with the
environment (live/demo) each one belongs to.

Use this to find the correct value for CTRADER_ACCOUNT_ID. The number shown in
the cTrader desktop/web UI is NOT the ctidTraderAccountId — only this call gives
the internal id the Open API expects.

Run from the IATIS project root:
    python scripts/ctrader_list_accounts.py

Requires (in .env): CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN
(CTRADER_ACCOUNT_ID is NOT needed here — that is what we are trying to discover.)
"""
from __future__ import annotations

import os
import sys
import threading
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

from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAGetAccountListByAccessTokenReq,
)
from twisted.internet import reactor


def main() -> int:
    client_id = os.environ.get("CTRADER_CLIENT_ID", "")
    client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "")
    access_token = os.environ.get("CTRADER_ACCESS_TOKEN", "")
    env = os.environ.get("CTRADER_ENVIRONMENT", "demo")
    host = "demo.ctraderapi.com" if env == "demo" else "live.ctraderapi.com"

    if not (client_id and client_secret and access_token):
        print("❌ Need CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN")
        return 2

    print(f"🔌 Connecting to {host}:5035 (env={env}) to list accounts...")
    client = Client(host, 5035, TcpProtocol)
    done = threading.Event()

    def on_connected(_c: object) -> None:
        req = ProtoOAApplicationAuthReq()
        req.clientId = client_id
        req.clientSecret = client_secret
        d = client.send(req, responseTimeoutInSeconds=15)
        d.addCallback(on_app_auth)
        d.addErrback(on_fail)

    def on_app_auth(_res: object) -> None:
        req = ProtoOAGetAccountListByAccessTokenReq()
        req.accessToken = access_token
        d = client.send(req, responseTimeoutInSeconds=15)
        d.addCallback(on_accounts)
        d.addErrback(on_fail)

    def on_accounts(message: object) -> None:
        res = Protobuf.extract(message)
        name = res.__class__.__name__
        if name != "ProtoOAGetAccountListByAccessTokenRes":
            print(f"❌ Unexpected response: {name} "
                  f"(code={getattr(res, 'errorCode', '')} "
                  f"{getattr(res, 'description', '')})")
            done.set()
            return
        accounts = list(getattr(res, "ctidTraderAccount", []))
        if not accounts:
            print("⚠️ Token is valid but has NO linked trader accounts.")
        else:
            print(f"\n✅ {len(accounts)} account(s) linked to this token:\n")
            for a in accounts:
                is_live = getattr(a, "isLive", False)
                login = getattr(a, "traderLogin", "")
                print(f"   ctidTraderAccountId = {a.ctidTraderAccountId}"
                      f"   |  env = {'LIVE' if is_live else 'DEMO'}"
                      f"   |  traderLogin (shown in UI) = {login}")
            print("\n→ Put the id whose env matches CTRADER_ENVIRONMENT into "
                  "CTRADER_ACCOUNT_ID.")
        done.set()

    def on_fail(failure: object) -> None:
        msg = (failure.getErrorMessage() if hasattr(failure, "getErrorMessage")
               else str(failure))
        print(f"❌ Failed: {msg}")
        done.set()

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(lambda _c, reason: None)

    threading.Thread(
        target=reactor.run, kwargs={"installSignalHandlers": False}, daemon=True
    ).start()
    time.sleep(0.1)
    reactor.callFromThread(client.startService)

    if not done.wait(timeout=30):
        print("❌ Timed out.")
        return 1
    reactor.callFromThread(client.stopService)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
