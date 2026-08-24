"""tests/test_hypothesis_execution_confluence.py -- tests for backtest/
hypothesis_execution.py's Phase 8B CONFLUENCE path
(build_confluence_execution_request()) and the identity-boundary guards
on both build_execution_request() and build_confluence_execution_request().
Hypothesis Discovery Engine, Phase 8B — Confluence Governed Identity."""
from __future__ import annotations

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_factory as hf


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


def _confluence_hyp_dict(**overrides) -> dict:
    hyps = hf.generate_confluence_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], decision_version=overrides.get("decision_version", "v1"),
        bundle=overrides.get("bundle", _bundle()), bundle_version=overrides.get("bundle_version"),
        risk_presets=[overrides.get("risk_preset", "balanced")],
    )
    h = hyps[0]
    return {
        "hypothesis_id": h.hypothesis_id, "symbol": h.symbol, "engine": h.engine,
        "engine_version": h.engine_version, "timeframe": h.timeframe, "risk_preset": h.risk_preset,
        "matrix_cell_fingerprint": h.matrix_cell_fingerprint, "decision_type": h.decision_type,
        "bundle_id": h.bundle_id, "bundle_version": h.bundle_version, "bundle_json": h.bundle_json,
    }


def _single_hyp_dict(**overrides) -> dict:
    hyps = hf.generate_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], engines=[overrides.get("engine", "price_action")],
        timeframes=[overrides.get("timeframe", "H1")], risk_presets=[overrides.get("risk_preset", "balanced")],
        engine_versions={overrides.get("engine", "price_action"): (overrides.get("engine_version", "v2"),)},
    )
    h = hyps[0]
    return {
        "hypothesis_id": h.hypothesis_id, "symbol": h.symbol, "engine": h.engine,
        "engine_version": h.engine_version, "timeframe": h.timeframe, "risk_preset": h.risk_preset,
        "matrix_cell_fingerprint": h.matrix_cell_fingerprint, "decision_type": h.decision_type,
    }


# --- basic binding -------------------------------------------------------


def test_build_confluence_execution_request_binds_one_hypothesis():
    h = _confluence_hyp_dict()
    req = he.build_confluence_execution_request([h], research_code_commit="abc123")
    assert req.hypothesis_ids == (h["hypothesis_id"],)
    assert len(req.cells) == 1
    assert req.cell_id_by_hypothesis[h["hypothesis_id"]] == req.cells[0].cell_id


def test_build_confluence_execution_request_binds_multiple_hypotheses():
    h1 = _confluence_hyp_dict(symbol="EURUSD")
    h2 = _confluence_hyp_dict(symbol="GBPUSD")
    req = he.build_confluence_execution_request([h1, h2], research_code_commit="abc123")
    assert set(req.hypothesis_ids) == {h1["hypothesis_id"], h2["hypothesis_id"]}
    assert len(req.cells) == 2


def test_build_confluence_execution_request_deduplicates_repeated_hypothesis_in_one_call():
    h = _confluence_hyp_dict()
    req = he.build_confluence_execution_request([h, h, h], research_code_commit="abc123")
    assert len(req.cells) == 1


def test_build_confluence_execution_request_real_cell_carries_the_stamped_commit():
    h = _confluence_hyp_dict()
    req = he.build_confluence_execution_request([h], research_code_commit="commit-xyz", data_provider="dukascopy")
    assert req.cells[0].research_code_commit == "commit-xyz"
    assert req.cells[0].data_provider == "dukascopy"
    assert req.cells[0].symbol == h["symbol"]
    assert req.cells[0].risk_preset == h["risk_preset"]
    assert req.cells[0].bundle["engines"] == _bundle()["engines"]


def test_build_confluence_execution_request_rejects_empty_list():
    with pytest.raises(he.HypothesisExecutionError, match="non-empty"):
        he.build_confluence_execution_request([])


# --- immutability / tamper detection --------------------------------------


def test_build_confluence_execution_request_rejects_a_tampered_bundle():
    h = _confluence_hyp_dict()
    import json

    bundle = json.loads(h["bundle_json"])
    bundle["engines"] = ["smc"]  # tamper: fewer engines than what was actually fingerprinted
    tampered = dict(h, bundle_json=json.dumps(bundle))
    with pytest.raises(he.HypothesisExecutionError, match="fingerprint mismatch"):
        he.build_confluence_execution_request([tampered])


def test_build_confluence_execution_request_rejects_missing_bundle_json():
    h = dict(_confluence_hyp_dict(), bundle_json=None)
    with pytest.raises(he.HypothesisExecutionError, match="bundle_json is missing"):
        he.build_confluence_execution_request([h])


def test_confluence_hypothesis_identity_immune_to_later_commit_changes():
    h = _confluence_hyp_dict()
    first = he.build_confluence_execution_request([h], research_code_commit="commit-A")
    second = he.build_confluence_execution_request([h], research_code_commit="commit-B")
    assert first.cells[0].symbol == second.cells[0].symbol == h["symbol"]
    assert first.cells[0].bundle["name"] == second.cells[0].bundle["name"]


# --- idempotency / non-collision (same two scenarios as SINGLE_ENGINE) ---


def test_same_confluence_hypothesis_same_commit_is_idempotent():
    h = _confluence_hyp_dict()
    req1 = he.build_confluence_execution_request([h], research_code_commit="commit-A")
    req2 = he.build_confluence_execution_request([h], research_code_commit="commit-A")
    assert req1.cell_id_by_hypothesis[h["hypothesis_id"]] == req2.cell_id_by_hypothesis[h["hypothesis_id"]]


def test_same_confluence_hypothesis_different_commit_does_not_collide():
    h = _confluence_hyp_dict()
    req_a = he.build_confluence_execution_request([h], research_code_commit="commit-A")
    req_b = he.build_confluence_execution_request([h], research_code_commit="commit-B")
    assert req_a.cell_id_by_hypothesis[h["hypothesis_id"]] != req_b.cell_id_by_hypothesis[h["hypothesis_id"]]


# --- identity-boundary guards (the critical Phase 8B addition) ------------


def test_build_execution_request_rejects_a_confluence_hypothesis():
    h = _confluence_hyp_dict()
    with pytest.raises(he.HypothesisExecutionError, match="not 'SINGLE_ENGINE'"):
        he.build_execution_request([h])


def test_build_confluence_execution_request_rejects_a_single_engine_hypothesis():
    h = _single_hyp_dict()
    with pytest.raises(he.HypothesisExecutionError, match="not 'CONFLUENCE'"):
        he.build_confluence_execution_request([h])


def test_build_confluence_execution_request_rejects_a_mixed_batch():
    """A batch containing even one wrong-type hypothesis must refuse the
    WHOLE call — never a partial bind."""
    confluence_h = _confluence_hyp_dict()
    single_h = _single_hyp_dict()
    with pytest.raises(he.HypothesisExecutionError, match="not 'CONFLUENCE'"):
        he.build_confluence_execution_request([confluence_h, single_h])


def test_build_execution_request_rejects_a_mixed_batch():
    confluence_h = _confluence_hyp_dict()
    single_h = _single_hyp_dict()
    with pytest.raises(he.HypothesisExecutionError, match="not 'SINGLE_ENGINE'"):
        he.build_execution_request([single_h, confluence_h])


def test_single_engine_hypothesis_missing_decision_type_key_still_treated_as_single_engine():
    """Backward compatibility: a pre-Phase-8B hypothesis dict (from before
    the decision_type column existed) has no 'decision_type' key at all —
    build_execution_request() must still accept it, defaulting to
    SINGLE_ENGINE, never raising KeyError."""
    h = _single_hyp_dict()
    del h["decision_type"]
    req = he.build_execution_request([h], research_code_commit="abc")
    assert len(req.cells) == 1


# --- reuse, never rebuild --------------------------------------------------


def test_build_confluence_execution_request_reuses_generate_matrix_cells():
    import inspect

    source = inspect.getsource(he.build_confluence_execution_request)
    assert "generate_matrix_cells(" in source
    assert "generate_discovery_cells(" not in source


def test_no_new_cartesian_product_logic_in_confluence_helpers():
    import inspect

    source = inspect.getsource(he)
    # neither confluence helper re-implements bundle validation itself
    assert "_validate_bundle" not in source


# --- end-to-end: binding through to real D1 persistence ---------------------


def _persist_new_family(req: "he.ExecutionRequest", family_id: str, planned_n: int | None = None):
    from storage import research_matrix as storage

    storage.upsert_family(family_id, planned_n=planned_n or len(req.cells), family_alpha=0.05)
    return storage.upsert_cells(list(req.cells), family_id, source_hypothesis_ids=req.source_hypothesis_ids_by_cell)


def test_bound_confluence_hypothesis_persists_as_a_queued_cell_with_provenance():
    from storage import research_matrix as storage

    h = _confluence_hyp_dict()
    req = he.build_confluence_execution_request([h], research_code_commit="abc123")
    result = _persist_new_family(req, "fam-confluence-exec-1")
    assert result == {"inserted": 1, "duplicate": 0}

    cell_id = req.cell_id_by_hypothesis[h["hypothesis_id"]]
    row = storage.get_cell(cell_id)
    assert row["status"] == "QUEUED"
    assert row["source_hypothesis_id"] == h["hypothesis_id"]
    assert row["symbol"] == h["symbol"]
    # Phase 1's own denormalized identity columns stay NULL for a
    # multi-engine confluence bundle -- single_engine_identity() correctly
    # returns None for it, exactly as it already does for every
    # pre-existing confluence-research cell.
    assert row["engine"] is None
    assert row["engine_version"] is None
    assert row["timeframe"] is None
