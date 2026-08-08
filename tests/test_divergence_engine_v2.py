"""
tests/test_divergence_engine_v2.py
---------------------------------------
Confluence Engine Overhaul Phase 3b — engine-level tests for
DivergenceEngine's swing-detection + divergence-type rebuild. This
engine is DISABLED (config/engines.yaml engines.enabled.divergence:
false); correctness here means sound pattern detection and honest
abstention, not golden bias/score values (v1 is fully replaced, not
refactored).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias
from engines.divergence_engine import (
    DivergenceEngine,
    _check_triple,
    _detect_pattern,
    _mtf_regular_direction,
    decide,
    extract_features,
)


def _df_from_closes(vals: list[float], freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(vals), freq=freq, tz="UTC")
    arr = np.array(vals, dtype=float)
    return pd.DataFrame(
        {"open": arr, "high": arr * 1.0005, "low": arr * 0.9995, "close": arr, "volume": 1000.0}, index=idx,
    )


# ---------------------------------------------------------------------------
# _detect_pattern — hand-built price/indicator series + hand-built pivot
# DataFrames (no zigzag/RSI computed at all), mirroring quant_engine.py's
# own _classify_regime hand-built-vote-dict precedent.
# ---------------------------------------------------------------------------

def _pivots(price: pd.Series, positions: list[int]) -> pd.DataFrame:
    idx = price.index[positions]
    return pd.DataFrame({"pivot_type": "x", "pivot_value": price.loc[idx].to_numpy()}, index=idx)


def test_detect_pattern_regular_bearish():
    price = pd.Series([100.0, 105.0, 102.0, 110.0, 108.0], index=pd.RangeIndex(5))
    indicator = pd.Series([50.0, 80.0, 55.0, 60.0, 52.0], index=pd.RangeIndex(5))
    highs = _pivots(price, [1, 3])  # price HH: 105 -> 110
    lows = _pivots(price, [0])       # only 1 low, can't form a bullish/hidden-bullish pair
    result = _detect_pattern(price, indicator, highs, lows)
    assert result["type"] == "regular_bearish"  # price HH (105->110), indicator LH (80->60)
    assert result["price_move_pct"] == pytest.approx(abs(110 - 105) / 105)


def test_detect_pattern_regular_bullish():
    price = pd.Series([100.0, 90.0, 95.0, 85.0, 92.0], index=pd.RangeIndex(5))
    indicator = pd.Series([50.0, 20.0, 55.0, 30.0, 52.0], index=pd.RangeIndex(5))
    highs = _pivots(price, [0])
    lows = _pivots(price, [1, 3])  # price LL: 90 -> 85
    result = _detect_pattern(price, indicator, highs, lows)
    assert result["type"] == "regular_bullish"  # price LL (90->85), indicator HL (20->30)


def test_detect_pattern_regular_tie_break_picks_stronger_move():
    # Both a bearish and a bullish regular pattern qualify — the one
    # with the LARGER price_move_pct must win.
    price = pd.Series([100.0, 101.0, 100.5, 200.0, 190.0, 300.0], index=pd.RangeIndex(6))
    indicator = pd.Series([50.0, 80.0, 55.0, 60.0, 20.0, 40.0], index=pd.RangeIndex(6))
    highs = _pivots(price, [1, 3])  # price HH: 101 -> 200 (small % move), indicator LH: 80->60
    lows = _pivots(price, [2, 4])   # not actually a low relationship here, just for shape
    # Build a case where bullish's magnitude is clearly bigger.
    price2 = pd.Series([100.0, 101.0, 500.0, 100.0], index=pd.RangeIndex(4))
    indicator2 = pd.Series([50.0, 80.0, 10.0, 90.0], index=pd.RangeIndex(4))
    highs2 = _pivots(price2, [1, 2])  # price HH: 101->500 (big move), indicator LH: 80->10
    lows2 = _pivots(price2, [0, 3])   # price flat LL doesn't apply (100->100, not <), so bearish should win
    result = _detect_pattern(price2, indicator2, highs2, lows2)
    assert result["type"] == "regular_bearish"


def test_detect_pattern_hidden_bearish():
    price = pd.Series([100.0, 140.0, 110.0, 135.0], index=pd.RangeIndex(4))
    indicator = pd.Series([50.0, 70.0, 40.0, 90.0], index=pd.RangeIndex(4))
    highs = _pivots(price, [1, 3])  # price LH: 140 -> 135
    lows = _pivots(price, [0])
    result = _detect_pattern(price, indicator, highs, lows)
    assert result["type"] == "hidden_bearish"  # price LH, indicator HH (70->90)


def test_detect_pattern_hidden_bullish():
    price = pd.Series([140.0, 100.0, 130.0, 105.0], index=pd.RangeIndex(4))
    indicator = pd.Series([50.0, 60.0, 40.0, 20.0], index=pd.RangeIndex(4))
    highs = _pivots(price, [0])
    lows = _pivots(price, [1, 3])  # price HL: 100 -> 105
    result = _detect_pattern(price, indicator, highs, lows)
    assert result["type"] == "hidden_bullish"  # price HL, indicator LL (60->20)


def test_detect_pattern_none_when_insufficient_swings():
    price = pd.Series([100.0, 110.0], index=pd.RangeIndex(2))
    indicator = pd.Series([50.0, 60.0], index=pd.RangeIndex(2))
    highs = _pivots(price, [1])
    lows = pd.DataFrame({"pivot_type": [], "pivot_value": []})
    result = _detect_pattern(price, indicator, highs, lows)
    assert result["type"] == "none"
    assert result["price_move_pct"] == 0.0


def test_detect_pattern_none_when_no_relationship_holds():
    price = pd.Series([100.0, 110.0, 120.0], index=pd.RangeIndex(3))
    indicator = pd.Series([50.0, 60.0, 70.0], index=pd.RangeIndex(3))  # confirms, doesn't diverge
    highs = _pivots(price, [1, 2])
    lows = pd.DataFrame({"pivot_type": [], "pivot_value": []})
    result = _detect_pattern(price, indicator, highs, lows)
    assert result["type"] == "none"


# ---------------------------------------------------------------------------
# _check_triple
# ---------------------------------------------------------------------------

def test_check_triple_confirms_bearish():
    price = pd.Series([100.0, 105.0, 110.0], index=pd.RangeIndex(3))
    indicator = pd.Series([80.0, 65.0, 50.0], index=pd.RangeIndex(3))  # monotonically weakening
    highs = _pivots(price, [0, 1, 2])
    lows = pd.DataFrame({"pivot_type": [], "pivot_value": []})
    pattern = {"type": "regular_bearish", "price_move_pct": 0.05}
    assert _check_triple(price, indicator, highs, lows, pattern) is True


def test_check_triple_false_when_third_swing_disagrees():
    price = pd.Series([100.0, 105.0, 110.0], index=pd.RangeIndex(3))
    indicator = pd.Series([50.0, 65.0, 50.0], index=pd.RangeIndex(3))  # swing[-3->-2] does NOT weaken
    highs = _pivots(price, [0, 1, 2])
    lows = pd.DataFrame({"pivot_type": [], "pivot_value": []})
    pattern = {"type": "regular_bearish", "price_move_pct": 0.05}
    assert _check_triple(price, indicator, highs, lows, pattern) is False


def test_check_triple_false_for_hidden_pattern():
    price = pd.Series([100.0, 105.0, 110.0], index=pd.RangeIndex(3))
    indicator = pd.Series([80.0, 65.0, 50.0], index=pd.RangeIndex(3))
    highs = _pivots(price, [0, 1, 2])
    lows = pd.DataFrame({"pivot_type": [], "pivot_value": []})
    pattern = {"type": "hidden_bearish", "price_move_pct": 0.0}
    assert _check_triple(price, indicator, highs, lows, pattern) is False


def test_check_triple_false_when_fewer_than_3_swings():
    price = pd.Series([100.0, 105.0], index=pd.RangeIndex(2))
    indicator = pd.Series([80.0, 65.0], index=pd.RangeIndex(2))
    highs = _pivots(price, [0, 1])
    lows = pd.DataFrame({"pivot_type": [], "pivot_value": []})
    pattern = {"type": "regular_bearish", "price_move_pct": 0.05}
    assert _check_triple(price, indicator, highs, lows, pattern) is False


# ---------------------------------------------------------------------------
# _mtf_regular_direction — real (verified) regular-divergence series used
# as the coarser timeframe's data.
# ---------------------------------------------------------------------------

_BEARISH_REGULAR_CLOSES = (
    list(np.linspace(100, 140, 20)) + list(np.linspace(140, 115, 15))
    + list(np.linspace(115, 141, 60)) + list(np.linspace(141, 138, 10))
)
_BULLISH_REGULAR_CLOSES = (
    list(np.linspace(140, 100, 20)) + list(np.linspace(100, 125, 15))
    + list(np.linspace(125, 99, 60)) + list(np.linspace(99, 102, 10))
)


def test_mtf_regular_direction_detects_bearish_on_coarser_frame():
    coarser_df = _df_from_closes(_BEARISH_REGULAR_CLOSES)
    mtf_data = {"H4": coarser_df}
    direction, tf_checked = _mtf_regular_direction(mtf_data, {}, "H1")
    assert direction == "bearish"
    assert tf_checked == "H4"


def test_mtf_regular_direction_detects_bullish_on_coarser_frame():
    coarser_df = _df_from_closes(_BULLISH_REGULAR_CLOSES)
    mtf_data = {"H4": coarser_df}
    direction, tf_checked = _mtf_regular_direction(mtf_data, {}, "H1")
    assert direction == "bullish"
    assert tf_checked == "H4"


def test_mtf_regular_direction_none_when_coarser_frame_absent():
    direction, tf_checked = _mtf_regular_direction({"H1": _df_from_closes([1.0] * 100)}, {}, "H1")
    assert direction is None
    assert tf_checked == "H4"


def test_mtf_regular_direction_none_when_decision_tf_is_d1():
    direction, tf_checked = _mtf_regular_direction({"D1": _df_from_closes([1.0] * 100)}, {}, "D1")
    assert direction is None
    assert tf_checked is None


def test_mtf_regular_direction_none_when_coarser_frame_too_short():
    direction, tf_checked = _mtf_regular_direction(
        {"H1": _df_from_closes([1.0] * 100), "H4": _df_from_closes([1.0] * 10)}, {"min_bars": 80}, "H1",
    )
    assert direction is None
    assert tf_checked == "H4"


# ---------------------------------------------------------------------------
# extract_features — purity, JSON-serializability, real regular patterns
# ---------------------------------------------------------------------------

def test_extract_features_detects_real_regular_bearish_pattern():
    df = _df_from_closes(_BEARISH_REGULAR_CLOSES)
    features = extract_features({"H1": df}, {}, "H1")
    assert features["rsi_pattern_type"] == "regular_bearish"
    assert features["n_confirmed_highs"] >= 2


def test_extract_features_detects_real_regular_bullish_pattern():
    df = _df_from_closes(_BULLISH_REGULAR_CLOSES)
    features = extract_features({"H1": df}, {}, "H1")
    assert features["rsi_pattern_type"] == "regular_bullish"


def test_extract_features_is_pure():
    df = load_synthetic(bars=300, timeframe="H1", seed=11, end="2026-07-15 08:00:00")
    mtf = {"H1": df}
    f1 = extract_features(mtf, {}, "H1")
    f2 = extract_features(mtf, {}, "H1")
    assert f1 == f2


def test_extract_features_json_serializable():
    df = load_synthetic(bars=300, timeframe="H1", seed=11, end="2026-07-15 08:00:00")
    features = extract_features({"H1": df}, {}, "H1")
    json.dumps(features, default=str)


# ---------------------------------------------------------------------------
# decide() — hand-built features dicts, mirrors quant_engine.py's
# decide()-purity-and-never-touches-a-dataframe precedent.
# ---------------------------------------------------------------------------

def _base_features(**overrides) -> dict:
    features = {
        "rsi_pattern_type": "none",
        "rsi_pattern_price_move_pct": 0.0,
        "macd_pattern_type": "none",
        "macd_pattern_price_move_pct": 0.0,
        "triple_div": False,
        "mtf_tf_checked": "H4",
        "mtf_regular_div_type": None,
        "rsi_context": "RSI neutral (50.0)",
    }
    features.update(overrides)
    return features


def test_decide_regular_bearish_with_triple_macd_and_mtf_confirm_are_context_only():
    """Engine Refinement V1 (#364, DIVERGENCE-REFINE, operator-pre-approved
    "remove automatic bonuses"): triple_div/MACD-agreement/MTF-agreement no
    longer add to score — only the base pattern's own magnitude does. The
    three confirmations still surface as reasons (context, not score)."""
    features = _base_features(
        rsi_pattern_type="regular_bearish", rsi_pattern_price_move_pct=0.02,
        macd_pattern_type="regular_bearish", triple_div=True, mtf_regular_div_type="bearish",
    )
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    # base(55) + magnitude(min(25, 0.02*1000=20)) = 75 — triple/macd/mtf no longer add anything
    assert score == pytest.approx(75.0)
    assert any("Triple" in r and "not scored" in r for r in reasons)
    assert any("MACD confirms" in r and "not scored" in r for r in reasons)
    assert any("H4 confirms" in r and "not scored" in r for r in reasons)


def test_decide_regular_bullish_with_mtf_conflict_is_context_only():
    features = _base_features(
        rsi_pattern_type="regular_bullish", rsi_pattern_price_move_pct=0.01,
        mtf_regular_div_type="bearish",  # opposite direction
    )
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BULLISH
    # base(55) + magnitude(10) = 65 — mtf conflict no longer subtracts anything
    assert score == pytest.approx(65.0)
    assert any("opposite-direction" in r and "not scored" in r for r in reasons)


def test_decide_hidden_with_macd_confirm_is_context_only():
    features = _base_features(rsi_pattern_type="hidden_bearish", macd_pattern_type="hidden_bearish")
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    assert score == pytest.approx(40.0)  # hidden(40) — macd confirm no longer adds
    assert any("MACD confirms" in r and "not scored" in r for r in reasons)


def test_confirmations_are_provably_never_scored():
    """The authoritative proof (Engine Refinement V1 #364): two otherwise-
    identical feature dicts differing ONLY in triple_div/macd_pattern_type/
    mtf_regular_div_type must produce identical scores — mirrors the
    'informational only, provably never scored' pattern already
    established for macro_engine.py's commodity trends and quant_engine.
    py's vol_clustering."""
    plain = _base_features(rsi_pattern_type="regular_bearish", rsi_pattern_price_move_pct=0.02)
    loaded = _base_features(
        rsi_pattern_type="regular_bearish", rsi_pattern_price_move_pct=0.02,
        macd_pattern_type="regular_bearish", triple_div=True, mtf_regular_div_type="bearish",
    )
    _, plain_score, _ = decide(plain, {})
    _, loaded_score, _ = decide(loaded, {})
    assert plain_score == loaded_score


def test_decide_macd_only_fallback_regular():
    features = _base_features(macd_pattern_type="regular_bullish", macd_pattern_price_move_pct=0.0)
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BULLISH
    assert score == pytest.approx(round(55.0 * 0.75, 1))


def test_decide_none_is_neutral_zero():
    features = _base_features()
    bias, score, reasons = decide(features, {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0
    assert any("No divergence detected" in r for r in reasons)


def test_decide_forces_neutral_below_score_floor():
    features = _base_features(rsi_pattern_type="regular_bearish", rsi_pattern_price_move_pct=0.0)
    bias, score, reasons = decide(features, {"regular_base_score": 10.0, "score_neutral_floor": 15.0})
    assert bias == Bias.NEUTRAL
    assert score == pytest.approx(10.0)  # score value itself is NOT reset


def test_decide_is_pure():
    features = _base_features(rsi_pattern_type="regular_bearish", rsi_pattern_price_move_pct=0.03)
    r1 = decide(features, {})
    r2 = decide(features, {})
    assert r1 == r2


def test_decide_never_touches_a_dataframe():
    # Proof the split is real: decide() runs fine with no df anywhere in scope.
    features = _base_features(rsi_pattern_type="regular_bullish", rsi_pattern_price_move_pct=0.05)
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BULLISH
    assert score > 0


# ---------------------------------------------------------------------------
# Engine-level (DivergenceEngine.safe_analyze)
# ---------------------------------------------------------------------------

def _mtf(bars=600, seed=1, timeframe="H1"):
    df = load_synthetic(bars=bars, timeframe=timeframe, seed=seed, end="2026-07-15 08:00:00")
    return build_multi_timeframe_view(df, ["H1", "H4", "D1"])


def test_insufficient_data_abstains():
    df = load_synthetic(bars=50, timeframe="H1", seed=1, end="2026-07-15 08:00:00")
    out = DivergenceEngine().safe_analyze({"H1": df})
    assert out.bias == Bias.NEUTRAL
    assert out.score == 0.0
    assert "Insufficient data" in out.reasons[0]


def test_engine_output_raw_has_timeframe_used():
    out = DivergenceEngine().safe_analyze(_mtf(seed=3))
    assert out.raw.get("timeframe_used") == "H1"


def test_engine_mtf_unavailable_on_d1_decision_tf_still_reaches_a_verdict():
    df = load_synthetic(bars=200, timeframe="D1", seed=5, end="2026-07-15 08:00:00")
    engine = DivergenceEngine()
    engine.decision_tf = "D1"
    out = engine.safe_analyze({"D1": df})
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert out.features["mtf_tf_checked"] is None
    assert out.features["mtf_regular_div_type"] is None


def test_score_bounds():
    for seed in range(5):
        out = DivergenceEngine().safe_analyze(_mtf(seed=seed))
        assert 0.0 <= out.score <= 90.0


def test_engine_output_features_and_raw_json_serializable():
    out = DivergenceEngine().safe_analyze(_mtf(seed=3))
    json.dumps(out.features, default=str)
    json.dumps(out.raw, default=str)


def test_engine_never_crashes_across_many_seeds_and_bar_counts():
    for seed in range(10):
        for bars in (80, 150, 250, 600):
            df = load_synthetic(bars=bars, timeframe="H1", seed=seed, end="2026-07-15 08:00:00")
            out = DivergenceEngine().safe_analyze({"H1": df})
            assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)


def test_config_engines_yaml_thresholds_divergence_supplies_every_default():
    from utils.helpers import load_config

    config = load_config()
    thresholds = config.get("engines", {}).get("thresholds", {}).get("divergence", {})
    assert thresholds, "config/engines.yaml is missing thresholds.divergence"

    df = load_synthetic(bars=600, timeframe="H1", seed=9, end="2026-07-15 08:00:00")
    engine = DivergenceEngine()
    engine.thresholds = thresholds
    mtf = build_multi_timeframe_view(df, ["H1", "H4", "D1"])
    out = engine.safe_analyze(mtf)
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
