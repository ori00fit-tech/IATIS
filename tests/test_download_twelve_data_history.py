"""tests/test_download_twelve_data_history.py

Regression coverage for scripts/download_twelve_data_history.py — the
plain-Twelve-Data-only M15/H1 downloader (third-tier of the Dukascopy ->
cTrader -> Twelve Data multi-provider download priority; deliberately no
Yahoo/ccxt fallback, unlike scripts/download_deep_history.py). No live
network call is ever made in these tests; requests.get is mocked
throughout, matching tests/test_download_ctrader_fx_history.py's/
tests/test_dukascopy_download.py's established convention for this class
of manual, operator-run download script.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import download_twelve_data_history as m
from download_twelve_data_history import (
    SUPPORTED_TIMEFRAMES,
    _integrity,
    _td_get,
    _to_frame,
    fetch_td_history,
    symbol_universe,
)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """fetch_td_history()/_td_get() sleep between/after real requests to be
    polite to Twelve Data's rate limit — irrelevant and slow in tests."""
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)


# ---------------------------------------------------------------------------
# module import: .env PermissionError -> actionable SystemExit, not a raw
# traceback (2026-08-11 — confirmed live on the VPS: this module's
# module-level, unguarded load_dotenv() call crashed at IMPORT time — even
# before main() ever ran — with the raw PermissionError traceback instead
# of the friendly message scripts/download_ctrader_fx_history.py already
# has). Loaded at module scope (not inside main()), so this is exercised
# via importlib.reload() with dotenv.load_dotenv patched, then reloaded
# again afterward to restore the module's normal, already-imported state
# for every other test in this file.
# ---------------------------------------------------------------------------


def test_module_import_reports_actionable_message_on_env_permission_error():
    try:
        with patch("dotenv.load_dotenv", side_effect=PermissionError("denied")):
            with pytest.raises(SystemExit, match="sudo -u iatis"):
                importlib.reload(m)
    finally:
        importlib.reload(m)  # restore normal module state for the rest of this file


def _bar(dt: str, close: float = 1.1, volume: float = 100) -> dict:
    return {"datetime": dt, "open": close, "high": close, "low": close, "close": close, "volume": volume}


def _fake_response(json_data: dict):
    resp = Mock()
    resp.json.return_value = json_data
    return resp


# ---------------------------------------------------------------------------
# timeframe / symbol-universe scope
# ---------------------------------------------------------------------------


def test_supported_timeframes_are_m15_and_h1():
    assert SUPPORTED_TIMEFRAMES == ("M15", "H1")


def test_symbol_universe_covers_all_24_configured_symbols_enabled_and_disabled():
    from utils.helpers import load_config

    cfg = load_config()
    universe = symbol_universe(cfg)
    assert len(universe) == 24
    # A mix of enabled and disabled/paused entries per config.yaml — this
    # is a bulk download utility, not a live-trading symbol filter, so
    # both must be present.
    for expected in ("EURUSD", "XAUUSD", "BTCUSD",   # enabled
                      "USOIL", "US30", "AAPL"):        # disabled/paused
        assert expected in universe


def test_symbol_universe_maps_internal_to_twelve_data_symbol():
    cfg = {"data": {"twelve_data_symbols": [
        {"internal": "EURUSD", "symbol": "EUR/USD", "enabled": True},
        {"internal": "USOIL", "symbol": "WTI/USD", "enabled": False},
    ]}}
    assert symbol_universe(cfg) == {"EURUSD": "EUR/USD", "USOIL": "WTI/USD"}


# ---------------------------------------------------------------------------
# interval-code translation (M15 -> 15min, H1 -> 1h via INTERVAL_MAP)
# ---------------------------------------------------------------------------


def test_fetch_td_history_requests_15min_interval_for_m15():
    resp = _fake_response({"values": [_bar("2024-01-02 00:00:00")]})
    with patch("download_twelve_data_history.requests.get", return_value=resp) as mock_get:
        fetch_td_history("EUR/USD", "M15", api_key="k", outputsize=5)
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["interval"] == "15min"


def test_fetch_td_history_requests_1h_interval_for_h1():
    resp = _fake_response({"values": [_bar("2024-01-02 00:00:00")]})
    with patch("download_twelve_data_history.requests.get", return_value=resp) as mock_get:
        fetch_td_history("EUR/USD", "H1", api_key="k", outputsize=5)
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["interval"] == "1h"


# ---------------------------------------------------------------------------
# pagination correctness (generalized from download_deep_history.py's
# fetch_td_deep, same backward-via-end_date mechanism)
# ---------------------------------------------------------------------------


def test_fetch_td_history_stops_once_chunk_is_smaller_than_outputsize():
    # outputsize=2: a first chunk of exactly 2 bars means "more may exist",
    # a second chunk of 1 bar means "history floor reached" -> stop.
    chunk1 = {"values": [_bar("2024-01-01 00:00:00"), _bar("2024-01-01 00:15:00")]}
    chunk2 = {"values": [_bar("2023-12-31 23:45:00")]}
    with patch("download_twelve_data_history.requests.get",
               side_effect=[_fake_response(chunk1), _fake_response(chunk2)]) as mock_get:
        df = fetch_td_history("EUR/USD", "M15", api_key="k", outputsize=2)
    assert mock_get.call_count == 2
    assert len(df) == 3
    assert df.index.is_monotonic_increasing


def test_fetch_td_history_pages_backward_using_oldest_bar_of_previous_chunk():
    chunk1 = {"values": [_bar("2024-01-01 00:00:00"), _bar("2024-01-01 00:15:00")]}
    chunk2 = {"values": [_bar("2023-12-31 23:45:00")]}
    with patch("download_twelve_data_history.requests.get",
               side_effect=[_fake_response(chunk1), _fake_response(chunk2)]) as mock_get:
        fetch_td_history("EUR/USD", "M15", api_key="k", outputsize=2)
    first_params = mock_get.call_args_list[0].kwargs["params"]
    second_params = mock_get.call_args_list[1].kwargs["params"]
    assert "end_date" not in first_params
    assert second_params["end_date"] == "2024-01-01 00:00:00+00:00"  # oldest bar of chunk 1


def test_fetch_td_history_dedupes_overlapping_chunks():
    chunk1 = {"values": [_bar("2024-01-01 00:00:00"), _bar("2024-01-01 00:15:00")]}
    # chunk2 overlaps chunk1 by the 00:00:00 bar (realistic: end_date is
    # inclusive) and is smaller than outputsize, so pagination stops here.
    chunk2 = {"values": [_bar("2024-01-01 00:00:00")]}
    with patch("download_twelve_data_history.requests.get",
               side_effect=[_fake_response(chunk1), _fake_response(chunk2)]):
        df = fetch_td_history("EUR/USD", "M15", api_key="k", outputsize=2)
    assert len(df) == 2  # not 3 — the duplicate timestamp collapses


def test_fetch_td_history_raises_when_first_response_has_no_values():
    with patch("download_twelve_data_history.requests.get",
               return_value=_fake_response({"code": 400, "message": "bad symbol"})):
        with pytest.raises(RuntimeError, match="bad symbol"):
            fetch_td_history("NOTASYMBOL/USD", "H1", api_key="k")


def test_fetch_td_history_stops_gracefully_once_paginated_past_the_floor():
    # A single valid chunk followed by a plan-floor-style error response
    # (no "values" key) must NOT raise, since at least one chunk succeeded.
    chunk1 = {"values": [_bar("2024-01-01 00:00:00"), _bar("2024-01-01 00:15:00")]}
    floor_response = {"code": 400, "message": "no earlier data"}
    with patch("download_twelve_data_history.requests.get",
               side_effect=[_fake_response(chunk1), _fake_response(floor_response)]):
        df = fetch_td_history("EUR/USD", "M15", api_key="k", outputsize=2)
    assert len(df) == 2


# ---------------------------------------------------------------------------
# rate-limit / transient-failure retry handling (_td_get)
# ---------------------------------------------------------------------------


def test_td_get_retries_on_429_then_succeeds():
    responses = [_fake_response({"code": 429}), _fake_response({"values": [_bar("2024-01-01 00:00:00")]})]
    with patch("download_twelve_data_history.requests.get", side_effect=responses) as mock_get:
        result = _td_get({"symbol": "EUR/USD", "interval": "1h"}, api_key="k")
    assert mock_get.call_count == 2
    assert "values" in result


def test_td_get_retries_on_request_exception_then_succeeds():
    import requests

    with patch("download_twelve_data_history.requests.get",
               side_effect=[requests.ConnectionError("boom"), _fake_response({"values": []})]) as mock_get:
        result = _td_get({"symbol": "EUR/USD", "interval": "1h"}, api_key="k")
    assert mock_get.call_count == 2
    assert result == {"values": []}


def test_td_get_gives_up_after_max_tries():
    with patch("download_twelve_data_history.requests.get", return_value=_fake_response({"code": 429})) as mock_get:
        result = _td_get({"symbol": "EUR/USD", "interval": "1h"}, api_key="k", tries=2)
    assert mock_get.call_count == 2
    # Every attempt was a 429 — never returned early, falls through to the
    # generic "gave up" result rather than fabricating a fake success.
    assert result == {"code": "network", "message": "request failed after retries"}


# ---------------------------------------------------------------------------
# frame shape / integrity helpers
# ---------------------------------------------------------------------------


def test_to_frame_produces_expected_ohlcv_columns_and_datetime_index():
    df = _to_frame([_bar("2024-01-02 00:00:00", close=1.2345, volume=42)])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df["volume"].iloc[0] == 42


def test_to_frame_defaults_missing_volume_to_zero():
    row = _bar("2024-01-02 00:00:00")
    del row["volume"]
    df = _to_frame([row])
    assert df["volume"].iloc[0] == 0.0


def test_integrity_reports_duplicate_and_bad_bar_counts():
    idx = pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02"], utc=True)
    df = pd.DataFrame(
        {"open": [1.0, 1.0, 1.0], "high": [0.9, 1.1, 1.1],  # first row: high < low
         "low": [1.0, 0.9, 0.9], "close": [1.0, 1.0, 1.0], "volume": [1, 1, 1]},
        index=idx,
    )
    note = _integrity(df)
    assert "dups=1" in note
    assert "high<low=1" in note
