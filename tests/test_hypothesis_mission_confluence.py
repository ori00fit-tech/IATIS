"""tests/test_hypothesis_mission_confluence.py -- tests for the Phase 8B
gap fix in backtest/hypothesis_mission.py::record_mission(): dispatching
to build_confluence_execution_request() for a CONFLUENCE hypothesis, and
refusing a batch that mixes SINGLE_ENGINE and CONFLUENCE hypotheses in
one call. Found and fixed while building Phase 8C's own integration
tests (record_mission() was previously hard-wired to build_execution_
request() only, so Phase 8B's own "CONFLUENCE hypothesis -> Mission" path
was structurally unreachable despite the contract claiming it worked)."""
from __future__ import annotations

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from storage import hypothesis_factory as hf_storage


def _bundle(**overrides) -> dict:
    base = {
        "name": "Prod4 Confluence Panel",
        "timeframes": ["H4"],
        "engines": ["smc", "price_action", "nnfx", "wyckoff"],
        "indicators": [],
        "context_filters": [],
    }
    base.update(overrides)
    return base


def _persist_confluence_hypothesis(**overrides) -> hf.Hypothesis:
    h = hf.generate_confluence_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], decision_version=overrides.get("decision_version", "v1"),
        bundle=overrides.get("bundle", _bundle()), risk_presets=[overrides.get("risk_preset", "balanced")],
    )[0]
    hf_storage.record_hypotheses([h])
    return h


def _persist_single_engine_hypothesis(**overrides) -> hf.Hypothesis:
    h = hf.generate_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], engines=[overrides.get("engine", "price_action")],
        timeframes=[overrides.get("timeframe", "H1")], risk_presets=[overrides.get("risk_preset", "balanced")],
    )[0]
    hf_storage.record_hypotheses([h])
    return h


def test_record_mission_binds_a_confluence_hypothesis():
    h = _persist_confluence_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    assert result["created"] is True
    assert len(result["bindings"]) == 1
    assert result["bindings"][0]["hypothesis_id"] == h.hypothesis_id


def test_record_mission_binds_multiple_confluence_hypotheses():
    h1 = _persist_confluence_hypothesis(symbol="EURUSD")
    h2 = _persist_confluence_hypothesis(symbol="GBPUSD")
    result = hm.record_mission([h1.hypothesis_id, h2.hypothesis_id], research_code_commit="commit-A")
    assert result["created"] is True
    assert len(result["bindings"]) == 2


def test_record_mission_rejects_a_batch_mixing_decision_types():
    single = _persist_single_engine_hypothesis()
    confluence = _persist_confluence_hypothesis()
    with pytest.raises(he.HypothesisExecutionError, match="mix decision_type"):
        hm.record_mission([single.hypothesis_id, confluence.hypothesis_id], research_code_commit="commit-A")


def test_record_mission_rejecting_a_mixed_batch_persists_nothing():
    from storage import hypothesis_mission as hm_storage

    single = _persist_single_engine_hypothesis()
    confluence = _persist_confluence_hypothesis()
    mission_id = hm.compute_mission_id(
        [single.hypothesis_id, confluence.hypothesis_id], "commit-A", None,
    )
    with pytest.raises(he.HypothesisExecutionError):
        hm.record_mission([single.hypothesis_id, confluence.hypothesis_id], research_code_commit="commit-A")
    assert hm_storage.get_mission(mission_id) is None


def test_record_mission_confluence_cell_carries_the_full_bundle():
    from storage import research_matrix as rm_storage

    h = _persist_confluence_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    cell = rm_storage.get_cell(cell_id)
    assert cell["status"] == "QUEUED"
    assert cell["source_hypothesis_id"] == h.hypothesis_id
    # Phase 1's denormalized engine/engine_version/timeframe columns stay
    # NULL for a genuinely multi-engine confluence bundle -- unchanged
    # single_engine_identity() behavior, reused verbatim.
    assert cell["engine"] is None


def test_record_mission_single_engine_path_still_works_unaffected():
    """Regression proof: the dispatch fix must not change SINGLE_ENGINE
    behavior at all."""
    h = _persist_single_engine_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    assert result["created"] is True
    assert len(result["bindings"]) == 1
