"""
tests/test_macro_engine.py
------------------------------
Confluence Engine Overhaul Phase 3c — the first test file
engines/macro_engine.py has ever had. This engine is DISABLED
(config/engines.yaml engines.enabled.macro: false) and its confluence
weight is already forced to 0.0 — correctness here means sound,
well-reasoned macro-context logic and honest abstention, not golden
bias/score values (v1 is fully replaced, not refactored).

extract_features()/decide() are genuinely pure given an already-fetched
snapshot dict, so most cases below need NO network mocking at all —
hand-built per-series DataFrames drive every scenario directly. Only the
engine-level analyze() tests monkeypatch
core.alt_data_loader.load_macro_snapshot, matching
tests/test_macro_sources.py's own established mocking convention one
level up.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.macro_engine import MacroEngine, decide, extract_features, is_usd_base_symbol


def _series_df(vals: list[float], freq: str = "D") -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(vals), freq=freq, tz="UTC")
    arr = np.array(vals, dtype=float)
    return pd.DataFrame({"open": arr, "high": arr, "low": arr, "close": arr, "volume": 0.0}, index=idx)


# ---------------------------------------------------------------------------
# extract_features() — individual signals, isolated
# ---------------------------------------------------------------------------

def test_extract_features_dxy_rising():
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40)))}
    f = extract_features(snapshot, {})
    assert f["dxy_direction"] == "up"
    assert f["dxy_spread_pct"] > 0


def test_extract_features_dxy_falling():
    snapshot = {"DXY": _series_df(list(np.linspace(110, 100, 40)))}
    f = extract_features(snapshot, {})
    assert f["dxy_direction"] == "down"
    assert f["dxy_spread_pct"] < 0


def test_extract_features_dxy_unavailable_when_too_short():
    snapshot = {"DXY": _series_df([100.0] * 5)}
    f = extract_features(snapshot, {})
    assert f["dxy_direction"] is None


def test_extract_features_spy_risk_on_vote():
    snapshot = {"SPY": _series_df(list(np.linspace(400, 430, 30)))}
    f = extract_features(snapshot, {})
    assert f["risk_on_votes"] == 1
    assert f["risk_off_votes"] == 0


def test_extract_features_spy_risk_off_vote():
    snapshot = {"SPY": _series_df(list(np.linspace(430, 400, 30)))}
    f = extract_features(snapshot, {})
    assert f["risk_off_votes"] == 1


def test_extract_features_vix_low_is_risk_on():
    snapshot = {"VIX": _series_df([10.0] * 10)}
    f = extract_features(snapshot, {})
    assert f["risk_on_votes"] == 1


def test_extract_features_vix_high_is_risk_off():
    snapshot = {"VIX": _series_df([30.0] * 10)}
    f = extract_features(snapshot, {})
    assert f["risk_off_votes"] == 1


def test_extract_features_yield_curve_inverted():
    snapshot = {"US10Y": _series_df([3.5] * 5), "US02Y": _series_df([4.0] * 5)}
    f = extract_features(snapshot, {})
    assert f["yield_inverted"] is True
    assert f["yield_spread"] == pytest.approx(-0.5)
    assert f["risk_off_votes"] == 1


def test_extract_features_yield_curve_normal():
    snapshot = {"US10Y": _series_df([4.5] * 5), "US02Y": _series_df([4.0] * 5)}
    f = extract_features(snapshot, {})
    assert f["yield_inverted"] is False
    assert f["risk_off_votes"] == 0


def test_extract_features_yield_curve_unavailable_when_missing():
    f = extract_features({}, {})
    assert f["yield_spread"] is None
    assert f["yield_inverted"] is None


def test_extract_features_credit_spread_widening_is_risk_off():
    snapshot = {"CREDIT_SPREAD": _series_df(list(np.linspace(1.8, 2.2, 25)))}
    f = extract_features(snapshot, {})
    assert f["risk_off_votes"] == 1
    assert f["risk_on_votes"] == 0


def test_extract_features_credit_spread_narrowing_is_risk_on():
    snapshot = {"CREDIT_SPREAD": _series_df(list(np.linspace(2.2, 1.8, 25)))}
    f = extract_features(snapshot, {})
    assert f["risk_on_votes"] == 1
    assert f["risk_off_votes"] == 0


def test_extract_features_fed_balance_sheet_expanding_is_risk_on():
    snapshot = {"FED_BALANCE_SHEET": _series_df(list(np.linspace(7000, 7100, 10)))}
    f = extract_features(snapshot, {})
    assert f["risk_on_votes"] == 1


def test_extract_features_fed_balance_sheet_contracting_is_risk_off():
    snapshot = {"FED_BALANCE_SHEET": _series_df(list(np.linspace(7100, 7000, 10)))}
    f = extract_features(snapshot, {})
    assert f["risk_off_votes"] == 1


def test_extract_features_commodities_trend_labels():
    snapshot = {
        "OIL_WTI": _series_df(list(np.linspace(70, 80, 25))),
        "COPPER": _series_df(list(np.linspace(4.0, 3.6, 8))),
        "NATGAS": _series_df([2.5] * 25),
    }
    f = extract_features(snapshot, {})
    assert f["oil_trend"] == "up"
    assert f["copper_trend"] == "down"
    assert f["natgas_trend"] == "flat"


def test_extract_features_commodities_never_affect_risk_votes():
    with_up = extract_features({"OIL_WTI": _series_df(list(np.linspace(70, 150, 25)))}, {})
    with_down = extract_features({"OIL_WTI": _series_df(list(np.linspace(150, 70, 25)))}, {})
    assert with_up["risk_on_votes"] == with_down["risk_on_votes"] == 0
    assert with_up["risk_off_votes"] == with_down["risk_off_votes"] == 0


def test_extract_features_data_loaded_and_errors():
    snapshot = {"DXY": _series_df([100.0] * 40), "VIX": _series_df([15.0] * 10)}
    f = extract_features(snapshot, {})
    assert f["data_loaded"] == ["DXY", "VIX"]
    assert "SPY" in f["load_errors"]
    assert "DXY" not in f["load_errors"]


def test_extract_features_is_pure():
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40)))}
    f1 = extract_features(snapshot, {})
    f2 = extract_features(snapshot, {})
    assert f1 == f2


def test_extract_features_json_serializable():
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40))), "SPY": _series_df([400.0] * 30)}
    f = extract_features(snapshot, {})
    json.dumps(f, default=str)


# ---------------------------------------------------------------------------
# decide() — never touches a DataFrame, purity, scoring composition
# ---------------------------------------------------------------------------

def _base_features(**overrides) -> dict:
    features = {
        "dxy_direction": None, "dxy_spread_pct": None,
        "risk_on_votes": 0, "risk_off_votes": 0, "risk_reasons": [],
        "yield_spread": None, "yield_inverted": None,
        "oil_trend": None, "copper_trend": None, "natgas_trend": None,
        "data_loaded": [], "load_errors": [],
    }
    features.update(overrides)
    return features


def test_decide_never_touches_a_dataframe():
    features = _base_features(dxy_direction="up", dxy_spread_pct=1.0, risk_off_votes=2)
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    assert score > 0


def test_decide_dxy_rising_confirmed_by_risk_off_is_bearish():
    features = _base_features(dxy_direction="up", dxy_spread_pct=2.0, risk_off_votes=3, risk_on_votes=0)
    bias, score, reasons = decide(features, {})
    assert bias == Bias.BEARISH
    assert any("confirms bearish" in r for r in reasons)


def test_decide_dxy_rising_conflicting_with_risk_on_reduces_score():
    features_conflict = _base_features(dxy_direction="up", dxy_spread_pct=2.0, risk_on_votes=3, risk_off_votes=0)
    features_neutral_risk = _base_features(dxy_direction="up", dxy_spread_pct=2.0)
    _, score_conflict, reasons = decide(features_conflict, {})
    _, score_neutral, _ = decide(features_neutral_risk, {})
    assert score_conflict < score_neutral
    assert any("conflicts" in r for r in reasons)


def test_decide_commodities_appear_in_reasons_never_in_score():
    features_with = _base_features(dxy_direction="up", dxy_spread_pct=2.0, oil_trend="up", copper_trend="down")
    features_without = _base_features(dxy_direction="up", dxy_spread_pct=2.0)
    _, score_with, reasons_with = decide(features_with, {})
    _, score_without, _ = decide(features_without, {})
    assert score_with == score_without
    assert any("informational only" in r for r in reasons_with)


def test_decide_no_data_is_neutral_zero():
    bias, score, reasons = decide(_base_features(), {})
    assert bias == Bias.NEUTRAL
    assert score == 0.0
    assert "DXY data unavailable" in reasons


def test_decide_reports_partial_data():
    features = _base_features(load_errors=["OIL_WTI", "COPPER"])
    _, _, reasons = decide(features, {})
    assert any("Partial data" in r and "OIL_WTI" in r for r in reasons)


def test_decide_is_pure():
    features = _base_features(dxy_direction="down", dxy_spread_pct=-1.5, risk_on_votes=2)
    r1 = decide(features, {})
    r2 = decide(features, {})
    assert r1 == r2


def test_decide_score_never_exceeds_cap():
    features = _base_features(dxy_direction="up", dxy_spread_pct=100.0, risk_off_votes=5)
    _, score, _ = decide(features, {})
    assert score <= 70.0


def test_decide_forces_neutral_below_score_floor():
    features = _base_features(dxy_direction="up", dxy_spread_pct=0.01)
    bias, score, _ = decide(features, {"score_neutral_floor": 15.0})
    assert bias == Bias.NEUTRAL
    assert score < 15.0


# ---------------------------------------------------------------------------
# Engine-level (MacroEngine.safe_analyze) — monkeypatches load_macro_snapshot
# ---------------------------------------------------------------------------

def test_engine_all_data_missing_abstains(monkeypatch):
    import core.alt_data_loader as adl
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: {})
    out = MacroEngine().safe_analyze({})
    assert out.bias == Bias.NEUTRAL
    assert out.score == 0.0
    assert "unavailable" in out.reasons[0]


def test_engine_partial_data_reports_and_still_opines(monkeypatch):
    import core.alt_data_loader as adl
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40)))}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)
    out = MacroEngine().safe_analyze({})
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert any("Partial data" in r for r in out.reasons)


def test_engine_raw_has_timeframe_used_d1(monkeypatch):
    import core.alt_data_loader as adl
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40))), "SPY": _series_df([400.0] * 30)}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)
    out = MacroEngine().safe_analyze({})
    assert out.raw.get("timeframe_used") == "D1"


def test_engine_mtf_data_is_genuinely_unused(monkeypatch):
    import core.alt_data_loader as adl
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40)))}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)
    out_empty = MacroEngine().safe_analyze({})
    out_populated = MacroEngine().safe_analyze({"H1": _series_df([1.0] * 10)})
    assert out_empty.bias == out_populated.bias
    assert out_empty.score == out_populated.score


def test_engine_output_features_and_raw_json_serializable(monkeypatch):
    import core.alt_data_loader as adl
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40))), "VIX": _series_df([15.0] * 10)}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)
    out = MacroEngine().safe_analyze({})
    json.dumps(out.features, default=str)
    json.dumps(out.raw, default=str)


def test_engine_evidence_level_and_probability_defaults(monkeypatch):
    import core.alt_data_loader as adl
    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40)))}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)
    out = MacroEngine().safe_analyze({})
    assert out.evidence_level == "HEURISTIC"
    assert out.probability is None


def test_engine_import_error_path_abstains_cleanly(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "core.alt_data_loader":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = MacroEngine().safe_analyze({})
    assert out.bias == Bias.NEUTRAL
    assert out.score == 0.0
    assert "Macro loader unavailable" in out.reasons[0]


def test_config_engines_yaml_thresholds_macro_supplies_every_default(monkeypatch):
    from utils.helpers import load_config
    import core.alt_data_loader as adl

    config = load_config()
    thresholds = config.get("engines", {}).get("thresholds", {}).get("macro", {})
    assert thresholds, "config/engines.yaml is missing thresholds.macro"

    snapshot = {"DXY": _series_df(list(np.linspace(100, 110, 40))), "SPY": _series_df([400.0] * 30)}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)
    engine = MacroEngine()
    engine.thresholds = thresholds
    out = engine.safe_analyze({})
    assert out.bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)


# ---------------------------------------------------------------------------
# BUG-008 (Forensic Audit, 2026-08-04) — DXY->bias mapping was symbol-
# agnostic: it always treated a stronger dollar as bearish for "the pair",
# which is correct for EURUSD/GBPUSD/XAUUSD/BTCUSD (USD is quote/priced-in)
# but backwards for USDJPY/USDCHF/USDCAD, where USD is the BASE currency —
# a stronger dollar makes those pairs RISE. Currently dormant (macro is
# `enabled: false`, `weights.macro: 0.0` in production), so no live trade
# is affected today, but the defect would fire the instant macro is ever
# re-enabled — fixed at the source regardless (same "latent landmine"
# discipline as BUG-005's SentimentEngine fix).
# ---------------------------------------------------------------------------

def test_is_usd_base_symbol_identifies_usd_base_pairs():
    assert is_usd_base_symbol("USDJPY") is True
    assert is_usd_base_symbol("USDCHF") is True
    assert is_usd_base_symbol("USDCAD") is True
    assert is_usd_base_symbol("usdjpy") is True  # case-insensitive


def test_is_usd_base_symbol_rejects_usd_quote_and_non_fx_symbols():
    assert is_usd_base_symbol("EURUSD") is False
    assert is_usd_base_symbol("GBPUSD") is False
    assert is_usd_base_symbol("XAUUSD") is False
    assert is_usd_base_symbol("BTCUSD") is False
    assert is_usd_base_symbol("US30") is False
    assert is_usd_base_symbol("") is False


def test_decide_dxy_rising_is_bearish_when_usd_is_not_base():
    """Regression pin: the original, unchanged mapping for every non-
    USD-base symbol (EURUSD, GBPUSD, XAUUSD, BTCUSD, ...) — the default
    usd_is_base=False must reproduce today's exact behavior. Spread is
    large enough (3.0%) to clear the score-neutral floor on the DXY
    component alone, with no risk votes to confound it."""
    features = _base_features(dxy_direction="up", dxy_spread_pct=3.0)
    bias, _, _ = decide(features, {})
    assert bias == Bias.BEARISH


def test_decide_dxy_rising_is_bullish_when_usd_is_base():
    """BUG-008 reproduction: for a USD-base pair, a stronger dollar must
    flip the DXY-driven bias to BULLISH, the exact opposite of the
    non-base case above on identical DXY input."""
    features = _base_features(dxy_direction="up", dxy_spread_pct=3.0)
    bias, _, reasons = decide(features, {}, usd_is_base=True)
    assert bias == Bias.BULLISH
    assert any("BASE currency" in r for r in reasons)


def test_decide_dxy_falling_is_bearish_when_usd_is_base():
    features = _base_features(dxy_direction="down", dxy_spread_pct=-3.0)
    bias, _, _ = decide(features, {}, usd_is_base=True)
    assert bias == Bias.BEARISH


def test_engine_flips_bias_for_usdjpy_vs_eurusd_on_identical_dxy_data(monkeypatch):
    """End-to-end reproduction through the real MacroEngine.analyze():
    identical DXY-rising data must produce OPPOSITE bias for USDJPY
    (USD-base) vs. a non-USD-base symbol, via main.py's real _symbol
    attribute-assignment pattern (BUG-005 precedent)."""
    import core.alt_data_loader as adl
    snapshot = {"DXY": _series_df(list(np.linspace(100, 130, 40)))}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)

    eurusd_engine = MacroEngine()
    eurusd_engine._symbol = "EURUSD"
    out_eurusd = eurusd_engine.safe_analyze({})

    usdjpy_engine = MacroEngine()
    usdjpy_engine._symbol = "USDJPY"
    out_usdjpy = usdjpy_engine.safe_analyze({})

    assert out_eurusd.bias == Bias.BEARISH
    assert out_usdjpy.bias == Bias.BULLISH
    assert out_usdjpy.raw["usd_is_base"] is True
    assert out_eurusd.raw["usd_is_base"] is False


def test_engine_without_symbol_attribute_defaults_to_non_usd_base(monkeypatch):
    """Every existing zero-arg construction site (tests, the backtest
    engine-construction loop, which does not set _symbol) must keep
    producing today's original mapping — no AttributeError, no behavior
    change when _symbol was never set."""
    import core.alt_data_loader as adl
    snapshot = {"DXY": _series_df(list(np.linspace(100, 130, 40)))}
    monkeypatch.setattr(adl, "load_macro_snapshot", lambda symbols: snapshot)
    out = MacroEngine().safe_analyze({})
    assert out.bias == Bias.BEARISH
    assert out.raw["usd_is_base"] is False
