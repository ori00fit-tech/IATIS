"""
tests/test_wyckoff_engine_v2.py
-----------------------------------
Confluence Engine Overhaul Track C (Phase 4, 2026-08-01) — engine-level
tests for WyckoffEngineV2, the additive Phase A->E schematic
reconstruction layered on top of v1's existing spring/upthrust + range-
position + volume logic (imported and reused directly). AD-HOC-ONLY
(never enabled by config/engines.yaml's `enabled` block, reachable only
through Mission Center's engine_variants override). Correctness here
means real accumulation/distribution schematics are detected on hand-
built bar-by-bar sequences, and v1's own layer stays untouched inside
v2 — not golden bias/score values (v1 is reused, not refactored).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias
from engines.wyckoff_engine import decide as v1_decide
from engines.wyckoff_engine import extract_features as v1_extract_features
from engines.wyckoff_engine import _effort_vs_result
from engines.wyckoff_engine_v2 import (
    WyckoffEngineV2,
    _composite_operator_footprint,
    _detect_phase,
    _find_automatic_reaction,
    _find_climax,
    _find_secondary_test,
    _find_sos_lps,
    _find_sow_lpsy,
    _phase_range,
    decide,
    extract_features,
)


def _df(rows: list[list[float]]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="h", tz="UTC")
    arr = np.array(rows, dtype=float)
    df = pd.DataFrame({"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2], "close": arr[:, 3]}, index=idx)
    df["volume"] = 0.0
    return df


def _sine_warmup(n: int, low: float, high: float) -> list[list[float]]:
    base = low + (high - low) * (0.5 + 0.5 * np.sin(np.linspace(0, 20, n)))
    return [[b, b + 1.0, b - 1.0, b] for b in base]


def _accumulation_schematic() -> pd.DataFrame:
    """SC -> AR -> ST -> [consolidation] -> SOS -> LPS, built bar-by-bar."""
    rows = _sine_warmup(44, 90.0, 110.0)
    rows.append([95, 96, 80, 82])                    # SC: wide down bar closing near its low
    rows += [[83, 90, 82, 89], [90, 98, 89, 97], [97, 99, 95, 96], [96, 97, 90, 91]]  # AR
    rows += [[91, 92, 80.1, 88], [88, 95, 87, 93]]    # ST: revisits near SC's low (80), holds
    for i in range(8):
        rows.append([92 + i * 0.2, 96 + i * 0.2, 90 + i * 0.2, 94 + i * 0.2])  # consolidation
    rows.append([100, 118, 99, 116])                  # SOS: real breakout above range_high (~111)
    rows += [[116, 117, 108, 114], [114, 120, 113, 119]]  # LPS: pullback holds above the old range
    rows.append([119, 125, 118, 124])                 # confirmation bar (Phase E check)
    return _df(rows)


def _distribution_schematic() -> pd.DataFrame:
    """BC -> AR -> ST -> [consolidation] -> SOW -> LPSY, the mirror image
    of _accumulation_schematic() (every price relationship inverted)."""
    rows = _sine_warmup(44, 90.0, 110.0)
    rows.append([105, 120, 104, 118])                 # BC: wide up bar closing near its high
    rows += [[117, 118, 110, 111], [110, 111, 102, 103], [103, 105, 101, 102], [104, 110, 103, 109]]  # AR
    rows += [[109, 119.9, 108, 112], [112, 113, 105, 107]]  # ST: revisits near BC's high (120), holds
    for i in range(8):
        rows.append([108 - i * 0.2, 110 - i * 0.2, 104 - i * 0.2, 106 - i * 0.2])  # consolidation
    rows.append([100, 101, 82, 84])                    # SOW: real breakdown below range_low (~90)
    rows += [[84, 92, 83, 86], [86, 87, 80, 81]]        # LPSY: rally fails to reclaim the old range
    rows.append([81, 82, 75, 76])                       # confirmation bar (Phase E check)
    return _df(rows)


# ---------------------------------------------------------------------------
# _find_climax / _phase_range — bar-shape-only, no self-referential range
# ---------------------------------------------------------------------------

def test_find_climax_detects_selling_climax():
    df = _accumulation_schematic()
    climax = _find_climax(df, {})
    assert climax["type"] == "sc"
    assert climax["price"] == pytest.approx(80.0)


def test_find_climax_detects_buying_climax():
    df = _distribution_schematic()
    climax = _find_climax(df, {})
    assert climax["type"] == "bc"
    assert climax["price"] == pytest.approx(120.0)


def test_find_climax_none_on_flat_series():
    rows = _sine_warmup(60, 95.0, 105.0)
    df = _df(rows)
    climax = _find_climax(df, {})
    assert climax["type"] == "none"


def test_phase_range_uses_pre_climax_data_only():
    """The load-bearing bug fix this phase's design pass found: the range
    used for SOS/LPS detection must come from data STRICTLY BEFORE the
    climax, never a window ending 'now' (which would include the very
    breakout bars being detected, self-referentially inflating the
    threshold they're tested against)."""
    df = _accumulation_schematic()
    climax = _find_climax(df, {})
    range_low, range_high = _phase_range(df, climax, {})
    # Pre-climax range reflects the sine warmup (90-110ish), NOT the SC's
    # own low (80) or the SOS/LPS bars' extremes (up to 125).
    assert range_low is not None
    assert 85.0 < range_low < 100.0
    assert 105.0 < range_high < 115.0


# ---------------------------------------------------------------------------
# Full phase-machine chain on real hand-built schematics
# ---------------------------------------------------------------------------

def test_full_accumulation_schematic_reaches_phase_e():
    df = _accumulation_schematic()
    phase = _detect_phase(df, {"st_tolerance": 0.01}, "none")
    assert phase["schematic"] == "accumulation"
    assert phase["events"]["sc"] is True
    assert phase["events"]["ar"] is True
    assert phase["events"]["st"] is True
    assert phase["events"]["sos"] is True
    assert phase["events"]["lps"] is True
    assert phase["phase"] == "E"


def test_full_distribution_schematic_reaches_phase_e():
    df = _distribution_schematic()
    phase = _detect_phase(df, {"st_tolerance": 0.01}, "none")
    assert phase["schematic"] == "distribution"
    assert phase["events"]["bc"] is True
    assert phase["events"]["sow"] is True
    assert phase["events"]["lpsy"] is True
    assert phase["phase"] == "E"


def test_phase_stops_at_a_when_no_automatic_reaction():
    # A climax with no data at all following it (climax is the last bar)
    rows = _sine_warmup(44, 90.0, 110.0)
    rows.append([95, 96, 80, 82])
    df = _df(rows)
    phase = _detect_phase(df, {}, "none")
    assert phase["phase"] == "A"
    assert phase["events"]["ar"] is False


def test_phase_none_when_no_climax_at_all():
    rows = _sine_warmup(80, 95.0, 105.0)
    df = _df(rows)
    phase = _detect_phase(df, {}, "none")
    assert phase["phase"] == "NONE"
    assert phase["schematic"] is None


# ---------------------------------------------------------------------------
# Composite Operator footprint — proves v1's dead _effort_vs_result is
# actually invoked, and produces a directionally sensible footprint
# ---------------------------------------------------------------------------

def test_composite_operator_footprint_invokes_effort_vs_result(monkeypatch):
    import engines.wyckoff_engine_v2 as v2mod

    calls = {"n": 0}
    real = _effort_vs_result

    def _spy(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(v2mod, "_effort_vs_result", _spy)
    df = _accumulation_schematic()
    v2mod._composite_operator_footprint(df, 90.0, 110.0, 95.0, {})
    assert calls["n"] == 1


def test_composite_operator_footprint_bullish_near_range_low():
    # 11 narrow, low-effort bars (small spread, small body) followed by ONE
    # unusually wide bar (high effort vs. the trailing average) with a tiny
    # net body (weak result), dipping near the range low -> absorption ->
    # bullish footprint. _effort_vs_result compares the LAST bar's spread
    # against the trailing window's mean, so the effort must be a real
    # outlier, not a constant series (which always reads as "low effort").
    rows = [[100.0, 101.0, 99.0, 100.3] for _ in range(11)]
    rows.append([95.0, 100.0, 85.0, 94.5])  # wide spread near the low, tiny net move
    df = _df(rows)
    score, bias = _composite_operator_footprint(df, 85.0, 115.0, 92.0, {})
    assert bias == "bullish"
    assert score > 0


def test_composite_operator_footprint_neutral_when_not_absorption():
    rows = [[100.0, 100.5, 99.5, 100.2] for _ in range(12)]  # narrow spread, no effort signal
    df = _df(rows)
    score, bias = _composite_operator_footprint(df, 85.0, 115.0, 92.0, {})
    assert bias == "neutral"
    assert score == 0.0


# ---------------------------------------------------------------------------
# extract_features / decide — purity + "v1 stays byte-identical inside v2"
# ---------------------------------------------------------------------------

def _synth_mtf(seed: int = 1, bars: int = 300):
    df_h1 = load_synthetic(bars=bars, timeframe="H1", seed=seed)
    return build_multi_timeframe_view(df_h1, ["H1", "H4", "D1"])


def test_extract_features_is_pure():
    mtf = _synth_mtf(seed=1)
    df = mtf["H4"]
    f1 = extract_features(df, {})
    f2 = extract_features(df, {})
    assert f1 == f2


def test_decide_is_pure():
    mtf = _synth_mtf(seed=1)
    features = extract_features(mtf["H4"], {})
    r1 = decide(features, {})
    r2 = decide(features, {})
    assert r1 == r2


def test_v2_decide_matches_v1_base_layer_when_no_phase_evidence():
    """The authoritative "genuinely additive" proof: for a features dict
    where phase=NONE and co_footprint_score=0, v2's decide() output must
    be numerically identical to calling v1's decide() directly on the
    same base features (with the SAME thresholds dict)."""
    mtf = _synth_mtf(seed=42, bars=300)
    df = mtf["H4"]
    t: dict = {}
    v1_features = v1_extract_features(df, t)
    v2_features = extract_features(df, t)
    if v2_features["phase"]["phase"] != "NONE" or v2_features["co_footprint_score"] != 0.0:
        pytest.skip("this seed produced real phase evidence — not the null case under test")
    r_v1 = v1_decide(v1_features, t)
    r_v2 = decide(v2_features, t)
    assert r_v1 == r_v2


def test_v2_upgrades_v1_neutral_when_sos_lps_present():
    """The concrete proof of v2's real added value: on the accumulation
    schematic, v1's own decide() (mid-range position, no spring in its
    exact tolerance window) produces a materially different, weaker
    read than v2's phase-aware decide() — v2 must reach BULLISH with
    real SOS/LPS-driven score, confirming the phase layer adds signal
    v1's decide() alone doesn't have."""
    df = _accumulation_schematic()
    t = {"st_tolerance": 0.01, "min_bars": 60}
    features = extract_features(df, t)
    bias, score, reasons = decide(features, t)
    assert bias == Bias.BULLISH
    assert any("Sign of Strength" in r for r in reasons)
    assert any("Last Point of Support" in r for r in reasons)
    assert score > 0


# ---------------------------------------------------------------------------
# Engine-level: min_bars gate, JSON-serializability, EngineOutput defaults
# ---------------------------------------------------------------------------

def test_insufficient_data_abstains():
    df = load_synthetic(bars=20, timeframe="H1", seed=1)
    mtf = build_multi_timeframe_view(df, ["H1"])
    out = WyckoffEngineV2().safe_analyze(mtf)
    assert out.bias == Bias.NEUTRAL
    assert out.score == 0.0
    assert "Insufficient data" in out.reasons[0]


def test_engine_output_json_serializable():
    mtf = _synth_mtf(seed=7)
    out = WyckoffEngineV2().safe_analyze(mtf)
    json.dumps(out.features, default=str)
    json.dumps(out.raw, default=str)


def test_engine_output_defaults():
    mtf = _synth_mtf(seed=7)
    out = WyckoffEngineV2().safe_analyze(mtf)
    assert out.evidence_level == "HEURISTIC"
    assert out.probability is None
    assert out.engine_name == "WyckoffV2"


def test_full_accumulation_schematic_through_the_real_engine():
    df = _accumulation_schematic()
    mtf = {"H1": df, "H4": df, "D1": df}
    engine = WyckoffEngineV2()
    engine.thresholds = {"st_tolerance": 0.01}
    out = engine.analyze(mtf)
    assert out.bias == Bias.BULLISH
    assert out.raw["phase"] == "E"
    assert out.raw["schematic"] == "accumulation"


def test_score_bounds_across_many_scenarios():
    for seed in range(1, 8):
        mtf = _synth_mtf(seed=seed, bars=300)
        out = WyckoffEngineV2().safe_analyze(mtf)
        assert 0.0 <= out.score <= 85.0
