"""
execution/dukascopy_jforex_client.py
---------------------------------------
Live/near-live order execution via the local dukas-api bridge — see
core/data_providers.py's Dukascopy JForex section and
docs/DUKASCOPY_JFOREX_BRIDGE_SETUP.md for the full architecture. This
module talks ONLY to the bridge's REST endpoints; it never imports the
JForex SDK or touches a live session directly.

THIRD-PARTY, UNOFFICIAL BRIDGE. Two real, load-bearing gaps in its
documented API (verified against its README, not assumed) shaped the
design here:

1. No documented account-balance/equity endpoint exists, so this client
   CANNOT do risk_pct-based position sizing the way CTraderClient/
   OandaClient do (both need a live balance for that formula). Position
   sizing here is DELIBERATELY a fixed quantity supplied by the caller
   (execution/trade_executor.py, from config.yaml's
   execution.dukascopy_jforex_fixed_quantity) — an honest fit for what
   the bridge actually exposes, not a guess dressed up as risk_pct math
   against a number that would silently go stale as the account's real
   equity moves.

2. No documented "list all open positions" endpoint exists either — only
   a single-position lookup by a client-supplied clientOrderID. has_
   open_position() below is therefore a best-effort check keyed on a
   DETERMINISTIC per-symbol clientOrderID (IATIS_{symbol}), not a true
   "is anything open for this symbol" check.

A THIRD gap surfaced while designing order placement, and is handled by
explicit, loudly-flagged assumption rather than silence:
- The documented PUT /api/v1/position (stop-loss/take-profit) endpoint
  takes takeProfitPips/stopLossPips — PIPS, not price levels — but no
  per-instrument pip-size convention is documented anywhere found. The
  pip sizes below (_PIP_SIZE) are the standard, widely-used FX/CFD
  convention (0.0001 for most pairs, 0.01 for JPY crosses) — NOT
  independently verified against a real running dukas-api instance from
  this sandbox (no network access, no real bridge to test against).
- The "quantity" field's exact unit (lots vs. base-currency units vs.
  something else) is likewise undocumented; treated here the same way
  CTraderOrder.volume is treated elsewhere in this codebase (an opaque
  number the caller supplies), not converted or scaled.

MANDATORY BEFORE ANY REAL SCHEDULER-LOOP USE (see
docs/DUKASCOPY_JFOREX_BRIDGE_SETUP.md's verification section): place
exactly ONE real order on the demo account through this client and
manually inspect its stop-loss, take-profit, and position size in the
JForex terminal UI. If any of the three assumptions above is wrong, this
is where it will be caught — before dukascopy_jforex_enabled ever gates
a real, unattended scheduler run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# Standard FX/CFD pip-size convention — UNVERIFIED against a real
# dukas-api instance (see module docstring). JPY crosses use 0.01;
# every other FX pair here uses 0.0001. Metals/crypto pip conventions
# vary widely by broker and are NOT confidently known for this bridge —
# entries below are a best-effort placeholder, flagged loudly so nobody
# mistakes them for a verified fact.
_PIP_SIZE: dict[str, float] = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDCHF": 0.0001, "AUDUSD": 0.0001,
    "USDCAD": 0.0001, "NZDUSD": 0.0001, "EURGBP": 0.0001, "EURCHF": 0.0001,
    "USDJPY": 0.01, "EURJPY": 0.01, "GBPJPY": 0.01, "AUDJPY": 0.01,
    # Best-effort, NOT verified — confirm against a real bridge order
    # before trusting SL/TP placement on these two.
    "XAUUSD": 0.01, "XAGUSD": 0.001,
}


class DukascopyJForexError(Exception):
    """Raised on a bridge-communication failure. Never carries anything
    resembling the operator's JForex credentials — this client only ever
    talks to the local bridge, never Dukascopy's own login servers."""


@dataclass
class DukascopyJForexOrder:
    symbol: str
    direction: str  # "BUY" | "SELL"
    quantity: float
    stop_loss: float
    take_profit: float
    client_order_id: str


@dataclass
class DukascopyJForexOrderResult:
    success: bool
    dukas_order_id: str = ""
    fill_price: float = 0.0
    error: str = ""


class DukascopyJForexClient:
    """Talks to the local dukas-api bridge's position endpoints. Never
    imports the JForex SDK or opens a session itself — see module header."""

    def __init__(self, base_url: str | None = None, environment: str | None = None, timeout: float = 15.0):
        url = base_url or os.environ.get("DUKASCOPY_JFOREX_BRIDGE_URL")
        if not url:
            raise DukascopyJForexError("DUKASCOPY_JFOREX_BRIDGE_URL is not set.")
        self.base_url = url.rstrip("/")
        # Self-reported, not verified against the bridge (dukas-api
        # documents no endpoint to query which server it's configured
        # for) — the same pattern CTraderClient/OandaClient already use
        # for their own `environment` attribute (CTRADER_ENVIRONMENT/
        # OANDA_ENVIRONMENT env vars, also self-reported, not queried
        # live). Must match what the bridge's own application.properties
        # actually points at — kept in sync by the operator.
        self.environment = environment or os.environ.get("DUKASCOPY_JFOREX_ENVIRONMENT", "demo")
        self.timeout = timeout
        logger.info(f"DukascopyJForexClient initialized (env={self.environment}) -> {self.base_url}")

    def _client_order_id(self, symbol: str) -> str:
        return f"IATIS_{symbol}"

    def has_open_position(self, symbol: str) -> bool:
        """Best-effort duplicate-position check — see module header for
        why this can't be a true "any open position for this symbol"
        check. Degrades to False (no known open position) on any lookup
        failure or 404, so a bridge hiccup can never permanently block
        trading — a false negative here is recoverable (worst case: a
        second order for the same symbol, still bounded by every other
        safety check in execute_from_report), a false positive would
        silently stop trading a symbol forever, which is the worse
        failure mode to avoid."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/position",
                params={"clientOrderID": self._client_order_id(symbol)},
                timeout=self.timeout,
            )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"Dukascopy JForex bridge: position lookup failed for {symbol}: {exc}")
            return False
        if not data:
            return False
        status = str(data.get("status", "")).upper()
        return status not in ("", "CLOSED", "REJECTED")

    def place_market_order(self, order: DukascopyJForexOrder) -> DukascopyJForexOrderResult:
        payload = {
            "instID": order.symbol,
            "clientOrderID": order.client_order_id,
            "orderSide": order.direction,
            "orderType": "MARKET",
            "quantity": order.quantity,
        }
        try:
            resp = requests.post(f"{self.base_url}/api/v1/position", json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            return DukascopyJForexOrderResult(success=False, error=f"Dukascopy JForex bridge: {exc}")

        if not data.get("orderSuccess"):
            return DukascopyJForexOrderResult(
                success=False, error=str(data.get("rejectReason") or "order rejected (no reason given)")
            )

        fill_price = float(data.get("fillPrice", 0.0) or 0.0)
        dukas_order_id = str(data.get("dukasOrderID", ""))

        # SL/TP are attached as a follow-up PUT — dukas-api's documented
        # POST /api/v1/position has no SL/TP fields of its own. A failure
        # here must NOT be silently swallowed: an open, unprotected
        # position is a real risk even on a demo account.
        try:
            self._attach_stop_loss_take_profit(order, fill_price)
        except Exception as exc:
            logger.error(
                f"⚠️  Dukascopy JForex: order {dukas_order_id} FILLED but SL/TP "
                f"attach failed: {exc} — position for {order.symbol} is open "
                f"WITHOUT protection. Check the JForex terminal manually."
            )

        return DukascopyJForexOrderResult(success=True, dukas_order_id=dukas_order_id, fill_price=fill_price)

    def _attach_stop_loss_take_profit(self, order: DukascopyJForexOrder, fill_price: float) -> None:
        pip_size = _PIP_SIZE.get(order.symbol)
        if pip_size is None or fill_price <= 0:
            raise DukascopyJForexError(
                f"no known pip size for {order.symbol!r} (or invalid fill price {fill_price}) "
                f"— refusing to guess a stop-loss/take-profit distance"
            )
        sl_pips = abs(fill_price - order.stop_loss) / pip_size
        tp_pips = abs(order.take_profit - fill_price) / pip_size
        resp = requests.put(
            f"{self.base_url}/api/v1/position",
            json={
                "clientOrderID": order.client_order_id,
                "stopLossPips": round(sl_pips, 1),
                "takeProfitPips": round(tp_pips, 1),
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
