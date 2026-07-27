#!/usr/bin/env python3
"""
scripts/mt5_bridge/mt5_bridge_server.py
-----------------------------------------
Wine-side HTTP bridge for MT5 (2026-07-27, operator request).

RUNS INSIDE WINE, under a Windows Python build, alongside a real MT5
terminal — NOT inside this repo's normal Linux venv. This is the ONLY
piece of IATIS that ever imports `MetaTrader5` (the official package is
Windows-only: a thin wrapper over a DLL that talks to a running MT5
terminal on the same machine). It exposes a minimal, localhost-only HTTP
JSON API that core/data_providers.py._fetch_mt5 (on the Linux side) polls
exactly like it polls Twelve Data or FCS API — plain HTTP GET, no push/
streaming, since MT5's own Python API has no streaming surface even on
native Windows.

Deliberately stdlib-only (http.server, json, urllib) — the Wine Python
environment should need nothing beyond `pip install MetaTrader5`, not a
copy of this repo's whole dependency tree. See docs/MT5_BRIDGE_SETUP.md
for how to install and run this under Wine + systemd.

SECURITY: binds to 127.0.0.1 ONLY by default — this must never be
reachable off-box. MT5_BRIDGE_TOKEN (if set) is checked via the
X-Bridge-Token header as defense-in-depth even though it's localhost-only,
matching the rest of this codebase's auth-everywhere posture.

Endpoints:
    GET /health
        -> {"connected": bool, "terminal": {...}, "account": {...}}
    GET /rates?symbol=EURUSD&timeframe=H1&count=500[&before=<epoch_s>]
        -> {"rates": [{"time": <epoch_s>, "open":.., "high":.., "low":..,
                        "close":.., "volume":..}, ...]}
        `symbol` is IATIS's internal name (e.g. "EURUSD", "XAUUSD") —
        translated to the broker's real symbol name via MT5_SYMBOL_MAP
        (env var, JSON object, e.g. '{"EURUSD": "EURUSD.a"}') before
        calling MT5. Unmapped symbols are passed through unchanged.
        `before` (optional, epoch seconds): page backward in time for
        historical downloads (scripts/download_mt5_history.py) via
        copy_rates_from(); omitted means "most recent N bars" via
        copy_rates_from_pos().

Run:
    wine python.exe mt5_bridge_server.py
Env:
    MT5_BRIDGE_HOST   default "127.0.0.1" — do not change without
                       understanding the security note above.
    MT5_BRIDGE_PORT   default "18812"
    MT5_BRIDGE_TOKEN  optional shared secret, checked against
                       X-Bridge-Token on every request when set.
    MT5_SYMBOL_MAP    optional JSON object, internal name -> broker name.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit(
        "MetaTrader5 package not found. This script must run under the "
        "Wine-hosted Windows Python that has it installed "
        "(wine python.exe -m pip install MetaTrader5) — see "
        "docs/MT5_BRIDGE_SETUP.md. It will never import on native Linux."
    )

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

_BRIDGE_TOKEN = os.environ.get("MT5_BRIDGE_TOKEN", "")
_SYMBOL_MAP: dict[str, str] = json.loads(os.environ.get("MT5_SYMBOL_MAP", "{}") or "{}")


def _broker_symbol(internal: str) -> str:
    return _SYMBOL_MAP.get(internal, internal)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "IATISMT5Bridge/1"

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write(f"[mt5-bridge] {self.address_string()} - {fmt % args}\n")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not _BRIDGE_TOKEN:
            return True
        return self.headers.get("X-Bridge-Token", "") == _BRIDGE_TOKEN

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return

        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._handle_health()
        elif parsed.path == "/rates":
            self._handle_rates(qs)
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_health(self) -> None:
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        self._send_json(200, {
            "connected": bool(terminal is not None and terminal.connected),
            "terminal": terminal._asdict() if terminal else None,
            "account": account._asdict() if account else None,
        })

    def _handle_rates(self, qs: dict[str, list[str]]) -> None:
        symbol = (qs.get("symbol") or [""])[0]
        timeframe = (qs.get("timeframe") or [""])[0]
        count_raw = (qs.get("count") or ["500"])[0]
        before_raw = (qs.get("before") or [None])[0]

        if not symbol:
            self._send_json(400, {"error": "symbol is required"})
            return
        if timeframe not in TIMEFRAME_MAP:
            self._send_json(400, {"error": f"unknown timeframe '{timeframe}', choose from {sorted(TIMEFRAME_MAP)}"})
            return
        try:
            count = int(count_raw)
        except ValueError:
            self._send_json(400, {"error": "count must be an integer"})
            return

        broker_symbol = _broker_symbol(symbol)
        tf = TIMEFRAME_MAP[timeframe]

        if not mt5.symbol_select(broker_symbol, True):
            self._send_json(502, {"error": f"MT5: could not select symbol '{broker_symbol}' (mapped from '{symbol}')"})
            return

        if before_raw:
            date_from = datetime.fromtimestamp(int(before_raw), tz=timezone.utc)
            rates = mt5.copy_rates_from(broker_symbol, tf, date_from, count)
        else:
            rates = mt5.copy_rates_from_pos(broker_symbol, tf, 0, count)

        if rates is None:
            self._send_json(502, {"error": f"MT5: copy_rates failed for '{broker_symbol}': {mt5.last_error()}"})
            return

        # tick_volume (not real_volume — most FX/CFD brokers report 0 for
        # real_volume; tick_volume is the standard proxy MT5's own charts
        # use by default) is what every other IATIS provider's "volume"
        # column represents in practice for these asset classes.
        out = [
            {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["tick_volume"]),
            }
            for r in rates
        ]
        self._send_json(200, {"rates": out})


def main() -> None:
    host = os.environ.get("MT5_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("MT5_BRIDGE_PORT", "18812"))

    if not mt5.initialize():
        sys.exit(f"mt5.initialize() failed: {mt5.last_error()} — is the MT5 terminal running and logged in?")

    print(f"[mt5-bridge] MT5 initialized. Serving on http://{host}:{port} "
          f"(token {'set' if _BRIDGE_TOKEN else 'NOT set — localhost-only trust'})")
    server = ThreadingHTTPServer((host, port), BridgeHandler)
    try:
        server.serve_forever()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
