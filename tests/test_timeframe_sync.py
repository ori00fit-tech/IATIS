"""
tests/test_timeframe_sync.py
--------------------------------
Regression tests for core.timeframe_sync.resample()/build_multi_timeframe_view()
— specifically BUG-011 (reports/forensic/13_CONFIRMED_BUGS.md): resampling a
TRUNCATED base-timeframe window (the shape every backtest bar-by-bar loop
produces via df.iloc[:i+1]) used to silently include a still-forming, partial
final bucket instead of dropping it — the exact class of bug BUG-010 already
fixed for live fetches, recurring here in the resample-from-a-finer-series
construction path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.timeframe_sync import (
    SUPPORTED_TIMEFRAMES,
    build_multi_timeframe_view,
    resample,
)


def _h4_df(n_days: int) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n_days * 6, freq="4h", tz="UTC")
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(scale=0.1, size=len(idx)))
    return pd.DataFrame(
        {"open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 100.0},
        index=idx,
    )


def test_resample_drops_still_forming_last_day_when_base_minutes_given():
    df = _h4_df(5)
    # Truncate mid-day-3 (only the first 3 of that day's 6 H4 bars present).
    window = df.iloc[: 6 + 6 + 3]
    full_d1 = df.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()

    out = resample(window, "D1", base_minutes=240)
    assert out.index[-1] == pd.Timestamp("2026-01-02", tz="UTC")
    assert out["close"].iloc[-1] == pytest.approx(full_d1.loc["2026-01-02", "close"])


def test_resample_keeps_last_day_when_window_ends_exactly_at_day_boundary():
    df = _h4_df(5)
    # Last bar of day 2 (20:00 UTC) — day 2 is fully covered.
    window = df.iloc[: 6 + 6]
    full_d1 = df.resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()

    out = resample(window, "D1", base_minutes=240)
    assert out.index[-1] == pd.Timestamp("2026-01-02", tz="UTC")
    assert out["close"].iloc[-1] == pytest.approx(full_d1.loc["2026-01-02", "close"])


def test_resample_without_base_minutes_preserves_old_behavior():
    """Backward-compat: base_minutes is optional (default None) — every
    caller that never passes it (there are none left in this codebase after
    the BUG-011 fix, but the parameter must stay opt-in-safe) gets the
    original, un-trimmed resample output."""
    df = _h4_df(5)
    window = df.iloc[: 6 + 6 + 3]
    out_default = resample(window, "D1")
    out_explicit_none = resample(window, "D1", base_minutes=None)
    pd.testing.assert_frame_equal(out_default, out_explicit_none)
    # And it includes the partial day (the pre-fix behavior) — 3 days total.
    assert len(out_default) == 3


def test_resample_noop_on_empty_dataframe():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    empty.index = pd.DatetimeIndex([], tz="UTC")
    out = resample(empty, "D1", base_minutes=240)
    assert len(out) == 0


def test_resample_unsupported_timeframe_raises():
    df = _h4_df(1)
    with pytest.raises(ValueError):
        resample(df, "W1", base_minutes=240)


def test_build_multi_timeframe_view_d1_and_h4_both_drop_still_forming_bucket():
    """build_multi_timeframe_view threads base_minutes through automatically
    (base_label = timeframes[0]) — no caller has to pass it explicitly."""
    df = _h4_df(5)
    window = df.iloc[: 6 + 6 + 3]  # mid-day-3, 3 of 6 H4 bars present
    views = build_multi_timeframe_view(window, ["H4", "D1"])
    assert views["H4"].index[-1] == window.index[-1]  # base view is untouched
    assert views["D1"].index[-1] == pd.Timestamp("2026-01-02", tz="UTC")


def test_build_multi_timeframe_view_h1_base_drops_partial_h4_and_d1():
    idx = pd.date_range("2026-01-01", periods=5 * 24, freq="1h", tz="UTC")
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(scale=0.05, size=len(idx)))
    df = pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close, "volume": 50.0},
        index=idx,
    )
    # Truncate 2h into the 3rd H4 bucket of day 3 (08:00-10:00 present, not 12:00).
    window = df.iloc[: 24 + 24 + 10]  # ends at 2026-01-03 09:00
    views = build_multi_timeframe_view(window, ["H1", "H4", "D1"])
    # H4 bucket 08:00-12:00 on day 3 only has 08:00/09:00 -> incomplete -> dropped.
    assert views["H4"].index[-1] == pd.Timestamp("2026-01-03 04:00:00", tz="UTC")
    # Day 3 itself is far from complete -> D1's last row is day 2.
    assert views["D1"].index[-1] == pd.Timestamp("2026-01-02", tz="UTC")


def test_supported_timeframes_matches_resample_rule_keys():
    assert set(SUPPORTED_TIMEFRAMES) == {"M15", "H1", "H4", "D1"}
