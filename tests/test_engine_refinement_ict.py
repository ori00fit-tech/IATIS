"""
tests/test_engine_refinement_ict.py
---------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — ICT refinement
(#362). Per the operator's explicit, pre-approved directive ("fix must
happen now"): DISCOUNT->BULLISH/PREMIUM->BEARISH auto-bias and the
automatic killzone_score bonus conflated CONTEXT with actual
trading-event evidence. This is a genuine SEMANTICS_FIX (not purely
observability, unlike SMC/PriceAction/NNFX/Wyckoff's refinements) —
explicitly pre-approved by the operator, and ICT is disabled by default
(config/engines.yaml engines.enabled.ict: false), so it changes zero
live-decision behavior. Pins:

1. Zone position (PREMIUM/DISCOUNT/EQUILIBRIUM) and killzone session
   timing NEVER set bias/score on their own anymore — confirmed directly
   by constructing a features dict with a "loud" zone/killzone context
   and no Judas swing, and asserting the result is NEUTRAL/0.0.
2. A detected Judas swing (the only real trading-event evidence this
   engine produces) is now the sole bias-setter: judas_dir="up" (false
   breakout above, reversed) -> BEARISH; judas_dir="down" -> BULLISH.
3. Zone/killzone/trend become confirmation-only modifiers on an
   already-real Judas signal — pinned individually (confirm bonus,
   conflict penalty, trend countertrend penalty).
4. raw["is_killzone"] bug fix: previously reported session.is_session_
   open alone (True during an Asia-session open too), not the London/
   NewYork/Overlap-only definition this engine's own killzone concept
   and decide()'s real check both use. Confirmed an Asia-hour bar no
   longer reports is_killzone=True.
5. raw gains additive context/event grouped keys; every pre-existing
   flat key is preserved.
6. Golden-value regression: ICT scenario A/B recaptured in
   tests/test_engine_config_extraction_no_behavior_change.py (this
   file's own responsibility is proving those new values are correct
   under the new logic, not re-deriving them).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.ict_engine import ICTEngine, decide, extract_features
from regimes.session_context import SessionContext


def _session(*, is_session_open: bool, primary_session: str, session_hour: int = 8) -> SessionContext:
    return SessionContext(
        active_sessions=[primary_session],
        primary_session=primary_session,
        is_overlap=False,
        is_session_open=is_session_open,
        session_hour=session_hour,
        volatility_expectation="MEDIUM",
    )


def _base_features(**overrides) -> dict:
    features = {
        "tf_session": "H1", "tf_range": "H1",
        "session": _session(is_session_open=False, primary_session="London"),
        "range_low": 100.0, "range_high": 110.0,
        "zone": "EQUILIBRIUM", "pct": 0.5,
        "is_judas": False, "judas_dir": "none",
        "in_uptrend": False, "in_downtrend": False,
    }
    features.update(overrides)
    return features


def _ohlcv(n: int, seed: int = 7, start_hour_utc: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(f"2024-01-01 {start_hour_utc:02d}:00", periods=n, freq="1h", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.3, n))
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


# ── zone/killzone alone never generate a signal ─────────────────────────

def test_loud_zone_and_killzone_context_alone_stays_neutral():
    features = _base_features(
        zone="PREMIUM", pct=0.95,
        session=_session(is_session_open=True, primary_session="NewYork"),
        is_judas=False, judas_dir="none",
    )
    bias, score, reasons = decide(features, {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0
    assert any("not itself a signal" in r for r in reasons)


def test_discount_zone_alone_no_longer_sets_bullish_bias():
    features = _base_features(zone="DISCOUNT", pct=0.05, is_judas=False)
    bias, score, _ = decide(features, {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0


def test_premium_zone_alone_no_longer_sets_bearish_bias():
    features = _base_features(zone="PREMIUM", pct=0.95, is_judas=False)
    bias, score, _ = decide(features, {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0


# ── Judas swing is the sole real trading-event evidence ─────────────────

def test_judas_up_sets_bearish_bias_alone():
    features = _base_features(is_judas=True, judas_dir="up", zone="EQUILIBRIUM")
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    assert score == 45.0  # judas_base_score default
    assert any("Judas swing UP" in r for r in reasons)


def test_judas_down_sets_bullish_bias_alone():
    features = _base_features(is_judas=True, judas_dir="down", zone="EQUILIBRIUM")
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BULLISH
    assert score == 45.0
    assert any("Judas swing DOWN" in r for r in reasons)


# ── zone/killzone/trend as confirmation-only modifiers ──────────────────

def test_zone_confirms_bearish_judas():
    features = _base_features(is_judas=True, judas_dir="up", zone="PREMIUM")
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    assert score == 65.0  # 45 + 20 zone_confirm_score
    assert any("Zone confirms" in r for r in reasons)


def test_zone_conflicts_with_bearish_judas():
    features = _base_features(is_judas=True, judas_dir="up", zone="DISCOUNT")
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    assert score == 35.0  # 45 - 10 zone_conflict_penalty
    assert any("Zone conflicts" in r for r in reasons)


def test_killzone_confirms_judas():
    features = _base_features(
        is_judas=True, judas_dir="down",
        session=_session(is_session_open=True, primary_session="London"),
    )
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BULLISH
    assert score == 60.0  # 45 + 15 killzone_confirm_score
    assert any("Killzone confirms" in r for r in reasons)


def test_no_killzone_no_killzone_bonus():
    features = _base_features(
        is_judas=True, judas_dir="down",
        session=_session(is_session_open=False, primary_session="London"),
    )
    _, score, reasons = decide(features, {})
    assert score == 45.0
    assert not any("Killzone confirms" in r for r in reasons)


def test_trend_conflict_penalizes_countertrend_judas():
    features = _base_features(is_judas=True, judas_dir="up", in_uptrend=True)
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    assert score == 35.0  # 45 - 10 trend_conflict_penalty
    assert any("countertrend" in r for r in reasons)


def test_all_confirmations_stack():
    features = _base_features(
        is_judas=True, judas_dir="up", zone="PREMIUM",
        session=_session(is_session_open=True, primary_session="Overlap"),
        in_downtrend=False, in_uptrend=False,
    )
    bias, score, _ = decide(features, {})
    assert bias == Bias.BEARISH
    assert score == 80.0  # min(45+20+15, score_cap=80.0)


# ── decide() purity ──────────────────────────────────────────────────────

def test_decide_is_pure():
    features = _base_features(is_judas=True, judas_dir="down", zone="DISCOUNT")
    r1 = decide(features, {})
    r2 = decide(features, {})
    assert r1 == r2


# ── raw["is_killzone"] bug fix: Asia session must not count ────────────

def test_is_killzone_false_during_asia_session_open():
    # Asia opens at 21:00 UTC — a bar at hour 21 is within the first 2
    # hours of the Asia session open, so session.is_session_open is True,
    # but Asia is not an ICT killzone (London/NewYork/Overlap only).
    df = _ohlcv(60, start_hour_utc=0)
    # Force the last bar's timestamp to a real Asia-open hour.
    new_idx = df.index[:-1].tolist() + [pd.Timestamp("2024-01-05 21:30", tz="UTC")]
    df.index = pd.DatetimeIndex(new_idx)
    eng = ICTEngine()
    eng.decision_tf = "H1"
    out = eng.analyze({"H1": df})
    assert out.raw["session"] == "Asia"
    assert out.raw["is_killzone"] is False
    assert out.raw["context"]["is_killzone"] is False


def test_is_killzone_true_during_london_session_open():
    df = _ohlcv(60, start_hour_utc=0)
    new_idx = df.index[:-1].tolist() + [pd.Timestamp("2024-01-05 07:30", tz="UTC")]
    df.index = pd.DatetimeIndex(new_idx)
    eng = ICTEngine()
    eng.decision_tf = "H1"
    out = eng.analyze({"H1": df})
    assert out.raw["session"] == "London"
    assert out.raw["is_killzone"] is True


# ── raw gains additive context/event grouping, pre-existing keys kept ──

def test_raw_gains_context_and_event_grouping_and_keeps_flat_keys():
    df = _ohlcv(60, seed=3)
    eng = ICTEngine()
    eng.decision_tf = "H1"
    out = eng.analyze({"H1": df})
    for key in ("timeframe_session", "timeframe_range", "session", "active_sessions",
                "is_killzone", "zone", "zone_pct", "dealing_range", "judas_swing"):
        assert key in out.raw
    assert "context" in out.raw
    assert "event" in out.raw
    assert set(out.raw["context"].keys()) == {
        "zone", "zone_pct", "is_killzone", "session", "in_uptrend", "in_downtrend",
    }
    assert out.raw["event"]["judas_swing"] == out.raw["judas_swing"]


# ── zero-behavior-change proof for anything NOT touched by the fix ─────

def test_extract_features_unchanged_shape():
    df = _ohlcv(60, seed=5)
    features = extract_features({"H1": df}, {})
    for key in ("tf_session", "tf_range", "session", "range_low", "range_high",
                "zone", "pct", "is_judas", "judas_dir", "in_uptrend", "in_downtrend"):
        assert key in features


def test_engine_output_well_formed():
    df = _ohlcv(300, seed=9)
    eng = ICTEngine()
    eng.decision_tf = "H1"
    out = eng.analyze({"H1": df})
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert isinstance(out.score, float)
    assert 0.0 <= out.score <= 80.0
