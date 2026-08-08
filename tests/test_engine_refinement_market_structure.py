"""
tests/test_engine_refinement_market_structure.py
----------------------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — Market Structure
refinement (#363). Per the operator's directive table entry ("Market
Structure -> audit + fix if needed"): audited the module's own claim to
identify specific structural EVENTS (BOS/CHoCH/MSS) "more nuanced" than
plain SMC swing-pair voting, and found the claim didn't match the
implementation — last_event was assigned purely from a geometric
comparison of the last 2-3 swing-pivot VALUES, with no requirement that
price ever actually broke a level. This is a genuine SEMANTICS_FIX (not
purely observability, like ICT's #362) — MarketStructure is disabled by
default (config/engines.yaml engines.enabled.market_structure: false),
so it changes zero live-decision behavior. Pins:

1. _classify_structure() now requires a real close-beyond-level break
   (mirroring smc_engine.detect_bos_choch's already-established
   convention) before assigning last_event to BOS/CHoCH/MSS — a clean
   swing-value pattern with no break stays last_event="none" and falls
   back to weak_structure_strength, not bos_strength/choch_strength.
2. `trend` (the HH/HL/LH/LL swing-pattern classification) is UNCHANGED
   by the fix — it's a legitimate geometric fact that doesn't need a
   break to be true.
3. Break direction must match the event direction — a bearish CHoCH
   pattern with only a bullish break (or no break at all) does not fire.
4. broke_level/break_direction/break_price are always reported, even
   when no event fires (observability), and even when close_now is None
   (backward-compatible default — never fabricates a break).
5. extract_features()/decide() purity preserved.
6. raw gains additive h1_broke_level/h1_break_direction/h1_break_price/
   h4_* keys; every pre-existing flat key is preserved.
7. Golden-value regression: MarketStructure scenario A recaptured in
   tests/test_engine_config_extraction_no_behavior_change.py (this
   file's own responsibility is proving that new value is correct under
   the new logic, not re-deriving it).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.market_structure_engine import (
    MarketStructureEngine,
    _classify_structure,
    decide,
    extract_features,
)


def _ohlcv(n: int, seed: int = 7, start_price: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = start_price + np.cumsum(rng.normal(0, 0.3, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, close) + 0.3,
            "low": np.minimum(o, close) - 0.3,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


# ── _classify_structure(): the level-break requirement ─────────────────

def _clean_bullish_swings():
    """highs strictly rising, lows strictly rising -> bullish_structure
    True by the geometric HH/HL comparison, independent of any break."""
    highs = [(0, 100.0), (2, 101.0), (4, 102.0), (6, 103.0)]
    lows = [(1, 98.0), (3, 99.0), (5, 100.0), (7, 101.0)]
    return highs, lows


def test_bos_does_not_fire_without_a_real_break():
    highs, lows = _clean_bullish_swings()
    # close_now below the last swing high (103.0) -> no break.
    result = _classify_structure(highs, lows, close_now=102.5)
    assert result["trend"] == "bullish"
    assert result["last_event"] == "none"
    assert result["broke_level"] is False
    assert result["strength"] == 45  # weak_structure_strength default, not bos_strength (65)


def test_bos_fires_when_close_breaks_last_swing_high():
    highs, lows = _clean_bullish_swings()
    result = _classify_structure(highs, lows, close_now=103.5)
    assert result["trend"] == "bullish"
    assert result["last_event"] == "BOS"
    assert result["last_event_bias"] == "bullish"
    assert result["broke_level"] is True
    assert result["break_direction"] == "bullish"
    assert result["break_price"] == 103.0
    assert result["strength"] == 65  # bos_strength default


def test_close_now_none_never_produces_an_event():
    """Backward-compatible default: omitting close_now must never
    fabricate a break — last_event stays 'none' even for an otherwise-
    qualifying clean swing pattern."""
    highs, lows = _clean_bullish_swings()
    result = _classify_structure(highs, lows)  # close_now defaults to None
    assert result["last_event"] == "none"
    assert result["broke_level"] is False
    assert result["break_direction"] == "none"
    assert result["break_price"] is None


def test_trend_is_unaffected_by_the_break_requirement():
    """trend (HH/HL geometric classification) must be identical whether
    or not a break occurred — only last_event/strength are gated."""
    highs, lows = _clean_bullish_swings()
    no_break = _classify_structure(highs, lows, close_now=102.5)
    with_break = _classify_structure(highs, lows, close_now=103.5)
    assert no_break["trend"] == with_break["trend"] == "bullish"
    assert no_break["structure_hh"] == with_break["structure_hh"]
    assert no_break["structure_hl"] == with_break["structure_hl"]


def test_choch_requires_break_in_the_matching_direction():
    """Geometric CHoCH-bullish pattern (was making LH, now HH+HL) but
    the current close only breaks the swing high in isolation without
    qualifying as a real reversal break -> confirm event still requires
    broke_level True and break_direction == 'bullish'."""
    # highs: LH then HH (recent_h[-3] > recent_h[-2], recent_h[-1] > recent_h[-2])
    highs = [(0, 105.0), (2, 104.0), (4, 103.0), (6, 106.0)]
    # lows: HL too (so this would classify MSS, not CHoCH, if it fires)
    lows = [(1, 98.0), (3, 99.0), (5, 100.0), (7, 101.0)]
    # No break: close stays below the last swing high (106.0).
    no_break = _classify_structure(highs, lows, close_now=105.5)
    assert no_break["last_event"] == "none"
    # Real break above 106.0 confirms the event.
    confirmed = _classify_structure(highs, lows, close_now=106.5)
    assert confirmed["last_event"] in ("CHoCH", "MSS")
    assert confirmed["last_event_bias"] == "bullish"
    assert confirmed["broke_level"] is True
    assert confirmed["break_direction"] == "bullish"


def test_event_does_not_fire_when_break_direction_disagrees():
    """A bearish-shaped swing pattern with only a bullish break (price
    broke the recent high, not the recent low) must not report a
    bearish event — direction must match, not just 'some' break."""
    # lows: HL then LL (bearish CHoCH candidate: recent_l[-3] < recent_l[-2], recent_l[-1] < recent_l[-2])
    highs = [(0, 105.0), (2, 104.0), (4, 106.0), (6, 107.0)]  # LH pattern too, so no bullish event competes
    lows = [(1, 100.0), (3, 101.0), (5, 99.0), (7, 98.0)]
    # close breaks the swing HIGH (bullish direction), not the low.
    result = _classify_structure(highs, lows, close_now=107.5)
    assert result["break_direction"] == "bullish"
    assert result["last_event"] != "CHoCH" or result["last_event_bias"] != "bearish"


def test_broke_level_fields_always_present_even_when_no_event():
    highs, lows = _clean_bullish_swings()
    result = _classify_structure(highs, lows, close_now=102.5)
    assert set(result.keys()) >= {"broke_level", "break_direction", "break_price"}
    assert result["broke_level"] is False
    assert result["break_direction"] == "none"
    assert result["break_price"] is None


def test_insufficient_swings_returns_safe_defaults():
    result = _classify_structure([], [], close_now=100.0)
    assert result["trend"] == "ranging"
    assert result["last_event"] == "none"
    assert result["broke_level"] is False


# ── extract_features()/decide() purity ──────────────────────────────────

def _mtf(seed: int = 3, bars: int = 400):
    df = _ohlcv(bars, seed=seed)
    return df, df.iloc[::4].copy()  # a coarser "macro" frame, real subsample


def test_extract_features_is_pure():
    df_cur, df_macro = _mtf()
    t = {}
    f1 = extract_features(df_cur, df_macro, t)
    f2 = extract_features(df_cur, df_macro, t)
    assert f1 == f2


def test_decide_is_pure():
    df_cur, df_macro = _mtf()
    features = extract_features(df_cur, df_macro, {})
    r1 = decide(features, {})
    r2 = decide(features, {})
    assert r1 == r2


def test_extract_features_reports_break_fields_on_h1_and_h4_struct():
    df_cur, df_macro = _mtf()
    features = extract_features(df_cur, df_macro, {})
    for struct_key in ("h1_struct", "h4_struct"):
        struct = features[struct_key]
        assert "broke_level" in struct
        assert "break_direction" in struct
        assert "break_price" in struct


# ── engine-level: analyze() ──────────────────────────────────────────────

def test_engine_output_well_formed():
    df_cur, df_macro = _mtf(seed=11, bars=600)
    mtf = {"H1": df_cur, "H4": df_macro}
    eng = MarketStructureEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(mtf)
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert isinstance(out.score, float)
    assert 0.0 <= out.score <= 85.0  # aligned_score_cap default


def test_raw_gains_break_observability_keys_and_keeps_flat_keys():
    df_cur, df_macro = _mtf(seed=5, bars=600)
    mtf = {"H1": df_cur, "H4": df_macro}
    eng = MarketStructureEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(mtf)
    for key in (
        "timeframe_h1", "timeframe_h4", "h1_trend", "h4_trend", "h1_event",
        "h1_event_direction", "h1_strength", "h4_strength", "aligned",
        "h1_structure_hh", "h1_structure_hl", "h1_structure_lh", "h1_structure_ll",
        "last_h1_high", "last_h1_low", "last_high_bar_age", "last_low_bar_age",
        # Engine Refinement V1 (#369, OBSERVABILITY): H4's own event was
        # computed identically to h1's but never surfaced before.
        "h4_event", "h4_event_direction",
    ):
        assert key in out.raw
    for key in ("h1_broke_level", "h1_break_direction", "h1_break_price",
                "h4_broke_level", "h4_break_direction", "h4_break_price"):
        assert key in out.raw


def test_h4_event_matches_h4_structs_own_last_event():
    """raw['h4_event']/'h4_event_direction' must be the SAME value
    _classify_structure() computed for h4_struct, not a re-derived or
    stale copy."""
    df_cur, df_macro = _mtf(seed=17, bars=600)
    mtf = {"H1": df_cur, "H4": df_macro}
    eng = MarketStructureEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(mtf)
    assert out.raw["h4_event"] == out.features["h4_struct"]["last_event"]
    assert out.raw["h4_event_direction"] == out.features["h4_struct"]["last_event_bias"]


def test_h4_event_observability_is_provably_never_scored():
    """Engine Refinement V1 (#369) load-bearing guarantee: h4_event/
    h4_event_direction are observability-only. decide() must produce a
    byte-identical bias/score/reasons regardless of what h4_struct's
    last_event/last_event_bias contain -- proving the new raw keys can
    never silently influence the verdict (decide() only ever reads
    h4_struct's trend/strength, confirmed by direct code read)."""
    df_cur, df_macro = _mtf(seed=5, bars=600)
    features = extract_features(df_cur, df_macro, {})
    base_h4 = dict(features["h4_struct"])
    variants = [
        {**base_h4, "last_event": "none", "last_event_bias": "none"},
        {**base_h4, "last_event": "BOS", "last_event_bias": "bullish"},
        {**base_h4, "last_event": "CHoCH", "last_event_bias": "bearish"},
        {**base_h4, "last_event": "MSS", "last_event_bias": "bullish"},
    ]
    results = []
    for h4_variant in variants:
        varied_features = {**features, "h4_struct": h4_variant}
        results.append(decide(varied_features, {}))
    first = results[0]
    for bias, score, reasons in results[1:]:
        assert bias == first[0]
        assert score == first[1]
        assert reasons == first[2]


def test_engine_output_features_json_serializable():
    df_cur, df_macro = _mtf(seed=9, bars=600)
    mtf = {"H1": df_cur, "H4": df_macro}
    eng = MarketStructureEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(mtf)
    json.dumps(out.features, default=str)  # must not raise


def test_insufficient_data_abstains_cleanly():
    df_cur = _ohlcv(10, seed=1)  # below min_bars default (30)
    mtf = {"H1": df_cur, "H4": df_cur}
    eng = MarketStructureEngine()
    eng.decision_tf = "H1"
    out = eng.analyze(mtf)
    assert out.bias == Bias.NEUTRAL
    assert out.score == 0.0
