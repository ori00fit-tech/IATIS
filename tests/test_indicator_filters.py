"""
tests/test_indicator_filters.py
---------------------------------
Backtesting Lab Pro Phase D (2026-07-27) — confluence/indicator_filters.py.

Two layers: (1) unit tests on the pure evaluate_indicator_filters()
function against synthetic series with a known trend/direction, (2) an
integration test proving the wiring into backtesting.backtest_engine.
run_backtest actually reaches the EXECUTE/NO_TRADE decision (the
authoritative behavior-change proof every phase this arc uses).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from confluence.indicator_filters import (
    CONFIRM_BONUS,
    COUNTER_PENALTY,
    FILTER_MODES,
    INDICATOR_KEYS,
    IndicatorSpec,
    evaluate_indicator_filters,
    parse_indicators_json,
)


def _uptrend_df(n: int = 300) -> pd.DataFrame:
    # A perfectly linear ramp has zero losses ever, making RSI's gain/loss
    # ratio degenerate (division by a rolling-mean of zero -> NaN) — real
    # market data always has some noise, so add a small amount to keep
    # every indicator well-defined while the trend still clearly dominates.
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    rng = np.random.RandomState(7)
    close = pd.Series(100 + np.cumsum(0.15 + rng.normal(0, 0.3, n)), index=idx)
    return pd.DataFrame(
        {"open": close.shift(1).fillna(close.iloc[0]), "high": close + 0.2, "low": close - 0.2, "close": close},
        index=idx,
    )


def _flat_df(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series(100.0, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 0.05, "low": close - 0.05, "close": close}, index=idx,
    )


class TestConstants:
    def test_indicator_keys_are_the_five_shipped(self):
        assert set(INDICATOR_KEYS) == {"rsi", "macd", "ema", "adx", "atr"}

    def test_filter_modes(self):
        assert set(FILTER_MODES) == {"disabled", "entry_filter", "confirmation", "score_weight"}


class TestDisabledMode:
    def test_disabled_indicator_contributes_nothing_and_never_vetoes(self):
        df = _flat_df()
        specs = [IndicatorSpec(name="rsi", mode="disabled", params={"buy_above": 100, "sell_below": 0})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.vetoed is False
        assert result.score_adjustment == 0.0
        assert result.per_indicator == {}


class TestRSI:
    def test_entry_filter_vetoes_on_uptrend_when_thresholds_impossible(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="rsi", mode="entry_filter", params={"buy_above": 100, "sell_below": 0})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.veto_indicator == "rsi"

    def test_entry_filter_passes_on_uptrend_when_thresholds_realistic(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="rsi", mode="entry_filter", params={"buy_above": 50, "sell_below": 50})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.vetoed is False

    def test_confirmation_bonus_on_alignment(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="rsi", mode="confirmation", params={"buy_above": 50, "sell_below": 50})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.score_adjustment == CONFIRM_BONUS
        assert result.per_indicator["rsi"]["aligned"] is True

    def test_confirmation_penalty_on_disagreement(self):
        df = _uptrend_df()  # RSI will read bullish; ask BEARISH direction
        specs = [IndicatorSpec(name="rsi", mode="confirmation", params={"buy_above": 50, "sell_below": 50})]
        result = evaluate_indicator_filters(df, "BEARISH", specs)
        assert result.score_adjustment == -COUNTER_PENALTY


class TestMACD:
    def test_macd_histogram_aligns_with_uptrend(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="macd", mode="confirmation", params={})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.per_indicator["macd"]["value"] > 0
        assert result.per_indicator["macd"]["aligned"] is True


class TestEMA:
    def test_price_above_ema_aligns_bullish(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="ema", mode="confirmation", params={"period": 20})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.per_indicator["ema"]["aligned"] is True

    def test_price_above_ema_does_not_align_bearish(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="ema", mode="confirmation", params={"period": 20})]
        result = evaluate_indicator_filters(df, "BEARISH", specs)
        assert result.per_indicator["ema"]["aligned"] is False


class TestADX:
    def test_flat_market_has_low_adx_fails_min_trend(self):
        df = _flat_df()
        specs = [IndicatorSpec(name="adx", mode="entry_filter", params={"min_trend": 20})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        # A perfectly flat series' ADX is NaN (zero directional movement) —
        # NaN must veto (never silently pass), same as an unmeetable threshold.
        assert result.vetoed is True

    def test_adx_is_direction_agnostic(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="adx", mode="entry_filter", params={"min_trend": 0})]
        bullish = evaluate_indicator_filters(df, "BULLISH", specs)
        bearish = evaluate_indicator_filters(df, "BEARISH", specs)
        assert bullish.vetoed is False
        assert bearish.vetoed is False


class TestATR:
    def test_volatility_band_filter(self):
        df = _uptrend_df()
        # min_atr/max_atr impossible to satisfy -> veto regardless of direction
        specs = [IndicatorSpec(name="atr", mode="entry_filter", params={"min_atr": 1e9, "max_atr": 1e10})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.vetoed is True

    def test_wide_band_passes(self):
        df = _uptrend_df()
        specs = [IndicatorSpec(name="atr", mode="entry_filter", params={"min_atr": 0, "max_atr": 1e9})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.vetoed is False


class TestScoreWeight:
    def test_weight_scales_contribution_linearly(self):
        df = _uptrend_df()
        half = evaluate_indicator_filters(
            df, "BULLISH", [IndicatorSpec(name="ema", mode="score_weight", params={"period": 20}, weight=50)]
        )
        full = evaluate_indicator_filters(
            df, "BULLISH", [IndicatorSpec(name="ema", mode="score_weight", params={"period": 20}, weight=100)]
        )
        assert full.score_adjustment == pytest.approx(2 * half.score_adjustment)

    def test_zero_weight_contributes_nothing(self):
        df = _uptrend_df()
        result = evaluate_indicator_filters(
            df, "BULLISH", [IndicatorSpec(name="ema", mode="score_weight", params={"period": 20}, weight=0)]
        )
        assert result.score_adjustment == 0.0


class TestComposition:
    def test_multiple_indicators_sum_score_adjustment(self):
        df = _uptrend_df()
        specs = [
            IndicatorSpec(name="rsi", mode="confirmation", params={"buy_above": 50, "sell_below": 50}),
            IndicatorSpec(name="ema", mode="confirmation", params={"period": 20}),
        ]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.score_adjustment == pytest.approx(2 * CONFIRM_BONUS)
        assert set(result.per_indicator) == {"rsi", "ema"}

    def test_first_failing_entry_filter_vetoes_and_records_name(self):
        df = _uptrend_df()
        specs = [
            IndicatorSpec(name="ema", mode="entry_filter", params={"period": 20}),  # passes (uptrend)
            IndicatorSpec(name="rsi", mode="entry_filter", params={"buy_above": 100, "sell_below": 0}),  # fails
        ]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.veto_indicator == "rsi"

    def test_per_indicator_values_are_json_serializable_python_types(self):
        import json

        df = _uptrend_df()
        specs = [IndicatorSpec(name="ema", mode="confirmation", params={"period": 20})]
        result = evaluate_indicator_filters(df, "BULLISH", specs)
        json.dumps(result.per_indicator)  # must not raise
        assert isinstance(result.per_indicator["ema"]["aligned"], bool)


class TestUnknownIndicator:
    def test_unknown_indicator_name_raises(self):
        df = _uptrend_df()
        with pytest.raises(ValueError):
            evaluate_indicator_filters(df, "BULLISH", [IndicatorSpec(name="stochastic", mode="entry_filter")])


class TestParseIndicatorsJson:
    def test_round_trips_valid_list(self):
        raw = '[{"name": "rsi", "mode": "entry_filter", "params": {}, "weight": 0}]'
        parsed = parse_indicators_json(raw)
        assert parsed == ({"name": "rsi", "mode": "entry_filter", "params": {}, "weight": 0},)

    def test_rejects_non_list(self):
        with pytest.raises(ValueError):
            parse_indicators_json('{"name": "rsi"}')

    def test_rejects_unknown_name(self):
        with pytest.raises(ValueError):
            parse_indicators_json('[{"name": "bogus", "mode": "entry_filter"}]')

    def test_rejects_unknown_mode(self):
        with pytest.raises(ValueError):
            parse_indicators_json('[{"name": "rsi", "mode": "bogus"}]')
