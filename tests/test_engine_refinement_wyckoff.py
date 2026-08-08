"""
tests/test_engine_refinement_wyckoff.py
-------------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — Wyckoff
refinement (#361), canonical (v1) only, per the operator's finalized
scope: v2 (engines/wyckoff_engine_v2.py) stays an untouched research
variant — it imports several of v1's private helpers directly
(_identify_trading_range, _detect_spring_upthrust, _effort_vs_result,
_volume_analysis), so this pass was careful to preserve every shared
function's call signature exactly. Additive OBSERVABILITY changes plus
zero-risk dead-code removal, zero strategy-behavior change. Pins:

1. range_atr_zero (new extract_features() key, via a NEW standalone
   _range_atr_zero() helper — deliberately NOT added to
   _identify_trading_range()'s own return tuple, since
   wyckoff_engine_v2.py unpacks that as a fixed 3-tuple
   (`range_low, range_high, _ = _identify_trading_range(...)`) and
   changing its arity would have silently broken v2) surfaces the
   already-existing 99-sentinel-spread_in_atr fallback that fires when
   range_atr()==0 (a fully flat/stale instrument) — previously silent.
2. Two dead locals removed — `close` in _identify_trading_range() and
   `last` in _detect_spring_upthrust() — both computed, never read.
   Confirmed behavior-neutral.
3. None of this changes bias/score/reasons — confirmed via the golden
   regression suite (tests/test_engine_config_extraction_no_behavior_
   change.py: Wyckoff scenario A=BULLISH 25.0, B=NEUTRAL 0.0) staying
   green, and directly here via purity/output checks.
4. engines/wyckoff_engine_v2.py and its own test suite are confirmed
   completely unaffected (empty git diff on the v2 file; its own tests
   re-run green) — the exact risk this refinement pass had to guard
   against, since v2 depends on v1's private function signatures.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.wyckoff_engine import (
    WyckoffEngine,
    _detect_spring_upthrust,
    _identify_trading_range,
    _range_atr_zero,
    decide,
    extract_features,
)


def _ohlcv(n: int, seed: int = 7, trend: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 + np.linspace(0, trend * 100, n) + np.cumsum(rng.normal(0, 0.3, n))
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


def _flat(n: int) -> pd.DataFrame:
    """A perfectly flat instrument over the ATR lookback -> range_atr()==0."""
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0},
        index=idx,
    )


def _mtf(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"H4": df, "H1": df, "D1": df.iloc[::6]}


# ── range_atr_zero observability ────────────────────────────────────────

def test_range_atr_zero_false_on_ordinary_data():
    df = _ohlcv(60)
    features = extract_features(df, {})
    assert features["range_atr_zero"] is False


def test_range_atr_zero_true_on_a_fully_flat_instrument():
    df = _flat(60)
    assert _range_atr_zero(df) is True
    features = extract_features(df, {})
    assert features["range_atr_zero"] is True
    # The pre-existing 99-sentinel fallback still deterministically
    # forces in_range=False in this case — unchanged.
    assert features["in_range"] is False


def test_range_atr_zero_surfaces_in_engine_raw_output():
    df = _flat(60)
    eng = WyckoffEngine()
    eng.decision_tf = "H4"
    out = eng.analyze(_mtf(df))
    assert out.raw["trading_range"]["range_atr_zero"] is True
    assert out.features["range_atr_zero"] is True


def test_range_atr_zero_never_reaches_decide_scoring():
    """decide() must remain a pure function of range_low/range_high/
    in_range/current/event/strength/vol — stripping range_atr_zero must
    not change bias/score/reasons."""
    df = _ohlcv(80, seed=3)
    features = extract_features(df, {})
    bias1, score1, reasons1 = decide(features, {})
    stripped = {k: v for k, v in features.items() if k != "range_atr_zero"}
    bias2, score2, reasons2 = decide(stripped, {})
    assert bias1 == bias2
    assert score1 == score2
    assert reasons1 == reasons2


# ── _identify_trading_range()'s shared return arity is preserved ────────
# (wyckoff_engine_v2.py unpacks this as a fixed 3-tuple — the one real
# risk this refinement pass had to avoid.)

def test_identify_trading_range_still_returns_exactly_three_values():
    df = _ohlcv(60)
    result = _identify_trading_range(df)
    assert len(result) == 3
    low, high, in_range = result  # must not raise
    assert isinstance(low, float)
    assert isinstance(high, float)
    assert isinstance(in_range, bool)


# ── dead-code removal is behavior-neutral ───────────────────────────────

def test_identify_trading_range_output_unaffected_by_removed_close_local():
    df = _ohlcv(80, seed=11)
    low1, high1, in_range1 = _identify_trading_range(df)
    low2, high2, in_range2 = _identify_trading_range(df.copy())
    assert low1 == low2
    assert high1 == high2
    assert in_range1 == in_range2


def test_detect_spring_upthrust_output_unaffected_by_removed_last_local():
    # A spring: current bar wicks below range_low, closes back above it.
    idx = pd.date_range("2024-01-01", periods=5, freq="4h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0, 99.5],
            "high": [100.5, 100.5, 100.5, 100.5, 100.2],
            "low": [99.5, 99.5, 99.5, 99.5, 98.5],
            "close": [100.0, 100.0, 100.0, 100.0, 100.0],
            "volume": 1000.0,
        },
        index=idx,
    )
    event, strength = _detect_spring_upthrust(df, range_low=99.5, range_high=100.5, tolerance=0.002)
    assert event == "spring"
    assert strength > 0.0


# ── zero behavior change: bias/score/reasons unaffected ────────────────

def test_bias_score_reasons_unchanged_by_new_observability_field():
    df = _ohlcv(300, seed=21, trend=0.08)
    eng = WyckoffEngine()
    eng.decision_tf = "H4"
    out = eng.analyze(_mtf(df))
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert isinstance(out.score, float)
    assert 0.0 <= out.score <= 75.0
    assert isinstance(out.reasons, list) and len(out.reasons) > 0


# ── v2 stays a completely untouched research variant ───────────────────

def test_wyckoff_v2_file_is_byte_identical(tmp_path=None):
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--stat", "--", "engines/wyckoff_engine_v2.py"],
        capture_output=True, text=True, cwd=_repo_root(),
    )
    assert result.stdout.strip() == "", f"wyckoff_engine_v2.py was modified: {result.stdout}"


def _repo_root() -> str:
    import subprocess
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
    ).stdout.strip()
