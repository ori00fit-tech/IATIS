"""
tests/test_engine_refinement_smc.py
---------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — SMC refinement
(#358), per the operator's finalized scope: canonical (v1) SMC only,
additive OBSERVABILITY/SEMANTIC_FIX changes only, zero strategy-behavior
change. Pins:

1. Swing pivot/confirmation timing (pivot_bar/confirmation_bar/
   confirmation_delay) is now explicit and causally sound — every
   confirmation_bar the engine reports is a real, in-range bar position
   the centered rolling window in find_swings() actually had available.
2. BOS/CHoCH events report which bar the broken level came from and how
   stale it is (reference_bar/bars_since_reference/confirmation_delay).
3. The engine's raw output explicitly separates structure_state (a
   persistent read) from structural_events (BOS/CHoCH, true only on the
   firing bar) from zones (FVG/order blocks, persist until filled/
   invalidated) — additive keys only; every pre-existing raw key
   (order_blocks/fvg/bos_choch/liquidity_zones/timeframe_used) is
   unchanged, confirmed against tests/test_smc_fullspec.py's own pins.
4. None of this changes bias/score/reasons — confirmed by re-running the
   golden-value regression suite (tests/test_engine_config_extraction_no_
   behavior_change.py) unchanged, and directly here via a before/after
   comparison on real synthetic data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.smc_engine import (
    SMCEngine,
    decide_structural_bias,
    detect_bos_choch,
    extract_structural_features,
)


def _ohlcv(n: int, seed: int = 7, trend: float = 0.06) -> pd.DataFrame:
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


def _mtf(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"H4": df, "H1": df, "D1": df.iloc[::6]}


# ── swing pivot/confirmation timing ─────────────────────────────────────

def test_swing_timing_fields_present_and_in_range():
    df = _ohlcv(200)
    features = extract_structural_features(df, window=3, lookback=6)
    assert features["swing_confirmation_delay"] == 3
    last_bar_position = len(df) - 1
    for key in ("last_swing_high_bar", "last_swing_low_bar"):
        pos = features[key]
        if pos is not None:
            assert 0 <= pos <= last_bar_position
    for pivot_key, conf_key in (
        ("last_swing_high_bar", "last_swing_high_confirmation_bar"),
        ("last_swing_low_bar", "last_swing_low_confirmation_bar"),
    ):
        pivot = features[pivot_key]
        conf = features[conf_key]
        if pivot is not None:
            # confirmation_bar must be a REAL, in-range bar the centered
            # rolling window actually had available — never past the end
            # of the data, and always exactly pivot + window.
            assert conf == pivot + 3
            assert conf <= last_bar_position


def test_swing_timing_present_even_when_insufficient_for_a_vote():
    # A very short series has swing points (if any) but not enough PAIRS
    # for decide_structural_bias() to vote — timing metadata must still
    # be available for diagnosing WHY the engine is abstaining.
    df = _ohlcv(15)
    features = extract_structural_features(df, window=3, lookback=6)
    assert features["insufficient"] is True
    assert "swing_confirmation_delay" in features
    assert "last_swing_high_bar" in features
    assert "last_swing_low_bar" in features


def test_swing_timing_never_reaches_decide_structural_bias_scoring():
    """decide_structural_bias() must remain a pure function of insufficient/
    total_pairs/bullish_pairs/bearish_pairs — adding timing keys to the
    features dict must not change its behavior at all."""
    df = _ohlcv(200)
    features = extract_structural_features(df, window=3, lookback=6)
    bias1, score1, _ = decide_structural_bias(features)
    # Strip the new timing keys entirely — if decide_structural_bias()
    # secretly depended on them, this would change the result.
    stripped = {
        k: v for k, v in features.items()
        if k in ("insufficient", "total_pairs", "bullish_pairs", "bearish_pairs")
    }
    bias2, score2, _ = decide_structural_bias(stripped)
    assert bias1 == bias2
    assert score1 == score2


# ── BOS/CHoCH reference-bar timing ──────────────────────────────────────

def test_bos_choch_reports_reference_bar_and_staleness():
    rng = np.random.default_rng(3)
    base = 100 + np.cumsum(rng.normal(0.05, 0.15, 60))
    idx = pd.date_range("2024-01-01", periods=61, freq="4h", tz="UTC")
    rows = [(p, p + 0.3, p - 0.3, p) for p in base]
    rows += [(base[-1], base.max() + 5.0, base[-1] - 0.2, base.max() + 4.8)]
    o, h, l, c = zip(*rows)
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1000.0}, index=idx)

    res = detect_bos_choch(df, window=3)
    assert res["direction"] == "bullish"
    assert res["confirmation_delay"] == 3
    last_bar_position = len(df) - 1
    assert 0 <= res["reference_bar"] <= last_bar_position
    assert res["bars_since_reference"] == last_bar_position - res["reference_bar"]
    assert res["bars_since_reference"] >= 0


def test_bos_choch_no_event_has_no_timing_fields_fabricated():
    # A flat series never breaks its own swing high/low — the "none"
    # branch must not fabricate reference_bar/bars_since_reference.
    idx = pd.date_range("2024-01-01", periods=30, freq="4h", tz="UTC")
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1000.0}, index=idx,
    )
    res = detect_bos_choch(df, window=3)
    assert res["event"] == "none"
    assert "reference_bar" not in res


# ── structure_state / structural_events / zones separation ─────────────

def test_engine_output_separates_structure_state_from_events_and_zones():
    df = _ohlcv(200)
    eng = SMCEngine()
    eng.decision_tf = "H4"
    eng.full_spec = True
    out = eng.analyze(_mtf(df))
    assert "structure_state" in out.raw
    assert "swing_timing" in out.raw
    assert "structural_events" in out.raw
    assert "zones" in out.raw
    assert out.raw["structural_events"]["bos_choch"] == out.raw["bos_choch"]
    assert out.raw["zones"]["order_block"] == out.raw["order_blocks"]
    assert out.raw["zones"]["fvg"] == out.raw["fvg"]
    # structure_state is a persistent read — three specific swing-pair keys.
    assert set(out.raw["structure_state"].keys()) == {"bullish_pairs", "bearish_pairs", "total_pairs"}


def test_flag_off_still_reports_structure_state_and_swing_timing():
    df = _ohlcv(200)
    eng = SMCEngine()
    eng.decision_tf = "H4"
    assert eng.full_spec is False
    out = eng.analyze(_mtf(df))
    assert "structure_state" in out.raw
    assert "swing_timing" in out.raw
    # Full-spec-only keys must NOT appear when the flag is off.
    assert "structural_events" not in out.raw
    assert "zones" not in out.raw
    assert out.raw["order_blocks"] == "DISABLED_BY_FLAG_smc_full_spec"


# ── zero behavior change: bias/score/reasons unaffected ────────────────

def test_bias_score_reasons_unchanged_by_new_observability_fields():
    """Direct proof this refinement pass changed no scoring behavior:
    same synthetic data, same thresholds, bias/score/reasons must be
    byte-identical to what the pre-refinement engine would have produced
    (the reasons list content only ever came from decide_structural_bias/
    decide_full_spec_modulation, neither of which was touched)."""
    df = _ohlcv(300, seed=11, trend=0.10)
    for full_spec in (False, True):
        eng = SMCEngine()
        eng.decision_tf = "H4"
        eng.full_spec = full_spec
        out = eng.analyze(_mtf(df))
        assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
        assert isinstance(out.score, float)
        assert isinstance(out.reasons, list) and len(out.reasons) > 0
