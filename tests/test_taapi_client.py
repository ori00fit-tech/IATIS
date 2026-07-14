"""tests/test_taapi_client.py — TAAPI.io client tests (unused-for-now infra)."""
from __future__ import annotations
from unittest.mock import patch, MagicMock

from fundamentals.taapi_client import get_indicator


def _mock_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def test_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("TAAPI_API_KEY", raising=False)
    assert get_indicator("rsi", "BTC/USDT") is None


def test_returns_value_on_success(monkeypatch):
    monkeypatch.setenv("TAAPI_API_KEY", "test_secret")
    with patch("requests.get", return_value=_mock_response({"value": 69.97})) as mock_get:
        result = get_indicator("rsi", "BTC/USDT", exchange="binance", interval="1h")
    assert result == 69.97
    called_url = mock_get.call_args[0][0]
    assert called_url == "https://api.taapi.io/rsi"
    assert mock_get.call_args[1]["params"]["symbol"] == "BTC/USDT"


def test_passes_extra_indicator_params(monkeypatch):
    monkeypatch.setenv("TAAPI_API_KEY", "test_secret")
    with patch("requests.get", return_value=_mock_response({"value": 4200.5})) as mock_get:
        get_indicator("ema", "BTC/USDT", interval="4h", period=20)
    assert mock_get.call_args[1]["params"]["period"] == 20


def test_returns_none_on_rate_limit_error_body(monkeypatch):
    """TAAPI.io's free tier returns rate-limit errors as HTTP 200 with an
    error body (verified 2026-07-14), not a 429 — must be checked explicitly."""
    monkeypatch.setenv("TAAPI_API_KEY", "test_secret")
    data = {"error": "You have exceeded your request limit (TAAPI.IO rate-limit)!"}
    with patch("requests.get", return_value=_mock_response(data)):
        result = get_indicator("rsi", "BTC/USDT")
    assert result is None


def test_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setenv("TAAPI_API_KEY", "test_secret")
    with patch("requests.get", side_effect=Exception("timeout")):
        result = get_indicator("rsi", "BTC/USDT")
    assert result is None
