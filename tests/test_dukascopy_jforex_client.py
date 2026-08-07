"""tests/test_dukascopy_jforex_client.py

Regression coverage for execution/dukascopy_jforex_client.py — the
local dukas-api bridge client for Dukascopy JForex order execution.
No live network call is ever made; requests.get/post/put are mocked
throughout, matching every other bridge-client test in this repo
(tests/test_provider_chains.py's MT5/Dukascopy-data tests,
tests/test_ctrader_refresh_access_token.py).
"""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from execution.dukascopy_jforex_client import (
    DukascopyJForexClient,
    DukascopyJForexError,
    DukascopyJForexOrder,
)


def _fake_resp(status_code=200, json_data=None):
    resp = Mock()
    resp.status_code = status_code
    resp.raise_for_status = Mock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def test_client_raises_without_bridge_url(monkeypatch):
    monkeypatch.delenv("DUKASCOPY_JFOREX_BRIDGE_URL", raising=False)
    with pytest.raises(DukascopyJForexError, match="not set"):
        DukascopyJForexClient()


def test_client_environment_defaults_to_demo(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    monkeypatch.delenv("DUKASCOPY_JFOREX_ENVIRONMENT", raising=False)
    client = DukascopyJForexClient()
    assert client.environment == "demo"


def test_client_environment_reads_env_var(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    monkeypatch.setenv("DUKASCOPY_JFOREX_ENVIRONMENT", "live")
    client = DukascopyJForexClient()
    assert client.environment == "live"


# ── has_open_position (best-effort duplicate check) ────────────────────────

def test_has_open_position_false_on_404(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    with patch("requests.get", return_value=_fake_resp(404)):
        assert client.has_open_position("EURUSD") is False


def test_has_open_position_true_when_status_open(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    with patch("requests.get", return_value=_fake_resp(200, {"status": "OPEN"})) as mock_get:
        assert client.has_open_position("EURUSD") is True
    assert mock_get.call_args.kwargs["params"]["clientOrderID"] == "IATIS_EURUSD"


def test_has_open_position_false_when_status_closed(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    with patch("requests.get", return_value=_fake_resp(200, {"status": "CLOSED"})):
        assert client.has_open_position("EURUSD") is False


def test_has_open_position_false_on_empty_response(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    with patch("requests.get", return_value=_fake_resp(200, {})):
        assert client.has_open_position("EURUSD") is False


def test_has_open_position_degrades_to_false_on_exception(monkeypatch):
    """A bridge hiccup must never permanently block trading."""
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    with patch("requests.get", side_effect=Exception("connection refused")):
        assert client.has_open_position("EURUSD") is False


# ── place_market_order ──────────────────────────────────────────────────────

def _order(symbol="EURUSD", direction="BUY", quantity=0.01, sl=1.0920, tp=1.0640):
    return DukascopyJForexOrder(
        symbol=symbol, direction=direction, quantity=quantity,
        stop_loss=sl, take_profit=tp, client_order_id=f"IATIS_{symbol}",
    )


def test_place_market_order_success_attaches_sl_tp(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()

    post_resp = _fake_resp(200, {
        "orderSuccess": True, "dukasOrderID": "jf123", "fillPrice": 1.0850,
    })
    put_resp = _fake_resp(200, {})

    with patch("requests.post", return_value=post_resp) as mock_post, \
         patch("requests.put", return_value=put_resp) as mock_put:
        result = client.place_market_order(_order())

    assert result.success is True
    assert result.dukas_order_id == "jf123"
    assert result.fill_price == 1.0850
    mock_post.assert_called_once()
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["instID"] == "EURUSD"
    assert sent_payload["quantity"] == 0.01
    mock_put.assert_called_once()
    put_payload = mock_put.call_args.kwargs["json"]
    # entry 1.0850, sl 1.0920 -> 70 pips at 0.0001 pip size; tp 1.0640 -> 210 pips
    assert put_payload["stopLossPips"] == pytest.approx(70.0, abs=0.5)
    assert put_payload["takeProfitPips"] == pytest.approx(210.0, abs=0.5)


def test_place_market_order_rejected_by_bridge(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    post_resp = _fake_resp(200, {"orderSuccess": False, "rejectReason": "insufficient margin"})
    with patch("requests.post", return_value=post_resp):
        result = client.place_market_order(_order())

    assert result.success is False
    assert result.error == "insufficient margin"


def test_place_market_order_http_error(monkeypatch):
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    with patch("requests.post", side_effect=Exception("connection refused")):
        result = client.place_market_order(_order())

    assert result.success is False
    assert "Dukascopy JForex bridge" in result.error


def test_place_market_order_success_survives_sl_tp_attach_failure(monkeypatch):
    """A filled order must still report success even if SL/TP attach
    fails afterward — the position is real, just unprotected; the
    failure is logged loudly (module docstring), not swallowed silently
    into a false 'order failed' result."""
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    post_resp = _fake_resp(200, {"orderSuccess": True, "dukasOrderID": "jf999", "fillPrice": 1.0850})
    with patch("requests.post", return_value=post_resp), \
         patch("requests.put", side_effect=Exception("PUT failed")):
        result = client.place_market_order(_order())

    assert result.success is True
    assert result.dukas_order_id == "jf999"


def test_place_market_order_refuses_unknown_pip_size(monkeypatch):
    """No known pip size for a symbol must not silently place an
    unprotected or wrongly-sized SL/TP — it raises, and the caller
    (place_market_order) catches it and logs loudly, still returning
    success=True for the fill itself (proven by the test above)."""
    monkeypatch.setenv("DUKASCOPY_JFOREX_BRIDGE_URL", "http://127.0.0.1:7080")
    client = DukascopyJForexClient()
    post_resp = _fake_resp(200, {"orderSuccess": True, "dukasOrderID": "jf1", "fillPrice": 1.0850})
    with patch("requests.post", return_value=post_resp), \
         patch("requests.put") as mock_put:
        result = client.place_market_order(_order(symbol="UNKNOWNSYM"))

    assert result.success is True  # fill still succeeded
    mock_put.assert_not_called()  # but SL/TP attach never attempted a guess


def test_pip_size_table_covers_fx_majors_minors_and_metals():
    from execution.dukascopy_jforex_client import _PIP_SIZE
    for sym in ["EURUSD", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
                "EURGBP", "EURCHF", "USDJPY", "EURJPY", "GBPJPY", "AUDJPY",
                "XAUUSD", "XAGUSD"]:
        assert sym in _PIP_SIZE
    # JPY crosses use the 0.01 convention, non-JPY FX uses 0.0001
    assert _PIP_SIZE["USDJPY"] == 0.01
    assert _PIP_SIZE["EURUSD"] == 0.0001
