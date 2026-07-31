"""
tests/test_price_action_engine_v2.py
----------------------------------------
Confluence Engine Overhaul Track C (Phase 4, 2026-08-01) — engine-level
tests for PriceActionEngineV2, the genuinely pure price-action rebuild
(no RSI/Bollinger Bands). This engine is AD-HOC-ONLY (never enabled by
config/engines.yaml's `enabled` block, reachable only through Mission
Center's engine_variants override); correctness here means sound
pattern detection and honest abstention on hand-built sequences, not
golden bias/score values (there is no v1 behavior to preserve — this is
a full rewrite, not a refactor).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias
from engines.price_action_engine_v2 import (
    PriceActionEngineV2,
    _closing_strength,
    _compression_ratio,
    _detect_fakey,
    _detect_failed_breakout,
    _detect_opening_drive,
    _detect_three_bar_play,
    _is_inside_bar,
    _is_nr_n,
    _is_outside_bar,
    _micro_trend,
    _volatility_contraction_score,
    decide,
    extract_features,
)


def _df(rows: list[list[float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC")
    arr = np.array(rows, dtype=float)
    return pd.DataFrame({"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2], "close": arr[:, 3]}, index=idx)


# ---------------------------------------------------------------------------
# Individual pattern detectors — hand-built OHLC sequences
# ---------------------------------------------------------------------------

def test_inside_bar_detected():
    df = _df([[100, 105, 95, 102], [100.5, 104, 96, 101]])
    assert _is_inside_bar(df) is True


def test_inside_bar_not_detected_on_outside_range():
    df = _df([[100, 105, 95, 102], [99, 106, 94, 101]])
    assert _is_inside_bar(df) is False


def test_outside_bar_detected():
    df = _df([[100, 102, 98, 101], [99, 106, 94, 101]])
    assert _is_outside_bar(df) is True


def test_nr7_detected_on_narrowest_bar():
    rows = [[100, 100 + r, 100, 100] for r in [5, 4, 6, 3, 5, 4, 1]]
    df = _df(rows)
    assert _is_nr_n(df, 7) is True
    assert _is_nr_n(df, 4) is True  # also the narrowest of the last 4


def test_nr7_false_when_not_narrowest():
    rows = [[100, 100 + r, 100, 100] for r in [5, 4, 6, 3, 5, 4, 8]]
    df = _df(rows)
    assert _is_nr_n(df, 7) is False


def test_fakey_bullish():
    # mother bar (wide) -> inside bar (tight) -> wick below inside low, close back above
    df = _df([
        [100.0, 102.0, 98.0, 99.0],   # mother
        [99.0, 99.5, 98.5, 99.0],     # inside bar (high<102, low>98)
        [98.3, 99.0, 80.0, 98.8],     # false break below inside low, close back above
    ])
    direction, strength = _detect_fakey(df, {})
    assert direction == "bullish"
    assert strength > 0.5


def test_fakey_bearish():
    df = _df([
        [100.0, 102.0, 98.0, 101.0],  # mother
        [100.5, 101.5, 100.0, 101.0],  # inside bar (high<102, low>98)
        [101.2, 120.0, 100.5, 101.3],  # false break above inside high (101.5), close back below it
    ])
    direction, strength = _detect_fakey(df, {})
    assert direction == "bearish"


def test_fakey_none_when_no_mother_inside_relationship():
    df = _df([[100, 105, 95, 102], [101, 106, 96, 103], [102, 107, 97, 104]])
    direction, _ = _detect_fakey(df, {})
    assert direction == "none"


def test_three_bar_play_bullish():
    df = _df([
        [100.0, 111.0, 99.0, 110.5],   # strong up bar
        [110.0, 110.8, 110.0, 110.3],  # small pause bar
        [110.5, 112.0, 110.3, 111.8],  # break above pause high
    ])
    direction, strength = _detect_three_bar_play(df, {})
    assert direction == "bullish"
    assert strength > 0


def test_three_bar_play_bearish():
    df = _df([
        [110.0, 111.0, 99.0, 100.0],   # strong down bar
        [100.2, 100.8, 100.0, 100.3],  # small pause bar
        [100.1, 100.3, 98.0, 98.5],    # break below pause low
    ])
    direction, strength = _detect_three_bar_play(df, {})
    assert direction == "bearish"


def test_three_bar_play_none_when_pause_bar_too_wide():
    df = _df([
        [100.0, 111.0, 99.0, 110.5],   # strong up bar (range 12)
        [105.0, 111.0, 99.0, 106.0],   # NOT a pause — nearly as wide as the strong bar
        [106.0, 113.0, 105.0, 112.0],
    ])
    direction, _ = _detect_three_bar_play(df, {})
    assert direction == "none"


def _zigzag_uptrend_df(n_legs: int = 4) -> pd.DataFrame:
    """Explicit humps (rise-rise-PEAK-fall-fall per leg), each leg's peak
    and trough higher than the last — real interior local extrema, unlike
    a monotonic ramp (which has NO swing points at all, since every high
    is beaten by a later one until the trend ends)."""
    highs, lows = [], []
    for i in range(n_legs):
        base = 100 + i * 20
        highs += [base + 5, base + 9, base + 15, base + 8, base + 4]
        lows += [base, base + 3, base + 9, base + 2, base - 2]
    rows = [[lows[i] + 1, highs[i], lows[i], (highs[i] + lows[i]) / 2] for i in range(len(highs))]
    return _df(rows)


def test_micro_trend_bullish_on_rising_swings():
    df = _zigzag_uptrend_df()
    direction, strength = _micro_trend(df, lookback=10, swing_window=2)
    assert direction == "bullish"
    assert strength == 1.0


def test_micro_trend_bearish_on_falling_swings():
    df = _zigzag_uptrend_df()
    # Reverse the OHLC order to mirror the uptrend into a downtrend, keeping
    # the same real local-extrema structure (avoids a second hand-built series).
    reversed_df = df.iloc[::-1].reset_index(drop=True)
    reversed_df.index = df.index
    direction, strength = _micro_trend(reversed_df, lookback=10, swing_window=2)
    assert direction == "bearish"
    assert strength == 1.0


def test_micro_trend_none_on_too_short_series():
    df = _df([[100, 101, 99, 100.5]] * 3)
    direction, strength = _micro_trend(df, lookback=5, swing_window=2)
    assert direction == "none"
    assert strength == 0.0


def test_compression_ratio_below_one_when_range_shrinking():
    wide_rows = [[100, 100 + 5, 100 - 5, 100] for _ in range(15)]
    tight_rows = [[100, 100 + 0.5, 100 - 0.5, 100] for _ in range(5)]
    df = _df(wide_rows + tight_rows)
    ratio = _compression_ratio(df, short_lookback=5, long_lookback=20)
    assert ratio < 1.0


def test_volatility_contraction_score_high_on_monotonic_shrink():
    ranges = [10, 8, 6, 4, 2, 1]
    rows = [[100, 100 + r, 100, 100] for r in ranges]
    df = _df(rows)
    score = _volatility_contraction_score(df, lookback=5)
    assert score == 1.0


def test_volatility_contraction_score_low_on_widening():
    ranges = [1, 2, 4, 6, 8, 10]
    rows = [[100, 100 + r, 100, 100] for r in ranges]
    df = _df(rows)
    score = _volatility_contraction_score(df, lookback=5)
    assert score == 0.0


def test_failed_breakout_bearish_reversal():
    # 21-bar prior range with high=110 (>= lookback+1 so the ATR/prior-range
    # window is fully populated), then a bar wicks above and closes back inside
    prior = [[105, 110, 100, 107] for _ in range(21)]
    breakout_bar = [[108, 118, 107, 109]]  # wicks to 118, closes at 109 (< prior high 110)
    df = _df(prior + breakout_bar)
    direction, strength = _detect_failed_breakout(df, lookback=20, atr_period=14)
    assert direction == "bearish"
    assert strength > 0


def test_failed_breakout_bullish_reversal():
    prior = [[105, 110, 100, 103] for _ in range(21)]
    breakout_bar = [[102, 103, 85, 101]]  # wicks to 85 (< prior low 100), closes at 101 (> 100)
    df = _df(prior + breakout_bar)
    direction, strength = _detect_failed_breakout(df, lookback=20, atr_period=14)
    assert direction == "bullish"


def test_opening_drive_bullish():
    df = _df([[100.0, 105.2, 99.8, 105.0]])  # open near low, close near high, tiny wicks
    direction, strength = _detect_opening_drive(df, {})
    assert direction == "bullish"
    assert strength > 0.8


def test_opening_drive_none_on_large_wicks():
    df = _df([[100.0, 110.0, 90.0, 101.0]])  # huge wicks both sides
    direction, _ = _detect_opening_drive(df, {})
    assert direction == "none"


def test_closing_strength_at_high_low_mid():
    assert _closing_strength(_df([[100, 110, 100, 110]])) == pytest.approx(1.0)
    assert _closing_strength(_df([[100, 110, 100, 100]])) == pytest.approx(-1.0)
    assert _closing_strength(_df([[100, 110, 100, 105]])) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# extract_features / decide — purity + engine-level behavior
# ---------------------------------------------------------------------------

def _synth_mtf(seed: int = 1, bars: int = 200):
    df_h1 = load_synthetic(bars=bars, timeframe="H1", seed=seed)
    return build_multi_timeframe_view(df_h1, ["H1", "H4", "D1"])


def test_extract_features_is_pure():
    mtf = _synth_mtf(seed=1)
    df = mtf["H1"]
    f1 = extract_features(df, {})
    f2 = extract_features(df, {})
    assert f1 == f2


def test_decide_is_pure():
    mtf = _synth_mtf(seed=1)
    features = extract_features(mtf["H1"], {})
    r1 = decide(features, {})
    r2 = decide(features, {})
    assert r1 == r2


def test_decide_bullish_from_opening_drive_alone():
    df = _df([[100 + i, 101 + i, 99 + i, 100.5 + i] for i in range(30)] + [[130.0, 140.0, 129.8, 139.9]])
    t = {"min_bars": 30}
    features = extract_features(df, t)
    bias, score, reasons = decide(features, t)
    assert bias == Bias.BULLISH
    assert score > 0
    assert any("Opening Drive" in r for r in reasons)


def test_decide_neutral_on_flat_featureless_bar():
    rows = [[100, 100.1, 99.9, 100.0] for _ in range(31)]
    df = _df(rows)
    features = extract_features(df, {})
    bias, score, reasons = decide(features, {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0


def test_score_bounds_across_many_scenarios():
    for seed in range(1, 10):
        mtf = _synth_mtf(seed=seed, bars=250)
        out = PriceActionEngineV2().safe_analyze(mtf)
        assert 0.0 <= out.score <= 80.0


def test_insufficient_data_abstains():
    df = load_synthetic(bars=10, timeframe="H1", seed=1)
    mtf = build_multi_timeframe_view(df, ["H1"])
    out = PriceActionEngineV2().safe_analyze(mtf)
    assert out.bias == Bias.NEUTRAL
    assert out.score == 0.0
    assert "Insufficient data" in out.reasons[0]


def test_engine_output_json_serializable():
    mtf = _synth_mtf(seed=7)
    out = PriceActionEngineV2().safe_analyze(mtf)
    json.dumps(out.features, default=str)
    json.dumps(out.raw, default=str)


def test_engine_output_defaults():
    mtf = _synth_mtf(seed=7)
    out = PriceActionEngineV2().safe_analyze(mtf)
    assert out.evidence_level == "HEURISTIC"
    assert out.probability is None
    assert out.engine_name == "PriceActionV2"


def test_no_rsi_or_bollinger_import():
    import inspect

    from engines import price_action_engine_v2 as mod

    source = inspect.getsource(mod)
    assert "bollinger_bands" not in source
    assert "rsi_wilder" not in source
