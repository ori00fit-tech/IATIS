"""
tests/test_engine_feature_decision_split.py
------------------------------------------------
Confluence Engine Overhaul Phase 2 — Feature Extraction / Decision Logic
split + EngineOutput schema unification.

Pins two properties for each of the 6 engines refactored this phase
(smc, price_action, nnfx, wyckoff, ict, market_structure):
  1. extract_features() is a pure function: same (df, thresholds) input
     always produces the same features dict.
  2. decide()/decide_structural_bias() is a pure function of its features
     dict: calling it twice with the SAME features dict produces the SAME
     (bias, score, reasons) — the decision logic never re-reads df/mtf_data.

Also pins EngineOutput's new additive schema fields (features,
probability, confidence_interval, expected_return, expected_drawdown,
sample_size, evidence_level) and their safe, non-fabricated defaults.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias, EngineOutput
from engines.ict_engine import ICTEngine
from engines.ict_engine import decide as ict_decide
from engines.ict_engine import extract_features as ict_extract
from engines.market_structure_engine import MarketStructureEngine
from engines.market_structure_engine import decide as ms_decide
from engines.market_structure_engine import extract_features as ms_extract
from engines.nnfx_engine import NNFXEngine
from engines.nnfx_engine import decide as nnfx_decide
from engines.nnfx_engine import extract_features as nnfx_extract
from engines.price_action_engine import PriceActionEngine
from engines.price_action_engine import decide as pa_decide
from engines.price_action_engine import extract_features as pa_extract
from engines.smc_engine import SMCEngine
from engines.smc_engine import decide_structural_bias, extract_structural_features
from engines.wyckoff_engine import WyckoffEngine
from engines.wyckoff_engine import decide as wyckoff_decide
from engines.wyckoff_engine import extract_features as wyckoff_extract


def _mtf(seed: int = 1, bars: int = 600) -> dict[str, pd.DataFrame]:
    df_h1 = load_synthetic(bars=bars, timeframe="H1", seed=seed)
    return build_multi_timeframe_view(df_h1, ["H1", "H4", "D1"])


# ---------------------------------------------------------------------------
# EngineOutput schema
# ---------------------------------------------------------------------------

def test_engine_output_new_fields_default_safely():
    out = EngineOutput(engine_name="X", bias=Bias.NEUTRAL, score=0.0)
    assert out.features == {}
    assert out.probability is None
    assert out.confidence_interval is None
    assert out.expected_return is None
    assert out.expected_drawdown is None
    assert out.sample_size is None
    assert out.evidence_level == "HEURISTIC"


def test_engine_output_to_dict_includes_new_fields():
    out = EngineOutput(engine_name="X", bias=Bias.BULLISH, score=50.0, features={"a": 1})
    d = out.to_dict()
    assert d["features"] == {"a": 1}
    assert d["probability"] is None
    assert d["confidence_interval"] is None
    assert d["evidence_level"] == "HEURISTIC"


def test_engine_output_to_dict_serializes_confidence_interval_as_list():
    out = EngineOutput(engine_name="X", bias=Bias.BULLISH, score=50.0, confidence_interval=(0.1, 0.9))
    assert out.to_dict()["confidence_interval"] == [0.1, 0.9]


def test_engine_output_backward_compatible_positional_construction():
    # Existing call sites across the codebase construct with 3 positional
    # args (engine_name, bias, score) — new fields must not break this.
    out = EngineOutput("SMC", Bias.BULLISH, 65.0)
    assert out.evidence_level == "HEURISTIC"
    assert out.features == {}


# ---------------------------------------------------------------------------
# Per-engine: analyze() populates EngineOutput.features
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", [
    SMCEngine, PriceActionEngine, NNFXEngine, WyckoffEngine, ICTEngine, MarketStructureEngine,
])
def test_analyze_populates_features_field(cls):
    out = cls().analyze(_mtf(seed=42))
    assert isinstance(out.features, dict)
    assert len(out.features) > 0
    assert out.evidence_level == "HEURISTIC"
    assert out.probability is None  # no engine has measured evidence yet


@pytest.mark.parametrize("cls", [
    SMCEngine, PriceActionEngine, NNFXEngine, WyckoffEngine, ICTEngine, MarketStructureEngine,
])
def test_engine_output_features_json_serializable(cls):
    import json
    out = cls().analyze(_mtf(seed=42))
    # Must not raise — Feature Extraction snapshots are meant for
    # storage/analysis (D1, chart_data.json), never a raw non-primitive
    # object like a dataclass instance.
    json.dumps(out.features, default=str)


# ---------------------------------------------------------------------------
# Per-engine: extract_features() purity
# ---------------------------------------------------------------------------

def test_price_action_extract_features_is_pure():
    mtf = _mtf(seed=1)
    df = mtf["H1"]
    t = {}
    f1 = pa_extract(df, t)
    f2 = pa_extract(df, t)
    assert f1 == f2


def test_nnfx_extract_features_is_pure():
    mtf = _mtf(seed=1)
    df = mtf["H1"]
    t = {}
    f1 = nnfx_extract(df, t)
    f2 = nnfx_extract(df, t)
    assert f1 == f2


def test_wyckoff_extract_features_is_pure():
    mtf = _mtf(seed=1)
    df = mtf["H4"]
    t = {}
    f1 = wyckoff_extract(df, t)
    f2 = wyckoff_extract(df, t)
    assert f1 == f2


def test_ict_extract_features_is_pure():
    mtf = _mtf(seed=1)
    t = {}
    f1 = ict_extract(mtf, t)
    f2 = ict_extract(mtf, t)
    # SessionContext isn't a plain dict, compare field by field
    assert f1["zone"] == f2["zone"]
    assert f1["pct"] == f2["pct"]
    assert f1["session"].primary_session == f2["session"].primary_session


def test_market_structure_extract_features_is_pure():
    mtf = _mtf(seed=1)
    t = {}
    f1 = ms_extract(mtf["H1"], mtf["H4"], t)
    f2 = ms_extract(mtf["H1"], mtf["H4"], t)
    assert f1 == f2


def test_smc_extract_structural_features_is_pure():
    mtf = _mtf(seed=1)
    df = mtf["H4"]
    f1 = extract_structural_features(df, window=3, lookback=6)
    f2 = extract_structural_features(df, window=3, lookback=6)
    assert f1 == f2


# ---------------------------------------------------------------------------
# Per-engine: decide() purity — same features in, same decision out
# ---------------------------------------------------------------------------

def test_price_action_decide_is_pure():
    mtf = _mtf(seed=1)
    features = pa_extract(mtf["H1"], {})
    r1 = pa_decide(features, {})
    r2 = pa_decide(features, {})
    assert r1 == r2


def test_nnfx_decide_is_pure():
    mtf = _mtf(seed=1)
    features = nnfx_extract(mtf["H1"], {})
    r1 = nnfx_decide(features, {})
    r2 = nnfx_decide(features, {})
    assert r1 == r2


def test_wyckoff_decide_is_pure():
    mtf = _mtf(seed=1)
    features = wyckoff_extract(mtf["H4"], {})
    r1 = wyckoff_decide(features, {})
    r2 = wyckoff_decide(features, {})
    assert r1 == r2


def test_ict_decide_is_pure():
    mtf = _mtf(seed=1)
    features = ict_extract(mtf, {})
    r1 = ict_decide(features, {})
    r2 = ict_decide(features, {})
    assert r1 == r2


def test_market_structure_decide_is_pure():
    mtf = _mtf(seed=1)
    features = ms_extract(mtf["H1"], mtf["H4"], {})
    r1 = ms_decide(features, {})
    r2 = ms_decide(features, {})
    assert r1 == r2


def test_smc_decide_structural_bias_is_pure():
    mtf = _mtf(seed=1)
    features = extract_structural_features(mtf["H4"], window=3, lookback=6)
    r1 = decide_structural_bias(features)
    r2 = decide_structural_bias(features)
    assert r1 == r2


def test_smc_decide_structural_bias_never_touches_a_dataframe():
    """decide_structural_bias() must be able to run on a features dict
    with no df in scope at all — proof the split is real, not cosmetic."""
    features = {"insufficient": False, "total_pairs": 4, "bullish_pairs": 3, "bearish_pairs": 1}
    bias, score, reasons = decide_structural_bias(features)
    assert bias == Bias.BULLISH
    assert score == pytest.approx(0.75 * 65.0, abs=0.1)
