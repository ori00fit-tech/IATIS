"""
fundamentals/taapi_client.py
------------------------------
TAAPI.io indicator client — NOT wired into any engine.

Every engine already computes its own indicators locally via pandas
(EMA/ADX/etc. — see engines/nnfx_engine.py, confluence/mtf_confirmation.py).
This client exists as available infrastructure only, the same way
requirements-ctrader.txt exists without being required for most flows —
add a real consumer deliberately later if one is ever needed, rather than
routing live indicator math through a third-party API by default.

Confirmed important constraint (verified 2026-07-14 against the real
endpoint, not the docs): the free tier's rate limit is extremely tight —
a second call made seconds after the first already returned
"You have exceeded your request limit (TAAPI.IO rate-limit)!". This rules
out real-time, multi-symbol/multi-timeframe engine consumption on the
free tier even if a use case arises.

API: https://api.taapi.io/{indicator}?secret=...&exchange=...&symbol=...
     &interval=...  (symbol format is ccxt-style, e.g. "BTC/USDT")
Response: {"value": <float>} on success, {"error": "..."} on failure
(including rate-limit errors, which arrive as HTTP 200 with an error body,
not a 429 — checked explicitly below rather than relying on status code).
"""
from __future__ import annotations

import os

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.taapi.io"


def get_indicator(
    indicator: str,
    symbol: str,
    exchange: str = "binance",
    interval: str = "1h",
    **params: object,
) -> float | None:
    """Fetch a single indicator value. Returns None on any failure
    (missing key, rate limit, unknown indicator/symbol) — never raises,
    since this is optional infrastructure, not part of any decision path.
    """
    api_key = os.environ.get("TAAPI_API_KEY", "")
    if not api_key:
        return None

    query = {"secret": api_key, "exchange": exchange, "symbol": symbol, "interval": interval, **params}
    try:
        resp = requests.get(f"{BASE_URL}/{indicator}", params=query, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning(f"TAAPI.io request failed for {indicator} {symbol}: {exc}")
        return None

    if "error" in data:
        logger.warning(f"TAAPI.io error for {indicator} {symbol}: {data['error']}")
        return None

    value = data.get("value")
    return float(value) if value is not None else None
