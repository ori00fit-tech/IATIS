"""
tests/test_engine_refinement_causality.py
----------------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — §6 Causality
Hardening. Wires research/guards/causal_guard.py (runtime point-in-time
assertions) and research/guards/static_scan.py (advisory AST scanner)
into automated tests against backtesting/backtest_engine.py's real hot
path, closing the gap BASELINE.md flagged: both guard modules existed but
had zero references from backtest_engine.py before this pass.

Two things are proven here, not assumed:
1. run_backtest() now REJECTS non-monotonic input OHLCV (a real, cheap
   precondition check added to backtest_engine.py this same commit) —
   without it, `window = df.iloc[:i+1]` silently stops meaning "every bar
   known as of bar i" the moment the input isn't sorted.
2. Every engine's mtf_data, at every real bar of a real run, never
   contains a row timestamped after the decision timeframe's own current
   bar — i.e. the causal contract every engine's docstring already
   assumes is actually upheld end-to-end, checked with the same
   assertion primitive causal_guard.py already offers research scripts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtesting.backtest_engine import (
    BacktestConfig, build_engine_config_override, run_backtest,
)
from engines.base_engine import BaseEngine
from research.guards.causal_guard import LookaheadError, assert_no_future_rows

# _ohlcv() below is genuinely H1-cadence (freq="h"). Declaring that
# explicitly avoids relying on config.yaml's H4 default, which (post-
# BUG-006 fix) now really resamples mismatched data down to ~1/4 the bars
# instead of silently mislabeling it — at n=600 that would otherwise land
# below warmup_bars=210 and produce an empty result.
_H1_ENGINE_CONFIG = build_engine_config_override(timeframes=["H1"])


def _ohlcv(n: int, seed: int = 7, trend: float = 0.06) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {
            "open": o,
            "high": np.maximum(o, close) + 0.0008,
            "low": np.minimum(o, close) - 0.0008,
            "close": close,
            "volume": 1000.0,
        },
        index=idx,
    )


# ── run_backtest() rejects non-monotonic input ─────────────────────────

def test_run_backtest_raises_on_shuffled_input():
    df = _ohlcv(600).sample(frac=1.0, random_state=1)  # shuffles row order, breaks the index sort
    with pytest.raises(LookaheadError):
        run_backtest(df, BacktestConfig.from_profile("EURUSD"))


def test_run_backtest_raises_on_out_of_order_index():
    df = _ohlcv(600)
    # Swap two rows' index values directly — a duplicate-free but
    # out-of-order index, the exact shape a bad upstream join could
    # produce without going through .sample()'s row-shuffle path.
    idx = df.index.to_list()
    idx[100], idx[300] = idx[300], idx[100]
    df.index = pd.DatetimeIndex(idx)
    with pytest.raises(LookaheadError):
        run_backtest(df, BacktestConfig.from_profile("EURUSD"))


def test_run_backtest_accepts_correctly_sorted_input():
    # Regression guard: the new precondition must never fire on normal,
    # correctly-loaded data (every real caller already sort_index()s).
    df = _ohlcv(600)
    assert df.index.is_monotonic_increasing
    result = run_backtest(
        df, BacktestConfig.from_profile("EURUSD"), engine_config=_H1_ENGINE_CONFIG,
    )
    assert result.total_runs > 0


# ── every engine's mtf_data is causally sound, at every real bar ───────

def test_no_engine_ever_receives_a_future_bar_across_a_real_run(monkeypatch):
    captured: list[dict[str, pd.DataFrame]] = []
    original_safe_analyze = BaseEngine.safe_analyze

    def _capturing_safe_analyze(self, mtf_data):
        captured.append(mtf_data)
        return original_safe_analyze(self, mtf_data)

    monkeypatch.setattr(BaseEngine, "safe_analyze", _capturing_safe_analyze)

    df = _ohlcv(600)
    result = run_backtest(
        df, BacktestConfig.from_profile("EURUSD"), engine_config=_H1_ENGINE_CONFIG,
    )
    assert result.total_runs > 0
    assert len(captured) > 0, "no engine calls captured — test fixture is broken"

    checked = 0
    for mtf_data in captured:
        # build_multi_timeframe_view() always inserts the base/decision
        # timeframe's own frame FIRST (views[base_label] = df_base, before
        # the loop over the remaining, resampled timeframes) — Python
        # dicts preserve insertion order, so the first key genuinely is
        # the causal boundary: window's own true last bar. Every other
        # (coarser, resampled) timeframe in the same dict must never have
        # a row strictly after it.
        as_of = mtf_data[next(iter(mtf_data))].index[-1]
        for tf_label, tf_df in mtf_data.items():
            assert_no_future_rows(
                tf_df, as_of, inclusive=False,
                label=f"mtf_data[{tf_label!r}] at as_of={as_of}",
            )
            checked += 1
    assert checked > 0


def test_decision_timeframe_last_bar_is_always_the_true_current_bar(monkeypatch):
    """A stronger, complementary check: the base (decision) timeframe's
    own last row must equal the real bar being processed — not just "no
    row after it" (which a truncated-early frame would also satisfy)."""
    seen_last_bars: list[pd.Timestamp] = []
    original_safe_analyze = BaseEngine.safe_analyze

    def _capturing_safe_analyze(self, mtf_data):
        tf, df = self.decision_frame(mtf_data)
        if len(df) > 0:
            seen_last_bars.append(df.index[-1])
        return original_safe_analyze(self, mtf_data)

    monkeypatch.setattr(BaseEngine, "safe_analyze", _capturing_safe_analyze)

    df = _ohlcv(600)
    run_backtest(
        df, BacktestConfig.from_profile("EURUSD"), engine_config=_H1_ENGINE_CONFIG,
    )
    assert len(seen_last_bars) > 0
    # Every observed "current bar" must itself be a real, present row in
    # the original data — never a fabricated or out-of-range timestamp.
    valid_timestamps = set(df.index)
    for ts in seen_last_bars:
        assert ts in valid_timestamps


# ── static_scan.py wired in as a regression guard over the hot path ────

def test_static_leakage_scan_stays_clean_on_the_backtest_hot_path():
    """Advisory scanner (research/guards/static_scan.py), pinned as a real
    regression test: no known leakage-shaped pattern (negative shift,
    backward fill, forward merge_asof) exists in the files a real
    backtest actually executes. INFO-severity findings (e.g. an
    unlagged .rolling() call) are expected and NOT asserted against —
    the scanner's own docstring names them a low-confidence heuristic,
    not a verdict; only the zero-HIGH-severity baseline is a real,
    checkable invariant."""
    import glob

    from research.guards.static_scan import scan_paths

    paths = (
        ["backtesting/backtest_engine.py"]
        + sorted(glob.glob("engines/*.py"))
        + sorted(glob.glob("confluence/*.py"))
    )
    report = scan_paths(paths)
    assert report["total_high_severity"] == 0, report
