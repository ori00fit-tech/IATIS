"""tests/test_indicators.py — utils/indicators.py (A2 consolidation).

Property tests for the shared indicator math, plus the invariant that
matters most: the two ATR variants are DIFFERENT on gapped data and
consumers must keep using the variant they were validated with.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.indicators import atr, range_atr, true_range


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="4h")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_true_range_uses_prev_close_on_gaps():
    # Bar 2 gaps up: H−L = 1, but |L − prevC| = 4 → TR must be 5 (H−prevC).
    df = _frame([(10, 11, 9, 10), (10, 16, 15, 15.5)])
    tr = true_range(df)
    assert tr.iloc[0] == pytest.approx(2.0)   # first bar: prevC is NaN → H−L
    assert tr.iloc[1] == pytest.approx(6.0)   # max(1, |16−10|, |15−10|)


def test_atr_is_nan_until_period_bars():
    df = _frame([(10, 11, 9, 10)] * 20)
    a = atr(df, period=14)
    assert a.iloc[:13].isna().all()
    assert not np.isnan(a.iloc[13])


def test_atr_constant_range_equals_range():
    df = _frame([(10, 11, 9, 10)] * 30)  # H−L = 2 every bar, no gaps
    assert atr(df, 14).iloc[-1] == pytest.approx(2.0)
    assert range_atr(df, 14) == pytest.approx(2.0)


def test_variants_differ_on_gapped_data():
    """The reason both exist: range_atr ignores gaps, atr() does not.
    On gappy bars they MUST disagree — a consumer switching variants is
    a behavior change, not a refactor."""
    rows = []
    price = 100.0
    for i in range(30):
        price += 5.0  # persistent gap between bars
        rows.append((price, price + 1, price, price + 0.5))
    df = _frame(rows)
    assert range_atr(df, 14) == pytest.approx(1.0)          # H−L only
    assert atr(df, 14).iloc[-1] == pytest.approx(5.5)       # gap included
    assert atr(df, 14).iloc[-1] != pytest.approx(range_atr(df, 14))


def test_range_atr_matches_legacy_inline_formula():
    """Exact equivalence with the formula the engines inlined:
    float((df['high'] - df['low']).tail(period).mean())."""
    rng = np.random.default_rng(3)
    close = 1.1 + rng.normal(0, 0.001, 60).cumsum()
    df = pd.DataFrame({
        "open": close, "high": close + rng.uniform(0, 0.002, 60),
        "low": close - rng.uniform(0, 0.002, 60), "close": close,
    }, index=pd.date_range("2026-01-01", periods=60, freq="4h"))
    legacy = float((df["high"] - df["low"]).tail(14).mean())
    assert range_atr(df, 14) == legacy


def test_volatility_classifier_reexport_unchanged():
    """Existing importers (backtest engine, regime detector) go through
    regimes.volatility_classifier.atr — it must be numerically identical
    to the consolidated implementation."""
    from regimes.volatility_classifier import atr as vc_atr
    rng = np.random.default_rng(4)
    close = 1.1 + rng.normal(0, 0.001, 60).cumsum()
    df = pd.DataFrame({
        "open": close, "high": close + 0.002, "low": close - 0.002,
        "close": close,
    }, index=pd.date_range("2026-01-01", periods=60, freq="4h"))
    pd.testing.assert_series_equal(vc_atr(df, 14), atr(df, 14))
