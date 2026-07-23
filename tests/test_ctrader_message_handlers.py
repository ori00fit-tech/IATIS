"""
tests/test_ctrader_message_handlers.py
-----------------------------------------
Unit coverage for execution/ctrader_client.py's inbound-message handlers
(_on_trader_res, _on_symbols_list_res, _on_symbol_details_res,
_on_reconcile_res, _on_execution_event, _on_order_error_event) — part of
closing the coverage gap flagged by docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md
P0-2 (this module was live on the cTrader demo account below its own
declared 60% coverage precondition).

These handlers all read their input via getattr(msg, field, default), so a
plain types.SimpleNamespace stands in for the real protobuf message without
needing the ctrader-open-api package installed (it isn't, in CI's base
requirements.txt — see requirements-ctrader.txt). No network, no reactor.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from execution.ctrader_client import CTraderClient, CTraderOrder, ConnectionState


@pytest.fixture
def client() -> CTraderClient:
    return CTraderClient(
        client_id="test", client_secret="test",
        account_id=12345, access_token="test",
    )


# ── _on_trader_res ──────────────────────────────────────────────────────

def test_on_trader_res_populates_account_info_and_scales_balance(client, monkeypatch):
    monkeypatch.setenv("CTRADER_ACCOUNT_CURRENCY", "USD")
    msg = SimpleNamespace(
        trader=SimpleNamespace(
            ctidTraderAccountId=12345, balance=1_000_00, moneyDigits=2,
            leverageInCents=3000,
        )
    )
    client._on_trader_res(msg)

    info = client._account_info
    assert info is not None
    assert info.balance == pytest.approx(1000.0)
    assert info.equity == pytest.approx(1000.0)
    assert info.leverage == 30
    assert info.currency == "USD"


def test_on_trader_res_missing_trader_field_does_not_raise(client):
    client._on_trader_res(SimpleNamespace())  # no `.trader` attribute
    assert client._account_info is None


def test_on_trader_res_promotes_to_ready_once_symbols_already_loaded(client):
    client._symbol_name_to_id = {"EURUSD": 1}  # simulate symbols already loaded
    msg = SimpleNamespace(
        trader=SimpleNamespace(ctidTraderAccountId=1, balance=100, moneyDigits=2, leverageInCents=0)
    )
    client._on_trader_res(msg)
    assert client._state == ConnectionState.READY


# ── _on_symbols_list_res ─────────────────────────────────────────────────

def test_on_symbols_list_res_populates_maps_and_sets_state(client):
    symbols = [
        SimpleNamespace(symbolName="EURUSD", symbolId=1),
        SimpleNamespace(symbolName="XAUUSD", symbolId=2),
    ]
    client._on_symbols_list_res(client=None, message=SimpleNamespace(symbol=symbols))

    assert client._symbol_name_to_id["EURUSD"] == 1
    assert client._symbol_id_to_name[2] == "XAUUSD"
    assert client._state == ConnectionState.SYMBOLS_LOADED


def test_on_symbols_list_res_empty_symbol_list_is_a_noop(client):
    client._on_symbols_list_res(client=None, message=SimpleNamespace(symbol=[]))
    assert client._symbol_name_to_id == {}
    assert client._state != ConnectionState.SYMBOLS_LOADED


def test_on_symbols_list_res_promotes_to_ready_once_account_already_loaded(client):
    from execution.ctrader_client import AccountInfo
    client._account_info = AccountInfo(
        account_id=1, balance=100.0, equity=100.0, margin_used=0.0,
        margin_free=100.0, currency="USD", leverage=30,
    )
    symbols = [SimpleNamespace(symbolName="EURUSD", symbolId=1)]
    client._on_symbols_list_res(client=None, message=SimpleNamespace(symbol=symbols))
    assert client._state == ConnectionState.READY


# ── _on_symbol_details_res ───────────────────────────────────────────────

def test_on_symbol_details_res_stores_specs(client):
    symbols = [
        SimpleNamespace(
            symbolId=7, digits=5, pipPosition=4, lotSize=100_000,
            minVolume=1000, stepVolume=1000, maxVolume=500_000,
        )
    ]
    client._on_symbol_details_res(SimpleNamespace(symbol=symbols))

    d = client._symbol_details[7]
    assert d.lot_size == 100_000
    assert d.min_volume == 1000
    assert d.step_volume == 1000
    assert d.max_volume == 500_000
    assert client._details_event.is_set()


def test_on_symbol_details_res_empty_list_does_not_set_event(client):
    client._on_symbol_details_res(SimpleNamespace(symbol=[]))
    assert client._symbol_details == {}
    assert not client._details_event.is_set()


# ── _on_reconcile_res ─────────────────────────────────────────────────────

def test_on_reconcile_res_rebuilds_positions_from_broker_truth(client):
    client._symbol_id_to_name[1] = "EURUSD"  # broker name → maps via CTRADER_TO_IATIS
    positions = [
        SimpleNamespace(
            positionId=999,
            price=1.0850,
            stopLoss=1.0800,
            takeProfit=1.0950,
            tradeData=SimpleNamespace(symbolId=1, tradeSide=1, volume=100_000),
        )
    ]
    client._on_reconcile_res(SimpleNamespace(position=positions))

    pos = client._positions["EURUSD"]
    assert pos.position_id == "999"
    assert pos.direction == "BUY"
    assert pos.entry_price == pytest.approx(1.0850)
    assert pos.stop_loss == pytest.approx(1.0800)


def test_on_reconcile_res_no_positions_leaves_map_untouched(client):
    client._on_reconcile_res(SimpleNamespace(position=[]))
    assert client._positions == {}


def test_on_reconcile_res_unresolved_symbol_falls_back_to_synthetic_key(client):
    # No entry in _symbol_id_to_name for symbolId=999 — must not collapse
    # onto a shared "" key alongside other unresolved positions.
    positions = [
        SimpleNamespace(
            positionId=1, price=100.0, stopLoss=0.0, takeProfit=0.0,
            tradeData=SimpleNamespace(symbolId=999, tradeSide=2, volume=1000),
        )
    ]
    client._on_reconcile_res(SimpleNamespace(position=positions))
    assert "SYMBOL_999" in client._positions
    assert client._positions["SYMBOL_999"].direction == "SELL"


def test_on_reconcile_res_clears_stale_positions_not_in_the_new_snapshot(client):
    from execution.ctrader_client import OpenPosition
    client._positions["STALE"] = OpenPosition(
        position_id="1", symbol="STALE", direction="BUY", volume=1000,
        entry_price=1.0, current_price=1.0, unrealized_pnl=0.0,
        stop_loss=0.0, take_profit=0.0,
    )
    client._on_reconcile_res(SimpleNamespace(position=[]))
    # Reconcile is broker-truth: no positions reported means none are open.
    assert client._positions == {}


# ── _on_execution_event ──────────────────────────────────────────────────

def test_on_execution_event_tracks_a_new_position(client):
    client._symbol_id_to_name[1] = "EURUSD"
    position = SimpleNamespace(
        positionId=42, positionStatus=1, price=1.10,
        stopLoss=1.09, takeProfit=1.12,
        tradeData=SimpleNamespace(symbolId=1, tradeSide=1, volume=100_000),
    )
    client._on_execution_event(SimpleNamespace(executionType=2, position=position))

    pos = client._positions["EURUSD"]
    assert pos.position_id == "42"
    assert pos.direction == "BUY"


def test_on_execution_event_removes_closed_position(client):
    from execution.ctrader_client import OpenPosition
    client._symbol_id_to_name[1] = "EURUSD"
    client._positions["EURUSD"] = OpenPosition(
        position_id="42", symbol="EURUSD", direction="BUY", volume=100_000,
        entry_price=1.10, current_price=1.10, unrealized_pnl=0.0,
        stop_loss=1.09, take_profit=1.12,
    )
    position = SimpleNamespace(
        positionId=42, positionStatus=2, price=1.11,
        tradeData=SimpleNamespace(symbolId=1, tradeSide=1, volume=100_000),
    )
    client._on_execution_event(SimpleNamespace(executionType=3, position=position))

    assert "EURUSD" not in client._positions


def test_on_execution_event_missing_position_is_a_noop(client):
    client._on_execution_event(SimpleNamespace(executionType=1, position=None))
    assert client._positions == {}


def test_on_execution_event_zero_position_id_is_ignored(client):
    position = SimpleNamespace(positionId=0)
    client._on_execution_event(SimpleNamespace(executionType=1, position=position))
    assert client._positions == {}


# ── _on_order_error_event ────────────────────────────────────────────────

def test_on_order_error_event_never_raises(client):
    client._on_order_error_event(
        SimpleNamespace(errorCode="TRADING_DISABLED", description="account restricted")
    )  # must not raise; nothing to assert beyond that — it only logs


# ── auth-chain response handlers (_on_app_auth / _on_account_auth) ─────────

def _fake_response(class_name: str, **fields):
    """A minimal object whose __class__.__name__ matches a protobuf type
    name, since _extract()/the handlers below dispatch on that string."""
    cls = type(class_name, (), {})
    obj = cls()
    for k, v in fields.items():
        setattr(obj, k, v)
    return obj


def test_on_app_auth_success_advances_state_and_sends_account_auth(client, monkeypatch):
    sent = []
    monkeypatch.setattr(client, "_send_account_auth", lambda c: sent.append(c))
    client._on_app_auth("fake-client", _fake_response("ProtoOAApplicationAuthRes"))
    assert client._state == ConnectionState.APP_AUTH_OK
    assert sent == ["fake-client"]


def test_on_app_auth_unexpected_response_sets_error_state(client, monkeypatch):
    monkeypatch.setattr(client, "_send_account_auth", lambda c: pytest.fail("must not proceed"))
    client._on_app_auth("fake-client", _fake_response("ProtoOAErrorRes", errorCode="X"))
    assert client._state == ConnectionState.ERROR


def test_on_account_auth_success_advances_state_and_fans_out(client, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_send_trader_req", lambda c: calls.append("trader"))
    monkeypatch.setattr(client, "_send_symbols_list_req", lambda c: calls.append("symbols"))
    monkeypatch.setattr(client, "_send_reconcile_req", lambda c: calls.append("reconcile"))

    client._on_account_auth("fake-client", _fake_response("ProtoOAAccountAuthRes"))

    assert client._state == ConnectionState.ACCOUNT_AUTH_OK
    assert calls == ["trader", "symbols", "reconcile"]


def test_on_account_auth_unexpected_response_sets_error_state(client, monkeypatch):
    monkeypatch.setattr(client, "_send_trader_req", lambda c: pytest.fail("must not proceed"))
    client._on_account_auth("fake-client", _fake_response("ProtoOAErrorRes"))
    assert client._state == ConnectionState.ERROR


# ── _send_* bootstrap requests (ctrader_open_api not installed here, so
# these exercise the ImportError branch — the same branch a genuinely
# missing/renamed protobuf message would hit in production) ────────────────

def test_send_app_auth_missing_dependency_sets_error_state(client):
    client._send_app_auth(client=object())
    assert client._state == ConnectionState.ERROR


def test_send_account_auth_missing_dependency_sets_error_state(client):
    client._send_account_auth(client=object())
    assert client._state == ConnectionState.ERROR


def test_send_trader_req_missing_dependency_does_not_raise(client):
    client._send_trader_req(client=object())  # logs and returns, no state change


def test_send_symbols_list_req_missing_dependency_does_not_raise(client):
    client._send_symbols_list_req(client=object())


def test_send_reconcile_req_missing_dependency_warns_without_raising(client):
    client._send_reconcile_req(client=object())  # ImportError branch, no state change


# ── _on_trendbars_res (delta-encoded OHLCV decode) ──────────────────────────

def test_on_trendbars_res_decodes_delta_encoded_bars(client):
    tb = SimpleNamespace(
        low=110_000_00, deltaOpen=50_00, deltaHigh=80_00, deltaClose=20_00,
        utcTimestampInMinutes=1000, volume=42,
    )
    client._on_trendbars_res(SimpleNamespace(trendbar=[tb]))

    assert client._trendbar_event.is_set()
    bars = client._trendbars
    assert len(bars) == 1
    b = bars[0]
    assert b["low"] == pytest.approx(110.0)
    assert b["open"] == pytest.approx(110.05)
    assert b["high"] == pytest.approx(110.08)
    assert b["close"] == pytest.approx(110.02)
    assert b["timestamp"] == 1000 * 60
    assert b["volume"] == 42


def test_on_trendbars_res_sorts_by_timestamp(client):
    early = SimpleNamespace(low=100_000_00, deltaOpen=0, deltaHigh=0, deltaClose=0,
                            utcTimestampInMinutes=5, volume=0)
    late = SimpleNamespace(low=100_000_00, deltaOpen=0, deltaHigh=0, deltaClose=0,
                           utcTimestampInMinutes=50, volume=0)
    client._on_trendbars_res(SimpleNamespace(trendbar=[late, early]))
    assert [b["timestamp"] for b in client._trendbars] == [5 * 60, 50 * 60]


def test_on_trendbars_res_malformed_bar_clears_and_sets_event(client):
    bad = SimpleNamespace(low="not-a-number")
    client._on_trendbars_res(SimpleNamespace(trendbar=[bad]))
    assert client._trendbars == []
    assert client._trendbar_event.is_set()  # callers waiting must not hang


def test_get_trendbars_unknown_symbol_returns_empty_without_reactor(client):
    assert client.get_trendbars("NOT_A_REAL_SYMBOL") == []


# ── simple getters / lifecycle ───────────────────────────────────────────

def test_get_spot_unmapped_iatis_symbol_returns_none(client):
    assert client.get_spot("NOT_A_REAL_SYMBOL") is None


def test_get_spot_by_name_unknown_broker_symbol_returns_none(client):
    assert client.get_spot_by_name("NOT_A_REAL_SYMBOL") is None


def test_list_symbols_returns_sorted_broker_names(client):
    client._symbol_id_to_name = {2: "XAUUSD", 1: "EURUSD"}
    assert client.list_symbols() == ["EURUSD", "XAUUSD"]


def test_has_open_position_reflects_positions_map(client):
    from execution.ctrader_client import OpenPosition
    assert client.has_open_position("EURUSD") is False
    client._positions["EURUSD"] = OpenPosition(
        position_id="1", symbol="EURUSD", direction="BUY", volume=1000,
        entry_price=1.0, current_price=1.0, unrealized_pnl=0.0,
        stop_loss=0.0, take_profit=0.0,
    )
    assert client.has_open_position("EURUSD") is True


def test_get_open_positions_returns_snapshot_list(client):
    from execution.ctrader_client import OpenPosition
    assert client.get_open_positions() == []
    client._positions["EURUSD"] = OpenPosition(
        position_id="1", symbol="EURUSD", direction="BUY", volume=1000,
        entry_price=1.0, current_price=1.0, unrealized_pnl=0.0,
        stop_loss=0.0, take_profit=0.0,
    )
    positions = client.get_open_positions()
    assert len(positions) == 1 and positions[0].symbol == "EURUSD"


def test_get_account_info_not_ready_returns_none(client):
    assert client._state != ConnectionState.READY
    assert client.get_account_info() is None


def test_get_account_info_ready_but_never_populated_returns_none(client):
    client._state = ConnectionState.READY
    assert client.get_account_info() is None


def test_test_connection_false_when_connect_fails(client, monkeypatch):
    monkeypatch.setattr(client, "connect", lambda timeout=10.0: False)
    assert client.test_connection() is False


def test_test_connection_false_when_account_info_missing(client, monkeypatch):
    monkeypatch.setattr(client, "connect", lambda timeout=10.0: True)
    monkeypatch.setattr(client, "get_account_info", lambda: None)
    assert client.test_connection() is False


def test_test_connection_true_when_connected_with_account_info(client, monkeypatch):
    from execution.ctrader_client import AccountInfo
    monkeypatch.setattr(client, "connect", lambda timeout=10.0: True)
    monkeypatch.setattr(client, "get_account_info", lambda: AccountInfo(
        account_id=1, balance=100.0, equity=100.0, margin_used=0.0,
        margin_free=100.0, currency="USD", leverage=30,
    ))
    assert client.test_connection() is True


def test_disconnect_with_no_client_is_a_noop(client):
    client._client = None
    client.disconnect()
    assert client._state == ConnectionState.DISCONNECTED
    assert client._intentional_disconnect is True


def test_disconnect_marks_intentional_before_tearing_down(client):
    client._client = object()  # no .stopService; reactor.running check will fail safely
    client.disconnect()
    assert client._intentional_disconnect is True
    assert client._state == ConnectionState.DISCONNECTED


# ── _on_error ────────────────────────────────────────────────────────────

def test_on_error_already_logged_in_is_benign_and_does_not_set_error_state(client):
    client._state = ConnectionState.APP_AUTH_OK
    client._on_error("app_auth", RuntimeError("ALREADY_LOGGED_IN — already authorized"))
    assert client._state == ConnectionState.APP_AUTH_OK  # unchanged, not ERROR


def test_on_error_real_failure_sets_error_state(client):
    client._on_error("trader_req", RuntimeError("TIMEOUT waiting for response"))
    assert client._state == ConnectionState.ERROR


def test_on_error_uses_get_error_message_when_available(client):
    class _Failure:
        def getErrorMessage(self):
            return "ALREADY_LOGGED_IN"
    client._state = ConnectionState.ACCOUNT_AUTH_OK
    client._on_error("account_auth", _Failure())
    assert client._state == ConnectionState.ACCOUNT_AUTH_OK  # benign path taken


def test_on_error_already_logged_in_outside_auth_context_still_errors(client):
    """P3-4 regression: the benign swallow is scoped to the two auth-stage
    contexts. An ALREADY_LOGGED_IN-shaped message on any other request
    (reconcile, symbols_list, trader_req, symbol_details, trendbars) is
    unexpected, not benign, and must still surface as an error — the old
    check matched the substring across every _on_error call site."""
    client._state = ConnectionState.SYMBOLS_LOADED
    client._on_error("reconcile", RuntimeError("ALREADY_LOGGED_IN — already authorized"))
    assert client._state == ConnectionState.ERROR


# ── _on_message dispatcher ────────────────────────────────────────────────

def test_on_message_routes_trader_res_to_its_handler(client, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "_on_trader_res", lambda m: seen.append(m))
    msg = _fake_response("ProtoOATraderRes")
    client._on_message(client=None, message=msg)
    assert seen == [msg]


def test_on_message_routes_execution_event_to_its_handler(client, monkeypatch):
    seen = []
    monkeypatch.setattr(client, "_on_execution_event", lambda m: seen.append(m))
    msg = _fake_response("ProtoOAExecutionEvent")
    client._on_message(client=None, message=msg)
    assert seen == [msg]


def test_on_message_ignores_message_from_a_superseded_client(client, monkeypatch):
    current = object()
    stale = object()
    client._client = current
    called = []
    monkeypatch.setattr(client, "_on_trader_res", lambda m: called.append(m))
    client._on_message(client=stale, message=_fake_response("ProtoOATraderRes"))
    assert called == []


def test_on_message_heartbeat_and_unhandled_types_do_not_raise(client):
    client._on_message(client=None, message=_fake_response("ProtoHeartbeatEvent"))
    client._on_message(client=None, message=_fake_response("SomeUnknownType", errorCode="X"))
    # Auth responses are handled by their correlated callbacks, not routed here.
    client._on_message(client=None, message=_fake_response("ProtoOAApplicationAuthRes"))


def test_on_message_dispatcher_error_is_swallowed(client, monkeypatch):
    def _boom(m):
        raise RuntimeError("handler exploded")
    monkeypatch.setattr(client, "_on_trader_res", _boom)
    client._on_message(client=None, message=_fake_response("ProtoOATraderRes"))  # must not raise


# ── _parse_execution_response ───────────────────────────────────────────────
# Regression coverage for the 2026-07-23 production bug: every single live
# cTrader fill logged "TCA: fill ... missing intended/fill price — not
# recorded" (storage/execution_quality.py) because this parser only read
# deal.executionPrice / order.executionPrice, both empty on the actual
# ProtoOAExecutionEvent this account receives for a market-order fill — the
# real fill price was sitting on position.price the whole time (the same
# field _on_execution_event/_on_reconcile_res already trust elsewhere in
# this file).

def _order() -> CTraderOrder:
    return CTraderOrder(
        symbol="EURUSD", direction="BUY", volume=100_000,
        stop_loss=1.0800, take_profit=1.0950,
    )


def test_parse_execution_response_reads_price_from_position_when_deal_is_empty(client):
    position = SimpleNamespace(positionId=654120888, price=1.08765)
    response = _fake_response(
        "ProtoOAExecutionEvent", executionType=2, errorCode="",
        deal=None, position=position, order=None,
    )
    result = client._parse_execution_response(_order(), response)

    assert result.success is True
    assert result.entry_price == 1.08765
    assert result.position_id == "654120888"


def test_parse_execution_response_prefers_deal_execution_price_over_position(client):
    deal = SimpleNamespace(executionPrice=1.09000, positionId=1, orderId=1)
    position = SimpleNamespace(positionId=1, price=1.08765)
    response = _fake_response(
        "ProtoOAExecutionEvent", executionType=2, errorCode="",
        deal=deal, position=position, order=None,
    )
    result = client._parse_execution_response(_order(), response)

    assert result.entry_price == 1.09000


def test_parse_execution_response_falls_back_to_order_entry_price_when_nothing_else_is_set(client):
    response = _fake_response(
        "ProtoOAExecutionEvent", executionType=1, errorCode="",
        deal=None, position=None, order=None,
    )
    order = CTraderOrder(
        symbol="EURUSD", direction="BUY", volume=100_000,
        stop_loss=1.0800, take_profit=1.0950, entry_price=1.0875,
    )
    result = client._parse_execution_response(order, response)

    assert result.entry_price == 1.0875
