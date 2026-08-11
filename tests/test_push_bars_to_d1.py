"""tests/test_push_bars_to_d1.py

Regression coverage for scripts/push_bars_to_d1.py — pushes already-
downloaded provider CSVs into storage/market_bars.py's D1 warehouse and
derives H4/D1 from the native H1 series. Uses tests/conftest.py's
autouse fake_d1 fixture (real SQL semantics, faked HTTP transport) —
no real D1 credentials or network needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import push_bars_to_d1 as m
from push_bars_to_d1 import (
    _asset_class_for_symbol,
    find_source_csv,
    load_csv,
    push_symbol,
    resample_ohlcv,
)
from storage import market_bars


def _write_csv(path: Path, n: int = 20, freq: str = "1h", seed: float = 1.10) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=n, freq=freq, tz="UTC")
    df = pd.DataFrame(
        {"open": [seed + 0.0001 * i for i in range(n)],
         "high": [seed + 0.0002 * i + 0.0002 for i in range(n)],
         "low": [seed + 0.0001 * i - 0.0002 for i in range(n)],
         "close": [seed + 0.0001 * i for i in range(n)],
         "volume": [10.0 + i for i in range(n)]},
        index=idx,
    )
    df.index.name = "datetime"
    df.to_csv(path)
    return df


# ---------------------------------------------------------------------------
# find_source_csv: priority order
# ---------------------------------------------------------------------------


def test_find_source_csv_prefers_multiprovider_over_everything_else(tmp_path):
    _write_csv(tmp_path / "EURUSD_M15_dukascopy.csv")
    _write_csv(tmp_path / "EURUSD_M15_ctrader.csv")
    _write_csv(tmp_path / "EURUSD_M15_multiprovider.csv")
    found = find_source_csv("EURUSD", "M15", tmp_path)
    assert found is not None
    path, source = found
    assert source == "multiprovider"
    assert path.name == "EURUSD_M15_multiprovider.csv"


def test_find_source_csv_falls_through_priority_order_when_preferred_missing(tmp_path):
    _write_csv(tmp_path / "EURUSD_M15_twelve_data.csv")
    _write_csv(tmp_path / "EURUSD_M15_ctrader.csv")
    found = find_source_csv("EURUSD", "M15", tmp_path)
    assert found is not None
    assert found[1] == "ctrader"  # cTrader outranks Twelve Data


def test_find_source_csv_returns_none_when_nothing_downloaded(tmp_path):
    assert find_source_csv("EURUSD", "M15", tmp_path) is None


# ---------------------------------------------------------------------------
# load_csv / resample_ohlcv
# ---------------------------------------------------------------------------


def test_load_csv_produces_utc_tz_aware_index(tmp_path):
    path = tmp_path / "EURUSD_H1_dukascopy.csv"
    _write_csv(path)
    df = load_csv(path)
    assert df.index.tz is not None
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_resample_ohlcv_h1_to_h4_aggregates_correctly():
    idx = pd.date_range("2024-01-02 00:00", periods=8, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {"open": [1.0 + 0.1 * i for i in range(8)], "high": [1.05 + 0.1 * i for i in range(8)],
         "low": [0.95 + 0.1 * i for i in range(8)], "close": [1.02 + 0.1 * i for i in range(8)],
         "volume": [10] * 8},
        index=idx,
    )
    out = resample_ohlcv(df, "H4")
    assert len(out) == 2
    row0 = out.iloc[0]
    assert row0["open"] == df["open"].iloc[0]
    assert row0["close"] == df["close"].iloc[3]
    assert row0["high"] == df["high"].iloc[0:4].max()
    assert row0["low"] == df["low"].iloc[0:4].min()
    assert row0["volume"] == 40


def test_resample_ohlcv_h1_to_d1_aggregates_a_full_day():
    idx = pd.date_range("2024-01-02 00:00", periods=24, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {"open": [1.0] * 24, "high": [1.5] * 24, "low": [0.5] * 24, "close": [1.1] * 24, "volume": [1] * 24},
        index=idx,
    )
    out = resample_ohlcv(df, "D1")
    assert len(out) == 1
    assert out.iloc[0]["volume"] == 24


# ---------------------------------------------------------------------------
# asset class lookup
# ---------------------------------------------------------------------------


def test_asset_class_for_symbol_looks_up_config_and_falls_back():
    cfg = {"data": {"twelve_data_symbols": [
        {"internal": "BTCUSD", "asset_class": "crypto"},
        {"internal": "EURUSD", "asset_class": "fx_major"},
    ]}}
    assert _asset_class_for_symbol("BTCUSD", cfg) == "crypto"
    assert _asset_class_for_symbol("UNKNOWN", cfg) == "fx_major"


# ---------------------------------------------------------------------------
# push_symbol: end-to-end (real fake-D1 writes, real resample/derive path)
# ---------------------------------------------------------------------------


_CFG = {"data": {"twelve_data_symbols": [{"internal": "EURUSD", "asset_class": "fx_major"}]}}


def test_push_symbol_pushes_native_m15_and_h1_and_derives_h4_d1(tmp_path):
    _write_csv(tmp_path / "EURUSD_M15_dukascopy.csv", n=40, freq="15min")
    _write_csv(tmp_path / "EURUSD_H1_dukascopy.csv", n=48, freq="1h")  # 2 full days

    result = push_symbol("EURUSD", tmp_path, _CFG)

    assert result["M15"]["source"] == "dukascopy"
    assert result["M15"]["rows"] == 40
    assert result["H1"]["source"] == "dukascopy"
    assert result["H1"]["rows"] == 48
    # Derived timeframes tagged with the "_resampled" suffix on the H1 source.
    assert result["H4"]["source"] == "dukascopy_resampled"
    assert result["D1"]["source"] == "dukascopy_resampled"
    assert result["H4"]["rows"] == 12   # 48 H1 bars / 4
    assert result["D1"]["rows"] == 2    # 48 H1 bars / 24

    assert market_bars.bar_count("EURUSD", "M15") == 40
    assert market_bars.bar_count("EURUSD", "H1") == 48
    assert market_bars.bar_count("EURUSD", "H4") == 12
    assert market_bars.bar_count("EURUSD", "D1") == 2

    # Every pushed timeframe gets a manifest row.
    for tf in ("M15", "H1", "H4", "D1"):
        manifest = market_bars.get_manifest("EURUSD", tf)
        assert manifest is not None
        assert manifest["row_count"] == result[tf]["rows"]


def test_push_symbol_reports_no_source_file_honestly_when_nothing_downloaded(tmp_path):
    result = push_symbol("EURUSD", tmp_path, _CFG)
    for tf in ("M15", "H1", "H4", "D1"):
        assert result[tf]["status"] == "NO_SOURCE_FILE"
        assert result[tf]["rows"] == 0
    assert market_bars.bar_count("EURUSD", "M15") == 0


def test_push_symbol_still_derives_h4_d1_when_only_h1_was_downloaded(tmp_path):
    _write_csv(tmp_path / "EURUSD_H1_ctrader.csv", n=24, freq="1h")
    result = push_symbol("EURUSD", tmp_path, _CFG)
    assert result["M15"]["status"] == "NO_SOURCE_FILE"
    assert result["H1"]["source"] == "ctrader"
    assert result["H4"]["source"] == "ctrader_resampled"
    assert result["H4"]["rows"] == 6
    assert result["D1"]["source"] == "ctrader_resampled"
    assert result["D1"]["rows"] == 1


def test_push_symbol_skips_deriving_h4_d1_when_h1_missing_even_if_m15_present(tmp_path):
    # Only M15 was fetched, no H1 — this codebase's own convention (Dukascopy
    # script's own docstring) is H4/D1 are always derived from H1, never M15.
    _write_csv(tmp_path / "EURUSD_M15_dukascopy.csv", n=40, freq="15min")
    result = push_symbol("EURUSD", tmp_path, _CFG)
    assert result["M15"]["source"] == "dukascopy"
    assert result["H1"]["status"] == "NO_SOURCE_FILE"
    assert result["H4"]["status"] == "NO_SOURCE_FILE"
    assert result["D1"]["status"] == "NO_SOURCE_FILE"


def test_push_symbol_is_idempotent_on_replay(tmp_path):
    _write_csv(tmp_path / "EURUSD_H1_dukascopy.csv", n=24, freq="1h")
    push_symbol("EURUSD", tmp_path, _CFG)
    push_symbol("EURUSD", tmp_path, _CFG)  # re-run against the same files
    assert market_bars.bar_count("EURUSD", "H1") == 24  # not 48 — INSERT OR REPLACE
    assert market_bars.bar_count("EURUSD", "H4") == 6


def test_push_symbol_reports_ready_status_for_clean_dense_data(tmp_path):
    # Gapless weekday M15/H1 series — should compute a real READY manifest.
    _write_csv(tmp_path / "EURUSD_M15_dukascopy.csv", n=96, freq="15min")   # one full day
    _write_csv(tmp_path / "EURUSD_H1_dukascopy.csv", n=48, freq="1h")      # two full days
    result = push_symbol("EURUSD", tmp_path, _CFG)
    assert result["M15"]["status"] == "READY"
    assert result["H1"]["status"] == "READY"
