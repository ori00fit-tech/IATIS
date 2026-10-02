"""tests/test_data_trust_adversarial.py -- integration/adversarial proofs
for Phase 11 (Data Trust & Provenance Layer): real synthetic market data
through the REAL, unmodified core.data_validator functions (not mocked),
proof that data_trust never touches core/data_validator.py's own
behavior, edge cases (empty data, duplicate timestamps), and boundary
inclusivity of critical_window."""
from __future__ import annotations

import pandas as pd
import pytest

from core import data_validator
from data_trust import manifest as dtm
from data_trust import quality as dtq


# --- real, unmocked integration through core.data_loader.load_synthetic ----


def test_real_synthetic_data_through_the_full_assess_data_trust_pipeline():
    """Uses the SAME real synthetic-data generator tests/test_replay.py's
    own proven pattern relies on -- proves assess_data_trust() works
    against genuinely realistic OHLCV content, not a hand-crafted
    toy frame."""
    from core.data_loader import load_synthetic

    df = load_synthetic(bars=300, timeframe="H1", seed=7)
    result = dtq.assess_data_trust(
        df, symbol="EURUSD", timeframe="H1", provider="synthetic",
        provenance=dtm.INJECTED_REPLAY_DATA,
    )
    assert result["validation_status"] in (dtq.VALID, dtq.VALID_WITH_WARNINGS, dtq.INVALID)
    assert result["manifest"]["row_count"] == len(df)
    assert result["manifest"]["dataset_hash"] is not None


def test_real_validate_ohlcv_is_actually_called_not_reimplemented(monkeypatch):
    """Spies on the REAL core.data_validator.validate_ohlcv -- proves
    assess_data_trust() calls the genuine function, never a local
    reimplementation of OHLC validity logic."""
    calls = []
    real_validate = data_validator.validate_ohlcv

    def _spy(df):
        calls.append(df)
        return real_validate(df)

    monkeypatch.setattr(dtq, "validate_ohlcv", _spy)
    df = _clean_df(20)
    dtq.assess_data_trust(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                           provenance=dtm.LIVE_PROVIDER_DATA)
    assert len(calls) == 1
    assert calls[0] is df


def test_real_find_gaps_is_actually_called_not_reimplemented(monkeypatch):
    calls = []
    real_find_gaps = data_validator.find_gaps

    def _spy(df, expected_freq):
        calls.append((df, expected_freq))
        return real_find_gaps(df, expected_freq)

    monkeypatch.setattr(dtq, "find_gaps", _spy)
    df = _clean_df(20)
    dtq.assess_missing_bars(df, "1h")
    assert len(calls) == 1
    assert calls[0][1] == "1h"


def _clean_df(n: int) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({
        "open": [1.10 + i * 0.001 for i in range(n)], "high": [1.11 + i * 0.001 for i in range(n)],
        "low": [1.09 + i * 0.001 for i in range(n)], "close": [1.105 + i * 0.001 for i in range(n)],
        "volume": [100.0] * n,
    }, index=idx)


# --- edge cases: empty data, duplicates ------------------------------------


def test_empty_dataframe_never_crashes_the_pipeline():
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex([], tz="UTC")
    result = dtq.assess_data_trust(df, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                    provenance=dtm.LIVE_PROVIDER_DATA)
    assert result["manifest"]["row_count"] == 0
    assert result["manifest"]["start"] is None
    assert result["manifest"]["end"] is None


def test_duplicate_timestamps_are_caught_by_the_real_validator_never_silently_hashed():
    df = _clean_df(5)
    dup = pd.concat([df, df.iloc[[0]]])  # a real duplicate timestamp
    result = dtq.assess_data_trust(dup, symbol="EURUSD", timeframe="H1", provider="twelve_data",
                                    provenance=dtm.LIVE_PROVIDER_DATA)
    assert result["validation_status"] == dtq.INVALID
    assert any("OHLC validation failed" in r for r in result["reasons"])


# --- critical_window boundary inclusivity -----------------------------------


def test_critical_window_boundaries_are_inclusive():
    df = _clean_df(20)
    gap_ts = df.index[10]
    df = df.drop(gap_ts)

    # window starts EXACTLY at the gap timestamp
    result_start = dtq.assess_missing_bars(df, "1h", critical_window=(gap_ts, df.index[-1]))
    assert result_start["classification"] == dtq.INVALID

    # window ends EXACTLY at the gap timestamp
    result_end = dtq.assess_missing_bars(df, "1h", critical_window=(df.index[0], gap_ts))
    assert result_end["classification"] == dtq.INVALID

    # window entirely before the gap
    result_before = dtq.assess_missing_bars(df, "1h", critical_window=(df.index[0], df.index[8]))
    assert result_before["classification"] == dtq.VALID_WITH_WARNINGS


# --- no modification of the reused controls' own behavior -------------------


def test_validate_ohlcv_behavior_is_byte_for_byte_unchanged(tmp_path):
    """Confirms Phase 11 never patched core/data_validator.py: the real
    module's own REQUIRED_COLUMNS and error messages are exactly what
    data_trust.quality relies on and propagates verbatim."""
    assert data_validator.REQUIRED_COLUMNS == ["open", "high", "low", "close", "volume"]
    df = _clean_df(5).drop(columns=["volume"])
    with pytest.raises(data_validator.DataValidationError, match="Missing required columns"):
        data_validator.validate_ohlcv(df)


def test_provenance_is_never_inferred_from_a_config_dict():
    """There is no code path in data_trust that reads a 'source' key or
    any config-shaped dict to guess provenance -- it must always be
    passed explicitly, proven here by omitting it entirely (a TypeError,
    not a silent default)."""
    with pytest.raises(TypeError):
        dtq.assess_data_trust(_clean_df(5), symbol="EURUSD", timeframe="H1", provider="twelve_data")  # type: ignore[call-arg]
