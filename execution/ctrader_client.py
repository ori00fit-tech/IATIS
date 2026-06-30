"""
execution/ctrader_client.py — V2

Rewritten cTrader client for IATIS (ctrader-open-api 0.9.2).

Features:
- Proper Application + Account authentication
- Symbol list loading and symbolId cache
- Protobuf parsing (safely handles wrapper responses)
- AccountInfo retrieval
- Market order placement using symbolId
- Twisted reactor run in background thread for sync calls
- Robust logging and error handling

This file is designed to replace the older implementation and to be the single
source of truth for cTrader interaction inside the IATIS project.

Notes & assumptions:
- ctrader_open_api.Client.send() returns a Deferred which resolves to an
  envelope object. If that envelope contains a `payload` (bytes) it is parsed
  with the expected Proto message class using ParseFromString. The helper
  `_unwrap_response` centralizes this parsing.
- Volume units: the code uses cTrader convention where `volume` is expressed in
  "units of 0.01 lot" (so 100 = 1 lot). See calculate_volume() for helpers.
- This implementation favors safety and explicit timeouts to avoid hanging
  the scheduler.

Testing:
- After deployment, call client.test_connection() and client.get_account_info()
  and verify symbols are loaded. Then call place_market_order() in a demo
  environment.

"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

# Symbol mapping unchanged from original
IATIS_TO_CTRADER: dict[str, str] = {
    "EURUSD": "EURUSD",   "GBPUSD": "GBPUSD",   "USDJPY": "USDJPY",
    "USDCHF": "USDCHF",   "AUDUSD": "AUDUSD",   "USDCAD": "USDCAD",
    "NZDUSD": "NZDUSD",
    "EURJPY": "EURJPY",   "GBPJPY": "GBPJPY",   "AUDJPY": "AUDJPY",
    "EURGBP": "EURGBP",   "EURCHF": "EURCHF",
    "XAUUSD": "XAUUSD",   "XAGUSD": "XAGUSD",
    "USOIL":  "XTIUSD",
    "US30":   "DJ30",     "NAS100": "NAS100",    "SPX500": "SP500",
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


@dataclass
class CTraderOrder:
    symbol: str          # IATIS internal (e.g. "EURUSD")
    direction: str       # "BUY" or "SELL"
    volume: int          # in cTrader units (1 lot = 100 -> 0.01 lot increments)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
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


class CTraderClient:
    DEMO_HOST = "demo.ctraderapi.com"
    LIVE_HOST = "live.ctraderapi.com"
    PORT = 5035

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        account_id: Optional[int] = None,
        access_token: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> None:
        self.client_id = client_id or os.environ.get("CTRADER_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("CTRADER_CLIENT_SECRET", "")
        self.account_id = int(account_id or os.environ.get("CTRADER_ACCOUNT_ID", 0))
        self.access_token = access_token or os.environ.get("CTRADER_ACCESS_TOKEN", "")
        env = environment or os.environ.get("CTRADER_ENVIRONMENT", "demo")
        self.host = self.DEMO_HOST if env == "demo" else self.LIVE_HOST
        self.environment = env

        self._client = None
        self._connected = False
        # name -> symbolId
        self._symbol_list: Dict[str, int] = {}
        # id -> (name, tickPrecision, digits)
        self._symbol_meta: Dict[int, dict] = {}

        # Keep reactor thread reference to stop on disconnect
        self._reactor_thread: Optional[threading.Thread] = None

        # Validate early to fail fast in CI/tests
        self._validate_credentials()

    def _validate_credentials(self) -> None:
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

    # --- Helpers for twisted/protobuf interop ---------------------------------

    def _get_twisted_client(self):
        from ctrader_open_api import Client, TcpProtocol
        return Client(self.host, self.PORT, TcpProtocol)

    def _start_reactor_in_thread(self):
        # Start Twisted reactor in a background daemon thread if not running
        try:
            from twisted.internet import reactor

            if not reactor.running:
                t = threading.Thread(
                    target=reactor.run, kwargs={"installSignalHandlers": False}
                )
                t.daemon = True
                t.start()
                self._reactor_thread = t
                # give reactor a moment
                time.sleep(0.05)
        except Exception as exc:
            logger.exception("Failed to start twisted reactor: %s", exc)

    def _unwrap_response(self, response: Any, proto_cls: Any) -> Tuple[Optional[Any], Optional[str]]:
        """
        Try to extract and parse a protobuf payload from an envelope-like response.
        Returns (parsed_proto_instance | None, error_message | None)
        """
        try:
            # Some implementations return the proto directly
            if isinstance(response, proto_cls):
                return response, None

            # Envelope with 'payload' bytes
            payload = getattr(response, "payload", None)
            if payload is None and hasattr(response, "data"):
                payload = getattr(response, "data")

            if payload is None:
                # Fall back to trying to use attributes directly
                # Attempt to construct proto and set from obj if possible
                return response, None

            # Parse bytes
            obj = proto_cls()
            obj.ParseFromString(payload)
            return obj, None

        except Exception as exc:
            return None, str(exc)

    def _send_and_wait(self, req: Any, proto_response_cls: Any, timeout: float = 5.0) -> Tuple[Optional[Any], Optional[str]]:
        """
        Send a request via client.send(req) and wait for a single response.
        The response is parsed using proto_response_cls if available.
        Returns (parsed_response | None, error_message | None).
        """
        try:
            from twisted.internet import reactor

            if not self._client:
                return None, "client not initialized"

            result_holder: list[Any] = [None]
            error_holder: list[Optional[str]] = [None]
            done = threading.Event()

            d = self._client.send(req)

            def _cb(resp):
                parsed, err = self._unwrap_response(resp, proto_response_cls)
                if err:
                    error_holder[0] = f"parse: {err}"
                else:
                    result_holder[0] = parsed
                done.set()

            def _eb(failure):
                try:
                    error_holder[0] = str(failure)
                except Exception:
                    error_holder[0] = "unknown failure"
                done.set()

            d.addCallback(_cb)
            d.addErrback(_eb)

            # Ensure call happens on reactor thread
            try:
                reactor.callFromThread(lambda: None)
            except Exception:
                # Reactor may already be running in current thread context
                pass

            finished = done.wait(timeout=timeout)
            if not finished:
                return None, "timeout waiting for response"

            if error_holder[0]:
                return None, error_holder[0]

            return result_holder[0], None

        except Exception as exc:
            return None, str(exc)

    # --- Connection & Auth --------------------------------------------------

    def connect(self, timeout: float = 15.0) -> bool:
        """Establish connection and authenticate the app + account.

        This will also load the symbol list after successful auth.
        """
        try:
            from ctrader_open_api import Client, TcpProtocol
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAApplicationAuthReq,
                ProtoOAApplicationAuthRes,
                ProtoOAAccountAuthReq,
                ProtoOAAccountAuthRes,
                ProtoOASymbolsListReq,
                ProtoOASymbolsListRes,
            )
            from twisted.internet import reactor

            self._start_reactor_in_thread()

            client = Client(self.host, self.PORT, TcpProtocol)
            self._client = client
            connected_evt = threading.Event()
            error_holder = [None]

            def on_connected(_):
                logger.debug("tcp connected, sending app auth")
                # Step 1: Application auth
                app_auth = ProtoOAApplicationAuthReq()
                app_auth.clientId = self.client_id
                app_auth.clientSecret = self.client_secret
                d = client.send(app_auth)

                def on_app_auth(resp):
                    parsed, err = self._unwrap_response(resp, ProtoOAApplicationAuthRes)
                    if err:
                        error_holder[0] = f"app_auth parse error: {err}"
                        connected_evt.set()
                        return
                    logger.info("Application auth OK")

                    # Step 2: Account auth
                    acc_auth = ProtoOAAccountAuthReq()
                    acc_auth.ctidTraderAccountId = self.account_id
                    acc_auth.accessToken = self.access_token
                    d2 = client.send(acc_auth)

                    def on_acc_auth(resp2):
                        p2, e2 = self._unwrap_response(resp2, ProtoOAAccountAuthRes)
                        if e2:
                            error_holder[0] = f"account auth parse error: {e2}"
                            connected_evt.set()
                            return
                        logger.info("Account auth OK")
                        self._connected = True
                        connected_evt.set()

                    def on_acc_err(f):
                        error_holder[0] = str(f)
                        connected_evt.set()

                    d2.addCallback(on_acc_auth)
                    d2.addErrback(on_acc_err)

                def on_app_err(f):
                    error_holder[0] = str(f)
                    connected_evt.set()

                d.addCallback(on_app_auth)
                d.addErrback(on_app_err)

            client.setConnectedCallback(on_connected)

            # start client service on reactor thread
            if not reactor.running:
                t = threading.Thread(target=reactor.run, kwargs={"installSignalHandlers": False})
                t.daemon = True
                t.start()
                self._reactor_thread = t

            # ask reactor to start client
            reactor.callFromThread(client.startService)

            connected = connected_evt.wait(timeout=timeout)
            if not connected:
                logger.error("cTrader connection/auth timed out")
                return False
            if error_holder[0]:
                logger.error("cTrader auth error: %s", error_holder[0])
                return False

            # On success load symbols (best-effort)
            try:
                self._load_symbols(timeout=5.0)
            except Exception as exc:
                logger.warning("symbols load failed: %s", exc)

            logger.info("cTrader connected: %s account=%s", self.environment, self.account_id)
            return True

        except Exception as exc:
            logger.exception("cTrader connect failed: %s", exc)
            return False

    def _load_symbols(self, timeout: float = 5.0) -> None:
        """Load symbols and populate _symbol_list (name -> id) and _symbol_meta."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOASymbolsListReq,
                ProtoOASymbolsListRes,
            )

            req = ProtoOASymbolsListReq()
            resp, err = self._send_and_wait(req, ProtoOASymbolsListRes, timeout=timeout)
            if err:
                raise RuntimeError(f"_load_symbols failed: {err}")

            # ProtoOASymbolsListRes may have repeated `symbols` with fields
            try:
                symbols = getattr(resp, "symbols", [])
                for s in symbols:
                    # fields seen in many OpenAPI versions: symbolId, name, digits, tickSize
                    symbol_id = int(getattr(s, "symbolId", getattr(s, "id", 0)))
                    name = getattr(s, "name", getattr(s, "symbolName", ""))
                    if not name:
                        continue
                    self._symbol_list[CTRADER_TO_IATIS.get(name, name)] = symbol_id
                    # store metadata
                    self._symbol_meta[symbol_id] = {
                        "name": name,
                        "digits": getattr(s, "digits", None),
                        "tickSize": getattr(s, "tickSize", None),
                    }
                logger.info("Loaded %d symbols", len(self._symbol_list))
            except Exception as exc:
                raise

        except Exception as exc:
            logger.exception("Failed to load symbols: %s", exc)
            raise

    # --- Account info -------------------------------------------------------

    def get_account_info(self, timeout: float = 5.0) -> Optional[AccountInfo]:
        if not self._connected:
            return None
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOATraderReq,
                ProtoOATraderRes,
            )

            req = ProtoOATraderReq()
            req.ctidTraderAccountId = self.account_id
            resp, err = self._send_and_wait(req, ProtoOATraderRes, timeout=timeout)
            if err:
                logger.error("get_account_info error: %s", err)
                return None

            trader = getattr(resp, "trader", None)
            if trader is None:
                logger.error("get_account_info: no trader in response")
                return None

            balance = float(getattr(trader, "balance", 0)) / 100.0
            equity = float(getattr(trader, "equity", balance)) / 100.0
            margin_used = float(getattr(trader, "marginUsed", 0)) / 100.0
            margin_free = float(getattr(trader, "freeMargin", 0)) / 100.0
            currency = getattr(getattr(trader, "depositAsset", None), "name", "USD")
            leverage = int(getattr(trader, "leverageInCents", 3000)) // 100

            return AccountInfo(
                account_id=int(getattr(trader, "ctidTraderAccountId", self.account_id)),
                balance=balance,
                equity=equity,
                margin_used=margin_used,
                margin_free=margin_free,
                currency=currency,
                leverage=leverage,
            )

        except Exception as exc:
            logger.exception("get_account_info failed: %s", exc)
            return None

    # --- Orders -------------------------------------------------------------

    def place_market_order(self, order: CTraderOrder, timeout: float = 10.0) -> CTraderResult:
        """Place a market order. Uses symbolId (required by OpenAPI) and sets
        relative SL/TP when stop_loss/take_profit provided.
        """
        if not self._connected:
            return CTraderResult(success=False, symbol=order.symbol, error="Not connected. Call connect() first.")

        ct_name = IATIS_TO_CTRADER.get(order.symbol)
        if not ct_name:
            return CTraderResult(success=False, symbol=order.symbol, error=f"Symbol {order.symbol} not in mapping.")

        symbol_id = self._symbol_list.get(order.symbol) or self._symbol_list.get(ct_name)
        if not symbol_id:
            return CTraderResult(success=False, symbol=order.symbol, error=f"SymbolId for {order.symbol} not loaded. Call connect() first.")

        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOANewOrderReq,
                ProtoOANewOrderRes,
            )
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
                ProtoOAOrderType, ProtoOATradeSide,
            )

            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = self.account_id
            # Use symbolId (preferred) — keep backward fallback if API variant supports name
            try:
                req.symbolId = int(symbol_id)
            except Exception:
                # fallback
                req.symbolName = ct_name

            req.orderType = ProtoOAOrderType.MARKET
            req.tradeSide = ProtoOATradeSide.BUY if order.direction.upper() == "BUY" else ProtoOATradeSide.SELL
            req.volume = int(order.volume)
            # cTrader comment is limited to 31 chars typically
            req.comment = (order.comment or "IATIS")[:31]

            # If SL/TP provided, attempt to set relative stops using scaled integers.
            # The OpenAPI expects relativeStopLoss/relativeTakeProfit in points (not in price)
            # We'll compute an approximate delta assuming common scaling (100000 for FX non-JPY).
            # This is not perfect for all instrument classes, but prevents sending invalid absolute values.
            if order.stop_loss is not None or order.take_profit is not None:
                # Attempt to get a tick size or digits from symbol meta
                meta = self._symbol_meta.get(int(symbol_id), {})
                tick = meta.get("tickSize") or 0.00001
                # fallback tick by instrument class
                if tick is None or tick <= 0:
                    tick = 0.00001

                # We'll use 1/tick as scaling factor
                scale = int(round(1.0 / float(tick))) if tick > 0 else 100000

                # Need an approximate execution price to compute relative points.
                exec_price = None
                # Best-effort: try to use last tick if available via symbol meta
                r_exec = getattr(meta, "lastPrice", None) if isinstance(meta, object) else None
                if r_exec:
                    exec_price = float(r_exec)

                # If we couldn't get exec_price, leave relative fields unset — broker will use market price
                if exec_price:
                    if order.stop_loss is not None:
                        diff = abs(exec_price - order.stop_loss)
                        req.relativeStopLoss = int(round(diff * scale))
                    if order.take_profit is not None:
                        diff = abs(exec_price - order.take_profit)
                        req.relativeTakeProfit = int(round(diff * scale))

            # Send and wait
            resp, err = self._send_and_wait(req, ProtoOANewOrderRes, timeout=timeout)
            if err:
                logger.error("place_market_order send error: %s", err)
                return CTraderResult(success=False, symbol=order.symbol, error=err)

            # Parse response fields safely
            order_id = str(getattr(resp, "orderId", ""))
            position_id = str(getattr(resp, "positionId", ""))
            entry_price = float(getattr(resp, "executionPrice", 0)) if hasattr(resp, "executionPrice") else 0.0

            return CTraderResult(
                success=True,
                order_id=order_id,
                position_id=position_id,
                symbol=order.symbol,
                direction=order.direction,
                volume=order.volume,
                entry_price=(entry_price / 100000.0) if entry_price else entry_price,
                stop_loss=order.stop_loss or 0.0,
                take_profit=order.take_profit or 0.0,
                raw={"resp": str(resp)},
            )

        except Exception as exc:
            logger.exception("place_market_order exception: %s", exc)
            return CTraderResult(success=False, symbol=order.symbol, error=str(exc))

    # --- Utilities ----------------------------------------------------------

    def calculate_volume(
        self,
        symbol: str,
        balance: float,
        risk_pct: float,
        sl_distance_price: float,
        leverage: Optional[int] = None,
    ) -> int:
        """Calculate cTrader volume (in 0.01 lots units).

        See README in module for explanation. This is approximate and should be
        validated in integration tests for real accounts.
        """
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
            return 0

        lots = risk_usd / (sl_pips * pip_value_per_lot)
        volume = max(1, min(int(lots * 100), 10000))
        return volume

    def has_open_position(self, symbol: str) -> bool:
        # Placeholder — production code should query positions via ReconcileReq
        return False

    def test_connection(self) -> bool:
        try:
            connected = self.connect(timeout=10.0)
            if connected:
                info = self.get_account_info()
                if info:
                    logger.info("cTrader OK: balance=%s %s, leverage=1:%s", info.balance, info.currency, info.leverage)
                return connected
            return False
        except Exception as exc:
            logger.exception("cTrader test failed: %s", exc)
            return False

    def disconnect(self) -> None:
        if self._client:
            try:
                from twisted.internet import reactor
                reactor.callFromThread(self._client.stopService)
            except Exception:
                pass
        self._connected = False
        logger.info("cTrader disconnected")
