"""tests/test_data_providers.py — Failover provider tests."""
from __future__ import annotations
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

from core.data_providers import (
    fetch_with_failover,
    DataFetchError,
    _fetch_twelve_data,
    _fetch_yahoo_finance,
    _fetch_fcs_api,
    _to_yfinance_symbol,
    _is_equity_symbol,
    _fetch_finnhub,
    _fetch_finnhub_equity,
    _fetch_alpha_vantage,
    _fetch_alpha_vantage_equity,
)


def _make_df(n=10):
    idx = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": [1.1] * n, "high": [1.2] * n,
        "low": [1.0] * n, "close": [1.15] * n, "volume": [0.0] * n,
    }, index=idx)


# --- Symbol conversion ---

def test_to_yfinance_symbol_fx():
    assert _to_yfinance_symbol("EUR/USD") == "EURUSD=X"
    assert _to_yfinance_symbol("GBP/JPY") == "GBPJPY=X"

def test_to_yfinance_symbol_special():
    assert _to_yfinance_symbol("XAU/USD") == "GC=F"
    assert _to_yfinance_symbol("BTC/USD") == "BTC-USD"
    assert _to_yfinance_symbol("DJI") == "^DJI"


# --- fetch_with_failover ---

def test_failover_uses_first_provider_on_success():
    df = _make_df()
    with patch("core.data_providers._fetch_twelve_data", return_value=df) as mock_td:
        result_df, provider = fetch_with_failover("EUR/USD", "H1", providers=["twelve_data"])
    assert provider == "twelve_data"
    assert len(result_df) == 10
    mock_td.assert_called_once()


def test_failover_skips_to_second_on_first_failure():
    df = _make_df()
    with patch("core.data_providers._fetch_twelve_data", side_effect=Exception("429")):
        with patch("core.data_providers._fetch_yahoo_finance", return_value=df) as mock_yf:
            result_df, provider = fetch_with_failover(
                "EUR/USD", "H1", providers=["twelve_data", "yahoo_finance"]
            )
    assert provider == "yahoo_finance"
    mock_yf.assert_called_once()


def test_failover_raises_when_all_fail():
    with patch("core.data_providers._fetch_twelve_data", side_effect=Exception("timeout")):
        with patch("core.data_providers._fetch_yahoo_finance", side_effect=Exception("404")):
            with patch("core.data_providers._fetch_alpha_vantage", side_effect=Exception("no key")):
                with pytest.raises(DataFetchError) as exc:
                    fetch_with_failover(
                        "EUR/USD", "H1",
                        providers=["twelve_data", "yahoo_finance", "alpha_vantage"]
                    )
    assert "All providers failed" in str(exc.value)


def test_failover_skips_alpha_vantage_when_no_key():
    df = _make_df()
    with patch("core.data_providers._fetch_twelve_data", side_effect=Exception("rate limit")):
        with patch("core.data_providers._fetch_yahoo_finance", return_value=df):
            result_df, provider = fetch_with_failover(
                "EUR/USD", "H1",
                providers=["twelve_data", "yahoo_finance"]
            )
    assert provider == "yahoo_finance"


def test_failover_returns_df_from_working_provider():
    """Ensures the returned DataFrame is the actual data, not a copy."""
    df = _make_df(20)
    with patch("core.data_providers._fetch_twelve_data", side_effect=Exception("down")):
        with patch("core.data_providers._fetch_yahoo_finance", return_value=df):
            result_df, provider = fetch_with_failover(
                "EUR/USD", "H1",
                providers=["twelve_data", "yahoo_finance"]
            )
    assert len(result_df) == 20
    assert provider == "yahoo_finance"


def test_failover_direct_yahoo_when_no_twelve_key(monkeypatch):
    """If TWELVE_DATA_API_KEY is missing, falls through to Yahoo."""
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    df = _make_df()
    with patch("core.data_providers._fetch_yahoo_finance", return_value=df):
        result_df, provider = fetch_with_failover(
            "EUR/USD", "H1",
            providers=["twelve_data", "yahoo_finance"]
        )
    assert provider == "yahoo_finance"


# --- FCS API ---

def _fcs_response(n=3):
    resp = {}
    base_ts = 1748815200
    for i in range(n):
        ts = base_ts + i * 3600
        resp[str(ts)] = {
            "o": 1.10 + i * 0.001, "h": 1.11 + i * 0.001,
            "l": 1.09 + i * 0.001, "c": 1.105 + i * 0.001,
            "v": 1000 + i, "t": ts, "tm": "2025-06-01 22:00:00",
        }
    return {"status": True, "code": 200, "msg": "Successfully", "response": resp}


def test_fcs_api_raises_without_key(monkeypatch):
    monkeypatch.delenv("FCS_API_KEY", raising=False)
    with pytest.raises(DataFetchError, match="FCS_API_KEY not set"):
        _fetch_fcs_api("EUR/USD", "H1", 100)


def test_fcs_api_parses_forex_response(monkeypatch):
    monkeypatch.setenv("FCS_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = _fcs_response(3)
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp) as mock_get:
        df = _fetch_fcs_api("EUR/USD", "H1", 100)
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    called_url, called_params = mock_get.call_args[0][0], mock_get.call_args[1]["params"]
    assert called_url == "https://api-v4.fcsapi.com/forex/history"
    assert called_params["symbol"] == "EURUSD"
    assert called_params["period"] == "1h"


def test_fcs_api_uses_stock_endpoint_for_indices(monkeypatch):
    monkeypatch.setenv("FCS_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = _fcs_response(3)
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp) as mock_get:
        _fetch_fcs_api("DJI", "D1", 100)
    called_url, called_params = mock_get.call_args[0][0], mock_get.call_args[1]["params"]
    assert called_url == "https://api-v4.fcsapi.com/stock/history"
    assert called_params["symbol"] == "DJ:DJI"


def test_fcs_api_raises_on_status_false(monkeypatch):
    monkeypatch.setenv("FCS_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": False, "msg": "Invalid access_key"}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(DataFetchError, match="Invalid access_key"):
            _fetch_fcs_api("EUR/USD", "H1", 100)


def test_fcs_api_raises_on_empty_response(monkeypatch):
    monkeypatch.setenv("FCS_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": True, "response": {}}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(DataFetchError, match="empty response"):
            _fetch_fcs_api("EUR/USD", "H1", 100)


def test_fcs_api_raises_on_unsupported_interval(monkeypatch):
    monkeypatch.setenv("FCS_API_KEY", "test_key")
    with pytest.raises(DataFetchError, match="unsupported interval"):
        _fetch_fcs_api("EUR/USD", "M2", 100)


def test_provider_logs_warning_on_fallback(caplog):
    import logging
    df = _make_df()
    with patch("core.data_providers._fetch_twelve_data", side_effect=Exception("503")):
        with patch("core.data_providers._fetch_yahoo_finance", return_value=df):
            with caplog.at_level(logging.WARNING, logger="core.data_providers"):
                fetch_with_failover(
                    "EUR/USD", "H1",
                    providers=["twelve_data", "yahoo_finance"]
                )
    assert any("failed" in r.message for r in caplog.records)


# --- Equity/ETF routing + fetch (2026-07-24, H001-independent Phase 1 build) ---

def test_is_equity_symbol():
    assert _is_equity_symbol("AAPL") is True
    assert _is_equity_symbol("SPY") is True
    assert _is_equity_symbol("EUR/USD") is False
    assert _is_equity_symbol("XAU/USD") is False


def test_fetch_finnhub_dispatches_equity_symbols_to_the_equity_endpoint(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    with patch("core.data_providers._fetch_finnhub_equity", return_value=_make_df()) as mock_eq:
        _fetch_finnhub("AAPL", "D1", 10)
    mock_eq.assert_called_once_with("AAPL", "D1", 10)


def test_fetch_alpha_vantage_dispatches_equity_symbols_to_the_equity_endpoint(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test_key")
    with patch("core.data_providers._fetch_alpha_vantage_equity", return_value=_make_df()) as mock_eq:
        _fetch_alpha_vantage("SPY", "D1", 10)
    mock_eq.assert_called_once_with("SPY", "D1", 10)


def test_fetch_finnhub_equity_raises_without_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with pytest.raises(DataFetchError, match="FINNHUB_API_KEY not set"):
        _fetch_finnhub_equity("AAPL", "D1", 10)


def _finnhub_stock_response(n=3):
    base = 1735689600  # 2025-01-01 UTC
    return {
        "s": "ok",
        "t": [base + i * 86400 for i in range(n)],
        "o": [100.0 + i for i in range(n)],
        "h": [101.0 + i for i in range(n)],
        "l": [99.0 + i for i in range(n)],
        "c": [100.5 + i for i in range(n)],
        "v": [1_000_000.0] * n,
    }


def test_fetch_finnhub_equity_parses_stock_candle_response(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = _finnhub_stock_response(3)
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp) as mock_get:
        df = _fetch_finnhub_equity("AAPL", "D1", 10)
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    called_url, called_params = mock_get.call_args[0][0], mock_get.call_args[1]["params"]
    assert called_url == "https://finnhub.io/api/v1/stock/candle"
    assert called_params["symbol"] == "AAPL"   # plain ticker, no OANDA:/BINANCE: prefix
    assert called_params["resolution"] == "D"


def test_fetch_finnhub_equity_raises_on_no_data(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"s": "no_data"}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(DataFetchError, match="no data"):
            _fetch_finnhub_equity("AAPL", "D1", 10)


def _av_daily_response(n=3):
    ts = {}
    for i in range(n):
        ts[f"2025-01-{i+1:02d}"] = {
            "1. open": str(100.0 + i), "2. high": str(101.0 + i),
            "3. low": str(99.0 + i), "4. close": str(100.5 + i),
            "5. volume": str(1_000_000),
        }
    return {"Time Series (Daily)": ts}


def test_fetch_alpha_vantage_equity_raises_without_key(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    with pytest.raises(DataFetchError, match="ALPHA_VANTAGE_API_KEY not set"):
        _fetch_alpha_vantage_equity("AAPL", "D1", 10)


def test_fetch_alpha_vantage_equity_uses_daily_function_for_d1(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = _av_daily_response(3)
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp) as mock_get:
        df = _fetch_alpha_vantage_equity("AAPL", "D1", 10)
    assert len(df) == 3
    called_params = mock_get.call_args[1]["params"]
    assert called_params["function"] == "TIME_SERIES_DAILY"
    assert called_params["symbol"] == "AAPL"


def test_fetch_alpha_vantage_equity_uses_weekly_function_for_w1(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"Weekly Time Series": _av_daily_response(2)["Time Series (Daily)"]}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp) as mock_get:
        df = _fetch_alpha_vantage_equity("SPY", "W1", 10)
    assert len(df) == 2
    assert mock_get.call_args[1]["params"]["function"] == "TIME_SERIES_WEEKLY"


def test_fetch_alpha_vantage_equity_uses_intraday_function_for_h1(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"Time Series (60min)": _av_daily_response(2)["Time Series (Daily)"]}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp) as mock_get:
        df = _fetch_alpha_vantage_equity("AAPL", "H1", 10)
    assert len(df) == 2
    called_params = mock_get.call_args[1]["params"]
    assert called_params["function"] == "TIME_SERIES_INTRADAY"
    assert called_params["interval"] == "60min"


def test_fetch_alpha_vantage_equity_raises_on_premium_information_message(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test_key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"Information": "premium endpoint"}
    mock_resp.raise_for_status.return_value = None
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(DataFetchError, match="premium endpoint"):
            _fetch_alpha_vantage_equity("AAPL", "D1", 10)
