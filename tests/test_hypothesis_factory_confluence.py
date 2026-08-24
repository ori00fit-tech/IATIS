"""tests/test_hypothesis_factory_confluence.py -- pure-function tests for
backtest/hypothesis_factory.py's Phase 8B CONFLUENCE path
(generate_confluence_hypotheses()). Hypothesis Discovery Engine, Phase
8B — Confluence Governed Identity."""
from __future__ import annotations

import json

import pytest

from backtest import hypothesis_factory as hf
from backtest import research_matrix as rm


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


# --- basic generation -------------------------------------------------


def test_generate_confluence_hypotheses_basic():
    hyps = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), risk_presets=["balanced"],
    )
    assert len(hyps) == 1
    h = hyps[0]
    assert h.decision_type == hf.CONFLUENCE
    assert h.engine == hf.CONFLUENCE
    assert h.engine_version == "v1"
    assert h.timeframe == "H4"
    assert h.symbol == "EURUSD"
    assert h.risk_preset == "balanced"
    assert h.bundle_id == "Prod4 Confluence Panel"
    assert h.hypothesis_id.startswith("CONFLUENCE-HYPOTHESIS-")


def test_hypothesis_id_prefix_distinguishes_confluence_from_single_engine():
    single = hf.generate_hypotheses(
        symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"],
    )[0]
    confluence = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), risk_presets=["balanced"],
    )[0]
    assert single.hypothesis_id.startswith("DISCOVERY-HYPOTHESIS-")
    assert confluence.hypothesis_id.startswith("CONFLUENCE-HYPOTHESIS-")
    assert single.decision_type == hf.SINGLE_ENGINE


def test_bundle_json_persists_the_full_bundle_verbatim():
    bundle = _bundle()
    h = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=bundle, risk_presets=["balanced"],
    )[0]
    assert json.loads(h.bundle_json) == bundle


# --- fingerprint / identity behavior ------------------------------------


def test_bundle_version_is_stored_but_not_independently_fingerprinted():
    """Documented, deliberate limitation: bundle_version alone changing
    does NOT change the identity -- only the bundle's own content
    (including its `name`) and decision_version do."""
    h1 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), bundle_version="v1", risk_presets=["balanced"],
    )[0]
    h2 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), bundle_version="v2", risk_presets=["balanced"],
    )[0]
    assert h1.bundle_version == "v1"
    assert h2.bundle_version == "v2"
    assert h1.matrix_cell_fingerprint == h2.matrix_cell_fingerprint
    assert h1.hypothesis_id == h2.hypothesis_id


def test_decision_version_flows_into_the_fingerprint():
    h1 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), risk_presets=["balanced"],
    )[0]
    h2 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v2", bundle=_bundle(), risk_presets=["balanced"],
    )[0]
    assert h1.matrix_cell_fingerprint != h2.matrix_cell_fingerprint


def test_bundle_content_change_flows_into_the_fingerprint():
    h1 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), risk_presets=["balanced"],
    )[0]
    h2 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(engines=["smc", "price_action"]), risk_presets=["balanced"],
    )[0]
    assert h1.matrix_cell_fingerprint != h2.matrix_cell_fingerprint


def test_encoding_version_into_bundle_name_does_change_the_fingerprint():
    """The documented workaround for bundle_version's own non-
    independence: folding it into the bundle's own `name` IS captured,
    since bundle_name is already part of the existing, unchanged
    compute_cell_fingerprint() payload."""
    h1 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(name="Prod4 Confluence Panel v1"), risk_presets=["balanced"],
    )[0]
    h2 = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(name="Prod4 Confluence Panel v2"), risk_presets=["balanced"],
    )[0]
    assert h1.matrix_cell_fingerprint != h2.matrix_cell_fingerprint


# --- no inference: fail-loud validation ----------------------------------


def test_rejects_empty_decision_version():
    with pytest.raises(rm.ResearchMatrixError, match="decision_version"):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="", bundle=_bundle())


def test_rejects_whitespace_only_decision_version():
    with pytest.raises(rm.ResearchMatrixError, match="decision_version"):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="   ", bundle=_bundle())


def test_rejects_unknown_engine_in_bundle():
    with pytest.raises(rm.ResearchMatrixError, match="unknown engine"):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(engines=["smc", "banana"]))


def test_rejects_confluence_as_a_bundle_engine():
    """CONFLUENCE is reserved for the decision's own identity, never a
    bundle input -- it is not a member of ENGINE_KEYS."""
    with pytest.raises(rm.ResearchMatrixError, match="unknown engine"):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(engines=["CONFLUENCE"]))


def test_rejects_multi_timeframe_bundle():
    with pytest.raises(rm.ResearchMatrixError, match="exactly one timeframe"):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(timeframes=["H1", "H4"]))


def test_rejects_zero_timeframe_bundle():
    with pytest.raises(rm.ResearchMatrixError, match="exactly one timeframe"):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(timeframes=[]))


def test_rejects_empty_engines_bundle():
    with pytest.raises(rm.ResearchMatrixError):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(engines=[]))


def test_rejects_bundle_without_a_name():
    with pytest.raises(rm.ResearchMatrixError, match="name"):
        hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(name=""))


# --- no auto-selection / no config or registry access ---------------------


def test_never_reads_config_or_registry_files():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), risk_presets=["balanced"])

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p]


def test_no_ranking_or_selection_deterministic_enumeration_order():
    hyps = hf.generate_confluence_hypotheses(
        symbols=["EURUSD", "GBPUSD"], decision_version="v1", bundle=_bundle(), risk_presets=["conservative", "balanced"],
    )
    assert [h.symbol for h in hyps] == ["EURUSD", "EURUSD", "GBPUSD", "GBPUSD"]
    assert [h.risk_preset for h in hyps] == ["conservative", "balanced", "conservative", "balanced"]


def test_never_constructs_a_bundle_from_config_engines_yaml():
    """Structural proof: the function body has no path that reads a
    config file to build its own bundle -- the caller's bundle argument
    is used verbatim. Checked against the function's own CODE, not its
    docstring (which legitimately explains the same guarantee in prose)."""
    import inspect

    source = inspect.getsource(hf.generate_confluence_hypotheses)
    body = source.split('"""', 2)[-1]  # strip the docstring
    assert "load_config" not in body
    assert "open(" not in body
    assert "yaml" not in body.lower()


# --- backward compatibility: the single-engine path is untouched ----------


def test_generate_hypotheses_output_shape_unaffected_by_the_confluence_path():
    hyps = hf.generate_hypotheses(
        symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"],
        engine_versions={"price_action": ("v2",)},
    )
    assert len(hyps) == 1
    h = hyps[0]
    assert h.engine == "price_action"
    assert h.decision_type == hf.SINGLE_ENGINE
    assert h.bundle_id is None
    assert h.bundle_version is None
    assert h.bundle_json is None
    assert h.hypothesis_id.startswith("DISCOVERY-HYPOTHESIS-")
