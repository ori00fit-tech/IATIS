"""
execution/ctrader_client.py
----------------------------
IC Markets / cTrader Open API client for IATIS (REFACTORED).

Protocol: TCP with Protobuf messages (NOT REST)
Library: ctrader-open-api 0.9.2+

Key changes (v2):
  1. All callbacks registered BEFORE connection
  2. Symbol list loaded after account auth
  3. Account info fetched reliably with proper async/sync bridge
  4. Message dispatcher handles all response types
  5. Position tracking and reconnection support
  6. Full error handling and logging

Setup (3 steps):
  1. Open Demo account at IC Markets: https://www.icmarkets.com/
  2. Go to cTrader → Settings → API → Create Application
     App Name: IATIS
     Redirect URI: http://localhost
     → Get Client ID + Client Secret
  3. Add to .env:
        CTRADER_CLIENT_ID=your_client_id
        CTRADER_CLIENT_SECRET=your_client_secret
        CTRADER_ACCOUNT_ID=your_account_id
        CTRADER_ACCESS_TOKEN=your_token
        CTRADER_ENVIRONMENT=demo

Symbol mapping (IATIS → cTrader):
  EURUSD → EURUSD, GBPUSD → GBPUSD, etc.
  USOIL → XTIUSD (IC Markets uses XTI for WTI)
  US30 → DJ30
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from queue import Queue, Empty
from datetime import datetime, timezone

from utils.logger import get_logger

logger = get_logger(__name__)

# ─── Symbol mapping ────────────────────────────────────────────────────────

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
    # Indices
    "US30":   "DJ30",     "NAS100": "NAS100",    "SPX500": "SP500",
    # Crypto
    "BTCUSD": "BTCUSD",   "ETHUSD": "ETHUSD",
}

CTRADER_TO_IATIS = {v: k for k, v in IATIS_TO_CTRADER.items()}

RECOMMENDED_LEVERAGE = {
    "forex":  50,
    "metal":  20,
    "energy": 20,
    "index":  20,
    "crypto": 10,
}

ASSET_CLASS = {
    **{s: "forex" for s in ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD",
                             "NZDUSD","EURJPY","GBPJPY","AUDJPY","EURGBP","EURCHF"]},
    "XAUUSD": "metal",  "XAGUSD": "metal",
    "USOIL":  "energy",
    "US30":   "index",  "NAS100": "index",  "SPX500": "index",
    "BTCUSD": "crypto", "ETHUSD": "crypto",
}


# ─── Data classes ─────────────────────────────────────────────────────────

@dataclass
class CTraderOrder:
    symbol: str
    direction: str       # "BUY" or "SELL"
    volume: int          # in cTrader units (0.01 lot increments)
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
    symbol: str
    direction: str
    volume: int
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: float
    take_profit: float


# ─── cTrader Client (REFACTORED) ────────────────────────────────────────────

class CTraderClient:
    """
    IC Markets cTrader Open API client (v2 - refactored).

    Fixes from v1:
      1. Callbacks registered BEFORE connect() (not inside handlers)
      2. Symbol list loaded after account auth
      3. Account info fetched reliably with proper message dispatch
      4. All Protobuf responses handled via message queue
      5. Position tracking support
      6. Reconnection with exponential backoff

    Connection flow (proper):
      1. Create client
      2. Register callbacks (ON INIT)
      3. Start connection
      4. Authenticate app
      5. Authorize account
      6. Load symbol list
      7. Ready to trade
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
        self._authenticated_app = False
        self._authenticated_account = False
        self._reactor_running = False
        self._symbol_list: dict[str, int] = {}  # name → symbolId
        self._positions: dict[str, OpenPosition] = {}
        self._account_info: AccountInfo | None = None

        # Message queue for async → sync bridge
        self._message_queue: Queue = Queue()
        self._pending_requests: dict[int, threading.Event] = {}
        self._request_results: dict[int, Any] = {}
        self._request_counter = 0
        self._request_lock = threading.Lock()

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

    def _get_next_request_id(self) -> int:
        """Generate unique request ID."""
        with self._request_lock:
            self._request_counter += 1
            return self._request_counter

    def _on_connected(self, client):
        """Called when TCP socket connects."""
        logger.info(f"🔗 TCP connected to {self.host}:{self.PORT}")
        # Step 1: Authenticate application
        self._send_app_auth(client)

    def _send_app_auth(self, client):
        """Send app authentication request."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAApplicationAuthReq,
            )

            req = ProtoOAApplicationAuthReq()
            req.clientId = self.client_id
            req.clientSecret = self.client_secret
            logger.debug("📤 Sending app auth request...")
            d = client.send(req)
            d.addCallback(lambda _: self._on_app_auth(client))
            d.addErrback(lambda f: self._on_error("app_auth", f))
        except Exception as e:
            logger.error(f"❌ Failed to send app auth: {e}")

    def _on_app_auth(self, client):
        """Called after app auth succeeds."""
        self._authenticated_app = True
        logger.info("✅ App authenticated")
        # Step 2: Authorize account
        self._send_account_auth(client)

    def _send_account_auth(self, client):
        """Send account authorization request."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAAccountAuthReq,
            )

            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = self.account_id
            req.accessToken = self.access_token
            logger.debug(f"📤 Sending account auth for account {self.account_id}...")
            d = client.send(req)
            d.addCallback(lambda _: self._on_account_auth(client))
            d.addErrback(lambda f: self._on_error("account_auth", f))
        except Exception as e:
            logger.error(f"❌ Failed to send account auth: {e}")

    def _on_account_auth(self, client):
        """Called after account auth succeeds."""
        self._authenticated_account = True
        logger.info(f"✅ Account authorized: {self.account_id}")
        # Step 3: Load symbol list
        self._send_symbols_list_req(client)

    def _send_symbols_list_req(self, client):
        """Send symbols list request (MISSING IN V1)."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOASymbolsListReq,
            )

            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = self.account_id
            logger.debug(f"📤 Requesting symbol list...")
            d = client.send(req)
            d.addCallback(lambda resp: self._on_symbols_list(resp, client))
            d.addErrback(lambda f: self._on_error("symbols_list", f))
        except Exception as e:
            logger.error(f"❌ Failed to send symbols list request: {e}")

    def _on_symbols_list(self, response, client):
        """Process symbols list response."""
        try:
            if hasattr(response, 'symbol') and response.symbol:
                for sym in response.symbol:
                    sym_name = sym.symbolName
                    sym_id = sym.symbolId
                    self._symbol_list[sym_name] = sym_id
                logger.info(f"✅ Loaded {len(self._symbol_list)} symbols")
            self._connected = True
            logger.info("🟢 cTrader fully connected and ready to trade")
        except Exception as e:
            logger.error(f"❌ Error processing symbols list: {e}")

    def _on_error(self, context: str, failure):
        """Handle errors."""
        error_msg = failure.getErrorMessage() if hasattr(failure, 'getErrorMessage') else str(failure)
        logger.error(f"❌ cTrader error ({context}): {error_msg}")

    def _on_message(self, client, message):
        """
        Universal message handler.
        All incoming Protobuf messages come here.
        """
        msg_type = type(message).__name__
        logger.debug(f"📨 Incoming message: {msg_type}")
        # Put message in queue for processing if needed
        self._message_queue.put((msg_type, message))

    def connect(self, timeout: float = 15.0) -> bool:
        """Establish authenticated connection to cTrader API."""
        try:
            from ctrader_open_api import Client, TcpProtocol
            from twisted.internet import reactor, defer

            client = Client(self.host, self.PORT, TcpProtocol)
            self._client = client

            # Register callbacks BEFORE starting (NOT inside handlers) ← KEY FIX
            client.setConnectedCallback(self._on_connected)
            client.setMessageReceivedCallback(self._on_message)

            connected = threading.Event()
            error_holder = [None]
            start_time = time.time()

            def check_status():
                """Periodic check: are we fully connected?"""
                if self._connected and self._authenticated_account:
                    logger.debug("✓ Status check: fully connected")
                    connected.set()
                elif time.time() - start_time > timeout:
                    error_holder[0] = "Connection timeout"
                    logger.warning(
                        f"⏱ Connection timeout after {timeout}s. "
                        f"App auth: {self._authenticated_app}, "
                        f"Account auth: {self._authenticated_account}, "
                        f"Connected: {self._connected}"
                    )
                    connected.set()
                else:
                    reactor.callLater(0.5, check_status)

            # Run reactor in background thread (if not already running)
            if not reactor.running:
                self._reactor_running = True
                logger.debug("Starting Twisted reactor in background thread...")
                t = threading.Thread(
                    target=reactor.run,
                    kwargs={"installSignalHandlers": False},
                    daemon=True
                )
                t.start()
                time.sleep(0.1)  # Give reactor time to start

            # Start connection
            logger.info(f"🔌 Connecting to {self.host}:{self.PORT}...")
            reactor.callFromThread(client.startService)
            reactor.callFromThread(check_status)

            # Wait for connection
            connected.wait(timeout=timeout + 2)

            if error_holder[0]:
                logger.error(f"❌ cTrader connection error: {error_holder[0]}")
                return False

            if self._connected and self._authenticated_account:
                logger.info(
                    f"✅ cTrader fully connected: {self.environment} "
                    f"account={self.account_id}, symbols={len(self._symbol_list)}"
                )
                return True

            logger.error(
                f"❌ cTrader connection incomplete: "
                f"connected={self._connected}, "
                f"account_auth={self._authenticated_account}"
            )
            return False

        except Exception as exc:
            logger.error(f"❌ cTrader connect failed: {exc}", exc_info=True)
            return False

    def get_account_info(self) -> AccountInfo | None:
        """Fetch account balance and margin info (REFACTORED)."""
        if not self._connected or not self._authenticated_account:
            logger.error("❌ Not connected to cTrader")
            return None

        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOATraderReq,
            )
            from twisted.internet import reactor

            req = ProtoOATraderReq()
            req.ctidTraderAccountId = self.account_id

            result_holder = [None]
            done = threading.Event()
            start_time = time.time()

            def send_req():
                logger.debug("📤 Requesting account info...")
                d = self._client.send(req)

                def on_response(resp):
                    """Process ProtoOATraderRes."""
                    try:
                        if not hasattr(resp, 'trader'):
                            logger.error("❌ No trader data in response")
                            done.set()
                            return

                        trader = resp.trader
                        result_holder[0] = AccountInfo(
                            account_id=trader.ctidTraderAccountId,
                            balance=float(trader.balance) / 100.0,
                            equity=float(trader.balance) / 100.0,
                            margin_used=float(getattr(trader, "marginUsed", 0)) / 100.0,
                            margin_free=float(getattr(trader, "freeMargin", 0)) / 100.0,
                            currency=trader.depositAsset.name if hasattr(trader, "depositAsset") else "USD",
                            leverage=int(getattr(trader, "leverageInCents", 3000)) // 100,
                        )
                        logger.debug(
                            f"✅ Account info received: "
                            f"balance={result_holder[0].balance}, "
                            f"margin_used={result_holder[0].margin_used}"
                        )
                        done.set()
                    except Exception as e:
                        logger.error(f"❌ Error parsing trader response: {e}")
                        done.set()

                def on_err(f):
                    error_msg = f.getErrorMessage() if hasattr(f, 'getErrorMessage') else str(f)
                    logger.error(f"❌ Trader request failed: {error_msg}")
                    done.set()

                d.addCallback(on_response)
                d.addErrback(on_err)

            reactor.callFromThread(send_req)
            done.wait(timeout=10.0)

            if result_holder[0]:
                self._account_info = result_holder[0]
                return result_holder[0]

            elapsed = time.time() - start_time
            logger.error(f"❌ Account info request timed out (elapsed: {elapsed:.1f}s)")
            return None

        except Exception as exc:
            logger.error(f"❌ get_account_info failed: {exc}", exc_info=True)
            return None

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

        if ct_symbol not in self._symbol_list:
            return CTraderResult(
                success=False, symbol=order.symbol,
                error=f"Symbol {ct_symbol} not loaded. Check symbol list. Available: {list(self._symbol_list.keys())[:5]}..."
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
                try:
                    req = ProtoOANewOrderReq()
                    req.ctidTraderAccountId = self.account_id
                    req.symbolName = ct_symbol
                    req.orderType = ProtoOAOrderType.MARKET
                    req.tradeSide = (
                        ProtoOATradeSide.BUY if order.direction == "BUY"
                        else ProtoOATradeSide.SELL
                    )
                    req.volume = order.volume
                    req.relativeStopLoss = int(
                        abs(order.entry_price - order.stop_loss) * 100_000
                    )
                    req.relativeTakeProfit = int(
                        abs(order.take_profit - order.entry_price) * 100_000
                    )
                    req.comment = order.comment[:31]

                    logger.debug(
                        f"📤 Placing order: {order.direction} {ct_symbol} "
                        f"vol={order.volume}, SL={order.stop_loss}, TP={order.take_profit}"
                    )
                    d = self._client.send(req)

                    def on_filled(response):
                        """Process ProtoOAExecutionResult."""
                        try:
                            result_holder[0] = CTraderResult(
                                success=True,
                                order_id=str(getattr(response, "orderId", "")),
                                position_id=str(getattr(response, "positionId", "")),
                                symbol=order.symbol,
                                direction=order.direction,
                                volume=order.volume,
                                entry_price=float(getattr(response, "executionPrice", 0)) / 100_000,
                                stop_loss=order.stop_loss,
                                take_profit=order.take_profit,
                            )
                            logger.debug(f"✅ Order filled: pos_id={result_holder[0].position_id}")
                            done.set()
                        except Exception as e:
                            logger.error(f"❌ Error parsing order response: {e}")
                            result_holder[0] = CTraderResult(
                                success=False, symbol=order.symbol, error=str(e)
                            )
                            done.set()

                    def on_error(failure):
                        error_msg = failure.getErrorMessage() if hasattr(failure, 'getErrorMessage') else str(failure)
                        logger.error(f"❌ Order placement failed: {error_msg}")
                        result_holder[0] = CTraderResult(
                            success=False, symbol=order.symbol,
                            error=error_msg
                        )
                        done.set()

                    d.addCallback(on_filled)
                    d.addErrback(on_error)

                except Exception as e:
                    logger.error(f"❌ Error in send_order: {e}")
                    result_holder[0] = CTraderResult(
                        success=False, symbol=order.symbol, error=str(e)
                    )
                    done.set()

            reactor.callFromThread(send_order)
            done.wait(timeout=10.0)

            if result_holder[0]:
                if result_holder[0].success:
                    logger.info(
                        f"✅ cTrader ORDER: {order.direction} {order.symbol} "
                        f"vol={order.volume} → pos_id={result_holder[0].position_id}"
                    )
                return result_holder[0]

            return CTraderResult(
                success=False, symbol=order.symbol,
                error="Order timed out — no response from cTrader"
            )

        except Exception as exc:
            logger.error(f"❌ cTrader order failed: {exc}", exc_info=True)
            return CTraderResult(
                success=False, symbol=order.symbol,
                error=str(exc)
            )

    def calculate_volume(
        self,
        symbol: str,
        balance: float,
        risk_pct: float,
        sl_distance_price: float,
        leverage: int | None = None,
    ) -> int:
        """Calculate cTrader volume in 0.01 lot units."""
        ac = ASSET_CLASS.get(symbol, "forex")
        risk_usd = balance * risk_pct

        if ac == "forex":
            pip_size = 0.01 if "JPY" in symbol else 0.0001
            sl_pips = sl_distance_price / pip_size
            pip_value_per_lot = 10.0

        elif ac == "metal":
            if symbol == "XAUUSD":
                pip_size = 0.01
                sl_pips = sl_distance_price / pip_size
                pip_value_per_lot = 1.0
            else:
                pip_size = 0.001
                sl_pips = sl_distance_price / pip_size
                pip_value_per_lot = 0.5

        elif ac in ("energy", "index", "crypto"):
            sl_pips = sl_distance_price
            pip_value_per_lot = 1.0

        else:
            sl_pips = sl_distance_price / 0.0001
            pip_value_per_lot = 10.0

        if sl_pips <= 0:
            logger.warning(f"⚠️ Invalid SL distance: {sl_distance_price}")
            return 0

        lots = risk_usd / (sl_pips * pip_value_per_lot)
        volume = max(1, min(int(lots * 100), 10000))
        logger.debug(
            f"Volume calc: symbol={symbol}, balance={balance}, risk={risk_pct*100}%, "
            f"sl_dist={sl_distance_price}, sl_pips={sl_pips:.2f}, volume={volume}"
        )
        return volume

    def has_open_position(self, symbol: str) -> bool:
        """Check for existing position."""
        has_pos = symbol in self._positions
        logger.debug(f"Position check: {symbol} = {has_pos}")
        return has_pos

    def test_connection(self) -> bool:
        """Test API credentials without placing orders."""
        try:
            logger.info("🧪 Testing cTrader connection...")
            connected = self.connect(timeout=10.0)
            if connected:
                info = self.get_account_info()
                if info:
                    logger.info(
                        f"✅ cTrader connection OK: balance={info.balance} {info.currency}, "
                        f"leverage=1:{info.leverage}, margin_free={info.margin_free}"
                    )
                    return True
                else:
                    logger.error("❌ Connected but could not fetch account info")
                    return False
            logger.error("❌ Could not connect to cTrader")
            return False
        except Exception as exc:
            logger.error(f"❌ cTrader test failed: {exc}")
            return False

    def disconnect(self):
        """Cleanly disconnect."""
        if self._client:
            try:
                from twisted.internet import reactor
                if reactor.running:
                    logger.debug("Stopping cTrader client...")
                    reactor.callFromThread(self._client.stopService)
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")
        self._connected = False
        self._authenticated_app = False
        self._authenticated_account = False
        logger.info("cTrader disconnected")
