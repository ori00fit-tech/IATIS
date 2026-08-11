"""tests/test_dukascopy_download.py

Regression coverage for scripts/download_dukascopy_history.py — the
free, credential-free Dukascopy historical-feed download script. No
live network call is ever made in these tests; requests.get is mocked
throughout (matching tests/test_download_deep_history.py's/
tests/test_ctrader_refresh_access_token.py's established convention for
this class of manual, operator-run script), since this sandbox's
outbound network policy blocks datafeed.dukascopy.com (confirmed via a
direct curl returning 403 through the agent proxy).
"""
from __future__ import annotations

import lzma
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from download_dukascopy_history import (  # noqa: E402
    DEFAULT_SYMBOLS,
    SYMBOL_MAP,
    TICK_RECORD_STRUCT,
    DukascopyFetchError,
    _PLAUSIBLE_RANGES,
    _POINT_VALUE_CANDIDATES,
    coarsen_bars,
    detect_point_value,
    download_symbol_hours,
    fetch_hour,
    ticks_to_m15_bars,
)
from core.data_validator import validate_ohlcv


def _compress_records(records: list[tuple]) -> bytes:
    raw = b"".join(TICK_RECORD_STRUCT.pack(*r) for r in records)
    return lzma.compress(raw)


def _fake_response(status_code: int = 200, content: bytes = b"", headers: dict | None = None):
    resp = Mock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = headers or {}
    return resp


# ---------------------------------------------------------------------------
# bi5 tick-record unpacking
# ---------------------------------------------------------------------------


def test_fetch_hour_unpacks_a_synthetic_compressed_blob():
    records = [
        (0, 108510, 108490, 1.5, 2.0),
        (1000, 108520, 108500, 1.0, 1.0),
    ]
    body = _compress_records(records)
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records


def test_fetch_hour_returns_none_on_404():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(404)):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 6, 10, tzinfo=timezone.utc))  # a Saturday
    assert ticks is None


def test_fetch_hour_returns_none_on_empty_200_body():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, b"")):
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks is None


def test_fetch_hour_raises_on_non_200_non_404():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(500)):
        with pytest.raises(DukascopyFetchError, match="HTTP 500"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))


def test_fetch_hour_raises_on_malformed_lzma():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, b"not-lzma-data")):
        with pytest.raises(DukascopyFetchError, match="LZMA"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))


def test_fetch_hour_raises_on_truncated_record_size():
    body = lzma.compress(b"\x00" * 13)  # not a multiple of 20 bytes
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(200, body)):
        with pytest.raises(DukascopyFetchError, match="not a multiple"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))


def test_fetch_hour_raises_on_request_exception():
    import requests

    with patch("download_dukascopy_history.requests.get", side_effect=requests.ConnectionError("boom")):
        with pytest.raises(DukascopyFetchError, match="request failed"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# 429 retry/backoff (2026-08-11 — confirmed live on the VPS: all 24 probe
# hours returned HTTP 429 with the pre-fix code, zero retry/resilience)
# ---------------------------------------------------------------------------


def test_fetch_hour_retries_429_then_succeeds():
    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(429), _fake_response(429), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert ticks == records
    assert mock_sleep.call_count == 2  # one sleep per 429 before the eventual success


def test_fetch_hour_raises_after_exhausting_429_retries():
    import download_dukascopy_history as m

    responses = [_fake_response(429)] * (m.MAX_429_RETRIES + 1)
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        with pytest.raises(DukascopyFetchError, match="HTTP 429"):
            fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    assert mock_sleep.call_count == m.MAX_429_RETRIES  # sleeps between attempts, not after the final failed one


def test_fetch_hour_honors_numeric_retry_after_header():
    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(429, headers={"Retry-After": "7"}), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    mock_sleep.assert_called_once_with(7.0)


def test_fetch_hour_falls_back_to_exponential_backoff_on_malformed_retry_after():
    import download_dukascopy_history as m

    records = [(0, 108510, 108490, 1.5, 2.0)]
    body = _compress_records(records)
    responses = [_fake_response(429, headers={"Retry-After": "not-a-number"}), _fake_response(200, body)]
    with patch("download_dukascopy_history.requests.get", side_effect=responses), \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        fetch_hour("EURUSD", datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
    mock_sleep.assert_called_once_with(m._429_BACKOFF_BASE_SECONDS)  # attempt=0 -> base * 2**0


def test_fetch_hour_still_treats_404_as_market_closed_not_a_retry_target():
    with patch("download_dukascopy_history.requests.get", return_value=_fake_response(404)) as mock_get, \
         patch("download_dukascopy_history.time.sleep") as mock_sleep:
        ticks = fetch_hour("EURUSD", datetime(2024, 1, 6, 10, tzinfo=timezone.utc))
    assert ticks is None
    assert mock_get.call_count == 1  # no retry for a 404 — it's an expected closed-market response
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# point-value auto-detection
# ---------------------------------------------------------------------------


def test_detect_point_value_eurusd_scale_100000():
    # 1.08510 * 100000 = 108510 — plausible EURUSD range is (0.7, 1.8)
    raw_prices = [108510, 108520, 108490]
    assert detect_point_value(raw_prices, "EURUSD") == 100_000


def test_detect_point_value_usdjpy_scale_1000():
    # 148.512 * 1000 = 148512 — plausible USDJPY range is (75, 165)
    raw_prices = [148512, 148600]
    assert detect_point_value(raw_prices, "USDJPY") == 1_000


def test_detect_point_value_xauusd_scale_1000():
    # 2050.35 * 1000 = 2050350 is out of int32-friendly realism for this
    # synthetic case, so use a smaller, still-plausible XAUUSD sample:
    # 1950.20 * 1000 = 1950200
    raw_prices = [1950200, 1951000]
    assert detect_point_value(raw_prices, "XAUUSD") == 1_000


def test_detect_point_value_raises_when_no_candidate_matches():
    # Deliberately implausible for every candidate scale on EURUSD.
    raw_prices = [7]
    with pytest.raises(DukascopyFetchError, match="no candidate point value"):
        detect_point_value(raw_prices, "EURUSD")


def test_detect_point_value_raises_for_unmapped_symbol():
    with pytest.raises(DukascopyFetchError, match="No plausible price range"):
        detect_point_value([108510], "NOTASYMBOL")


def test_detect_point_value_raises_on_empty_sample():
    with pytest.raises(DukascopyFetchError, match="no raw prices"):
        detect_point_value([], "EURUSD")


def test_every_symbol_map_entry_has_a_plausible_range():
    # detect_point_value() cannot run for any symbol missing this —
    # a config-drift regression guard.
    missing = [s for s in SYMBOL_MAP if s not in _PLAUSIBLE_RANGES]
    assert missing == []


def test_candidate_scales_cover_the_documented_set():
    assert _POINT_VALUE_CANDIDATES == (100, 1_000, 10_000, 100_000)


# ---------------------------------------------------------------------------
# tick -> M15 OHLCV bucketing correctness
# ---------------------------------------------------------------------------


def test_ticks_to_m15_bars_mid_price_and_ohlc_bounds():
    # All four ticks fall within ms_offset 0-3000, well inside the first
    # 15-minute sub-bucket -> exactly one bar, same values the old H1-only
    # implementation would have produced for this same input.
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [
        (0, 108520, 108500, 1.0, 1.0),      # mid 108510
        (1000, 108600, 108580, 0.5, 0.5),   # mid 108590 (high)
        (2000, 108400, 108380, 0.5, 0.5),   # mid 108390 (low)
        (3000, 108450, 108430, 1.0, 1.0),   # mid 108440 (close)
    ]
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 1
    bar = bars[0]
    assert bar["datetime"] == hour
    assert bar["open"] == pytest.approx(1.08510)
    assert bar["high"] == pytest.approx(1.08590)
    assert bar["low"] == pytest.approx(1.08390)
    assert bar["close"] == pytest.approx(1.08440)
    assert bar["volume"] == pytest.approx(6.0)
    # validate_ohlcv's exact structural constraints, satisfied by construction:
    assert bar["high"] >= bar["low"]
    assert bar["high"] >= max(bar["open"], bar["close"])
    assert bar["low"] <= min(bar["open"], bar["close"])


def test_ticks_to_m15_bars_returns_empty_list_for_empty_ticks():
    hour = datetime(2024, 1, 6, 3, tzinfo=timezone.utc)  # closed Saturday hour
    assert ticks_to_m15_bars(hour, [], point_value=100_000) == []


def test_ticks_to_m15_bars_splits_across_multiple_sub_buckets():
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [
        (0, 108520, 108500, 1.0, 1.0),                  # bucket 0 (0-15min)
        (14 * 60 * 1000, 108530, 108510, 1.0, 1.0),     # bucket 0 (0-15min)
        (15 * 60 * 1000, 108600, 108580, 1.0, 1.0),     # bucket 1 (15-30min)
        (44 * 60 * 1000, 108610, 108590, 1.0, 1.0),     # bucket 2 (30-45min)
        (46 * 60 * 1000, 108400, 108380, 1.0, 1.0),     # bucket 3 (45-60min)
    ]
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 4
    assert [b["datetime"] for b in bars] == [
        hour,
        hour + timedelta(minutes=15),
        hour + timedelta(minutes=30),
        hour + timedelta(minutes=45),
    ]
    assert bars[0]["volume"] == pytest.approx(4.0)  # 2 ticks in bucket 0
    assert bars[1]["volume"] == pytest.approx(2.0)  # 1 tick in bucket 1
    assert bars[2]["volume"] == pytest.approx(2.0)  # 1 tick in bucket 2
    assert bars[3]["volume"] == pytest.approx(2.0)  # 1 tick in bucket 3


def test_ticks_to_m15_bars_clamps_ms_offset_near_hour_boundary():
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [(3_599_999, 108520, 108500, 1.0, 1.0)]  # a hair under 60 min -> bucket 3
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 1
    assert bars[0]["datetime"] == hour + timedelta(minutes=45)


def test_ticks_to_m15_bars_clamps_exact_hour_boundary_ms_offset():
    # ms_offset=3_600_000 is exactly one hour -> 3_600_000 // 900_000 == 4,
    # which the min(3, ...) clamp must land in the LAST sub-bucket (3),
    # never raise or silently drop the tick.
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [(3_600_000, 108520, 108500, 1.0, 1.0)]
    bars = ticks_to_m15_bars(hour, ticks, point_value=100_000)
    assert len(bars) == 1
    assert bars[0]["datetime"] == hour + timedelta(minutes=45)


def test_download_symbol_hours_produces_a_validate_ohlcv_clean_frame():
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
        datetime(2024, 1, 6, 3, tzinfo=timezone.utc),  # weekend -> 404, skipped
    ]

    def fake_get(url, timeout=None):
        if "2024/00/06" in url:
            return _fake_response(404)
        hour_num = int(url.rsplit("/", 1)[-1][:2])
        base = 108500 + hour_num
        records = [
            (0, base + 10, base - 10, 1.0, 1.0),
            (30_000, base + 20, base, 1.0, 1.0),
        ]
        return _fake_response(200, _compress_records(records))

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get):
        df = download_symbol_hours("EURUSD", "EURUSD", hours, workers=2)

    assert len(df) == 2  # the weekend hour contributed no bar
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert validate_ohlcv(df) is True


def test_download_symbol_hours_skips_a_bad_hour_without_aborting():
    hours = [
        datetime(2024, 1, 2, 9, tzinfo=timezone.utc),
        datetime(2024, 1, 2, 10, tzinfo=timezone.utc),
    ]
    good_body = _compress_records([(0, 108510, 108490, 1.0, 1.0), (30_000, 108520, 108500, 1.0, 1.0)])

    def fake_get(url, timeout=None):
        if "09h_ticks" in url:
            return _fake_response(500)  # a genuine, non-404 failure
        return _fake_response(200, good_body)

    with patch("download_dukascopy_history.requests.get", side_effect=fake_get):
        df = download_symbol_hours("EURUSD", "EURUSD", hours, workers=2)

    assert len(df) == 1  # the bad hour was skipped, not fatal to the whole download


# ---------------------------------------------------------------------------
# coarsening M15 (base) -> H1/H4/D1
# ---------------------------------------------------------------------------


def test_coarsen_bars_m15_is_a_passthrough():
    idx = pd.date_range("2024-01-02", periods=3, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {"open": [1.0, 1.1, 1.2], "high": [1.05, 1.15, 1.25], "low": [0.95, 1.05, 1.15],
         "close": [1.02, 1.12, 1.22], "volume": [10, 20, 30]},
        index=idx,
    )
    out = coarsen_bars(df, "M15")
    pd.testing.assert_frame_equal(out, df)


def test_coarsen_bars_m15_to_h1_aggregates_correctly():
    idx = pd.date_range("2024-01-02 00:00", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {"open": [1.0, 1.1, 1.2, 1.3], "high": [1.05, 1.15, 1.25, 1.35],
         "low": [0.95, 1.05, 1.15, 1.25], "close": [1.02, 1.12, 1.22, 1.32],
         "volume": [10, 20, 30, 40]},
        index=idx,
    )
    out = coarsen_bars(df, "H1")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 1.0
    assert row["close"] == 1.32
    assert row["high"] == 1.35
    assert row["low"] == 0.95
    assert row["volume"] == 100
    assert validate_ohlcv(out) is True


def test_coarsen_bars_m15_to_h4_aggregates_across_16_bars():
    idx = pd.date_range("2024-01-02 00:00", periods=16, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [1.0 + 0.01 * i for i in range(16)],
            "high": [1.05 + 0.01 * i for i in range(16)],
            "low": [0.95 + 0.01 * i for i in range(16)],
            "close": [1.02 + 0.01 * i for i in range(16)],
            "volume": [10] * 16,
        },
        index=idx,
    )
    out = coarsen_bars(df, "H4")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == pytest.approx(df["open"].iloc[0])
    assert row["close"] == pytest.approx(df["close"].iloc[-1])
    assert row["high"] == pytest.approx(df["high"].max())
    assert row["low"] == pytest.approx(df["low"].min())
    assert row["volume"] == pytest.approx(160)
    assert validate_ohlcv(out) is True


def test_m15_to_h1_coarsening_matches_direct_single_hour_tick_aggregation():
    """The module docstring's load-bearing correctness claim: building M15
    bars first, then coarsening to H1, must be mathematically identical to
    directly aggregating the whole hour's raw ticks into one H1 bar."""
    hour = datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    ticks = [
        (0, 108520, 108500, 1.0, 1.0),                # bucket 0 -> open
        (5 * 60 * 1000, 108600, 108580, 0.5, 0.5),    # bucket 0 -> overall high
        (20 * 60 * 1000, 108400, 108380, 0.5, 0.5),   # bucket 1 -> overall low
        (50 * 60 * 1000, 108450, 108430, 1.0, 1.0),   # bucket 3 -> close
    ]
    point_value = 100_000

    m15_bars = ticks_to_m15_bars(hour, ticks, point_value)
    m15_df = pd.DataFrame(m15_bars).set_index("datetime").sort_index()
    m15_df.index = pd.DatetimeIndex(m15_df.index, tz="UTC")
    coarsened = coarsen_bars(m15_df, "H1")

    # Direct single-hour aggregation of the SAME ticks, bypassing M15 entirely.
    mids = [((ask / point_value) + (bid / point_value)) / 2.0 for _, ask, bid, _, _ in ticks]
    total_volume = sum(ask_vol + bid_vol for _, _, _, ask_vol, bid_vol in ticks)

    assert len(coarsened) == 1
    row = coarsened.iloc[0]
    assert row["open"] == pytest.approx(mids[0])
    assert row["high"] == pytest.approx(max(mids))
    assert row["low"] == pytest.approx(min(mids))
    assert row["close"] == pytest.approx(mids[-1])
    assert row["volume"] == pytest.approx(total_volume)


# ---------------------------------------------------------------------------
# symbol-map coverage / scope
# ---------------------------------------------------------------------------


def test_symbol_map_scoped_to_fx_metals_crypto_excludes_usoil_and_stocks():
    excluded = {"USOIL", "US30", "NAS100", "SPX500", "AAPL", "NVDA", "SPY", "QQQ"}
    assert excluded.isdisjoint(SYMBOL_MAP)


def test_default_symbols_are_all_mapped():
    assert set(DEFAULT_SYMBOLS).issubset(SYMBOL_MAP)


def test_fx_majors_minors_and_metals_map_1_to_1():
    high_confidence = [
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
        "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF", "XAUUSD", "XAGUSD",
    ]
    for sym in high_confidence:
        assert SYMBOL_MAP[sym] == sym
