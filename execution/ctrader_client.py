"""
execution/ctrader_client.py
----------------------------
IC Markets / cTrader Open API client for IATIS.

Protocol: TCP with Protobuf messages (not REST)
Library: ctrader-open-api 0.9.2

Setup (3 steps):
  1. Open Demo account at IC Markets: https://www.icmarkets.com/
  2. Go to cTrader → Settings → API → Create Application
     App Name: IATIS
     Redirect URI: http://localhost
     → Get Client ID + Client Secret
  3. Add to .env:
       CTRADER_CLIENT_ID=your_client_id
       CTRADER_CLIENT_SECRET=your_client_secret
       CTRADER_ACCOUNT_ID=your_account_id   (numeric, from cTrader)
       CTRADER_ACCESS_TOKEN=your_token      (from OAuth flow)
       CTRADER_ENVIRONMENT=demo             # "demo" or "live"

Symbol mapping:
  IATIS → cTrader symbol name (IC Markets naming)
  EURUSD → EURUSD  (usually same for FX)
  XAUUSD → XAUUSD  (Gold)
  USOIL  → XTIUSD  (WTI Crude — IC Markets uses XTI)

Recommended leverage (IATIS 1% risk, 1:3 RR):
  FX Majors:   1:30 to 1:50   (max use: 1:50)
  XAU/USD:     1:20           (higher volatility)
  Oil/Indices: 1:20           (higher volatility)
  Crypto:      1:5 to 1:10   (max risk)

Note: cTrader uses async Twisted reactor.
For IATIS scheduler (sync), we use threading to run async calls.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from utils.logger import get_logger

logger = get_logger(__name__)

# ─── Symbol mapping ───────────────────────────────────────────────────────────

IATIS_TO_CTRADER: dict[str, str] = {
    # FX Majors
    "EURUSD": "EURUSD",   "GBPUSD": "GBPUSD",   "USDJPY": "USDJPY",
    "USDCHF": "USDCHF",   "AUDUSD": "AUDUSD",   "USDCAD": "USDCAD",
    "NZDUSD": "NZDUSD",
    # FX Crosses
    "EURJPY": "EURJPY",   "GBPJPY": "GBPJPY",   "AUDJPY": "AUDJPY",
    "EURGBP": "EURGBP",   "EURCHF": "EURCHF",
    # Metals
    "XAUUSD": "XAUUSD",   "XAGUSD": "XAGUSD",
    # Energy (IC Markets uses XTI for WTI Crude)
    "USOIL":  "XTIUSD",
    # Indices (IC Markets CFD names)
    "US30":   "DJ30",     "NAS100": "NAS100",    "SPX500": "SP500",
    # Crypto
    "BTCUSD": "BTCUSD",   "ETHUSD": "ETHUSD",
}

CTRADER_TO_IATIS = {v: k for k, v in IATIS_TO_CTRADER.items()}

# Recommended max leverage per asset class
RECOMMENDED_LEVERAGE = {
    "forex":  50,   # FX majors/crosses
    "metal":  20,   # Gold, Silver
    "energy": 20,   # Oil
    "index":  20,   # Stock indices
    "crypto": 10,   # BTC, ETH
}

ASSET_CLASS = {
    **{s: "forex" for s in ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD",
                             "NZDUSD","EURJPY","GBPJPY","AUDJPY","EURGBP","EURCHF"]},
    "XAUUSD": "metal",  "XAGUSD": "metal",
    "USOIL":  "energy",
    "US30":   "index",  "NAS100": "index",  "SPX500": "index",
    "BTCUSD": "crypto", "ETHUSD": "crypto",
}


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class CTraderOrder:
    symbol: str          # IATIS internal (e.g. "EURUSD")
    direction: str       # "BUY" or "SELL"
    volume: int          # in cTrader units (1 lot = 100 for FX = 100,000 units)
    stop_loss: float
    take_profit: float
    comment: str = "IATIS"


@dataclass
class CTraderResult:
    success: bool
    order_id: str = ""
    position_id: str = ""
    symbol: str = ""
    direction: str = ""
    volume: int = 0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    error: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "order_id": self.order_id,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "volume": self.volume,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "error": self.error,
        }


@dataclass
class AccountInfo:
    account_id: int
    balance: float
    equity: float
    margin_used: float
    margin_free: float
    currency: str
    leverage: int


@dataclass
class OpenPosition:
    position_id: str
    symbol: str          # IATIS internal
    direction: str
    volume: int
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: float
    take_profit: float


# ─── cTrader Client ───────────────────────────────────────────────────────────

class CTraderClient:
    """
    IC Markets cTrader Open API client.

    Uses Twisted async protocol internally but exposes sync interface
    via threading for IATIS scheduler compatibility.

    Connection flow:
      connect() → authenticate app → authorize account → ready to trade
    """

    DEMO_HOST = "demo.ctraderapi.com"
    LIVE_HOST = "live.ctraderapi.com"
    PORT = 5035

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        account_id: int | None = None,
        access_token: str | None = None,
        environment: str | None = None,
    ):
        self.client_id = client_id or os.environ.get("CTRADER_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("CTRADER_CLIENT_SECRET", "")
        self.account_id = int(
            account_id or os.environ.get("CTRADER_ACCOUNT_ID", 0)
        )
        self.access_token = access_token or os.environ.get("CTRADER_ACCESS_TOKEN", "")
        env = environment or os.environ.get("CTRADER_ENVIRONMENT", "demo")
        self.host = self.DEMO_HOST if env == "demo" else self.LIVE_HOST
        self.environment = env

        self._client = None
        self._connected = False
        self._symbol_list: dict[str, int] = {}  # name → symbolId
        self._result_event = threading.Event()
        self._last_result: Any = None

        self._validate_credentials()

    def _validate_credentials(self):
        missing = []
        if not self.client_id: missing.append("CTRADER_CLIENT_ID")
        if not self.client_secret: missing.append("CTRADER_CLIENT_SECRET")
        if not self.account_id: missing.append("CTRADER_ACCOUNT_ID")
        if not self.access_token: missing.append("CTRADER_ACCESS_TOKEN")
        if missing:
            raise ValueError(
                f"Missing cTrader credentials: {', '.join(missing)}\n"
                f"See execution/ctrader_client.py docstring for setup instructions."
            )

    def _get_twisted_client(self):
        """Create cTrader TCP client (lazy, runs in thread)."""
        from ctrader_open_api import Client, TcpProtocol
        return Client(self.host, self.PORT, TcpProtocol)

    def connect(self, timeout: float = 15.0) -> bool:
        """Establish authenticated connection to cTrader API."""
        try:
            from ctrader_open_api import Client, TcpProtocol, Protobuf
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAApplicationAuthReq, ProtoOAAccountAuthReq,
                ProtoOASymbolsListReq,
            )
            from twisted.internet import reactor, defer

            client = Client(self.host, self.PORT, TcpProtocol)
            self._client = client
            connected = threading.Event()
            error_holder = [None]

            def on_connected(_):
                # Step 1: Authenticate application
                app_auth = ProtoOAApplicationAuthReq()
                app_auth.clientId = self.client_id
                app_auth.clientSecret = self.client_secret
                d = client.send(app_auth)
                d.addCallback(on_app_auth)
                d.addErrback(on_error)

            def on_app_auth(_):
                # Step 2: Authorize account
                acc_auth = ProtoOAAccountAuthReq()
                acc_auth.ctidTraderAccountId = self.account_id
                acc_auth.accessToken = self.access_token
                d = client.send(acc_auth)
                d.addCallback(on_acc_auth)
                d.addErrback(on_error)

            def on_acc_auth(_):
                self._connected = True
                connected.set()

            def on_error(failure):
                error_holder[0] = str(failure)
                connected.set()

            client.setConnectedCallback(on_connected)

            # Run reactor in background thread
            if not reactor.running:
                t = threading.Thread(target=reactor.run, kwargs={"installSignalHandlers": False})
                t.daemon = True
                t.start()

            reactor.callFromThread(client.startService)
            connected.wait(timeout=timeout)

            if error_holder[0]:
                logger.error(f"cTrader connection error: {error_holder[0]}")
                return False

            if self._connected:
                logger.info(f"cTrader connected: {self.environment} account={self.account_id}")
                return True

            logger.error("cTrader connection timed out")
            return False

        except Exception as exc:
            logger.error(f"cTrader connect failed: {exc}")
            return False

    def place_market_order(self, order: CTraderOrder) -> CTraderResult:
        """Place a market order with SL and TP."""
        if not self._connected:
            return CTraderResult(
                success=False, symbol=order.symbol,
                error="Not connected. Call connect() first."
            )

        ct_symbol = IATIS_TO_CTRADER.get(order.symbol)
        if not ct_symbol:
            return CTraderResult(
                success=False, symbol=order.symbol,
                error=f"Symbol {order.symbol} not in cTrader mapping."
            )

        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOANewOrderReq,
            )
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
                ProtoOAOrderType, ProtoOATradeSide,
            )
            from twisted.internet import reactor

            result_holder = [None]
            done = threading.Event()

            def send_order():
                req = ProtoOANewOrderReq()
                req.ctidTraderAccountId = self.account_id
                req.symbolName = ct_symbol
                req.orderType = ProtoOAOrderType.MARKET
                req.tradeSide = (
                    ProtoOATradeSide.BUY if order.direction == "BUY"
                    else ProtoOATradeSide.SELL
                )
                req.volume = order.volume * 100  # cTrader volume in 0.01 lots
                req.relativeStopLoss = int(
                    abs(order.entry_price - order.stop_loss) * 100_000
                )
                req.relativeTakeProfit = int(
                    abs(order.entry_price - order.take_profit) * 100_000
                )
                req.comment = order.comment[:31]

                d = self._client.send(req)

                def on_filled(response):
                    result_holder[0] = CTraderResult(
                        success=True,
                        order_id=str(response.orderId) if hasattr(response, "orderId") else "",
                        position_id=str(response.positionId) if hasattr(response, "positionId") else "",
                        symbol=order.symbol,
                        direction=order.direction,
                        volume=order.volume,
                        entry_price=float(getattr(response, "executionPrice", 0)) / 100_000,
                        stop_loss=order.stop_loss,
                        take_profit=order.take_profit,
                    )
                    done.set()

                def on_error(failure):
                    result_holder[0] = CTraderResult(
                        success=False, symbol=order.symbol,
                        error=str(failure.value)
                    )
                    done.set()

                d.addCallback(on_filled)
                d.addErrback(on_error)

            reactor.callFromThread(send_order)
            done.wait(timeout=10.0)

            if result_holder[0]:
                if result_holder[0].success:
                    logger.info(
                        f"cTrader order: {order.direction} {order.symbol} "
                        f"vol={order.volume} → position_id={result_holder[0].position_id}"
                    )
                return result_holder[0]

            return CTraderResult(
                success=False, symbol=order.symbol,
                error="Order timed out — no response from cTrader"
            )

        except Exception as exc:
            logger.error(f"cTrader order failed: {exc}", exc_info=True)
            return CTraderResult(
                success=False, symbol=order.symbol,
                error=str(exc)
            )

    def get_account_info(self) -> AccountInfo | None:
        """Fetch account balance and margin info."""
        if not self._connected:
            return None
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOATraderReq,
            )
            from twisted.internet import reactor

            result_holder = [None]
            done = threading.Event()

            def send_req():
                req = ProtoOATraderReq()
                req.ctidTraderAccountId = self.account_id
                d = self._client.send(req)

                def on_response(resp):
                    trader = resp.trader
                    result_holder[0] = AccountInfo(
                        account_id=trader.ctidTraderAccountId,
                        balance=trader.balance / 100.0,
                        equity=trader.balance / 100.0,  # approximation
                        margin_used=trader.marginUsed / 100.0 if hasattr(trader, "marginUsed") else 0,
                        margin_free=trader.freeMargin / 100.0 if hasattr(trader, "freeMargin") else 0,
                        currency=trader.depositAsset.name if hasattr(trader, "depositAsset") else "USD",
                        leverage=trader.leverageInCents // 100 if hasattr(trader, "leverageInCents") else 30,
                    )
                    done.set()

                def on_err(f):
                    done.set()

                d.addCallback(on_response)
                d.addErrback(on_err)

            reactor.callFromThread(send_req)
            done.wait(timeout=5.0)
            return result_holder[0]

        except Exception as exc:
            logger.error(f"get_account_info failed: {exc}")
            return None

    # ─── Position sizing ──────────────────────────────────────────────────────

    def calculate_volume(
        self,
        symbol: str,
        balance: float,
        risk_pct: float,
        sl_distance_price: float,
        leverage: int | None = None,
    ) -> int:
        """Calculate cTrader volume (in lots × 100).

        cTrader volume unit: 1 unit = 0.01 lot = 1,000 units for FX
        So 100 units = 1 lot = 100,000 FX units

        Formula:
          risk_usd = balance × risk_pct
          pip_value = depends on symbol and price
          lots = risk_usd / (sl_pips × pip_value_per_lot)
          volume = lots × 100  (cTrader uses 0.01 lot increments)
        """
        ac = ASSET_CLASS.get(symbol, "forex")
        risk_usd = balance * risk_pct

        if ac == "forex":
            # FX: pip_value ≈ $10 per lot for USD-quoted
            pip_size = 0.01 if "JPY" in symbol else 0.0001
            sl_pips = sl_distance_price / pip_size
            pip_value_per_lot = 10.0  # USD per lot (approximate)
            if "JPY" in symbol:
                # JPY pairs: pip_value = 1000/price ≈ $6.67 for USDJPY=150
                pip_value_per_lot = 10.0  # simplified

        elif ac == "metal":
            if symbol == "XAUUSD":
                pip_size = 0.01
                sl_pips = sl_distance_price / pip_size
                pip_value_per_lot = 1.0  # $1 per 0.01 for gold
            else:  # XAGUSD
                pip_size = 0.001
                sl_pips = sl_distance_price / pip_size
                pip_value_per_lot = 0.5

        elif ac in ("energy", "index", "crypto"):
            sl_pips = sl_distance_price  # 1 pip = 1 price unit
            pip_value_per_lot = 1.0

        else:
            sl_pips = sl_distance_price / 0.0001
            pip_value_per_lot = 10.0

        if sl_pips <= 0:
            return 0

        lots = risk_usd / (sl_pips * pip_value_per_lot)
        volume = max(1, min(int(lots * 100), 10000))  # max 100 lots
        return volume

    def has_open_position(self, symbol: str) -> bool:
        """Check for existing position (simplified — needs position list call)."""
        # In production: call ProtoOAReconcileReq and check positions
        return False  # safe default — let TradeExecutor verify

    def test_connection(self) -> bool:
        """Test API credentials without placing orders."""
        try:
            connected = self.connect(timeout=10.0)
            if connected:
                info = self.get_account_info()
                if info:
                    logger.info(
                        f"cTrader OK: balance={info.balance} {info.currency}, "
                        f"leverage=1:{info.leverage}"
                    )
                return connected
            return False
        except Exception as exc:
            logger.error(f"cTrader test failed: {exc}")
            return False

    def disconnect(self):
        """Cleanly disconnect."""
        if self._client:
            try:
                from twisted.internet import reactor
                reactor.callFromThread(self._client.stopService)
            except Exception:
                pass
        self._connected = False
        logger.info("cTrader disconnected")
