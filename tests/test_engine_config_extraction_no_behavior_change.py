"""
tests/test_engine_config_extraction_no_behavior_change.py
------------------------------------------------------------
Confluence Engine Overhaul Phase 1 — the load-bearing regression test.

Golden bias/score values below were captured from the CURRENT (pre-
refactor) engine code, against the two fixed synthetic mtf_data scenarios
built here, BEFORE any indicator-unification or config-extraction edit
was made to smc_engine.py / price_action_engine.py / nnfx_engine.py /
wyckoff_engine.py / ict_engine.py / market_structure_engine.py.

The refactor (moving indicator math into utils/indicators.py, moving
magic numbers into config/engines.yaml's new `thresholds:` section, read
via self.thresholds.get(name, CURRENT_HARDCODED_VALUE)) must reproduce
these exact values in TWO configurations:
  1. Zero-arg construction (self.thresholds stays the BaseEngine default
     {} — every existing test/script call site that never sets it).
  2. Constructed with the real config/engines.yaml thresholds populated
     (the live/backtest construction-site path).
If either path drifts from these numbers, the refactor changed live
trading behavior — CLAUDE.md rule 6 territory, not a safe pure refactor.
"""

from __future__ import annotations

import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.ict_engine import ICTEngine
from engines.market_structure_engine import MarketStructureEngine
from engines.nnfx_engine import NNFXEngine
from engines.price_action_engine import PriceActionEngine
from engines.smc_engine import SMCEngine
from engines.wyckoff_engine import WyckoffEngine
from utils.helpers import load_config

# Two independent synthetic scenarios exercising different bias/score
# branches across all 6 engines.
#
# `end` is pinned (2026-07-31 fix): load_synthetic()'s default end
# anchors to pd.Timestamp.now("UTC"), and core.timeframe_sync.resample()
# anchors H4/D1 bucket boundaries to CLOCK time, not to the data's own
# start — so an un-pinned `end` silently reshuffles which H1 bars land
# in the final H4/D1 candle depending on what wall-clock hour the suite
# happens to run at. Confirmed by direct reproduction: with the OLD
# (unpinned) fixture, Wyckoff's scenario-A score flipped between 25.0
# and 40.0 purely based on run time, zero code involved. Every golden
# value below was (re-)captured against this exact pinned `end`.
_FIXED_END = "2026-07-15 08:00:00"
_SCENARIO_A = dict(bars=600, timeframe="H1", seed=42, start_price=1.0850, end=_FIXED_END)
_SCENARIO_B = dict(bars=600, timeframe="H1", seed=7, start_price=1950.0, end=_FIXED_END)

# Golden values captured from the current (already-refactored, already
# behavior-verified) engine code against the pinned-`end` fixtures above.
_GOLDEN = {
    "A": {
        "SMC": ("BEARISH", 45.5),
        "PriceAction": ("BEARISH", 70.0),
        "NNFX": ("BEARISH", 68.0),
        "Wyckoff": ("BULLISH", 40.0),
        "ICT": ("NEUTRAL", 10.0),
        "MarketStructure": ("BEARISH", 65.0),
    },
    "B": {
        "SMC": ("BEARISH", 65.0),
        "PriceAction": ("NEUTRAL", 0.0),
        "NNFX": ("BEARISH", 55.0),
        "Wyckoff": ("NEUTRAL", 0.0),
        "ICT": ("BULLISH", 55.0),
        "MarketStructure": ("BULLISH", 40.0),
    },
}

_ENGINE_CLASSES = {
    "SMC": (SMCEngine, "smc"),
    "PriceAction": (PriceActionEngine, "price_action"),
    "NNFX": (NNFXEngine, "nnfx"),
    "Wyckoff": (WyckoffEngine, "wyckoff"),
    "ICT": (ICTEngine, "ict"),
    "MarketStructure": (MarketStructureEngine, "market_structure"),
}


def _build_mtf(scenario: dict) -> dict:
    df_h1 = load_synthetic(
        bars=scenario["bars"], timeframe=scenario["timeframe"],
        seed=scenario["seed"], start_price=scenario["start_price"],
        end=scenario["end"],
    )
    return build_multi_timeframe_view(df_h1, ["H1", "H4", "D1"])


@pytest.fixture(scope="module")
def mtf_a():
    return _build_mtf(_SCENARIO_A)


@pytest.fixture(scope="module")
def mtf_b():
    return _build_mtf(_SCENARIO_B)


@pytest.fixture(scope="module")
def real_thresholds():
    config = load_config()
    return config.get("engines", {}).get("thresholds", {})


@pytest.mark.parametrize("engine_name", list(_ENGINE_CLASSES.keys()))
@pytest.mark.parametrize("scenario_key,mtf_fixture", [("A", "mtf_a"), ("B", "mtf_b")])
def test_zero_arg_construction_matches_golden_value(
    engine_name, scenario_key, mtf_fixture, request
):
    """Path 1: self.thresholds is the BaseEngine class default ({}) —
    every existing zero-arg construction site (tests, research scripts)."""
    mtf = request.getfixturevalue(mtf_fixture)
    cls, _key = _ENGINE_CLASSES[engine_name]
    engine = cls()
    out = engine.analyze(mtf)

    expected_bias, expected_score = _GOLDEN[scenario_key][engine_name]
    assert out.bias.value == expected_bias
    assert round(out.score, 4) == pytest.approx(expected_score)


@pytest.mark.parametrize("engine_name", list(_ENGINE_CLASSES.keys()))
@pytest.mark.parametrize("scenario_key,mtf_fixture", [("A", "mtf_a"), ("B", "mtf_b")])
def test_real_config_thresholds_matches_golden_value(
    engine_name, scenario_key, mtf_fixture, real_thresholds, request
):
    """Path 2: self.thresholds populated from the real config/engines.yaml
    thresholds block — the live/backtest engine-construction-site path.
    Since every extracted value equals its pre-refactor hardcoded default,
    this must reproduce the identical golden value as the zero-arg path."""
    mtf = request.getfixturevalue(mtf_fixture)
    cls, key = _ENGINE_CLASSES[engine_name]
    engine = cls()
    engine.thresholds = real_thresholds.get(key, {})
    out = engine.analyze(mtf)

    expected_bias, expected_score = _GOLDEN[scenario_key][engine_name]
    assert out.bias.value == expected_bias
    assert round(out.score, 4) == pytest.approx(expected_score)


def test_config_engines_yaml_has_thresholds_for_all_six_in_scope_engines(real_thresholds):
    for _name, key in _ENGINE_CLASSES.values():
        assert key in real_thresholds, f"missing thresholds block for {key}"
        assert isinstance(real_thresholds[key], dict)
        assert len(real_thresholds[key]) > 0


def test_config_engines_yaml_does_not_extract_phase3_rebuild_targets(real_thresholds):
    """macro/divergence/sentiment are deliberately NOT config-extracted in
    Phase 1 — their Phase 3 rebuilds replace their thresholds wholesale.
    quant is the exception: Phase 3a (2026-08-01) already fully rebuilt it
    as a regime-aware statistical engine, config-first from day one (see
    tests/test_quant_engine_v2.py) — its thresholds block is real and
    expected here, not a Phase 1 extraction artifact."""
    for key in ("macro", "divergence", "sentiment"):
        assert key not in real_thresholds
    assert "quant" in real_thresholds


def test_base_engine_thresholds_defaults_to_empty_dict():
    from engines.base_engine import BaseEngine

    class _Dummy(BaseEngine):
        def analyze(self, mtf_data):
            raise NotImplementedError

    assert _Dummy().thresholds == {}
