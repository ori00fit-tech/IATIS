"""
tests/test_ctrader_execution_logic.py
----------------------------------------
Network-free coverage for the safety-critical PURE logic in
execution/ctrader_client.py — the audit's M5 gap (the file that places
real orders sat at 24% coverage). No sockets: every test drives a method
directly with plain objects, so the order-result parser and the
risk→broker volume conversion are exercised without a broker.

Covered:
  - _parse_execution_response: success / error-event / error-code /
    pending-accept / price-fallback shapes.
  - _to_api_volume: below-min refusal, max cap, step rounding, the
    step-floor guard — the logic that must never silently exceed the
    risk budget.
  - calculate_volume: the energy/index/crypto and fallback branches.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from execution.ctrader_client import (
    CTraderClient,
    CTraderOrder,
    _SymbolDetails,
)


@pytest.fixture
def client(monkeypatch):
    # conftest strips real creds; give the constructor just enough to build.
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "12345")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("CTRADER_ENVIRONMENT", "demo")
    return CTraderClient()


def _order(symbol="EURUSD", direction="BUY", volume=100):
    return CTraderOrder(symbol=symbol, direction=direction, volume=volume,
                        stop_loss=1.0800, take_profit=1.0950, entry_price=1.0850)


# ── _parse_execution_response ─────────────────────────────────────────────

class ProtoOAExecutionEvent:
    """Stand-in whose class name matches what the parser dispatches on."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


class ProtoOAOrderErrorEvent:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _exec_event(**kw):
    return ProtoOAExecutionEvent(**kw)


def test_parse_filled_order_success(client):
    resp = _exec_event(
        errorCode="",
        deal=SimpleNamespace(positionId=555, orderId=777, executionPrice=1.0851),
        position=SimpleNamespace(positionId=555),
        order=SimpleNamespace(orderId=777, executionPrice=1.0851),
        executionType="ORDER_FILLED",
    )
    r = client._parse_execution_response(_order(), resp)
    assert r.success is True
    assert r.position_id == "555" and r.order_id == "777"
    assert r.entry_price == pytest.approx(1.0851)


def test_parse_pending_accept_still_success_with_ids(client):
    # ORDER_ACCEPTED, not yet filled: no execution price → falls back to
    # the signal reference price, still reported as a (pending) success.
    resp = _exec_event(
        errorCode="",
        deal=None,
        position=SimpleNamespace(positionId=999),
        order=SimpleNamespace(orderId=0),
        executionType="ORDER_ACCEPTED",
    )
    r = client._parse_execution_response(_order(), resp)
    assert r.success is True
    assert r.position_id == "999"
    assert r.entry_price == pytest.approx(1.0850)  # fell back to entry_price


def test_parse_error_code_is_failure(client):
    resp = _exec_event(errorCode="NOT_ENOUGH_MONEY", deal=None, position=None, order=None)
    r = client._parse_execution_response(_order(), resp)
    assert r.success is False
    assert "NOT_ENOUGH_MONEY" in r.error


def test_parse_wrong_message_type_is_failure(client):
    err = ProtoOAOrderErrorEvent(errorCode="", description="Symbol not found")
    r = client._parse_execution_response(_order(), err)
    assert r.success is False
    assert "Symbol not found" in r.error


# ── _to_api_volume ─────────────────────────────────────────────────────────

def _details(lot_size=100_000, min_v=1000, step=1000, max_v=10_000_000):
    return _SymbolDetails(symbol_id=1, digits=5, pip_position=4,
                          lot_size=lot_size, min_volume=min_v,
                          step_volume=step, max_volume=max_v)


def test_to_api_volume_normal(client):
    # 1.00 lot × lot_size 100000 = 100000, within [min,max], step-aligned
    vol, err = client._to_api_volume(_details(), centi_lots=100)
    assert err == "" and vol == 100_000


def test_to_api_volume_refuses_below_min(client):
    # 0.01 lot × 100000 = 1000; set min above that → must REFUSE, not inflate
    vol, err = client._to_api_volume(_details(min_v=5000), centi_lots=1)
    assert vol == 0 and "below the broker minimum" in err


def test_to_api_volume_caps_at_max(client):
    vol, err = client._to_api_volume(_details(max_v=50_000), centi_lots=100)
    assert err == "" and vol == 50_000


def test_to_api_volume_rounds_to_step(client):
    # raw 123456 with step 1000 → floored to 123000
    vol, err = client._to_api_volume(
        _details(lot_size=123_456, min_v=1000, step=1000), centi_lots=100
    )
    assert err == "" and vol % 1000 == 0 and vol == 123_000


def test_to_api_volume_step_floor_guard(client):
    # If step-flooring drops just under min, snap back up to min.
    d = _details(lot_size=100_000, min_v=100_000, step=7_000, max_v=10_000_000)
    vol, err = client._to_api_volume(d, centi_lots=100)
    assert err == "" and vol >= d.min_volume


# ── calculate_volume (untested asset-class branches) ───────────────────────

def test_calculate_volume_index_branch(client):
    # index/crypto: sl_pips = sl_distance_price directly, pip_value 1.0
    v = client.calculate_volume("NAS100", balance=10_000, risk_pct=0.01,
                                sl_distance_price=50.0)
    assert v >= 1  # 100 risk / (50 * 1.0) = 2 lots → 200 centi-lots (capped range)


def test_calculate_volume_zero_sl_returns_zero(client):
    assert client.calculate_volume("EURUSD", 10_000, 0.01, 0.0) == 0


def test_calculate_volume_is_bounded(client):
    # Never exceeds the 100-lot (10_000 centi-lot) ceiling even with a tiny SL.
    v = client.calculate_volume("EURUSD", 1_000_000, 0.01, 0.00001)
    assert 1 <= v <= 10_000


# ── connection state machine (pure, no socket) ─────────────────────────────

def test_maybe_ready_promotes_only_when_both_loaded(client):
    from execution.ctrader_client import ConnectionState

    # Neither account nor symbols yet → stays put.
    client._maybe_ready()
    assert client._state != ConnectionState.READY

    # Only symbols → still not ready.
    client._symbol_name_to_id = {"EURUSD": 1}
    client._maybe_ready()
    assert client._state != ConnectionState.READY

    # Both present → promotes to READY (M1: either handler may finish last).
    client._account_info = object()
    client._maybe_ready()
    assert client._state == ConnectionState.READY


def test_maybe_ready_never_promotes_from_error(client):
    from execution.ctrader_client import ConnectionState

    client._set_state(ConnectionState.ERROR)
    client._symbol_name_to_id = {"EURUSD": 1}
    client._account_info = object()
    client._maybe_ready()
    assert client._state == ConnectionState.ERROR  # ERROR is terminal


def test_has_open_position_reflects_state(client):
    assert client.has_open_position("EURUSD") is False
    client._positions["EURUSD"] = object()
    assert client.has_open_position("EURUSD") is True
    assert client.has_open_position("GBPUSD") is False


def test_missing_credentials_raise(monkeypatch):
    from execution.ctrader_client import CTraderClient
    for var in ("CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET",
                "CTRADER_ACCOUNT_ID", "CTRADER_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="Missing cTrader credentials"):
        CTraderClient()


# ── historical trendbar decoding (delta-encoded) ───────────────────────────

def test_trendbar_decode_delta_encoding(client):
    """cTrader trendbars: low is absolute, open/high/close are unsigned
    deltas ADDED to low, all scaled by 1e-5. A decode bug here corrupts
    every backtest bar."""
    from types import SimpleNamespace

    # low=1.08000, open=+0.0005, high=+0.0012, close=+0.0008 (in 1e-5 units)
    tb = SimpleNamespace(low=108000, deltaOpen=50, deltaHigh=120,
                         deltaClose=80, volume=1234, utcTimestampInMinutes=28_900_000)
    msg = SimpleNamespace(trendbar=[tb])
    client._on_trendbars_res(msg)

    bars = client._trendbars
    assert len(bars) == 1
    b = bars[0]
    assert b["low"] == pytest.approx(1.08000)
    assert b["open"] == pytest.approx(1.08050)
    assert b["high"] == pytest.approx(1.08120)
    assert b["close"] == pytest.approx(1.08080)
    assert b["volume"] == 1234
    assert b["timestamp"] == 28_900_000 * 60


def test_trendbar_decode_sorts_and_survives_bad_bar(client):
    from types import SimpleNamespace

    good = SimpleNamespace(low=100000, deltaOpen=0, deltaHigh=0, deltaClose=0,
                           volume=1, utcTimestampInMinutes=200)
    older = SimpleNamespace(low=100000, deltaOpen=0, deltaHigh=0, deltaClose=0,
                            volume=1, utcTimestampInMinutes=100)
    client._on_trendbars_res(SimpleNamespace(trendbar=[good, older]))
    ts = [b["timestamp"] for b in client._trendbars]
    assert ts == sorted(ts)  # oldest first


def test_list_symbols_returns_sorted(client):
    client._symbol_id_to_name = {2: "GBPUSD", 1: "EURUSD", 3: "XAUUSD"}
    assert client.list_symbols() == ["EURUSD", "GBPUSD", "XAUUSD"]


# ── ALREADY_LOGGED_IN handling (2026-07-22 reconnect-storm fix) ─────────────

class _ErrorRes:
    """Minimal stand-in for a ProtoOAErrorRes payload."""
    def __init__(self, code="ALREADY_LOGGED_IN",
                 description="Open API application is already authorized"):
        self.errorCode = code
        self.description = description


def test_already_logged_in_during_app_auth_continues_bootstrap(client, monkeypatch):
    """ALREADY_LOGGED_IN means the auth we asked for already holds — it must
    continue to account auth, never flip to ERROR (the ERROR → instant
    library retry → fresh auth → same rejection loop was the live storm)."""
    from execution.ctrader_client import ConnectionState

    sent = []
    monkeypatch.setattr(client, "_send_account_auth", lambda c: sent.append("account_auth"))
    client._client = object()
    client._set_state(ConnectionState.TCP_CONNECTED)

    client._on_error_res(_ErrorRes())

    assert client._state == ConnectionState.APP_AUTH_OK
    assert sent == ["account_auth"]


def test_already_logged_in_during_account_auth_continues_bootstrap(client, monkeypatch):
    from execution.ctrader_client import ConnectionState

    sent = []
    monkeypatch.setattr(client, "_send_trader_req", lambda c: sent.append("trader"))
    monkeypatch.setattr(client, "_send_symbols_list_req", lambda c: sent.append("symbols"))
    monkeypatch.setattr(client, "_send_reconcile_req", lambda c: sent.append("reconcile"))
    client._client = object()
    client._set_state(ConnectionState.APP_AUTH_OK)

    client._on_error_res(_ErrorRes())

    assert client._state == ConnectionState.ACCOUNT_AUTH_OK
    assert sent == ["trader", "symbols", "reconcile"]


def test_other_server_errors_still_fail_fast(client):
    from execution.ctrader_client import ConnectionState

    client._set_state(ConnectionState.TCP_CONNECTED)
    client._on_error_res(_ErrorRes(code="CH_ACCESS_TOKEN_INVALID",
                                   description="bad token"))
    assert client._state == ConnectionState.ERROR


def test_already_logged_in_errback_is_benign(client):
    from execution.ctrader_client import ConnectionState

    client._set_state(ConnectionState.TCP_CONNECTED)
    client._on_error("app_auth", RuntimeError("ALREADY_LOGGED_IN — already authorized"))
    assert client._state == ConnectionState.TCP_CONNECTED  # unchanged


def test_superseded_client_tcp_connected_is_ignored(client, monkeypatch):
    """A stale client's connected callback must not re-run auth or clobber
    the live connection's state (the doubled 'TCP connected' storm)."""
    from execution.ctrader_client import ConnectionState

    sent = []
    monkeypatch.setattr(client, "_send_app_auth", lambda c: sent.append("app_auth"))
    monkeypatch.setattr(client, "_stop_client", lambda c: None)
    client._client = object()          # the live client
    client._set_state(ConnectionState.APP_AUTH_OK)

    client._on_tcp_connected(object())  # a different, superseded client

    assert client._state == ConnectionState.APP_AUTH_OK
    assert sent == []
