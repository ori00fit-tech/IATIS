"""
tests/test_engine_refinement_base_contract.py
------------------------------------------------
Engine Refinement V1 (research/engine-refinement-v1) — §3 Base Contract
hardening. Pins the five new EngineOutput fields (score_type,
causal_timestamp, data_quality, error_type, error_message) and
safe_analyze()'s generic metadata-filling behavior.

Not a rewrite of tests/test_engine_feature_decision_split.py's own
EngineOutput coverage (features/probability/confidence_interval/
expected_return/expected_drawdown/sample_size/evidence_level/crashed) —
that file keeps pinning those exactly as before; this file is additive,
covering only what this refinement pass added.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.data_loader import load_synthetic
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import BaseEngine, Bias, EngineOutput
from engines.smc_engine import SMCEngine


# ── EngineOutput defaults ──────────────────────────────────────────

def test_new_fields_default_to_safe_no_information_values():
    out = EngineOutput(engine_name="X", bias=Bias.BULLISH, score=50.0)
    assert out.score_type == "HEURISTIC"
    assert out.causal_timestamp is None
    assert out.data_quality == {}
    assert out.error_type is None
    assert out.error_message is None


def test_score_type_is_distinct_from_evidence_level():
    """The two fields answer different questions — evidence_level says
    whether the score has been measured/validated, score_type says what
    KIND of number it is. Both default to a heuristic posture, but
    setting one must never implicitly change the other."""
    out = EngineOutput(engine_name="X", bias=Bias.BULLISH, score=50.0, evidence_level="MEASURED")
    assert out.score_type == "HEURISTIC"  # unaffected by evidence_level


def test_to_dict_includes_all_five_new_keys():
    out = EngineOutput(
        engine_name="X", bias=Bias.BULLISH, score=50.0,
        score_type="HEURISTIC", causal_timestamp="2026-01-01T00:00:00+00:00",
        data_quality={"bars_available": 300}, error_type=None, error_message=None,
    )
    d = out.to_dict()
    assert d["score_type"] == "HEURISTIC"
    assert d["causal_timestamp"] == "2026-01-01T00:00:00+00:00"
    assert d["data_quality"] == {"bars_available": 300}
    assert d["error_type"] is None
    assert d["error_message"] is None


def test_backward_compatible_positional_construction_still_works():
    """Every new field was appended AFTER every existing field, all with
    defaults — the original 3-positional-arg construction pattern used
    throughout this codebase (engine_name, bias, score) must still work
    unchanged."""
    out = EngineOutput("X", Bias.NEUTRAL, 0.0)
    assert out.score_type == "HEURISTIC"
    assert out.causal_timestamp is None


# ── safe_analyze() metadata filling ────────────────────────────────

def _real_mtf_data(bars: int = 300) -> dict[str, pd.DataFrame]:
    df = load_synthetic(bars=bars, timeframe="H1", seed=7)
    return build_multi_timeframe_view(df, ["H1", "H4", "D1"])


def test_safe_analyze_fills_causal_timestamp_from_real_decision_frame():
    engine = SMCEngine()
    engine.decision_tf = "H1"
    mtf = _real_mtf_data()
    out = engine.safe_analyze(mtf)
    assert out.causal_timestamp is not None
    assert out.causal_timestamp == str(mtf["H1"].index[-1])


def test_safe_analyze_fills_data_quality_requested_vs_used():
    engine = SMCEngine()
    engine.decision_tf = "H1"
    mtf = _real_mtf_data()
    out = engine.safe_analyze(mtf)
    assert out.data_quality["decision_tf_requested"] == "H1"
    assert out.data_quality["decision_tf_used"] == "H1"
    assert out.data_quality["bars_available"] == len(mtf["H1"])


def test_safe_analyze_data_quality_reflects_fallback_when_requested_tf_missing():
    """decision_tf="D1" but only H1/H4 supplied -> decision_frame() falls
    back to H1 -> data_quality must say so honestly, not silently claim
    D1 was used."""
    engine = SMCEngine()
    engine.decision_tf = "D1"
    mtf = _real_mtf_data()
    del mtf["D1"]
    out = engine.safe_analyze(mtf)
    assert out.data_quality["decision_tf_requested"] == "D1"
    assert out.data_quality["decision_tf_used"] == "H1"


def test_safe_analyze_never_overwrites_an_engine_supplied_causal_timestamp():
    class CustomTimestampEngine(BaseEngine):
        name = "Custom"

        def analyze(self, mtf_data):
            return EngineOutput(
                engine_name=self.name, bias=Bias.NEUTRAL, score=0.0,
                causal_timestamp="ENGINE_SUPPLIED",
            )

    out = CustomTimestampEngine().safe_analyze(_real_mtf_data())
    assert out.causal_timestamp == "ENGINE_SUPPLIED"


# ── crash path: error_type/error_message + logging, not silent ────

def test_crash_path_sets_error_type_and_message():
    class BrokenEngine(BaseEngine):
        name = "Broken"

        def analyze(self, mtf_data):
            raise KeyError("missing_column")

    out = BrokenEngine().safe_analyze(_real_mtf_data())
    assert out.crashed is True
    assert out.error_type == "KeyError"
    assert "missing_column" in out.error_message


def test_crash_path_still_fills_causal_timestamp_and_data_quality():
    """The crash happens INSIDE analyze() — decision_frame() metadata was
    already collected before that call, so a crashed output is still
    traceable to which bar/timeframe it was attempted against."""
    class BrokenEngine(BaseEngine):
        name = "Broken"

        def analyze(self, mtf_data):
            raise RuntimeError("boom")

    mtf = _real_mtf_data()
    out = BrokenEngine().safe_analyze(mtf)
    assert out.causal_timestamp == str(mtf["H1"].index[-1])
    assert out.data_quality["bars_available"] == len(mtf["H1"])


def test_crash_is_logged_not_silently_swallowed(caplog):
    import logging

    class BrokenEngine(BaseEngine):
        name = "Broken"

        def analyze(self, mtf_data):
            raise ValueError("simulated failure")

    with caplog.at_level(logging.WARNING):
        BrokenEngine().safe_analyze(_real_mtf_data())
    assert any("Broken engine crashed" in r.message for r in caplog.records)


def test_metadata_collection_failure_never_crashes_the_wrapper():
    """decision_frame() itself can raise (e.g. StopIteration on an empty
    mtf_data dict) — that must never propagate out of safe_analyze()."""
    class BrokenEngine(BaseEngine):
        name = "Broken"

        def analyze(self, mtf_data):
            raise ValueError("real failure")

    out = BrokenEngine().safe_analyze({})  # empty dict -> decision_frame() raises internally
    assert out.crashed is True
    assert out.causal_timestamp is None
    assert out.data_quality == {}
