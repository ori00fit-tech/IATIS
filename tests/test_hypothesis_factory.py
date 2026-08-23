"""tests/test_hypothesis_factory.py -- pure-function tests for
backtest/hypothesis_factory.py (Hypothesis Discovery Engine, Phase 2)."""
from __future__ import annotations

import pytest

from backtest import hypothesis_factory as hf
from backtest import research_matrix as rm


def test_generate_hypotheses_basic_cartesian_count():
    # 2 symbols x (price_action: v1,v2 + smc: v1) x 2 timeframes x 1 preset = 12
    hyps = hf.generate_hypotheses(
        symbols=["EURUSD", "GBPUSD"], engines=["price_action", "smc"], timeframes=["H1", "H4"],
        risk_presets=["balanced"],
    )
    assert len(hyps) == 12


def test_generate_hypotheses_ids_are_unique():
    hyps = hf.generate_hypotheses(symbols=["EURUSD", "GBPUSD"], engines=["price_action", "smc"], timeframes=["H1", "H4"])
    ids = [h.hypothesis_id for h in hyps]
    assert len(ids) == len(set(ids))


def test_generate_hypotheses_id_format():
    hyps = hf.generate_hypotheses(symbols=["EURUSD"], engines=["smc"], timeframes=["H1"], risk_presets=["balanced"])
    assert len(hyps) == 1
    h = hyps[0]
    assert h.hypothesis_id == f"DISCOVERY-HYPOTHESIS-{h.matrix_cell_fingerprint}"


def test_generate_hypotheses_matches_generate_discovery_cells_fingerprints():
    """A hypothesis's own matrix_cell_fingerprint must be EXACTLY the
    fingerprint generate_discovery_cells() independently produces for the
    same (symbol, engine, version, timeframe, risk_preset) — the two must
    never drift apart, since a hypothesis's whole identity is its pointer
    to the cell that would test it."""
    hyps = hf.generate_hypotheses(symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"], engine_versions={"price_action": ("v2",)})
    cells = rm.generate_discovery_cells(symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"], engine_versions={"price_action": ("v2",)})
    assert len(hyps) == 1
    assert len(cells) == 1
    assert hyps[0].matrix_cell_fingerprint == cells[0].fingerprint


def test_generate_hypotheses_carries_correct_identity_fields():
    hyps = hf.generate_hypotheses(symbols=["EURUSD"], engines=["price_action"], timeframes=["H4"], risk_presets=["aggressive"], engine_versions={"price_action": ("v2",)})
    h = hyps[0]
    assert h.symbol == "EURUSD"
    assert h.engine == "price_action"
    assert h.engine_version == "v2"
    assert h.timeframe == "H4"
    assert h.risk_preset == "aggressive"


def test_generate_hypotheses_claim_mentions_the_combination():
    hyps = hf.generate_hypotheses(symbols=["EURUSD"], engines=["smc"], timeframes=["H1"], risk_presets=["balanced"])
    claim = hyps[0].claim
    assert "EURUSD" in claim
    assert "smc" in claim
    assert "H1" in claim
    assert "balanced" in claim


def test_generate_hypotheses_claim_never_states_a_specific_numeric_threshold():
    """The claim template must stay generic -- any actual bar a
    combination must clear belongs entirely to the existing Stage A/
    Bonferroni/Stage B machinery, never re-invented or hardcoded here."""
    hyps = hf.generate_hypotheses(symbols=["EURUSD"], engines=["smc"], timeframes=["H1"], risk_presets=["balanced"])
    claim = hyps[0].claim
    for forbidden in ("profit_factor", "PF >", "PF=", "win rate", "300 trades"):
        assert forbidden not in claim


def test_generate_hypotheses_never_ranks_scores_or_selects_a_best():
    """NON-NEGOTIABLE (operator's own explicit Phase 2 guardrail): this
    factory is a deterministic generator ONLY -- no Hypothesis field, and
    nothing in the returned list's ORDER, may express a rank, score, or
    'best' claim. Order must be exactly generate_discovery_cells()'s own
    deterministic enumeration order, and every hypothesis field must be a
    plain fact about the combination, never a verdict."""
    hyps = hf.generate_hypotheses(symbols=["EURUSD", "GBPUSD"], engines=["smc", "nnfx"], timeframes=["H1", "H4"])
    forbidden_keys = ("score", "rank", "verdict", "edge", "best", "winner", "priority")
    for h in hyps:
        for field_name in ("symbol", "engine", "engine_version", "timeframe", "risk_preset"):
            value = getattr(h, field_name)
            assert not any(fk in value.lower() for fk in forbidden_keys)


def test_generate_hypotheses_is_deterministic_for_identical_inputs():
    a = hf.generate_hypotheses(symbols=["EURUSD", "GBPUSD"], engines=["smc", "price_action"], timeframes=["H1", "H4"])
    b = hf.generate_hypotheses(symbols=["EURUSD", "GBPUSD"], engines=["smc", "price_action"], timeframes=["H1", "H4"])
    assert [h.hypothesis_id for h in a] == [h.hypothesis_id for h in b]


def test_generate_hypotheses_order_matches_generate_discovery_cells_order():
    hyps = hf.generate_hypotheses(symbols=["EURUSD", "GBPUSD"], engines=["smc", "nnfx"], timeframes=["H1", "H4"])
    cells = rm.generate_discovery_cells(symbols=["EURUSD", "GBPUSD"], engines=["smc", "nnfx"], timeframes=["H1", "H4"])
    assert [h.matrix_cell_fingerprint for h in hyps] == [c.fingerprint for c in cells]


def test_generate_hypotheses_reuses_generate_discovery_cells_validation():
    """No duplicated validation logic -- an unknown engine/timeframe/
    risk_preset is rejected via the exact same ResearchMatrixError
    generate_discovery_cells() itself raises."""
    with pytest.raises(rm.ResearchMatrixError):
        hf.generate_hypotheses(symbols=["EURUSD"], engines=["not_a_real_engine"], timeframes=["H1"])
    with pytest.raises(rm.ResearchMatrixError):
        hf.generate_hypotheses(symbols=["EURUSD"], engines=["smc"], timeframes=[])
    with pytest.raises(rm.ResearchMatrixError):
        hf.generate_hypotheses(symbols=["EURUSD"], engines=["smc"], timeframes=["H1"], risk_presets=["yolo"])


def test_generate_hypotheses_never_touches_config_or_registry():
    """A pure function with no D1/file access whatsoever, same RESEARCH-
    ONLY guarantee as every other function in this engine."""
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    hf.generate_hypotheses(symbols=["EURUSD"], engines=["smc", "price_action", "nnfx", "wyckoff"], timeframes=["M15", "H1", "H4", "D1"])

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after generate_hypotheses()"
