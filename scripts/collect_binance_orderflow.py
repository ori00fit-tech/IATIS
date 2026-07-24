#!/usr/bin/env python3
"""
scripts/collect_binance_orderflow.py
----------------------------------------
H104 (research/results/registry.json — "Crypto tick-level order-flow
imbalance ... as an independent entry signal for BTCUSD/ETHUSD, distinct
from the rejected bar-level volume input"). Data collection ONLY — this
is infrastructure, not a signal or a backtest. H104's own decision rule
requires ~3 months of real collection before any verdict is even
possible (n < 150 after 3 months = FAIL) — tick-level history cannot be
backfilled from Binance's REST API the way H019's funding rate or the FX
deep-history downloads could; it only exists going FORWARD from whenever
this collector starts running. Running this script IS starting that
clock, nothing more.

LONG-RUNNING SERVICE, not a periodic/cron script (contrast every other
scripts/download_*.py or scripts/collect_*.py in this repo) — deploy it
under systemd as a service (iatis-orderflow-collector.service), not a
timer. It holds one persistent WebSocket connection per symbol to
Binance's public aggTrade stream (no auth, no rate limit — it's a
server push, not a polled REST endpoint) and reconnects with backoff on
any drop.

Mechanism (Cumulative Delta):
    Binance aggTrade payload's "m" field is "is the buyer the maker":
      m=true  -> the BUYER posted the resting limit order; the SELLER
                 was the aggressor (taker) -> this trade was SELL-initiated.
      m=false -> the SELLER was the maker; the BUYER was the aggressor
                 (taker) -> this trade was BUY-initiated.
    Delta = sum(qty, buy-initiated trades) - sum(qty, sell-initiated
    trades) within a bar. This is the exact "aggressor-side
    classification" H104's own registry entry distinguishes it from the
    already-rejected crypto_volume experiment (which only ever measured
    an undifferentiated volume total, never aggressor side).

Output: one JSON line per symbol per completed 15-minute bar, appended to
data/{SYMBOL}_orderflow_15min.jsonl (gitignored, like every other
data/*.csv historical dataset — see data/README.md's convention).

Usage (VPS, run under systemd — see iatis-orderflow-collector.service):
    python3 -m scripts.collect_binance_orderflow
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
BAR_INTERVAL_SEC = 15 * 60

# Binance combined-stream ws symbol -> IATIS internal symbol.
WS_SYMBOLS = {"btcusdt": "BTCUSD", "ethusdt": "ETHUSD"}

logger = logging.getLogger("orderflow_collector")


@dataclass
class DeltaAccumulator:
    """Pure, no I/O — accumulates one bar's worth of aggTrade ticks. Unit-
    tested directly (tests/test_collect_binance_orderflow.py) without any
    network involved; the async WebSocket loop below is a thin,
    NOT-independently-tested wrapper around this."""
    buy_qty: float = 0.0
    sell_qty: float = 0.0
    high: float | None = None
    low: float | None = None
    last_price: float | None = None
    trade_count: int = 0

    def add_trade(self, price: float, qty: float, is_buyer_maker: bool) -> None:
        if is_buyer_maker:
            self.sell_qty += qty  # seller was the aggressor
        else:
            self.buy_qty += qty   # buyer was the aggressor
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)
        self.last_price = price
        self.trade_count += 1

    @property
    def delta(self) -> float:
        return self.buy_qty - self.sell_qty

    def is_empty(self) -> bool:
        return self.trade_count == 0

    def to_bar(self, symbol: str, bar_start_ms: int) -> dict:
        return {
            "symbol": symbol,
            "bar_start": datetime.fromtimestamp(bar_start_ms / 1000, tz=timezone.utc).isoformat(),
            "bar_start_ms": bar_start_ms,
            "buy_qty": round(self.buy_qty, 8),
            "sell_qty": round(self.sell_qty, 8),
            "delta": round(self.delta, 8),
            "high": self.high, "low": self.low, "close": self.last_price,
            "trade_count": self.trade_count,
        }


def bar_start_ms(trade_ts_ms: int, interval_sec: int = BAR_INTERVAL_SEC) -> int:
    """Floor `trade_ts_ms` to the start of its bar — pure, testable."""
    interval_ms = interval_sec * 1000
    return (trade_ts_ms // interval_ms) * interval_ms


def parse_agg_trade(message: str) -> dict | None:
    """Parses one raw aggTrade WebSocket message. Returns None for
    anything that isn't a trade payload (e.g. a subscription ack) rather
    than raising — a malformed/unexpected message must not kill the
    connection loop."""
    try:
        data = json.loads(message)
        return {
            "price": float(data["p"]),
            "qty": float(data["q"]),
            "is_buyer_maker": bool(data["m"]),
            "trade_ts_ms": int(data["T"]),
        }
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None


def append_bar(bar: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a") as f:
        f.write(json.dumps(bar, default=str) + "\n")


async def collect_symbol(ws_symbol: str, internal_symbol: str) -> None:
    """One persistent connection, one symbol. Runs forever — reconnects
    with a fixed backoff on any drop rather than exiting, since this is
    meant to run for months under systemd (Restart=always is a second
    layer of defense, not the primary reconnection path)."""
    import websockets

    out_path = DATA_DIR / f"{internal_symbol}_orderflow_15min.jsonl"
    url = f"wss://stream.binance.com:9443/ws/{ws_symbol}@aggTrade"
    acc = DeltaAccumulator()
    current_bar_start: int | None = None

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                logger.info(f"{internal_symbol}: connected ({url})")
                async for message in ws:
                    trade = parse_agg_trade(message)
                    if trade is None:
                        continue
                    this_bar_start = bar_start_ms(trade["trade_ts_ms"])
                    if current_bar_start is None:
                        current_bar_start = this_bar_start
                    elif this_bar_start != current_bar_start:
                        if not acc.is_empty():
                            append_bar(acc.to_bar(internal_symbol, current_bar_start), out_path)
                        acc = DeltaAccumulator()
                        current_bar_start = this_bar_start
                    acc.add_trade(trade["price"], trade["qty"], trade["is_buyer_maker"])
        except Exception as exc:
            logger.warning(f"{internal_symbol}: connection error ({exc}) — "
                           f"reconnecting in 5s")
            await asyncio.sleep(5)


async def main_async() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    logger.info(f"Starting orderflow collection for: {list(WS_SYMBOLS.values())}")
    await asyncio.gather(*(
        collect_symbol(ws_sym, internal_sym) for ws_sym, internal_sym in WS_SYMBOLS.items()
    ))


def main() -> int:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
