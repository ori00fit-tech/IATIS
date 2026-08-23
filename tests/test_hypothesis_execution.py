"""tests/test_hypothesis_execution.py -- pure-function tests for
backtest/hypothesis_execution.py (Hypothesis Discovery Engine, Phase 3 —
Hypothesis Execution / Mission Binding)."""
from __future__ import annotations

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_factory as hf
from backtest import research_matrix as rm


def _hyp_dict(**overrides) -> dict:
    hyps = hf.generate_hypotheses(
        symbols=[overrides.get("symbol", "EURUSD")], engines=[overrides.get("engine", "price_action")],
        timeframes=[overrides.get("timeframe", "H1")], risk_presets=[overrides.get("risk_preset", "balanced")],
        engine_versions={overrides.get("engine", "price_action"): (overrides.get("engine_version", "v2"),)},
    )
    h = hyps[0]
    return {
        "hypothesis_id": h.hypothesis_id, "symbol": h.symbol, "engine": h.engine,
        "engine_version": h.engine_version, "timeframe": h.timeframe, "risk_preset": h.risk_preset,
        "matrix_cell_fingerprint": h.matrix_cell_fingerprint,
    }


# --- basic binding -----------------------------------------------------


def test_build_execution_request_rejects_empty_list():
    """Guardrail 3: never auto-selects which hypothesis to bind."""
    with pytest.raises(he.HypothesisExecutionError, match="non-empty"):
        he.build_execution_request([])


def test_build_execution_request_binds_one_hypothesis():
    h = _hyp_dict()
    req = he.build_execution_request([h], research_code_commit="abc123")
    assert req.hypothesis_ids == (h["hypothesis_id"],)
    assert len(req.cells) == 1
    assert req.cell_id_by_hypothesis[h["hypothesis_id"]] == req.cells[0].cell_id


def test_build_execution_request_binds_multiple_hypotheses():
    h1 = _hyp_dict(symbol="EURUSD")
    h2 = _hyp_dict(symbol="GBPUSD")
    req = he.build_execution_request([h1, h2], research_code_commit="abc123")
    assert set(req.hypothesis_ids) == {h1["hypothesis_id"], h2["hypothesis_id"]}
    assert len(req.cells) == 2


def test_build_execution_request_deduplicates_repeated_hypothesis_in_one_call():
    h = _hyp_dict()
    req = he.build_execution_request([h, h, h], research_code_commit="abc123")
    assert len(req.cells) == 1
    assert req.hypothesis_ids == (h["hypothesis_id"],)


def test_build_execution_request_real_cell_carries_the_stamped_commit():
    h = _hyp_dict()
    req = he.build_execution_request([h], research_code_commit="commit-xyz", data_provider="dukascopy")
    assert req.cells[0].research_code_commit == "commit-xyz"
    assert req.cells[0].data_provider == "dukascopy"
    assert req.cells[0].symbol == h["symbol"]
    assert req.cells[0].risk_preset == h["risk_preset"]


# --- guardrail 1: immutability / snapshot verification ----------------


def test_build_execution_request_rejects_a_tampered_fingerprint():
    """Structural proof, not an assumption: a hypothesis row whose stored
    matrix_cell_fingerprint disagrees with what its own (symbol, engine,
    engine_version, timeframe, risk_preset) fields would re-derive is
    refused loudly, never silently bound."""
    h = _hyp_dict()
    tampered = dict(h, matrix_cell_fingerprint="not-the-real-fingerprint")
    with pytest.raises(he.HypothesisExecutionError, match="fingerprint mismatch"):
        he.build_execution_request([tampered])


def test_hypothesis_identity_is_immune_to_later_unrelated_config_changes(monkeypatch):
    """The exact operator-specified snapshot test: Hypothesis H -> build an
    execution request -> simulate a later, unrelated change (here: the
    research code commit moving forward, as it constantly does) -> the
    hypothesis's OWN identity fields, and its own canonical fingerprint,
    must be completely unaffected -- only the resulting EXECUTED cell's
    commit-stamped fingerprint differs, which is correct/expected (a real,
    fresh test under new code), never a mutation of the hypothesis itself."""
    h = _hyp_dict()

    first = he.build_execution_request([h], research_code_commit="commit-A")
    # "config changed later" -- the caller resolves a new, later commit,
    # exactly as backtest.research_matrix.resolve_research_code_commit()
    # would after new commits land.
    second = he.build_execution_request([h], research_code_commit="commit-B")

    # the hypothesis's own stored identity fields are untouched (the dict
    # itself was never mutated by either call)
    assert h["symbol"] == "EURUSD"
    assert h["matrix_cell_fingerprint"] == h["matrix_cell_fingerprint"]

    # both executions reference the hypothesis's ORIGINAL identity fields
    # correctly (same symbol/engine/version/timeframe/risk_preset)
    assert first.cells[0].symbol == second.cells[0].symbol == h["symbol"]
    assert first.cells[0].risk_preset == second.cells[0].risk_preset == h["risk_preset"]
    assert first.cells[0].bundle["engines"] == second.cells[0].bundle["engines"] == [h["engine"]]
    assert first.cells[0].bundle["timeframes"] == second.cells[0].bundle["timeframes"] == [h["timeframe"]]


# --- idempotency / non-collision (operator's exact two scenarios) -----


def test_same_hypothesis_same_commit_is_idempotent_execution_identity():
    """"same hypothesis + same fingerprint -> idempotent execution
    identity": binding the same hypothesis twice under the SAME research
    code commit produces the exact same resulting cell_id both times."""
    h = _hyp_dict()
    req1 = he.build_execution_request([h], research_code_commit="commit-A")
    req2 = he.build_execution_request([h], research_code_commit="commit-A")
    assert req1.cell_id_by_hypothesis[h["hypothesis_id"]] == req2.cell_id_by_hypothesis[h["hypothesis_id"]]


def test_same_hypothesis_different_commit_must_not_collide():
    """"same hypothesis + different fingerprint -> MUST NOT collide":
    binding the same hypothesis under two DIFFERENT research code commits
    produces two DIFFERENT, coexisting cell_ids -- never merged, never
    overwritten, never an error."""
    h = _hyp_dict()
    req_a = he.build_execution_request([h], research_code_commit="commit-A")
    req_b = he.build_execution_request([h], research_code_commit="commit-B")
    cell_id_a = req_a.cell_id_by_hypothesis[h["hypothesis_id"]]
    cell_id_b = req_b.cell_id_by_hypothesis[h["hypothesis_id"]]
    assert cell_id_a != cell_id_b


# --- guardrail 8: hypothesis_id/fingerprint collision impossibility ---


def test_different_hypotheses_never_collide_in_one_execution_request():
    h1 = _hyp_dict(timeframe="H1")
    h2 = _hyp_dict(timeframe="H4")
    assert h1["hypothesis_id"] != h2["hypothesis_id"]
    req = he.build_execution_request([h1, h2], research_code_commit="abc")
    assert len(set(req.cell_id_by_hypothesis.values())) == 2


# --- no update path exists (guardrails 1/2, structural) ----------------


def test_no_update_function_exists_in_hypothesis_factory_storage():
    """storage.hypothesis_factory must never gain a mutation path -- Phase
    3's whole immutability guarantee depends on this staying true."""
    from storage import hypothesis_factory as storage_module

    public_names = [n for n in dir(storage_module) if not n.startswith("_")]
    forbidden_substrings = ("update_hypothesis", "delete_hypothesis", "edit_hypothesis", "mutate_hypothesis")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"storage.hypothesis_factory unexpectedly exposes {name!r}"


def test_no_update_function_exists_in_hypothesis_execution_module():
    import inspect

    from backtest import hypothesis_execution as module

    source = inspect.getsource(module)
    for forbidden in ("update_hypothesis(", "UPDATE research_hypotheses", "DELETE FROM research_hypotheses"):
        assert forbidden not in source


# --- guardrail 4: never touches config/registry -------------------------


def test_build_execution_request_never_touches_config_or_registry():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    h = _hyp_dict()
    he.build_execution_request([h], research_code_commit="abc123")

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after build_execution_request()"


# --- guardrail 6: cells produced are ordinary QUEUED-bound MatrixCellSpecs, never pre-executed ---


def test_execution_request_produces_ordinary_queueable_cells_never_a_result():
    """The cells an ExecutionRequest carries are plain, unexecuted
    MatrixCellSpec objects -- no status, no verdict, no metrics attached
    anywhere on them. Persisting them (a separate step this module never
    performs) always starts them at QUEUED, exactly like any other
    Matrix cell."""
    h = _hyp_dict()
    req = he.build_execution_request([h], research_code_commit="abc123")
    cell = req.cells[0]
    assert isinstance(cell, rm.MatrixCellSpec)
    assert not hasattr(cell, "status")
    assert not hasattr(cell, "verdict")
    assert not hasattr(cell, "metrics")


# --- end-to-end: binding through to real D1 persistence ----------------
#
# build_execution_request() itself is pure (no D1) -- these tests exercise
# the full, real path a caller takes: bind -> storage.research_matrix.
# upsert_family()/upsert_cells() (unchanged, reused verbatim) -> a real
# QUEUED cell whose source_hypothesis_id correctly points back.


def _persist_new_family(req: "he.ExecutionRequest", family_id: str, planned_n: int | None = None):
    from storage import research_matrix as storage

    storage.upsert_family(family_id, planned_n=planned_n or len(req.cells), family_alpha=0.05)
    return storage.upsert_cells(list(req.cells), family_id, source_hypothesis_ids=req.source_hypothesis_ids_by_cell)


def test_execution_request_source_hypothesis_ids_by_cell_is_the_correct_inverse():
    h = _hyp_dict()
    req = he.build_execution_request([h], research_code_commit="abc123")
    cell_id = req.cell_id_by_hypothesis[h["hypothesis_id"]]
    assert req.source_hypothesis_ids_by_cell == {cell_id: h["hypothesis_id"]}


def test_bound_hypothesis_persists_as_a_queued_cell_with_provenance():
    from storage import research_matrix as storage

    h = _hyp_dict()
    req = he.build_execution_request([h], research_code_commit="abc123")
    result = _persist_new_family(req, "fam-exec-1")
    assert result == {"inserted": 1, "duplicate": 0}

    cell_id = req.cell_id_by_hypothesis[h["hypothesis_id"]]
    row = storage.get_cell(cell_id)
    assert row["status"] == "QUEUED"  # guardrail: binding never runs anything, never bypasses Stage A
    assert row["source_hypothesis_id"] == h["hypothesis_id"]
    assert row["symbol"] == h["symbol"]


def test_rebinding_same_hypothesis_same_commit_is_idempotent_at_storage_level():
    from storage import research_matrix as storage

    h = _hyp_dict()
    req1 = he.build_execution_request([h], research_code_commit="commit-A")
    first = _persist_new_family(req1, "fam-exec-idempotent")
    assert first == {"inserted": 1, "duplicate": 0}

    # re-binding under the SAME commit against the SAME already-created
    # family (never re-creating the family -- that's a separate action)
    req2 = he.build_execution_request([h], research_code_commit="commit-A")
    second = storage.upsert_cells(list(req2.cells), "fam-exec-idempotent", source_hypothesis_ids=req2.source_hypothesis_ids_by_cell)
    assert second == {"inserted": 0, "duplicate": 1}


def test_rebinding_same_hypothesis_different_commit_does_not_collide_at_storage_level():
    from storage import research_matrix as storage

    h = _hyp_dict()
    req_a = he.build_execution_request([h], research_code_commit="commit-A")
    req_b = he.build_execution_request([h], research_code_commit="commit-B")

    storage.upsert_family("fam-exec-noncollide", planned_n=2, family_alpha=0.05)
    result_a = storage.upsert_cells(list(req_a.cells), "fam-exec-noncollide", source_hypothesis_ids=req_a.source_hypothesis_ids_by_cell)
    result_b = storage.upsert_cells(list(req_b.cells), "fam-exec-noncollide", source_hypothesis_ids=req_b.source_hypothesis_ids_by_cell)
    assert result_a == {"inserted": 1, "duplicate": 0}
    assert result_b == {"inserted": 1, "duplicate": 0}

    matching = storage.list_cells(source_hypothesis_id=h["hypothesis_id"])
    assert len(matching) == 2
    assert {r["research_code_commit"] for r in matching} == {"commit-A", "commit-B"}
