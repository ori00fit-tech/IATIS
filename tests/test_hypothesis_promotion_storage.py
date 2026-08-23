"""tests/test_hypothesis_promotion_storage.py -- D1 round-trip,
identity-chain, and fail-closed governance tests for backtest.
hypothesis_promotion.evaluate_promotion()/record_promotion() +
storage/hypothesis_promotion.py (Hypothesis Discovery Engine, Phase 5 —
Symbol-Scoped Promotion Gate).

Exercises the FULL, real path: Hypothesis (persisted) -> Mission (Phase
4) -> QUEUED Matrix Cell -> the cell's governance state advanced via the
SAME storage.research_matrix.update_cell() the real orchestrator uses ->
evaluate_promotion()/record_promotion() reading and deciding from that
real, persisted state -- plus the BUG-001-style identity/scope
verification (reports/forensic/13_CONFIRMED_BUGS.md) that must FAIL HARD
before any decision is ever produced."""
from __future__ import annotations

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from backtest import hypothesis_promotion as hp
from storage import hypothesis_factory as hf_storage
from storage import hypothesis_promotion as hp_storage
from storage import research_matrix as rm_storage


def _persist_hypothesis(**overrides) -> hf.Hypothesis:
    symbol = overrides.get("symbol", "EURUSD")
    engine = overrides.get("engine", "price_action")
    hyps = hf.generate_hypotheses(
        symbols=[symbol], engines=[engine], timeframes=[overrides.get("timeframe", "H1")],
        risk_presets=[overrides.get("risk_preset", "balanced")],
        engine_versions={engine: (overrides.get("engine_version", "v2"),)},
    )
    h = hyps[0]
    hf_storage.record_hypotheses([h])
    return h


def _fresh_triple(**overrides):
    """A real Hypothesis -> Mission -> freshly-QUEUED Matrix Cell chain,
    exactly as Phase 4's record_mission() produces it. The returned cell
    is QUEUED (non-terminal) -- tests move it to whatever governance
    state they need directly via storage.research_matrix.update_cell(),
    since that function only guards against re-touching an ALREADY
    terminal cell, never enforces a specific transition order."""
    h = _persist_hypothesis(**overrides)
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    cell_id = result["bindings"][0]["cell_id"]
    return h, result["mission_id"], cell_id


# --- decision states, driven through the real pipeline's own storage -----


def test_promoted_when_cell_is_validated_and_confirmed():
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")

    result = hp.evaluate_promotion(h.hypothesis_id, mission_id, cell_id)
    assert result["decision"] == hp.PROMOTED
    assert result["symbol"] == h.symbol


def test_not_promoted_when_cell_is_rejected():
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="REJECTED", rejection_reason="did not survive Matrix Family correction")

    result = hp.evaluate_promotion(h.hypothesis_id, mission_id, cell_id)
    assert result["decision"] == hp.NOT_PROMOTED


def test_blocked_when_cell_is_still_queued():
    h, mission_id, cell_id = _fresh_triple()
    result = hp.evaluate_promotion(h.hypothesis_id, mission_id, cell_id)
    assert result["decision"] == hp.BLOCKED


def test_blocked_when_cell_is_candidate_awaiting_stage_b():
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="CANDIDATE", stage_a_p_value=0.0001)
    result = hp.evaluate_promotion(h.hypothesis_id, mission_id, cell_id)
    assert result["decision"] == hp.BLOCKED


def test_blocked_when_cell_is_insufficient_data():
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="INSUFFICIENT_DATA", rejection_reason="zero R-multiple variance")
    result = hp.evaluate_promotion(h.hypothesis_id, mission_id, cell_id)
    assert result["decision"] == hp.BLOCKED


# --- BUG-001-style identity/scope verification (fail hard) ---------------


def test_evaluate_promotion_rejects_a_hypothesis_never_bound_into_the_named_mission():
    h1, mission_id_1, cell_id_1 = _fresh_triple(symbol="EURUSD")
    h2, _mission_id_2, _cell_id_2 = _fresh_triple(symbol="GBPUSD")

    with pytest.raises(he.HypothesisExecutionError, match="not bound into mission"):
        hp.evaluate_promotion(h2.hypothesis_id, mission_id_1, cell_id_1)


def test_evaluate_promotion_rejects_a_mismatched_cell_id():
    h1, mission_id_1, cell_id_1 = _fresh_triple(symbol="EURUSD")
    _h2, _mission_id_2, cell_id_2 = _fresh_triple(symbol="GBPUSD")

    with pytest.raises(he.HypothesisExecutionError, match="does not match the cell"):
        hp.evaluate_promotion(h1.hypothesis_id, mission_id_1, cell_id_2)


def test_evaluate_promotion_rejects_unknown_hypothesis_id():
    _h, mission_id, cell_id = _fresh_triple()
    with pytest.raises(he.HypothesisExecutionError, match="unknown hypothesis_id"):
        hp.evaluate_promotion("DISCOVERY-HYPOTHESIS-ghost", mission_id, cell_id)


def test_evaluate_promotion_rejects_unknown_mission_id():
    h, _mission_id, cell_id = _fresh_triple()
    with pytest.raises(he.HypothesisExecutionError, match="unknown mission_id"):
        hp.evaluate_promotion(h.hypothesis_id, "HYPOTHESIS-MISSION-ghost", cell_id)


def test_evaluate_promotion_rejects_unknown_cell_id():
    h, mission_id, _cell_id = _fresh_triple()
    with pytest.raises(he.HypothesisExecutionError, match="does not match the cell"):
        hp.evaluate_promotion(h.hypothesis_id, mission_id, "MATRIX-CELL-ghost")


def test_evaluate_promotion_rejects_a_cross_symbol_scope_tamper(fake_d1):
    """The direct structural analogue of BUG-001: if a Matrix Cell's own
    symbol were ever made to disagree with the Hypothesis it is bound to
    (impossible through the normal record_mission() path, so simulated
    here by tampering the persisted row directly, the same way a real
    data-corruption or client bug would surface it), evaluate_promotion()
    must FAIL HARD -- never silently accept the mismatched scope and
    report a verdict as if it were legitimate."""
    h, mission_id, cell_id = _fresh_triple(symbol="EURUSD")
    fake_d1.execute("UPDATE research_matrix_cells SET symbol=? WHERE cell_id=?", ("GBPUSD", cell_id))
    fake_d1.commit()

    with pytest.raises(he.HypothesisExecutionError, match="symbol scope mismatch"):
        hp.evaluate_promotion(h.hypothesis_id, mission_id, cell_id)


def test_evaluate_promotion_rejects_a_tampered_binding_fingerprint(fake_d1):
    """The Mission binding's own recorded hypothesis_fingerprint (stamped
    once, at record_mission() time, from the hypothesis's real stored
    fingerprint) must still agree with that hypothesis's CURRENT stored
    fingerprint -- simulated here via direct row tampering, the same way
    a real data-corruption or client bug would surface it."""
    h, mission_id, cell_id = _fresh_triple()
    fake_d1.execute(
        "UPDATE research_hypothesis_mission_bindings SET hypothesis_fingerprint=? WHERE hypothesis_id=?",
        ("not-the-real-fingerprint", h.hypothesis_id),
    )
    fake_d1.commit()

    with pytest.raises(he.HypothesisExecutionError, match="does not match the fingerprint"):
        hp.evaluate_promotion(h.hypothesis_id, mission_id, cell_id)


def test_evaluate_promotion_rejects_when_cells_source_hypothesis_id_disagrees(fake_d1):
    h1, mission_id_1, cell_id_1 = _fresh_triple(symbol="EURUSD")
    fake_d1.execute("UPDATE research_matrix_cells SET source_hypothesis_id=? WHERE cell_id=?", ("DISCOVERY-HYPOTHESIS-someone-else", cell_id_1))
    fake_d1.commit()

    with pytest.raises(he.HypothesisExecutionError, match="source_hypothesis_id"):
        hp.evaluate_promotion(h1.hypothesis_id, mission_id_1, cell_id_1)


# --- no partial / no side effects on a rejected identity chain -----------


def test_no_promotion_record_or_cell_mutation_survives_a_rejected_identity_chain():
    h1, mission_id_1, cell_id_1 = _fresh_triple(symbol="EURUSD")
    h2, _mission_id_2, _cell_id_2 = _fresh_triple(symbol="GBPUSD")
    before = rm_storage.get_cell(cell_id_1)

    with pytest.raises(he.HypothesisExecutionError):
        hp.record_promotion(h2.hypothesis_id, mission_id_1, cell_id_1)

    assert hp_storage.list_promotions() == []
    assert rm_storage.get_cell(cell_id_1) == before  # Promotion never mutates the Matrix Cell


# --- record_promotion(): persistence, idempotency, history ---------------


def test_record_promotion_persists_a_promoted_decision():
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")

    result = hp.record_promotion(h.hypothesis_id, mission_id, cell_id, created_by="ops")
    assert result["created"] is True
    assert result["decision"] == hp.PROMOTED
    assert result["promotion_id"].startswith("PROMOTION-")

    fetched = hp_storage.get_promotion(result["promotion_id"])
    assert fetched["decision"] == hp.PROMOTED
    assert fetched["hypothesis_id"] == h.hypothesis_id
    assert fetched["mission_id"] == mission_id
    assert fetched["cell_id"] == cell_id
    assert fetched["symbol"] == h.symbol
    assert fetched["governance_snapshot"]["cell_status"] == "VALIDATED"


def test_record_promotion_never_mutates_the_matrix_cell():
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="REJECTED", rejection_reason="did not survive correction")
    before = rm_storage.get_cell(cell_id)

    hp.record_promotion(h.hypothesis_id, mission_id, cell_id)

    assert rm_storage.get_cell(cell_id) == before


def test_record_promotion_is_idempotent_for_unchanged_governance_state():
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="REJECTED", rejection_reason="did not survive correction")

    first = hp.record_promotion(h.hypothesis_id, mission_id, cell_id)
    second = hp.record_promotion(h.hypothesis_id, mission_id, cell_id)

    assert first["promotion_id"] == second["promotion_id"]
    assert first["created"] is True
    assert second["created"] is False
    assert len(hp_storage.list_promotions_for_hypothesis(h.hypothesis_id)) == 1


def test_a_real_governance_state_change_produces_a_new_coexisting_promotion_record():
    """BLOCKED while Stage B is pending, later superseded by PROMOTED once
    Stage B confirms -- both decisions stay in the append-only ledger,
    never an overwrite of the earlier (correctly incomplete, at the time)
    BLOCKED record."""
    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="CANDIDATE", stage_a_p_value=0.0001)
    first = hp.record_promotion(h.hypothesis_id, mission_id, cell_id)
    assert first["decision"] == hp.BLOCKED

    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    second = hp.record_promotion(h.hypothesis_id, mission_id, cell_id)
    assert second["decision"] == hp.PROMOTED

    assert first["promotion_id"] != second["promotion_id"]
    history = hp_storage.list_promotions_for_hypothesis(h.hypothesis_id)
    assert {r["promotion_id"] for r in history} == {first["promotion_id"], second["promotion_id"]}


def test_symbol_scoped_promotion_does_not_extend_to_a_different_symbol():
    """One symbol's PROMOTED decision must never be readable as if it
    applied to another symbol's own (different) hypothesis/cell -- each
    promotion record is scoped to its own cell_id/symbol, never merged or
    generalized across symbols."""
    h_eur, mission_eur, cell_eur = _fresh_triple(symbol="EURUSD")
    h_gbp, mission_gbp, cell_gbp = _fresh_triple(symbol="GBPUSD")
    rm_storage.update_cell(cell_eur, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    rm_storage.update_cell(cell_gbp, status="REJECTED", rejection_reason="did not survive correction")

    promoted = hp.record_promotion(h_eur.hypothesis_id, mission_eur, cell_eur)
    rejected = hp.record_promotion(h_gbp.hypothesis_id, mission_gbp, cell_gbp)

    assert promoted["decision"] == hp.PROMOTED
    assert promoted["symbol"] == "EURUSD"
    assert rejected["decision"] == hp.NOT_PROMOTED
    assert rejected["symbol"] == "GBPUSD"
    eur_promotions = hp_storage.list_promotions(symbol="EURUSD")
    assert all(p["symbol"] == "EURUSD" for p in eur_promotions)


# --- forensic chain / reverse lookups --------------------------------------


def test_forensic_chain_from_a_promotion_id_back_to_mission_and_hypothesis():
    h, mission_id, cell_id = _fresh_triple(symbol="XAUUSD", engine="wyckoff", timeframe="H4")
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    result = hp.record_promotion(h.hypothesis_id, mission_id, cell_id, created_by="ops")

    record = hp_storage.get_promotion(result["promotion_id"])
    assert record["hypothesis_id"] == h.hypothesis_id
    assert record["hypothesis_fingerprint"] == h.matrix_cell_fingerprint
    assert record["mission_id"] == mission_id
    assert record["cell_id"] == cell_id
    assert record["symbol"] == "XAUUSD"
    assert record["created_by"] == "ops"

    by_hypothesis = hp_storage.list_promotions_for_hypothesis(h.hypothesis_id)
    assert record["promotion_id"] in {r["promotion_id"] for r in by_hypothesis}

    by_mission = hp_storage.list_promotions_for_mission(mission_id)
    assert record["promotion_id"] in {r["promotion_id"] for r in by_mission}


def test_list_promotions_filters_by_decision():
    h1, m1, c1 = _fresh_triple(symbol="EURUSD")
    h2, m2, c2 = _fresh_triple(symbol="GBPUSD")
    rm_storage.update_cell(c1, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    rm_storage.update_cell(c2, status="REJECTED", rejection_reason="no")
    hp.record_promotion(h1.hypothesis_id, m1, c1)
    hp.record_promotion(h2.hypothesis_id, m2, c2)

    promoted_only = hp_storage.list_promotions(decision=hp.PROMOTED)
    assert all(p["decision"] == hp.PROMOTED for p in promoted_only)
    assert any(p["cell_id"] == c1 for p in promoted_only)
    assert not any(p["cell_id"] == c2 for p in promoted_only)


# --- indexes / config isolation --------------------------------------------


def test_promotion_table_indexes_exist(fake_d1):
    hp_storage.list_promotions()  # triggers _init(con)
    idx_names = {r[0] for r in fake_d1.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert {"idx_rhp_hypothesis", "idx_rhp_mission", "idx_rhp_cell", "idx_rhp_symbol", "idx_rhp_decision"} <= idx_names


def test_record_promotion_never_touches_config_or_registry():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    h, mission_id, cell_id = _fresh_triple()
    rm_storage.update_cell(cell_id, status="VALIDATED", stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    hp.record_promotion(h.hypothesis_id, mission_id, cell_id)

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after record_promotion()"
