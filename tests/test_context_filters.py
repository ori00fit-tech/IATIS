"""
tests/test_context_filters.py
---------------------------------
AI Research Lab / Mission Center — Context Filters (2026-07-30) —
confluence/context_filters.py.

Mirrors tests/test_indicator_filters.py's structure: pure unit tests on
evaluate_context_filters() against synthetic data with known session/
day-of-week/volatility/regime/direction properties.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from confluence.context_filters import (
    CONTEXT_KEYS,
    ContextSpec,
    evaluate_context_filters,
    parse_context_filters_json,
)
from confluence.indicator_filters import CONFIRM_BONUS, COUNTER_PENALTY, FILTER_MODES
from regimes.regime_detector import detect_regime
from regimes.session_context import detect_session_from_df
from regimes.volatility_classifier import classify_volatility


def _uptrend_df(n: int = 300, start: str = "2024-01-01", freq: str = "4h") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    rng = np.random.RandomState(7)
    close = pd.Series(100 + np.cumsum(0.15 + rng.normal(0, 0.3, n)), index=idx)
    return pd.DataFrame(
        {"open": close.shift(1).fillna(close.iloc[0]), "high": close + 0.2, "low": close - 0.2, "close": close},
        index=idx,
    )


def _flat_df(n: int = 300, start: str = "2024-01-01", freq: str = "4h") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = pd.Series(100.0, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 0.05, "low": close - 0.05, "close": close}, index=idx,
    )


def _tiny_df(n: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series(100.0, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 0.05, "low": close - 0.05, "close": close}, index=idx,
    )


class TestConstants:
    def test_context_keys_are_the_five_shipped(self):
        assert set(CONTEXT_KEYS) == {"session", "day_of_week", "volatility_regime", "market_regime", "direction"}

    def test_reuses_indicator_filters_filter_modes(self):
        assert set(FILTER_MODES) == {"disabled", "entry_filter", "confirmation", "score_weight"}


class TestDisabledMode:
    def test_disabled_context_contributes_nothing_and_never_vetoes(self):
        df = _uptrend_df()
        specs = [ContextSpec(name="session", mode="disabled", params={"allowed": []})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False
        assert result.score_adjustment == 0.0
        assert result.per_context == {}


class TestSession:
    def test_default_allowed_is_a_noop_and_always_aligns(self):
        # 13:00 UTC on a real bar -> primary_session is deterministic
        idx = pd.date_range("2024-01-01 13:00", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0]}, index=idx)
        specs = [ContextSpec(name="session", mode="entry_filter", params={})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False

    def test_allowed_matching_primary_session_passes(self):
        idx = pd.date_range("2024-01-01 13:00", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0]}, index=idx)
        ctx = detect_session_from_df(df)
        specs = [ContextSpec(name="session", mode="entry_filter", params={"allowed": [ctx.primary_session]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False

    def test_allowed_excluding_current_session_vetoes(self):
        idx = pd.date_range("2024-01-01 13:00", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0]}, index=idx)
        ctx = detect_session_from_df(df)
        other = [s for s in ("Asia", "London", "NewYork", "Overlap")
                 if s != ctx.primary_session and s not in ctx.active_sessions]
        assert other, "expected at least one session excluded from the active set"
        specs = [ContextSpec(name="session", mode="entry_filter", params={"allowed": [other[0]]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.veto_context == "session"

    def test_empty_window_returns_no_value_and_vetoes(self):
        df = pd.DataFrame({"open": [], "high": [], "low": [], "close": []})
        specs = [ContextSpec(name="session", mode="entry_filter", params={})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.per_context["session"]["value"] is None


class TestDayOfWeek:
    def test_default_allowed_days_are_monday_through_friday(self):
        # 2024-01-06 is a Saturday (dayofweek=5)
        idx = pd.date_range("2024-01-06 10:00", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0]}, index=idx)
        specs = [ContextSpec(name="day_of_week", mode="entry_filter", params={})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.per_context["day_of_week"]["value"] == 5

    def test_explicit_allowed_days_can_include_weekend(self):
        idx = pd.date_range("2024-01-06 10:00", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0]}, index=idx)
        specs = [ContextSpec(name="day_of_week", mode="entry_filter", params={"allowed_days": [5, 6]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False

    def test_weekday_passes_default(self):
        # 2024-01-01 is a Monday (dayofweek=0)
        idx = pd.date_range("2024-01-01 10:00", periods=1, freq="4h", tz="UTC")
        df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.0]}, index=idx)
        specs = [ContextSpec(name="day_of_week", mode="entry_filter", params={})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False


class TestVolatilityRegime:
    def test_insufficient_bars_returns_none_and_vetoes(self):
        df = _tiny_df(n=3)
        specs = [ContextSpec(name="volatility_regime", mode="entry_filter", params={"period": 14})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.per_context["volatility_regime"]["value"] is None

    def test_allowed_matching_actual_label_passes(self):
        df = _uptrend_df()
        actual = classify_volatility(df, period=14, lookback=100).iloc[-1]
        assert actual != "unknown"
        specs = [ContextSpec(name="volatility_regime", mode="entry_filter", params={"allowed": [actual]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False

    def test_allowed_excluding_actual_label_vetoes(self):
        df = _uptrend_df()
        actual = classify_volatility(df, period=14, lookback=100).iloc[-1]
        others = [lbl for lbl in ("low", "normal", "high", "extreme") if lbl != actual]
        specs = [ContextSpec(name="volatility_regime", mode="entry_filter", params={"allowed": others})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.veto_context == "volatility_regime"


class TestMarketRegime:
    def test_insufficient_bars_returns_none_and_vetoes(self):
        df = _tiny_df(n=3)
        specs = [ContextSpec(name="market_regime", mode="entry_filter", params={})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.per_context["market_regime"]["value"] is None

    def test_uptrend_classified_trending_and_allowed_passes(self):
        df = _uptrend_df()
        regime_result = detect_regime(df)
        assert regime_result.regime.value == "TRENDING"
        specs = [ContextSpec(name="market_regime", mode="entry_filter", params={"allowed": ["TRENDING"]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False

    def test_flat_market_classified_ranging_and_disallowed_vetoes(self):
        df = _flat_df()
        regime_result = detect_regime(df)
        assert regime_result.regime.value == "RANGING"
        specs = [ContextSpec(name="market_regime", mode="entry_filter", params={"allowed": ["TRENDING"]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.veto_context == "market_regime"


class TestDirection:
    def test_allowed_matching_direction_passes(self):
        df = _uptrend_df()
        specs = [ContextSpec(name="direction", mode="entry_filter", params={"allowed": ["BULLISH"]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is False

    def test_allowed_excluding_direction_vetoes(self):
        df = _uptrend_df()
        specs = [ContextSpec(name="direction", mode="entry_filter", params={"allowed": ["BEARISH"]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.veto_context == "direction"

    def test_confirmation_bonus_on_alignment(self):
        df = _uptrend_df()
        specs = [ContextSpec(name="direction", mode="confirmation", params={"allowed": ["BULLISH"]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.score_adjustment == CONFIRM_BONUS

    def test_confirmation_penalty_on_disagreement(self):
        df = _uptrend_df()
        specs = [ContextSpec(name="direction", mode="confirmation", params={"allowed": ["BEARISH"]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.score_adjustment == -COUNTER_PENALTY


class TestScoreWeight:
    def test_weight_scales_contribution_linearly(self):
        df = _uptrend_df()
        half = evaluate_context_filters(
            df, "BULLISH", [ContextSpec(name="direction", mode="score_weight", params={"allowed": ["BULLISH"]}, weight=50)]
        )
        full = evaluate_context_filters(
            df, "BULLISH", [ContextSpec(name="direction", mode="score_weight", params={"allowed": ["BULLISH"]}, weight=100)]
        )
        assert full.score_adjustment == pytest.approx(2 * half.score_adjustment)

    def test_zero_weight_contributes_nothing(self):
        df = _uptrend_df()
        result = evaluate_context_filters(
            df, "BULLISH", [ContextSpec(name="direction", mode="score_weight", params={"allowed": ["BULLISH"]}, weight=0)]
        )
        assert result.score_adjustment == 0.0


class TestComposition:
    def test_multiple_context_filters_sum_score_adjustment(self):
        df = _uptrend_df()
        specs = [
            ContextSpec(name="direction", mode="confirmation", params={"allowed": ["BULLISH"]}),
            ContextSpec(name="day_of_week", mode="confirmation", params={}),  # 2024-01-01 is Monday
        ]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.score_adjustment == pytest.approx(2 * CONFIRM_BONUS)
        assert set(result.per_context) == {"direction", "day_of_week"}

    def test_first_failing_entry_filter_vetoes_and_records_name(self):
        df = _uptrend_df()
        specs = [
            ContextSpec(name="direction", mode="entry_filter", params={"allowed": ["BULLISH"]}),  # passes
            ContextSpec(name="day_of_week", mode="entry_filter", params={"allowed_days": []}),  # fails
        ]
        result = evaluate_context_filters(df, "BULLISH", specs)
        assert result.vetoed is True
        assert result.veto_context == "day_of_week"

    def test_per_context_values_are_json_serializable_python_types(self):
        import json

        df = _uptrend_df()
        specs = [ContextSpec(name="direction", mode="confirmation", params={"allowed": ["BULLISH"]})]
        result = evaluate_context_filters(df, "BULLISH", specs)
        json.dumps(result.per_context)  # must not raise
        assert isinstance(result.per_context["direction"]["aligned"], bool)


class TestUnknownContext:
    def test_unknown_context_name_raises(self):
        df = _uptrend_df()
        with pytest.raises(ValueError):
            evaluate_context_filters(df, "BULLISH", [ContextSpec(name="fear_greed", mode="entry_filter")])


class TestParseContextFiltersJson:
    def test_round_trips_valid_list(self):
        raw = '[{"name": "direction", "mode": "entry_filter", "params": {"allowed": ["BULLISH"]}, "weight": 0}]'
        parsed = parse_context_filters_json(raw)
        assert parsed == ({"name": "direction", "mode": "entry_filter", "params": {"allowed": ["BULLISH"]}, "weight": 0},)

    def test_rejects_non_list(self):
        with pytest.raises(ValueError):
            parse_context_filters_json('{"name": "direction"}')

    def test_rejects_unknown_name(self):
        with pytest.raises(ValueError):
            parse_context_filters_json('[{"name": "bogus", "mode": "entry_filter"}]')

    def test_rejects_unknown_mode(self):
        with pytest.raises(ValueError):
            parse_context_filters_json('[{"name": "direction", "mode": "bogus"}]')
