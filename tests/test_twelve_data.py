"""
tests/test_twelve_data.py
----------------------------
Tests for core.twelve_data_client — all using mocked HTTP responses,
because the sandbox environment can't reach api.twelvedata.com and we
don't want to burn real API credits in CI. The actual live integration
is verified manually once on a real machine (see the instructions at
the bottom of this file).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from core.twelve_data_client import (
    RateLimitExceeded,
    RateLimiter,
    TwelveDataClient,
    TwelveDataError,
    _parse_response,
)
from core.data_loader import _to_td_symbol


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TD_RESPONSE = {
    "meta": {"symbol": "EUR/USD", "interval": "1h", "currency_base": "Euro"},
    "status": "ok",
    "values": [
        {"datetime": "2026-06-01 00:00:00", "open": "1.0850", "high": "1.0870",
         "low": "1.0840", "close": "1.0865", "volume": "1200"},
        {"datetime": "2026-06-01 01:00:00", "open": "1.0865", "high": "1.0880",
         "low": "1.0855", "close": "1.0875", "volume": "950"},
        {"datetime": "2026-06-01 02:00:00", "open": "1.0875", "high": "1.0890",
         "low": "1.0865", "close": "1.0882", "volume": "1100"},
    ],
}

ERROR_RESPONSE = {
    "status": "error",
    "code": 400,
    "message": "**symbol** not found: INVALID",
}


# ---------------------------------------------------------------------------
# Symbol conversion
# ---------------------------------------------------------------------------

def test_to_td_symbol_converts_6char():
    assert _to_td_symbol("EURUSD") == "EUR/USD"
    assert _to_td_symbol("XAUUSD") == "XAU/USD"
    assert _to_td_symbol("GBPUSD") == "GBP/USD"


def test_to_td_symbol_passthrough_if_already_slash():
    assert _to_td_symbol("EUR/USD") == "EUR/USD"
    assert _to_td_symbol("XAU/USD") == "XAU/USD"


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def test_parse_response_returns_ohlcv_contract():
    df = _parse_response(SAMPLE_TD_RESPONSE)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "datetime"
    assert len(df) == 3


def test_parse_response_values_are_float():
    df = _parse_response(SAMPLE_TD_RESPONSE)
    for col in ["open", "high", "low", "close"]:
        assert df[col].dtype == float


def test_parse_response_index_is_utc_datetime():
    df = _parse_response(SAMPLE_TD_RESPONSE)
    assert str(df.index.tz) == "UTC"


def test_parse_response_sorted_ascending():
    df = _parse_response(SAMPLE_TD_RESPONSE)
    assert df.index.is_monotonic_increasing


def test_parse_response_raises_on_empty_values():
    with pytest.raises(TwelveDataError):
        _parse_response({"status": "ok", "values": []})


def test_parse_response_raises_on_missing_values_key():
    with pytest.raises(TwelveDataError):
        _parse_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

def test_rate_limiter_increments_and_returns_remaining(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.twelve_data_client.RateLimiter._USAGE_FILE",
        tmp_path / "td_usage.json",
    )
    limiter = RateLimiter()
    remaining = limiter.check_and_increment()
    assert remaining == 799  # 800 - 1


def test_rate_limiter_resets_on_new_day(tmp_path, monkeypatch):
    usage_file = tmp_path / "td_usage.json"
    monkeypatch.setattr("core.twelve_data_client.RateLimiter._USAGE_FILE", usage_file)

    # write usage for yesterday
    yesterday = "2026-01-01"
    usage_file.write_text(json.dumps({"date": yesterday, "count": 799}))

    limiter = RateLimiter()
    remaining = limiter.check_and_increment()
    assert remaining == 799  # fresh day, only 1 used


def test_rate_limiter_blocks_when_daily_limit_reached(tmp_path, monkeypatch):
    usage_file = tmp_path / "td_usage.json"
    monkeypatch.setattr("core.twelve_data_client.RateLimiter._USAGE_FILE", usage_file)

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage_file.write_text(json.dumps({"date": today, "count": 800}))

    limiter = RateLimiter()
    with pytest.raises(RateLimitExceeded, match="Daily limit"):
        limiter.check_and_increment()


def test_rate_limiter_remaining_today_reflects_usage(tmp_path, monkeypatch):
    usage_file = tmp_path / "td_usage.json"
    monkeypatch.setattr("core.twelve_data_client.RateLimiter._USAGE_FILE", usage_file)

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage_file.write_text(json.dumps({"date": today, "count": 100}))

    limiter = RateLimiter()
    assert limiter.remaining_today() == 700


# ---------------------------------------------------------------------------
# TwelveDataClient — mocked HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_client(tmp_path, monkeypatch):
    """A TwelveDataClient with rate limiter persisted to tmp_path and
    HTTP session mocked — no real network calls.
    """
    monkeypatch.setattr(
        "core.twelve_data_client.RateLimiter._USAGE_FILE",
        tmp_path / "td_usage.json",
    )
    monkeypatch.setattr(
        "core.twelve_data_client.CACHE_DIR",
        tmp_path / "cache",
    )
    return TwelveDataClient(api_key="test_key_123")


def _mock_response(data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    mock.raise_for_status = MagicMock()
    return mock


def test_client_raises_on_empty_api_key():
    with pytest.raises(ValueError, match="API key"):
        TwelveDataClient(api_key="")


def test_client_time_series_returns_dataframe(mock_client):
    with patch.object(mock_client._session, "get",
                      return_value=_mock_response(SAMPLE_TD_RESPONSE)):
        df = mock_client.time_series("EUR/USD", "H1", use_cache=False)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_client_raises_on_api_error_response(mock_client):
    with patch.object(mock_client._session, "get",
                      return_value=_mock_response(ERROR_RESPONSE)):
        with pytest.raises(TwelveDataError, match="not found"):
            mock_client.time_series("INVALID", "H1", use_cache=False)


def test_client_uses_cache_on_second_call(mock_client):
    with patch.object(mock_client._session, "get",
                      return_value=_mock_response(SAMPLE_TD_RESPONSE)) as mock_get:
        mock_client.time_series("EUR/USD", "H1", use_cache=True)
        mock_client.time_series("EUR/USD", "H1", use_cache=True)

    # Second call should hit the cache — only 1 real HTTP request made
    assert mock_get.call_count == 1


def test_client_interval_map_accepts_internal_labels(mock_client):
    """M15, H1, H4 etc. should be translated to Twelve Data labels."""
    with patch.object(mock_client._session, "get",
                      return_value=_mock_response(SAMPLE_TD_RESPONSE)) as mock_get:
        mock_client.time_series("EUR/USD", "H1", use_cache=False)

    call_params = mock_get.call_args[1]["params"]
    assert call_params["interval"] == "1h"  # not "H1"


def test_client_passes_correct_params(mock_client):
    with patch.object(mock_client._session, "get",
                      return_value=_mock_response(SAMPLE_TD_RESPONSE)) as mock_get:
        mock_client.time_series("EUR/USD", "H4", outputsize=200, use_cache=False)

    call_params = mock_get.call_args[1]["params"]
    assert call_params["symbol"] == "EUR/USD"
    assert call_params["interval"] == "4h"
    assert call_params["outputsize"] == 200
    assert call_params["timezone"] == "UTC"
    assert call_params["order"] == "ASC"


# ---------------------------------------------------------------------------
# load_from_twelve_data integration (mocked)
# ---------------------------------------------------------------------------

def test_load_from_twelve_data_returns_ohlcv_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.twelve_data_client.RateLimiter._USAGE_FILE",
        tmp_path / "td_usage.json",
    )
    monkeypatch.setattr("core.twelve_data_client.CACHE_DIR", tmp_path / "cache")

    from core.data_loader import load_from_twelve_data
    from core.data_validator import validate_ohlcv

    with patch("requests.Session.get",
               return_value=_mock_response(SAMPLE_TD_RESPONSE)):
        df = load_from_twelve_data("EUR/USD", "H1", api_key="test_key", use_cache=False)

    assert isinstance(df, pd.DataFrame)
    assert validate_ohlcv(df) is True


# ---------------------------------------------------------------------------
# Manual live test instructions (not run in CI)
# ---------------------------------------------------------------------------
# To verify the client works against the real Twelve Data API on your machine:
#
#   1. Add TWELVE_DATA_API_KEY to your .env file
#   2. Run:
#
#      python3 -c "
#      from dotenv import load_dotenv; import os; load_dotenv()
#      from core.twelve_data_client import TwelveDataClient
#      client = TwelveDataClient(os.environ['TWELVE_DATA_API_KEY'])
#      print('Credits remaining:', client.remaining_today())
#      df = client.time_series('EUR/USD', 'H1', outputsize=5)
#      print(df)
#      "
#
# This costs 1 API credit.
