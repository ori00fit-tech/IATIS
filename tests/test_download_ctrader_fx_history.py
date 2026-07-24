"""Unit tests for scripts/download_ctrader_fx_history.py's pagination
logic — the part that doesn't need a live cTrader connection. A fake
client scripts a sequence of get_trendbars() batches and asserts the
downloader pages backward correctly, dedupes, and stops on each of the
three termination conditions (empty batch, no new bars, target reached)."""
from __future__ import annotations

import pandas as pd
import pytest

import scripts.download_ctrader_fx_history as m
from scripts.download_ctrader_fx_history import download_symbol_deep


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """download_symbol_deep() sleeps REQUEST_SLEEP_SEC between real
    requests to be polite to cTrader's API — irrelevant and slow against
    the fake client these tests use."""
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)


class _FakeClient:
    """Replays pre-scripted batches keyed by call order, recording the
    to_timestamp_ms each call was made with so pagination direction can
    be asserted."""

    def __init__(self, batches: list[list[dict]]):
        self._batches = batches
        self.calls: list[dict] = []

    def get_trendbars(self, symbol, period="H1", count=1000, to_timestamp_ms=None):
        self.calls.append({"symbol": symbol, "period": period,
                           "count": count, "to_timestamp_ms": to_timestamp_ms})
        if len(self.calls) > len(self._batches):
            return []
        return self._batches[len(self.calls) - 1]


def _bar(ts_sec: int, close: float = 1.1, volume: int = 100) -> dict:
    return {"timestamp": ts_sec, "open": close, "high": close, "low": close,
            "close": close, "volume": volume}


DAY = 86_400


def test_pages_backward_using_oldest_bar_of_previous_batch():
    # batch 1: newest 3 bars (t=300,200,100); batch 2: older 3 bars
    batch1 = [_bar(300), _bar(200), _bar(100)]
    batch2 = [_bar(90), _bar(80), _bar(70)]
    client = _FakeClient([batch1, batch2])

    df = download_symbol_deep(client, "EURUSD", years=1000 / (365.25 * 24))
    # first call: to_timestamp_ms is None (defaults to "now" inside get_trendbars)
    assert client.calls[0]["to_timestamp_ms"] is None
    # second call must page strictly before the oldest bar of batch 1 (t=100s -> 100_000ms - 1)
    assert client.calls[1]["to_timestamp_ms"] == 100 * 1000 - 1
    assert len(df) == 6
    assert df.index.is_monotonic_increasing


def test_stops_on_empty_batch():
    batch1 = [_bar(300), _bar(200)]
    client = _FakeClient([batch1, []])  # second call returns nothing -> history floor
    df = download_symbol_deep(client, "EURUSD", years=1000 / (365.25 * 24))
    assert len(client.calls) == 2
    assert len(df) == 2


def test_stops_when_batch_has_no_new_bars():
    batch1 = [_bar(300), _bar(200)]
    # a pathological repeat of the same bars (server oddity) must not loop
    # forever — a 3rd, genuinely-new-data batch proves the loop stopped
    # BECAUSE of the no-new-bars guard, not merely because the fake ran dry
    batch3_never_reached = [_bar(100), _bar(90)]
    client = _FakeClient([batch1, batch1, batch3_never_reached])
    df = download_symbol_deep(client, "EURUSD", years=1000 / (365.25 * 24))
    assert len(client.calls) == 2
    assert len(df) == 2  # no duplicates added


def test_dedupes_overlapping_batches():
    # batch2 overlaps batch1 by one bar (t=100 appears in both) — realistic
    # since the window math over-fetches
    batch1 = [_bar(300), _bar(200), _bar(100)]
    batch2 = [_bar(100), _bar(90)]
    client = _FakeClient([batch1, batch2, []])
    df = download_symbol_deep(client, "EURUSD", years=1000 / (365.25 * 24))
    assert len(df) == 4  # 300,200,100,90 — not 5
    assert df["volume"].iloc[0] == 100  # sanity: real column survives


def test_stops_once_target_bar_count_reached():
    # years chosen so target_bars is small and met after batch 1 alone
    small_batch = [_bar(i * 3600) for i in range(50)]
    client = _FakeClient([small_batch, small_batch])
    df = download_symbol_deep(client, "EURUSD", years=40 / (365.25 * 24))  # target ~40 bars
    assert len(client.calls) == 1  # never needed a second page
    assert len(df) == 50


def test_empty_symbol_returns_empty_dataframe():
    client = _FakeClient([[]])
    df = download_symbol_deep(client, "EURUSD", years=1.0)
    assert df.empty


def test_output_has_expected_ohlcv_columns_and_datetime_index():
    batch = [_bar(100, close=1.2345, volume=42)]
    client = _FakeClient([batch, []])
    df = download_symbol_deep(client, "EURUSD", years=1000 / (365.25 * 24))
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df["volume"].iloc[0] == 42
