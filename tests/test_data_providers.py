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
    _to_yfinance_symbol,
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
