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


# ---------- duplicate detection / timezone / integrity score (module 3) ----------

def test_cache_status_missing_has_zero_integrity_score(dm):
    status = dm.cache_status("EURUSD", "1h")
    assert status["integrity_score"] == 0
    assert status["duplicate_count"] == 0
    assert status["timezone"] is None


def test_cache_status_ok_has_perfect_integrity_score_and_utc_timezone(dm):
    now = datetime.now(timezone.utc)
    index = pd.date_range(end=now, periods=200, freq="1h")
    _write_cache(dm, "EURUSD", "1h", index)
    status = dm.cache_status("EURUSD", "1h")
    assert status["integrity_score"] == 100
    assert status["duplicate_count"] == 0
    assert status["timezone"] == "UTC"


def test_cache_status_detects_duplicate_timestamps(dm):
    now = datetime.now(timezone.utc)
    index = pd.date_range(end=now, periods=200, freq="1h")
    # Duplicate a handful of timestamps — corruption S3 in verify_data_integrity.py checks for.
    index = index.append(index[:5])
    _write_cache(dm, "EURUSD", "1h", index.sort_values())
    status = dm.cache_status("EURUSD", "1h")
    assert status["duplicate_count"] == 5
    assert status["integrity_score"] < 100


def test_cache_status_integrity_score_never_negative(dm):
    old = datetime.now(timezone.utc) - timedelta(days=2)
    index = pd.date_range(end=old, periods=200, freq="1h")
    index = index.delete(range(100, 110)).append(index[:20])
    _write_cache(dm, "EURUSD", "1h", index.sort_values())
    status = dm.cache_status("EURUSD", "1h")
    assert 0 <= status["integrity_score"] <= 100
