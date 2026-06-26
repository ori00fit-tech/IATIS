"""
execution/oanda_client.py
--------------------------
OANDA REST API client for paper trading (demo) and live trading.

Setup:
  1. Register at https://www.oanda.com/
  2. Create a Demo (paper trading) account
  3. Go to Manage API Access → Generate token
  4. Add to .env:
       OANDA_API_KEY=your_token_here
       OANDA_ACCOUNT_ID=your_account_id_here
       OANDA_ENVIRONMENT=practice   # or "live" for real money

Symbol mapping:
  IATIS internal → OANDA format
  EURUSD → EUR_USD
  XAUUSD → XAU_USD
  USOIL  → BCO_USD (Brent) or WTICO_USD (WTI)
  BTCUSD → not supported on OANDA → use Binance

Endpoints used:
  GET  /v3/accounts/{id}/summary        — balance, margin, NAV
  GET  /v3/accounts/{id}/openTrades     — open positions
  POST /v3/accounts/{id}/orders         — place order
  PUT  /v3/accounts/{id}/trades/{id}/close — close position
  GET  /v3/instruments/{instrument}/candles — price data
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# ─── Symbol mapping ───────────────────────────────────────────────────────────

IATIS_TO_OANDA: dict[str, str] = {
    # FX Majors
    "EURUSD": "EUR_USD",  "GBPUSD": "GBP_USD",  "USDJPY": "USD_JPY",
    "USDCHF": "USD_CHF",  "AUDUSD": "AUD_USD",  "USDCAD": "USD_CAD",
    "NZDUSD": "NZD_USD",
    # FX Crosses
    "EURJPY": "EUR_JPY",  "GBPJPY": "GBP_JPY",  "AUDJPY": "AUD_JPY",
    "EURGBP": "EUR_GBP",  "EURCHF": "EUR_CHF",
    # Metals
    "XAUUSD": "XAU_USD",  "XAGUSD": "XAG_USD",
    # Energy (OANDA uses Brent or WTI)
    "USOIL":  "WTICO_USD",
    # Indices (OANDA has CFDs)
    "US30":   "US30_USD",  "NAS100": "NAS100_USD",  "SPX500": "SPX500_USD",
    # Crypto — OANDA does NOT support BTC/ETH
    # BTCUSD → use Binance (separate client)
    # ETHUSD → use Binance (separate client)
}

OANDA_TO_IATIS = {v: k for k, v in IATIS_TO_OANDA.items()}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class AccountSummary:
    account_id: str
    currency: str
    balance: float
    nav: float          # Net Asset Value (includes unrealized P&L)
    unrealized_pl: float
    margin_used: float
    margin_available: float
    open_trade_count: int


@dataclass
class TradeOrder:
    symbol: str         # IATIS internal (e.g. "EURUSD")
    direction: str      # "BUY" or "SELL"
    units: int          # positive=buy, negative=sell
    stop_loss: float
    take_profit: float
    client_id: str = ""  # optional tag


@dataclass
class TradeResult:
    success: bool
    trade_id: str = ""
    symbol: str = ""
    direction: str = ""
    units: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    error: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class OpenTrade:
    trade_id: str
    symbol: str         # IATIS internal
    direction: str      # BUY / SELL
    units: int
    entry_price: float
    current_price: float
    unrealized_pl: float
    stop_loss: float
    take_profit: float


# ─── Client ───────────────────────────────────────────────────────────────────

class OandaClient:
    """OANDA REST API v20 client.

    Supports both practice (demo) and live environments.
    Practice environment is strongly recommended for testing.
    """

    PRACTICE_URL = "https://api-fxpractice.oanda.com"
    LIVE_URL = "https://api-fxtrade.oanda.com"

    def __init__(
        self,
        api_key: str | None = None,
        account_id: str | None = None,
        environment: str | None = None,
    ):
        self.api_key = api_key or os.environ.get("OANDA_API_KEY", "")
        self.account_id = account_id or os.environ.get("OANDA_ACCOUNT_ID", "")
        env = environment or os.environ.get("OANDA_ENVIRONMENT", "practice")
        self.base_url = self.PRACTICE_URL if env == "practice" else self.LIVE_URL
        self.environment = env

        if not self.api_key:
            raise ValueError(
                "OANDA_API_KEY not set. "
                "Register at oanda.com → Manage API Access → Generate token. "
                "Add to .env: OANDA_API_KEY=your_token"
            )
        if not self.account_id:
            raise ValueError(
                "OANDA_ACCOUNT_ID not set. "
                "Find in OANDA dashboard → Account → Account ID. "
                "Add to .env: OANDA_ACCOUNT_ID=your_account_id"
            )

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "UNIX",
        })
        logger.info(
            f"OANDA client initialized ({self.environment}) "
            f"account={self.account_id}"
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        resp = self._session.post(url, json=body, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = self._session.put(url, json=body or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ─── Account ──────────────────────────────────────────────────────────────

    def get_account_summary(self) -> AccountSummary:
        """Fetch account balance, NAV, margin info."""
        data = self._get(f"/v3/accounts/{self.account_id}/summary")
        acc = data["account"]
        return AccountSummary(
            account_id=acc["id"],
            currency=acc["currency"],
            balance=float(acc["balance"]),
            nav=float(acc["NAV"]),
            unrealized_pl=float(acc["unrealizedPL"]),
            margin_used=float(acc["marginUsed"]),
            margin_available=float(acc["marginAvailable"]),
            open_trade_count=int(acc.get("openTradeCount", 0)),
        )

    # ─── Positions ────────────────────────────────────────────────────────────

    def get_open_trades(self) -> list[OpenTrade]:
        """Get all currently open trades."""
        data = self._get(f"/v3/accounts/{self.account_id}/openTrades")
        trades = []
        for t in data.get("trades", []):
            instrument = t.get("instrument", "")
            iatis_sym = OANDA_TO_IATIS.get(instrument, instrument)
            units = int(float(t.get("currentUnits", 0)))
            trades.append(OpenTrade(
                trade_id=t["id"],
                symbol=iatis_sym,
                direction="BUY" if units > 0 else "SELL",
                units=abs(units),
                entry_price=float(t.get("price", 0)),
                current_price=float(t.get("currentUnits", 0)),  # approximate
                unrealized_pl=float(t.get("unrealizedPL", 0)),
                stop_loss=float(t.get("stopLossOrder", {}).get("price", 0)),
                take_profit=float(t.get("takeProfitOrder", {}).get("price", 0)),
            ))
        return trades

    def has_open_position(self, symbol: str) -> bool:
        """Check if we already have an open position for this symbol."""
        trades = self.get_open_trades()
        return any(t.symbol == symbol for t in trades)

    # ─── Orders ───────────────────────────────────────────────────────────────

    def place_market_order(self, order: TradeOrder) -> TradeResult:
        """Place a market order with SL and TP.

        Args:
            order: TradeOrder with symbol, direction, units, sl, tp

        Returns:
            TradeResult with success/failure details
        """
        oanda_sym = IATIS_TO_OANDA.get(order.symbol)
        if not oanda_sym:
            return TradeResult(
                success=False,
                symbol=order.symbol,
                error=f"Symbol {order.symbol} not supported on OANDA. "
                      f"For BTCUSD/ETHUSD use Binance client.",
            )

        units = order.units if order.direction == "BUY" else -order.units

        body: dict[str, Any] = {
            "order": {
                "type": "MARKET",
                "instrument": oanda_sym,
                "units": str(units),
                "timeInForce": "FOK",  # Fill or Kill
                "stopLossOnFill": {
                    "price": f"{order.stop_loss:.5f}",
                    "timeInForce": "GTC",
                },
                "takeProfitOnFill": {
                    "price": f"{order.take_profit:.5f}",
                    "timeInForce": "GTC",
                },
            }
        }

        if order.client_id:
            body["order"]["clientExtensions"] = {
                "id": order.client_id[:32],
                "comment": "IATIS",
            }

        try:
            result = self._post(
                f"/v3/accounts/{self.account_id}/orders", body
            )
            fill = result.get("orderFillTransaction", {})
            trade_id = fill.get("tradeOpened", {}).get("tradeID", "")

            logger.info(
                f"OANDA order placed: {order.direction} {order.units} {order.symbol} "
                f"trade_id={trade_id}"
            )

            return TradeResult(
                success=True,
                trade_id=trade_id,
                symbol=order.symbol,
                direction=order.direction,
                units=order.units,
                entry_price=float(fill.get("price", 0)),
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                raw=result,
            )

        except requests.HTTPError as exc:
            error_body = {}
            try:
                error_body = exc.response.json()
            except Exception:
                pass
            error_msg = error_body.get("errorMessage", str(exc))
            logger.error(f"OANDA order failed for {order.symbol}: {error_msg}")
            return TradeResult(
                success=False,
                symbol=order.symbol,
                error=error_msg,
                raw=error_body,
            )

    def close_trade(self, trade_id: str) -> bool:
        """Close an open trade by ID."""
        try:
            self._put(
                f"/v3/accounts/{self.account_id}/trades/{trade_id}/close"
            )
            logger.info(f"OANDA trade {trade_id} closed")
            return True
        except Exception as exc:
            logger.error(f"Failed to close trade {trade_id}: {exc}")
            return False

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def calculate_units(
        self,
        symbol: str,
        risk_usd: float,
        sl_distance: float,
    ) -> int:
        """Calculate position size in units from risk amount.

        OANDA uses 'units' not 'lots':
          1 standard lot FX = 100,000 units
          For XAUUSD: 1 unit = 1 troy ounce

        Args:
            symbol: IATIS symbol (e.g. "EURUSD")
            risk_usd: dollar amount to risk (e.g. 100)
            sl_distance: stop loss distance in price (e.g. 0.0030)

        Returns:
            units (positive integer)
        """
        if sl_distance <= 0:
            return 0

        if symbol in ("XAUUSD",):
            # Gold: 1 unit = 1 oz, pip_value ≈ $1/unit
            units = int(risk_usd / sl_distance)
        elif symbol in ("BTCUSD", "ETHUSD"):
            # Crypto not on OANDA
            return 0
        else:
            # FX: pip_value ≈ $10 per 100,000 units for USD-quoted
            # Simplified: units = risk / sl_distance
            units = int(risk_usd / sl_distance)

        return max(1, min(units, 1_000_000))

    def test_connection(self) -> bool:
        """Test API connectivity. Returns True if connected."""
        try:
            summary = self.get_account_summary()
            logger.info(
                f"OANDA connection OK: "
                f"balance={summary.balance} {summary.currency}, "
                f"NAV={summary.nav}"
            )
            return True
        except Exception as exc:
            logger.error(f"OANDA connection failed: {exc}")
            return False
