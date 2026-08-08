"""
tests/test_quant_engine_v2.py
---------------------------------
Confluence Engine Overhaul Phase 3a — engine-level tests for
QuantEngine's regime-aware rebuild. This engine is DISABLED
(config/engines.yaml engines.enabled.quant: false); correctness here
means sound statistics and honest abstention, not golden bias/score
values (v1 is fully replaced, not refactored).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias
from engines.quant_engine import QuantEngine, _classify_regime, decide, extract_features


# ---------------------------------------------------------------------------
# _classify_regime unit tests — hand-built vote-count inputs, no real
# statistics computed at all (mirrors
# test_smc_decide_structural_bias_never_touches_a_dataframe's precedent).
# ---------------------------------------------------------------------------

def test_classify_regime_clean_trending_vote():
    # Trending voters: hurst(0.7>0.55), VR(1.5>1.10), autocorr(0.3>0.10),
    # er(0.5>0.30) = 4 votes. adf/half_life never vote trending (one-sided).
    regime, votes, confidence = _classify_regime(
        hurst=0.7, variance_ratio_val=1.5, adf_p=0.5,
        autocorr_val=0.3, er=0.5, hl=None, entropy_val=0.3, t={},
    )
    assert regime == "TRENDING"
    assert votes["trending"] == 4
    assert confidence == pytest.approx(1.0)


def test_classify_regime_clean_mean_reverting_vote():
    # MR voters: hurst(0.3<0.45), VR(0.5<0.90), adf(0.01<0.05),
    # autocorr(-0.3<-0.10), er(0.05<0.15), half_life(5.0<=20) = 6 votes.
    regime, votes, confidence = _classify_regime(
        hurst=0.3, variance_ratio_val=0.5, adf_p=0.01,
        autocorr_val=-0.3, er=0.05, hl=5.0, entropy_val=0.3, t={},
    )
    assert regime == "MEAN_REVERTING"
    assert votes["mean_reverting"] == 6
    assert confidence == pytest.approx(1.0)


def test_classify_regime_all_abstain_is_unknown():
    """Engine Refinement V1 (#365, QUANT-REFINE): zero votes cast is a
    data-insufficiency problem (UNKNOWN), not a statistical finding that
    the market is random (RANDOM) — these were conflated before this fix."""
    regime, votes, confidence = _classify_regime(
        hurst=None, variance_ratio_val=None, adf_p=None,
        autocorr_val=None, er=None, hl=None, entropy_val=None, t={},
    )
    assert regime == "UNKNOWN"
    assert confidence == 0.0
    assert votes["abstain"] == 7


def test_classify_regime_narrow_margin_tie_is_random():
    # 1 trending vote (hurst) vs 1 mean-reverting vote (adf) -> margin
    # of 1 not exceeded -> RANDOM, not a coin-flip classification.
    regime, votes, _confidence = _classify_regime(
        hurst=0.7, variance_ratio_val=None, adf_p=0.01,
        autocorr_val=None, er=None, hl=None, entropy_val=0.3, t={},
    )
    assert regime == "RANDOM"


def test_classify_regime_entropy_dominant_is_random_despite_directional_votes():
    regime, votes, _confidence = _classify_regime(
        hurst=0.7, variance_ratio_val=1.5, adf_p=0.5,
        autocorr_val=0.3, er=0.5, hl=None, entropy_val=0.95, t={},
    )
    assert votes["trending"] == 4
    assert votes["random"] == 1
    # trending(4) > mean_reverting(0)+margin(1) and trending(4) > random(1) -> TRENDING wins
    # (entropy only overrides when it wins the majority, not merely when it casts a vote)
    assert regime == "TRENDING"


def test_classify_regime_insufficient_evidence_is_unknown():
    # Engine Refinement V1 (#365): only 1 vote cast total (below
    # regime_min_votes=2 default) -> UNKNOWN (data insufficiency), not
    # RANDOM (a real statistical finding that needs enough votes cast).
    regime, votes, confidence = _classify_regime(
        hurst=0.7, variance_ratio_val=None, adf_p=None,
        autocorr_val=None, er=None, hl=None, entropy_val=None, t={},
    )
    assert regime == "UNKNOWN"
    assert confidence == 0.0


# ---------------------------------------------------------------------------
# decide() purity — never touches a DataFrame
# ---------------------------------------------------------------------------

def test_decide_never_touches_a_dataframe():
    features = {
        "regime": "MEAN_REVERTING",
        "regime_votes": {"trending": 0, "mean_reverting": 4, "random": 0, "abstain": 3},
        "regime_confidence": 1.0,
        "vol_regime": "NORMAL",
        "atr_percentile": 0.5,
        "zscore": -2.5,
        "rsi": 25.0,
        "half_life_bars": 5.0,
        "vol_clustering": 0.1,
    }
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BULLISH
    assert score > 0
    assert len(reasons) > 0


def test_decide_is_pure():
    features = {
        "regime": "TRENDING",
        "regime_votes": {"trending": 3, "mean_reverting": 0, "random": 0, "abstain": 4},
        "regime_confidence": 1.0,
        "vol_regime": "NORMAL",
        "atr_percentile": 0.5,
        "zscore": None,
        "rsi": 60.0,
        "half_life_bars": None,
        "vol_clustering": None,
        "trend_direction": "up",
        "efficiency_ratio": 0.4,
    }
    r1 = decide(features, {})
    r2 = decide(features, {})
    assert r1 == r2


def test_decide_random_regime_always_neutral():
    features = {
        "regime": "RANDOM",
        "regime_votes": {"trending": 0, "mean_reverting": 0, "random": 0, "abstain": 7},
        "regime_confidence": 0.0,
        "vol_regime": "NORMAL",
        "atr_percentile": 0.5,
        "zscore": -3.0,  # even an extreme z-score must not drive a decision here
        "rsi": 10.0,
        "half_life_bars": None,
        "vol_clustering": None,
    }
    bias, score, reasons = decide(features, {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0
    assert any("no-guess policy" in r for r in reasons)
    assert any("No statistically significant regime detected" in r for r in reasons)


def test_decide_unknown_regime_always_neutral_with_distinct_reason():
    """Engine Refinement V1 (#365): UNKNOWN abstains identically to RANDOM
    (NEUTRAL/0.0) but with a distinct, data-insufficiency-specific reason —
    proving the fix changed only the reported classification/reasoning,
    never the live decision."""
    features = {
        "regime": "UNKNOWN",
        "regime_votes": {"trending": 0, "mean_reverting": 0, "random": 0, "abstain": 7},
        "regime_confidence": 0.0,
        "vol_regime": "NORMAL",
        "atr_percentile": 0.5,
        "zscore": -3.0,
        "rsi": 10.0,
        "half_life_bars": None,
        "vol_clustering": None,
    }
    bias, score, reasons = decide(features, {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0
    assert any("no-guess policy" in r for r in reasons)
    assert any("Too few diagnostics could vote" in r for r in reasons)
    assert not any("No statistically significant regime detected" in r for r in reasons)


# ---------------------------------------------------------------------------
# extract_features() purity
# ---------------------------------------------------------------------------

def test_extract_features_is_pure():
    df = load_synthetic(bars=300, timeframe="H1", seed=11)
    f1 = extract_features(df, {}, "H1")
    f2 = extract_features(df, {}, "H1")
    assert f1 == f2


def test_extract_features_json_serializable():
    df = load_synthetic(bars=300, timeframe="H1", seed=11)
    features = extract_features(df, {}, "H1")
    json.dumps(features, default=str)


def test_extract_features_default_symbol_preserves_prior_behavior():
    """extract_features()'s 4th positional/keyword `symbol` param must be
    fully backward-compatible: every pre-existing 3-arg call keeps the
    exact prior 365-day realized-vol annualization."""
    df = load_synthetic(bars=300, timeframe="H1", seed=11)
    f_no_symbol = extract_features(df, {}, "H1")
    f_empty_symbol = extract_features(df, {}, "H1", "")
    assert f_no_symbol["bars_per_year_used"] == f_empty_symbol["bars_per_year_used"] == pytest.approx(8760.0)
    assert f_no_symbol["realized_vol_annualized"] == pytest.approx(f_empty_symbol["realized_vol_annualized"])


def test_extract_features_fx_symbol_corrects_annualization_but_not_decision():
    """A real FX/metals symbol gets the corrected 261-trading-day
    annualization for realized_vol_annualized/bars_per_year_used, but
    decide() never reads either field (grep-confirmed) -- so bias/score
    must be byte-identical to the same run with no symbol context."""
    df = load_synthetic(bars=300, timeframe="H1", seed=11)
    f_no_symbol = extract_features(df, {}, "H1")
    f_fx = extract_features(df, {}, "H1", "EURUSD")

    assert f_fx["bars_per_year_used"] == pytest.approx((261.0 * 24 * 60) / 60)
    assert f_no_symbol["bars_per_year_used"] != f_fx["bars_per_year_used"]
    assert f_fx["realized_vol_annualized"] != f_no_symbol["realized_vol_annualized"]

    bias_a, score_a, _ = decide(f_no_symbol, {})
    bias_b, score_b, _ = decide(f_fx, {})
    assert bias_a == bias_b
    assert score_a == pytest.approx(score_b)


def test_analyze_threads_self_symbol_into_extract_features():
    """QuantEngine.analyze() must pass self._symbol through (same
    getattr(self, "_symbol", "") pattern as BUG-008's MacroEngine fix) so
    a real engine built with a symbol attached gets the corrected FX
    annualization automatically, with zero decision-output change."""
    df = load_synthetic(bars=600, timeframe="H1", seed=3)
    mtf = build_multi_timeframe_view(df, ["H1", "H4", "D1"])

    engine_no_symbol = QuantEngine()
    engine_fx = QuantEngine()
    engine_fx._symbol = "EURUSD"

    out_no_symbol = engine_no_symbol.safe_analyze(mtf)
    out_fx = engine_fx.safe_analyze(mtf)

    assert out_no_symbol.bias == out_fx.bias
    assert out_no_symbol.score == pytest.approx(out_fx.score)
    assert out_no_symbol.raw["bars_per_year_used"] != out_fx.raw["bars_per_year_used"]
    assert out_fx.raw["bars_per_year_used"] == pytest.approx((261.0 * 24 * 60) / 60)


# ---------------------------------------------------------------------------
# Engine-level (QuantEngine.safe_analyze)
# ---------------------------------------------------------------------------

def _mtf(bars=600, seed=1, timeframe="H1"):
    df = load_synthetic(bars=bars, timeframe=timeframe, seed=seed)
    return build_multi_timeframe_view(df, ["H1", "H4", "D1"])


def test_insufficient_data_abstains():
    df = load_synthetic(bars=50, timeframe="H1", seed=1)
    out = QuantEngine().safe_analyze({"H1": df})
    assert out.bias == Bias.NEUTRAL
    assert out.score == 0.0
    assert "Insufficient data" in out.reasons[0]


def test_degeneracy_case_150_bars_hurst_none_but_engine_still_opines():
    """At 150 bars, Hurst's default lags [10,20,40,80,160] only leave
    {10,20,40} with >=2 full chunks (149 return-observations // 80 = 1
    chunk, // 160 = 0 chunks) -- 3 valid lags, below hurst_min_lags=4 --
    so Hurst legitimately returns None. Variance ratio, by contrast,
    degrades gracefully to whatever history .tail() actually returns
    (150 bars is already >> its own q*2+1=21 floor), so it stays a real
    number even though the requested variance_ratio_lookback(200)
    exceeds what's available -- proving those two features have
    genuinely different degradation behavior, not the same "not enough
    for the lookback -> None" rule. Either way, the engine must still be
    ABLE to reach a non-insufficient-data verdict from the remaining
    voters when one feature is None, not force full abstention."""
    df = load_synthetic(bars=150, timeframe="H1", seed=7)
    features = extract_features(df, {}, "H1")
    assert features["hurst"] is None
    assert features["variance_ratio"] is not None
    assert features["adf_pvalue"] is not None
    assert features["efficiency_ratio"] is not None
    # decide() must run without error on this partially-None feature set
    out = QuantEngine().safe_analyze({"H1": df})
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)


def test_score_bounds():
    for seed in range(5):
        out = QuantEngine().safe_analyze(_mtf(seed=seed))
        assert 0.0 <= out.score <= 70.0


def test_engine_output_features_and_raw_json_serializable():
    out = QuantEngine().safe_analyze(_mtf(seed=3))
    json.dumps(out.features, default=str)
    json.dumps(out.raw, default=str)


def test_engine_output_evidence_level_and_probability_defaults():
    out = QuantEngine().safe_analyze(_mtf(seed=3))
    assert out.evidence_level == "HEURISTIC"
    assert out.probability is None
    assert out.confidence_interval is None
    assert out.expected_return is None
    assert out.expected_drawdown is None
    assert out.sample_size is None


def test_engine_output_has_regime_and_votes_in_features():
    out = QuantEngine().safe_analyze(_mtf(seed=3))
    assert out.features["regime"] in ("TRENDING", "MEAN_REVERTING", "RANDOM", "UNKNOWN")
    assert "regime_votes" in out.features
    assert set(out.features["regime_votes"].keys()) == {"trending", "mean_reverting", "random", "abstain"}


def test_engine_output_raw_mirrors_features():
    out = QuantEngine().safe_analyze(_mtf(seed=3))
    assert out.raw["regime"] == out.features["regime"]
    assert out.raw["timeframe_used"] == "H1"


def test_engine_never_crashes_across_many_seeds_and_bar_counts():
    for seed in range(10):
        for bars in (100, 150, 250, 600):
            df = load_synthetic(bars=bars, timeframe="H1", seed=seed)
            out = QuantEngine().safe_analyze({"H1": df})
            assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)


def test_config_engines_yaml_thresholds_quant_supplies_every_default():
    from utils.helpers import load_config

    config = load_config()
    quant_thresholds = config.get("engines", {}).get("thresholds", {}).get("quant", {})
    assert quant_thresholds, "config/engines.yaml is missing thresholds.quant"

    df = load_synthetic(bars=600, timeframe="H1", seed=9)
    engine = QuantEngine()
    engine.thresholds = quant_thresholds
    out = engine.safe_analyze({"H1": df, "H4": df, "D1": df})
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
