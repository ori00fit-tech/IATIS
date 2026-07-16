#!/usr/bin/env python3
"""
scripts/ctrader_inspect_symbols.py
----------------------------------
Diagnostic for two live findings:
  1. Which broker symbol names correspond to the IATIS index symbols
     (US30 / NAS100 / SPX500) — the current mapping (DJ30/NAS100/SP500) is wrong.
  2. Whether ProtoOASymbolByIdReq returns real lotSize/min/step for the symbols
     we care about (so place_market_order uses live specs, not the fallback).

It connects, lists all symbols, prints candidates that look like indices, then
fetches full specs for a chosen set and prints lotSize/digits/min/step.

Run from the IATIS project root:
    python scripts/ctrader_inspect_symbols.py
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
    ProtoOAAccountAuthReq,
    ProtoOASymbolsListReq,
    ProtoOASymbolByIdReq,
)
from twisted.internet import reactor

# Broker names we already know exist for the non-index IATIS symbols, plus a few
# spec probes. Index names are discovered, not assumed.
KNOWN_PROBE_NAMES = ["EURUSD", "XAUUSD", "XAGUSD", "XTIUSD", "BTCUSD", "ETHUSD"]
INDEX_HINTS = ["30", "US30", "DOW", "DJ", "NAS", "NDX", "US100", "SPX", "SP", "US500", "500"]


def main() -> int:
    cid = os.environ.get("CTRADER_CLIENT_ID", "")
    csec = os.environ.get("CTRADER_CLIENT_SECRET", "")
    token = os.environ.get("CTRADER_ACCESS_TOKEN", "")
    acc = int(os.environ.get("CTRADER_ACCOUNT_ID", 0))
    env = os.environ.get("CTRADER_ENVIRONMENT", "demo")
    host = "demo.ctraderapi.com" if env == "demo" else "live.ctraderapi.com"

    if not (cid and csec and token and acc):
        print("❌ Need CTRADER_CLIENT_ID/SECRET/ACCESS_TOKEN/ACCOUNT_ID in .env")
        return 2

    client = Client(host, 5035, TcpProtocol)
    done = threading.Event()
    name_to_id: dict[str, int] = {}

    def on_connected(_c: object) -> None:
        r = ProtoOAApplicationAuthReq()
        r.clientId = cid
        r.clientSecret = csec
        client.send(r, responseTimeoutInSeconds=15).addCallbacks(on_app, on_fail)

    def on_app(_res: object) -> None:
        r = ProtoOAAccountAuthReq()
        r.ctidTraderAccountId = acc
        r.accessToken = token
        client.send(r, responseTimeoutInSeconds=15).addCallbacks(on_acc, on_fail)

    def on_acc(_res: object) -> None:
        r = ProtoOASymbolsListReq()
        r.ctidTraderAccountId = acc
        client.send(r, responseTimeoutInSeconds=20).addCallbacks(on_symbols, on_fail)

    def on_symbols(message: object) -> None:
        res = Protobuf.extract(message)
        for s in getattr(res, "symbol", []):
            name_to_id[s.symbolName] = s.symbolId

        print(f"\n📊 {len(name_to_id)} symbols total.\n")
        print("🔎 Index-like candidates (match these to US30/NAS100/SPX500):")
        for nm in sorted(name_to_id):
            up = nm.upper().replace(" ", "").replace(".", "")
            if any(h in up for h in ["US30", "DOW", "DJ30", "WALL", "NAS", "NDX",
                                     "US100", "USTEC", "SPX", "US500", "SP500", "SPX500"]):
                print(f"    {nm}  (id={name_to_id[nm]})")

        # Probe the default set, plus any broker symbol names passed on the
        # command line (e.g. index names discovered above:
        #   python -m scripts.ctrader_inspect_symbols US30 US500 USTEC
        # ). Unknown names are reported so a typo is obvious.
        requested = KNOWN_PROBE_NAMES + [a for a in sys.argv[1:] if not a.startswith("-")]
        probe, unknown = [], []
        for n in requested:
            (probe if n in name_to_id else unknown).append(n)
        # de-dup, preserve order
        probe = list(dict.fromkeys(probe))
        if unknown:
            print(f"⚠️  Not on broker (skipped): {unknown}")
        ids = [name_to_id[n] for n in probe]
        print(f"\n🧾 Fetching specs for: {probe}")
        r = ProtoOASymbolByIdReq()
        r.ctidTraderAccountId = acc
        r.symbolId.extend(ids)
        client.send(r, responseTimeoutInSeconds=20).addCallbacks(on_specs, on_fail)

    def on_specs(message: object) -> None:
        res = Protobuf.extract(message)
        name = res.__class__.__name__
        if name != "ProtoOASymbolByIdRes":
            print(f"❌ Unexpected specs response: {name} "
                  f"(code={getattr(res, 'errorCode', '')} "
                  f"{getattr(res, 'description', '')})")
            done.set()
            return
        id_to_name = {v: k for k, v in name_to_id.items()}
        print("\n🧾 Live specs (this proves lotSize is available):")
        for s in getattr(res, "symbol", []):
            print(f"    {id_to_name.get(s.symbolId, s.symbolId):10} "
                  f"lotSize={getattr(s, 'lotSize', 0):>12} "
                  f"digits={getattr(s, 'digits', 0)} "
                  f"pip={getattr(s, 'pipPosition', 0)} "
                  f"minVol={getattr(s, 'minVolume', 0)} "
                  f"stepVol={getattr(s, 'stepVolume', 0)}")
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

    if not done.wait(timeout=35):
        print("❌ Timed out.")
        return 1
    reactor.callFromThread(client.stopService)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
