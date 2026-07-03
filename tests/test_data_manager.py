"""tests/test_data_manager.py — DataManager.cache_status() (Data Center backend)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core.data_manager import DataManager


@pytest.fixture
def dm(tmp_path, monkeypatch):
    monkeypatch.setattr("core.data_manager.DATA_DIR", tmp_path)
    return DataManager()


def _write_cache(dm: DataManager, symbol: str, timeframe: str, index: pd.DatetimeIndex) -> None:
    df = pd.DataFrame(
        {"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 100},
        index=index,
    )
    path = dm._cache_path(symbol, timeframe)
    df.to_csv(path)


def test_cache_status_missing(dm):
    status = dm.cache_status("EURUSD", "1h")
    assert status["status"] == "MISSING"
    assert status["bars"] == 0


def test_cache_status_ok(dm):
    now = datetime.now(timezone.utc)
    index = pd.date_range(end=now, periods=200, freq="1h")
    _write_cache(dm, "EURUSD", "1h", index)
    status = dm.cache_status("EURUSD", "1h")
    assert status["status"] == "OK"
    assert status["bars"] == 200
    assert status["gap_count_30d"] == 0


def test_cache_status_stale(dm):
    old = datetime.now(timezone.utc) - timedelta(days=2)
    index = pd.date_range(end=old, periods=200, freq="1h")
    _write_cache(dm, "EURUSD", "1h", index)
    status = dm.cache_status("EURUSD", "1h")
    assert status["status"] == "STALE"


def test_cache_status_gaps(dm):
    now = datetime.now(timezone.utc)
    index = pd.date_range(end=now, periods=200, freq="1h")
    # Drop a chunk from the middle of the trailing window to create a gap.
    index = index.delete(range(100, 110))
    _write_cache(dm, "EURUSD", "1h", index)
    status = dm.cache_status("EURUSD", "1h")
    assert status["status"] == "GAPS"
    assert status["gap_count_30d"] > 0
