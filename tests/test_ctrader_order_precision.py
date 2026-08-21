"""tests/test_ctrader_order_precision.py
-------------------------------------------
Regression coverage for the 2026-08-17 live-execution fix: cTrader
rejected a real BUY ETHUSD order with "INVALID_REQUEST — Relative stop
loss has invalid precision". Root-caused against cTrader's own Open API
docs (help.ctrader.com/open-api/symbol-data/, /open-api/model-messages/)
and community forum (community.ctrader.com/forum/connect-api-support/
37262/): SPOT_SCALE=100_000 is a FIXED, symbol-independent wire-protocol
constant (never the bug) — the real defect is that the SL/TP distance was
never rounded to the SYMBOL's own decimal precision (`digits`) before
being re-scaled to the integer sent to the broker, so ordinary float
arithmetic noise produced a value that isn't an exact multiple of that
symbol's tick size.

storage/outcome_tracker.py's own pip-size comment (verified live against
IC Markets cTrader, 2026-07-16) already documents ETHUSD/BTCUSD's real
digits=2 (vs. EURUSD's digits=5) — used here as the test fixture value,
not invented.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from execution.ctrader_client import (
    CTraderClient,
    CTraderOrder,
    ConnectionState,
    _SymbolDetails,
    _price_to_relative_units,
    _round_price_to_digits,
)
from risk.pretrade_limits import manual_override_context


# ─── _round_price_to_digits / _price_to_relative_units (pure math) ────────

def test_round_price_to_digits_eurusd_5dp():
    assert _round_price_to_digits(1.234567891, 5) == Decimal("1.23457")


def test_round_price_to_digits_ethusd_2dp():
    # A distance with float noise well past ETHUSD's real 2-decimal
    # precision — exactly the shape of value place_market_order() used to
    # send raw, unrounded, straight into "invalid precision."
    assert _round_price_to_digits(12.345678901234, 2) == Decimal("12.35")


def test_round_price_to_digits_never_reimports_binary_float_noise():
    # Decimal(str(x)) (not Decimal(x)) is the whole point of this helper —
    # 0.1 + 0.2 in raw binary float is 0.30000000000000004.
    value = 0.1 + 0.2
    assert _round_price_to_digits(value, 2) == Decimal("0.30")


def test_price_to_relative_units_is_exact_multiple_of_tick_scale():
    # digits=2 -> every rounded Decimal is a multiple of 0.01 -> every
    # relative unit must be an exact multiple of 100000 // 10**2 = 1000.
    dist = _round_price_to_digits(12.345678901234, 2)
    rel = _price_to_relative_units(dist, 100_000)
    assert rel == 1_235_000
    assert rel % 1000 == 0


def test_price_to_relative_units_eurusd_5dp_multiple_of_1():
    dist = _round_price_to_digits(0.00308, 5)
    rel = _price_to_relative_units(dist, 100_000)
    assert rel == 308
    assert rel % 1 == 0  # digits=5 -> every integer is already a valid multiple


# ─── _compute_relative_sl_tp — the real ETHUSD reproduction ────────────────

def _client_with_symbol(digits: int, symbol_id: int = 501) -> CTraderClient:
    c = CTraderClient(client_id="test", client_secret="test", account_id=12345, access_token="test")
    c._state = ConnectionState.READY
    c._symbol_name_to_id["ETHUSD"] = symbol_id
    c._symbol_details[symbol_id] = _SymbolDetails(
        symbol_id=symbol_id, digits=digits, pip_position=2,
        lot_size=100, min_volume=1, step_volume=1, max_volume=10_000,
    )
    return c


def test_ethusd_realistic_noisy_distance_produces_tick_aligned_relative_values():
    """The literal reproduction: a BUY ETHUSD order whose entry/SL prices
    (as any real Python float computation naturally produces) don't land
    on an exact 2-decimal boundary. Before this fix, rel_sl would be
    computed from the RAW unrounded distance and could fail cTrader's
    'invalid precision' check; after this fix it is always an exact
    multiple of the symbol's own tick scale."""
    client = _client_with_symbol(digits=2)
    # Ask=3214.6789..., not a clean cent value — matches how a real signal
    # entry price (itself derived from indicator math) looks in practice.
    ask_scaled = int(round(3214.678912345 * client.SPOT_SCALE))
    bid_scaled = int(round(3214.628912345 * client.SPOT_SCALE))
    order = CTraderOrder(
        symbol="ETHUSD", direction="BUY", volume=200,
        stop_loss=3200.111111111, take_profit=3250.999999999,
    )
    with patch.object(client, "_get_spot_scaled", return_value=(bid_scaled, ask_scaled)):
        rel_sl, rel_tp, entry_price, error = client._compute_relative_sl_tp(
            order, "ETHUSD", 501, client._symbol_details[501]
        )
    assert error == ""
    assert rel_sl > 0 and rel_tp > 0
    tick_units = client.SPOT_SCALE // 10 ** 2  # digits=2 -> 1000
    assert rel_sl % tick_units == 0, f"rel_sl={rel_sl} not tick-aligned for digits=2"
    assert rel_tp % tick_units == 0, f"rel_tp={rel_tp} not tick-aligned for digits=2"


def test_eurusd_5digit_precision_still_computes_correctly():
    """FX majors (digits=5) never triggered the bug in practice, but this
    pins that the fix doesn't regress the common case."""
    client = _client_with_symbol(digits=5, symbol_id=1)
    client._symbol_name_to_id["EURUSD"] = 1
    ask_scaled = int(round(1.08543 * client.SPOT_SCALE))
    bid_scaled = int(round(1.08533 * client.SPOT_SCALE))
    order = CTraderOrder(
        symbol="EURUSD", direction="BUY", volume=100,
        stop_loss=1.08300, take_profit=1.09000,
    )
    with patch.object(client, "_get_spot_scaled", return_value=(bid_scaled, ask_scaled)):
        rel_sl, rel_tp, entry_price, error = client._compute_relative_sl_tp(
            order, "EURUSD", 1, client._symbol_details[1]
        )
    assert error == ""
    assert rel_sl == pytest.approx(243, abs=2)
    assert rel_tp == pytest.approx(457, abs=2)


def test_no_sl_or_tp_requested_is_a_pure_noop():
    client = _client_with_symbol(digits=2)
    order = CTraderOrder(symbol="ETHUSD", direction="BUY", volume=200, stop_loss=0, take_profit=0)
    rel_sl, rel_tp, entry_price, error = client._compute_relative_sl_tp(
        order, "ETHUSD", 501, client._symbol_details[501]
    )
    assert (rel_sl, rel_tp, entry_price, error) == (0, 0, 0.0, "")


def test_no_live_spot_refuses_rather_than_guessing():
    client = _client_with_symbol(digits=2)
    order = CTraderOrder(symbol="ETHUSD", direction="BUY", volume=200, stop_loss=3200.0, take_profit=3300.0)
    with patch.object(client, "_get_spot_scaled", return_value=None):
        rel_sl, rel_tp, entry_price, error = client._compute_relative_sl_tp(
            order, "ETHUSD", 501, client._symbol_details[501]
        )
    assert rel_sl == 0 and rel_tp == 0
    assert "No live spot price" in error


def test_buy_sl_above_entry_is_rejected_directionally():
    client = _client_with_symbol(digits=2)
    ask_scaled = int(round(3214.67 * client.SPOT_SCALE))
    order = CTraderOrder(symbol="ETHUSD", direction="BUY", volume=200, stop_loss=3300.0, take_profit=0)
    with patch.object(client, "_get_spot_scaled", return_value=(ask_scaled, ask_scaled)):
        rel_sl, rel_tp, entry_price, error = client._compute_relative_sl_tp(
            order, "ETHUSD", 501, client._symbol_details[501]
        )
    assert "must be below entry" in error


# ─── Fail-closed on missing/implausible symbol metadata ────────────────────

def test_place_market_order_refuses_when_symbol_details_never_fetched():
    client = _client_with_symbol(digits=2)
    client._symbol_details.pop(501)  # never arrived / fetch failed
    with patch.object(client, "_ensure_symbol_details", return_value=None), \
         manual_override_context("test_prec_1", "unit test: missing symbol details"):
        result = client.place_market_order(
            CTraderOrder(symbol="ETHUSD", direction="BUY", volume=200, stop_loss=3200.0,
                         take_profit=3300.0, decision_id="test_prec_1")
        )
    assert result.success is False
    assert "lotSize" in result.error


def test_place_market_order_refuses_on_implausible_digits():
    """FAIL CLOSED (per the live-execution fix's own requirement): a
    digits value outside any real symbol's range means stale/corrupt
    metadata — refuse rather than guess a precision."""
    client = _client_with_symbol(digits=99)
    with patch.object(client, "_ensure_symbol_details", return_value=client._symbol_details[501]), \
         manual_override_context("test_prec_2", "unit test: implausible digits"):
        result = client.place_market_order(
            CTraderOrder(symbol="ETHUSD", direction="BUY", volume=200, stop_loss=3200.0,
                         take_profit=3300.0, decision_id="test_prec_2")
        )
    assert result.success is False
    assert "Implausible digits" in result.error


def test_place_market_order_refuses_when_symbol_not_in_loaded_list():
    client = CTraderClient(client_id="test", client_secret="test", account_id=12345, access_token="test")
    client._state = ConnectionState.READY
    with manual_override_context("test_prec_3", "unit test: symbol not in loaded list"):
        result = client.place_market_order(
            CTraderOrder(symbol="ETHUSD", direction="BUY", volume=200, stop_loss=3200.0,
                         take_profit=3300.0, decision_id="test_prec_3")
        )
    assert result.success is False
    assert "not in loaded symbol list" in result.error


# ─── ProtoOA message dispatcher: Subscribe/UnsubscribeSpotsRes ─────────────

def test_subscribe_spots_res_does_not_hit_unhandled_warning_path(caplog):
    import logging

    # These tests don't have the real ctrader_open_api package installed
    # (requirements-ctrader.txt, split out — see module docstring) — a
    # stand-in class whose __class__.__name__ matches the real proto type
    # name is enough, since _on_message dispatches purely on that string.
    class ProtoOASubscribeSpotsRes:
        pass

    class ProtoOAUnsubscribeSpotsRes:
        pass

    client = CTraderClient(client_id="test", client_secret="test", account_id=12345, access_token="test")
    caplog.set_level(logging.WARNING, logger="execution.ctrader_client")
    client._on_message(None, ProtoOASubscribeSpotsRes())
    client._on_message(None, ProtoOAUnsubscribeSpotsRes())
    assert not any("Unhandled message type" in r.message for r in caplog.records)
