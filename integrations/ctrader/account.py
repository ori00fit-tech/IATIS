"""
integrations/ctrader/account.py
---------------------------------
discover_accounts() — the ProtoOAApplicationAuthReq -> ProtoOAGetAccountListByAccessTokenReq
sequence that resolves a raw access_token to the real ctidTraderAccountId
values it's linked to. Same Twisted/reactor bootstrap
scripts/ctrader_list_accounts.py has always used, moved into a reusable,
timeout-bounded function (the script becomes a thin CLI wrapper printing
this function's result).

This is a short-lived, throwaway connection (connect, ask, disconnect) —
NOT the persistent, singleton CTraderClient used for trading
(execution/ctrader_client.py, core/data_providers.py::get_shared_ctrader_client()).
It does not take the cross-process session lock and is not subject to
"only one session per account+app" in any way that matters for a
few-second, one-shot discovery call.

Requires ctrader_open_api/twisted (requirements-ctrader.txt) — imported
lazily inside the function body, matching execution/ctrader_client.py's
own established convention, so importing this MODULE never fails even
when that optional install is absent.
"""
from __future__ import annotations

import threading
import time


class CTraderAccountDiscoveryError(Exception):
    """Raised when the discovery round trip fails or times out."""


def discover_accounts(
    client_id: str,
    client_secret: str,
    access_token: str,
    environment: str = "demo",
    timeout: float = 20.0,
) -> list[dict]:
    """Returns [{"ctid_trader_account_id": int, "is_live": bool,
    "trader_login": str}, ...] linked to access_token. Raises
    CTraderAccountDiscoveryError on failure/timeout."""
    from ctrader_open_api import Client, Protobuf, TcpProtocol
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq,
        ProtoOAGetAccountListByAccessTokenReq,
    )
    from twisted.internet import reactor

    host = "demo.ctraderapi.com" if environment == "demo" else "live.ctraderapi.com"
    client = Client(host, 5035, TcpProtocol)
    done = threading.Event()
    result: dict = {"accounts": None, "error": None}

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
        if res.__class__.__name__ != "ProtoOAGetAccountListByAccessTokenRes":
            result["error"] = (
                f"Unexpected response: {res.__class__.__name__} "
                f"(code={getattr(res, 'errorCode', '')} {getattr(res, 'description', '')})"
            )
            done.set()
            return
        result["accounts"] = [
            {
                "ctid_trader_account_id": a.ctidTraderAccountId,
                "is_live": bool(getattr(a, "isLive", False)),
                "trader_login": getattr(a, "traderLogin", ""),
            }
            for a in getattr(res, "ctidTraderAccount", [])
        ]
        done.set()

    def on_fail(failure: object) -> None:
        msg = failure.getErrorMessage() if hasattr(failure, "getErrorMessage") else str(failure)
        result["error"] = msg
        done.set()

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(lambda _c, reason: None)

    reactor_thread_started = reactor.running
    if not reactor_thread_started:
        threading.Thread(target=reactor.run, kwargs={"installSignalHandlers": False}, daemon=True).start()
        time.sleep(0.1)
    reactor.callFromThread(client.startService)

    ok = done.wait(timeout=timeout)
    try:
        reactor.callFromThread(client.stopService)
    except Exception:
        pass

    if not ok:
        raise CTraderAccountDiscoveryError(f"Timed out after {timeout}s.")
    if result["error"]:
        raise CTraderAccountDiscoveryError(result["error"])
    return result["accounts"] or []
