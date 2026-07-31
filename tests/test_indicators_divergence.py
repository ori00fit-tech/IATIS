"""
tests/test_indicators_divergence.py
----------------------------------------
Confluence Engine Overhaul Phase 3b — hand-computed and adversarial-
sequence correctness tests for the swing/divergence primitives added to
utils/indicators.py for engines/divergence_engine.py's rebuild
(rsi_wilder, macd, zigzag_pivots).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.indicators import macd, rsi, rsi_wilder, zigzag_pivots


# ---------------------------------------------------------------------------
# rsi_wilder — matches v1 divergence_engine.py's pre-rebuild _rsi() exactly,
# and is genuinely different from rsi()'s SMA-smoothed formula.
# ---------------------------------------------------------------------------

def test_rsi_wilder_matches_hand_computed_formula():
    s = pd.Series([1.0, 2.0, 1.0, 2.0, 3.0, 2.0, 3.0, 4.0, 3.0, 4.0])
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    expected = 100 - (100 / (1 + gain / loss.replace(0, 1e-10)))
    result = rsi_wilder(s, period=14)
    pd.testing.assert_series_equal(result, expected)


def test_rsi_wilder_differs_from_sma_smoothed_rsi():
    # A step change in returns hits EWM (Wilder) and SMA smoothing
    # differently — proves these are two real, distinct formulas, not a
    # redundant pair that should have been unified. Compare from bar 30
    # onward, well past both formulas' warmup, so the comparison is
    # never confounded by one still holding NaN.
    rng = np.random.default_rng(3)
    s = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))
    wilder = rsi_wilder(s, period=14).iloc[30:]
    sma = rsi(s, period=14).iloc[30:]
    assert not np.allclose(wilder.to_numpy(), sma.to_numpy(), atol=1e-6)


# ---------------------------------------------------------------------------
# macd — matches v1 divergence_engine.py's pre-rebuild _macd() exactly.
# ---------------------------------------------------------------------------

def test_macd_matches_hand_computed_formula():
    s = pd.Series([1.0, 2.0, 3.0, 2.5, 3.5, 4.0, 3.8, 4.2, 4.5, 4.3, 4.8, 5.0])
    ema_fast = s.ewm(span=4, adjust=False).mean()
    ema_slow = s.ewm(span=6, adjust=False).mean()
    expected_macd = ema_fast - ema_slow
    expected_signal = expected_macd.ewm(span=3, adjust=False).mean()
    macd_line, signal_line = macd(s, fast=4, slow=6, signal=3)
    pd.testing.assert_series_equal(macd_line, expected_macd)
    pd.testing.assert_series_equal(signal_line, expected_signal)


# ---------------------------------------------------------------------------
# zigzag_pivots — adversarial sequences chosen to prove specific
# properties, not just happy-path shapes.
# ---------------------------------------------------------------------------

def _confirmed(res: pd.DataFrame) -> pd.DataFrame:
    return res[res["pivot_type"].notna()]


def test_zigzag_clean_v_confirms_start_high_and_low():
    vals = list(range(100, 90, -1)) + list(range(90, 100))
    s = pd.Series(vals, dtype=float)
    atr = pd.Series([1.0] * len(vals))
    res = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=3))
    assert list(res["pivot_type"]) == ["high", "low"]
    assert res.iloc[0]["pivot_value"] == 100.0
    assert res.iloc[1]["pivot_value"] == 90.0
    assert res.index[1] == 10


def test_zigzag_monotonic_series_never_gets_stuck_after_spacing_block():
    # A confirmed low is immediately followed by a shallow 1-bar bounce
    # that satisfies the MAGNITUDE threshold but not the SPACING one —
    # the deeper move afterward (to 76, well past the original low of
    # 90) must still get picked up, not silently discarded because the
    # first (too-early) reversal attempt already "used up" the leg.
    vals = (
        list(range(100, 89, -1))        # 100 -> 90 (10 bars)
        + [92]                            # shallow, too-early bounce
        + list(range(85, 75, -1))         # keeps sliding well past 90, down to 76
        + list(range(78, 86))             # real reversal, confirmed after enough spacing
    )
    s = pd.Series(vals, dtype=float)
    atr = pd.Series([1.0] * len(vals))
    res = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=3))
    lows = res[res["pivot_type"] == "low"]
    assert 90.0 not in lows["pivot_value"].to_list()  # the premature low must NOT be the one confirmed
    assert 76.0 in lows["pivot_value"].to_list()       # the real, deeper low must be confirmed instead


def test_zigzag_sub_threshold_wiggle_does_not_fragment_the_leg():
    # A wiggle smaller than the ATR-magnitude threshold must not be
    # treated as its own confirmed pivot — the leg should read through
    # it to the eventual larger, real reversal.
    vals = [100.0, 99.0, 98.0, 97.0, 97.3, 96.0, 95.0, 80.0, 85.0, 90.0, 95.0]
    s = pd.Series(vals)
    atr = pd.Series([1.0] * len(vals))
    res = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=2))
    # The tiny 97 -> 97.3 wiggle must never appear as a confirmed pivot value.
    assert 97.3 not in res["pivot_value"].to_list()


def test_zigzag_respects_min_bars_between_for_a_genuinely_short_leg():
    # Huge swings, but each leg is only 1 bar long — with a strict
    # spacing requirement, NOTHING should ever confirm; relaxing it
    # lets the same real oscillation through.
    vals = [100.0, 90.0, 100.0, 90.0, 100.0, 90.0, 100.0]
    s = pd.Series(vals)
    atr = pd.Series([1.0] * len(vals))
    res_blocked = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=5))
    assert len(res_blocked) == 0
    res_allowed = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=1))
    assert len(res_allowed) >= 3


def test_zigzag_last_open_extreme_is_never_written_out():
    vals = list(range(100, 130))  # monotonic rise, never reverses
    s = pd.Series(vals, dtype=float)
    atr = pd.Series([1.0] * len(vals))
    res = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=3))
    # Only the START point (100, retroactively a "low") can be confirmed
    # once the rise is big enough — the series' own endpoint (the running
    # high) must never appear, since it's still open/unconfirmed.
    assert 129.0 not in res["pivot_value"].to_list()


def test_zigzag_handles_leading_nan_warmup():
    vals = [np.nan, np.nan, np.nan] + list(range(100, 90, -1)) + list(range(90, 100))
    s = pd.Series(vals, dtype=float)
    atr_vals = [np.nan, np.nan, np.nan] + [1.0] * (len(vals) - 3)
    atr = pd.Series(atr_vals)
    res = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=3))
    assert len(res) >= 1  # doesn't crash, and still finds real pivots after the NaN warmup


def test_zigzag_full_multi_leg_sequence():
    vals = list(range(100, 89, -1)) + list(range(90, 111)) + list(range(110, 94, -1))
    s = pd.Series(vals, dtype=float)
    atr = pd.Series([1.0] * len(vals))
    res = _confirmed(zigzag_pivots(s, atr, min_move_atr_mult=1.5, min_bars_between=3))
    assert list(res["pivot_type"]) == ["high", "low", "high"]
    assert list(res["pivot_value"]) == [100.0, 90.0, 110.0]


def test_zigzag_empty_and_too_short_series_do_not_crash():
    empty = pd.Series([], dtype=float)
    atr_empty = pd.Series([], dtype=float)
    res = zigzag_pivots(empty, atr_empty)
    assert len(res) == 0

    short = pd.Series([1.0])
    atr_short = pd.Series([1.0])
    res_short = zigzag_pivots(short, atr_short)
    assert res_short["pivot_type"].isna().all()
