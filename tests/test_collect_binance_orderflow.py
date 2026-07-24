"""Unit tests for scripts/collect_binance_orderflow.py's pure logic —
DeltaAccumulator, bar_start_ms(), and parse_agg_trade(). None of this
needs a network; the async WebSocket loop (collect_symbol) is a thin,
NOT-independently-tested wrapper around these, per the module's own
docstring."""
from __future__ import annotations

import json

from scripts.collect_binance_orderflow import (
    BAR_INTERVAL_SEC,
    DeltaAccumulator,
    bar_start_ms,
    parse_agg_trade,
)


# ── DeltaAccumulator ──

def test_empty_accumulator_is_empty():
    acc = DeltaAccumulator()
    assert acc.is_empty()
    assert acc.delta == 0.0


def test_buy_initiated_trade_increases_buy_qty_and_delta():
    acc = DeltaAccumulator()
    acc.add_trade(price=100.0, qty=2.0, is_buyer_maker=False)
    assert acc.buy_qty == 2.0
    assert acc.sell_qty == 0.0
    assert acc.delta == 2.0
    assert not acc.is_empty()


def test_sell_initiated_trade_increases_sell_qty_and_negative_delta():
    acc = DeltaAccumulator()
    acc.add_trade(price=100.0, qty=3.0, is_buyer_maker=True)
    assert acc.sell_qty == 3.0
    assert acc.buy_qty == 0.0
    assert acc.delta == -3.0


def test_mixed_trades_net_delta():
    acc = DeltaAccumulator()
    acc.add_trade(price=100.0, qty=5.0, is_buyer_maker=False)  # buy
    acc.add_trade(price=101.0, qty=2.0, is_buyer_maker=True)   # sell
    assert acc.buy_qty == 5.0
    assert acc.sell_qty == 2.0
    assert acc.delta == 3.0
    assert acc.trade_count == 2


def test_high_low_close_track_across_trades():
    acc = DeltaAccumulator()
    acc.add_trade(price=100.0, qty=1.0, is_buyer_maker=False)
    acc.add_trade(price=105.0, qty=1.0, is_buyer_maker=True)
    acc.add_trade(price=98.0, qty=1.0, is_buyer_maker=False)
    assert acc.high == 105.0
    assert acc.low == 98.0
    assert acc.last_price == 98.0  # close = most recent trade


def test_to_bar_shape_and_rounding():
    acc = DeltaAccumulator()
    acc.add_trade(price=100.0, qty=1.23456789123, is_buyer_maker=False)
    bar = acc.to_bar("BTCUSD", bar_start_ms=1595548800000)
    assert bar["symbol"] == "BTCUSD"
    assert bar["bar_start_ms"] == 1595548800000
    assert bar["bar_start"] == "2020-07-24T00:00:00+00:00"
    assert bar["buy_qty"] == round(1.23456789123, 8)
    assert bar["sell_qty"] == 0.0
    assert bar["delta"] == round(1.23456789123, 8)
    assert bar["high"] == 100.0
    assert bar["low"] == 100.0
    assert bar["close"] == 100.0
    assert bar["trade_count"] == 1


# ── bar_start_ms ──

def test_bar_start_ms_floors_to_interval():
    interval_ms = BAR_INTERVAL_SEC * 1000
    ts = 1595548800000 + 12345  # a bit into the bar
    assert bar_start_ms(ts) == 1595548800000
    assert (ts - bar_start_ms(ts)) < interval_ms


def test_bar_start_ms_exact_boundary_is_idempotent():
    ts = 1595548800000
    assert bar_start_ms(ts) == ts


def test_bar_start_ms_custom_interval():
    # 60-second bars instead of the default 15 minutes.
    assert bar_start_ms(1595548830000, interval_sec=60) == 1595548800000


# ── parse_agg_trade ──

def test_parse_agg_trade_valid_message():
    msg = json.dumps({"p": "100.50", "q": "2.0", "m": True, "T": 1595548800123})
    trade = parse_agg_trade(msg)
    assert trade == {
        "price": 100.50, "qty": 2.0,
        "is_buyer_maker": True, "trade_ts_ms": 1595548800123,
    }


def test_parse_agg_trade_buyer_maker_false():
    msg = json.dumps({"p": "1.0", "q": "1.0", "m": False, "T": 1})
    trade = parse_agg_trade(msg)
    assert trade["is_buyer_maker"] is False


def test_parse_agg_trade_malformed_json_returns_none():
    assert parse_agg_trade("{not valid json") is None


def test_parse_agg_trade_missing_field_returns_none():
    msg = json.dumps({"p": "100.0", "q": "1.0", "m": True})  # no "T"
    assert parse_agg_trade(msg) is None


def test_parse_agg_trade_subscription_ack_returns_none():
    # Binance sends non-trade control messages (e.g. subscribe acks)
    # on combined streams; these must not crash the collector.
    msg = json.dumps({"result": None, "id": 1})
    assert parse_agg_trade(msg) is None


def test_parse_agg_trade_non_numeric_price_returns_none():
    msg = json.dumps({"p": "not-a-number", "q": "1.0", "m": True, "T": 1})
    assert parse_agg_trade(msg) is None
