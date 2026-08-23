"""tests/test_hypothesis_mission_storage.py -- D1 round-trip and
orchestration tests for backtest.hypothesis_mission.record_mission() +
storage/hypothesis_mission.py (Hypothesis Discovery Engine, Phase 4 —
Mission Center Integration).

These exercise the FULL, real path: Hypothesis (persisted) -> record_
mission() -> ExecutionRequest (re-verified) -> storage.research_matrix.
upsert_family()/upsert_cells() (unchanged) -> QUEUED cell + Mission
bookkeeping row + binding row -- and the forensic chain back through all
of them."""
from __future__ import annotations

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_factory as hf
from backtest import hypothesis_mission as hm
from storage import hypothesis_factory as hf_storage
from storage import hypothesis_mission as hm_storage
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


# --- basic Mission creation -----------------------------------------------


def test_record_mission_creates_family_and_queued_cell_with_bindings():
    h = _persist_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A", data_provider="dukascopy")

    assert result["created"] is True
    assert result["mission_id"].startswith("HYPOTHESIS-MISSION-")
    assert len(result["bindings"]) == 1
    binding = result["bindings"][0]
    assert binding["hypothesis_id"] == h.hypothesis_id
    assert binding["hypothesis_fingerprint"] == h.matrix_cell_fingerprint

    family = rm_storage.get_family(result["family_id"])
    assert family is not None
    assert family["planned_n"] == 1

    cell = rm_storage.get_cell(binding["cell_id"])
    assert cell is not None
    assert cell["status"] == "QUEUED"
    assert cell["source_hypothesis_id"] == h.hypothesis_id
    assert cell["symbol"] == h.symbol
    assert cell["research_code_commit"] == "commit-A"


def test_record_mission_binds_multiple_hypotheses_into_one_mission():
    h1 = _persist_hypothesis(symbol="EURUSD")
    h2 = _persist_hypothesis(symbol="GBPUSD")
    result = hm.record_mission([h1.hypothesis_id, h2.hypothesis_id], research_code_commit="commit-A")

    assert result["created"] is True
    assert len(result["bindings"]) == 2
    family = rm_storage.get_family(result["family_id"])
    assert family["planned_n"] == 2

    cell_ids = {b["cell_id"] for b in result["bindings"]}
    assert len(cell_ids) == 2
    for cell_id in cell_ids:
        assert rm_storage.get_cell(cell_id)["status"] == "QUEUED"


def test_record_mission_deduplicates_repeated_hypothesis_id_in_one_call():
    h = _persist_hypothesis()
    result = hm.record_mission([h.hypothesis_id, h.hypothesis_id], research_code_commit="commit-A")
    assert len(result["bindings"]) == 1
    family = rm_storage.get_family(result["family_id"])
    assert family["planned_n"] == 1


# --- no orphan execution: unknown id refused -------------------------------


def test_record_mission_refuses_unknown_hypothesis_id_and_persists_nothing():
    with pytest.raises(he.HypothesisExecutionError, match="unknown hypothesis_id"):
        hm.record_mission(["DISCOVERY-HYPOTHESIS-ghost"], research_code_commit="commit-A")

    assert rm_storage.list_families() == []


def test_record_mission_refuses_when_any_id_in_a_mixed_list_is_unknown():
    """A real, persisted hypothesis alongside a fabricated id must refuse
    the WHOLE Mission -- never a partial bind."""
    h = _persist_hypothesis()
    with pytest.raises(he.HypothesisExecutionError, match="unknown hypothesis_id"):
        hm.record_mission([h.hypothesis_id, "DISCOVERY-HYPOTHESIS-ghost"], research_code_commit="commit-A")

    # the real hypothesis's own cell must not have been queued either --
    # refusing is all-or-nothing, not best-effort.
    assert rm_storage.list_families() == []


# --- idempotency / non-collision (same two scenarios as Phase 3) ----------


def test_record_mission_is_idempotent_for_the_same_inputs():
    h = _persist_hypothesis()
    first = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A", data_provider="dukascopy")
    second = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A", data_provider="dukascopy")

    assert first["mission_id"] == second["mission_id"]
    assert first["family_id"] == second["family_id"]
    assert first["created"] is True
    assert second["created"] is False
    assert second["bindings"] == first["bindings"]

    # no duplicate cell was queued
    assert len(rm_storage.list_cells(family_id=first["family_id"])) == 1


def test_record_mission_different_commit_produces_a_different_coexisting_mission():
    h = _persist_hypothesis()
    a = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")
    b = hm.record_mission([h.hypothesis_id], research_code_commit="commit-B")

    assert a["mission_id"] != b["mission_id"]
    assert a["family_id"] != b["family_id"]
    cell_a = rm_storage.get_cell(a["bindings"][0]["cell_id"])
    cell_b = rm_storage.get_cell(b["bindings"][0]["cell_id"])
    assert cell_a["cell_id"] != cell_b["cell_id"]
    assert {cell_a["research_code_commit"], cell_b["research_code_commit"]} == {"commit-A", "commit-B"}


def test_record_mission_order_of_hypothesis_ids_does_not_change_the_mission():
    h1 = _persist_hypothesis(symbol="EURUSD")
    h2 = _persist_hypothesis(symbol="GBPUSD")
    a = hm.record_mission([h1.hypothesis_id, h2.hypothesis_id], research_code_commit="commit-A")
    b = hm.record_mission([h2.hypothesis_id, h1.hypothesis_id], research_code_commit="commit-A")
    assert a["mission_id"] == b["mission_id"]
    assert b["created"] is False


# --- crash-safety: recovering from a partial persist -----------------------


def test_record_mission_recovers_when_the_family_and_cells_already_exist_but_the_mission_row_does_not():
    """Simulates a process dying AFTER storage.research_matrix.upsert_
    family()/upsert_cells() succeeded but BEFORE storage.hypothesis_
    mission.persist_mission() ran. A retried record_mission() call must
    recompute the SAME deterministic family_id, recognize the family
    already exists, skip re-creating it, and simply finish writing the
    missing Mission bookkeeping -- never raise, never duplicate, never
    orphan the already-queued cell."""
    h = _persist_hypothesis()
    mission_id = hm.compute_mission_id([h.hypothesis_id], "commit-A", None)
    family_id = hm._mission_family_id(mission_id)

    req = he.build_execution_request([{
        "hypothesis_id": h.hypothesis_id, "symbol": h.symbol, "engine": h.engine,
        "engine_version": h.engine_version, "timeframe": h.timeframe, "risk_preset": h.risk_preset,
        "matrix_cell_fingerprint": h.matrix_cell_fingerprint,
    }], research_code_commit="commit-A")
    import json as _json
    rm_storage.upsert_family(family_id, planned_n=len(req.cells), family_alpha=0.05, symbols_json=_json.dumps([h.symbol]))
    rm_storage.upsert_cells(list(req.cells), family_id, source_hypothesis_ids=req.source_hypothesis_ids_by_cell)
    assert hm_storage.get_mission(mission_id) is None  # the simulated crash point

    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")

    assert result["mission_id"] == mission_id
    assert result["family_id"] == family_id
    assert hm_storage.get_mission(mission_id) is not None
    assert len(rm_storage.list_cells(family_id=family_id)) == 1  # never duplicated


# --- forensic chain: Mission -> binding -> Hypothesis -> Cell -------------


def test_forensic_chain_is_fully_joinable_from_a_mission_id():
    h = _persist_hypothesis(symbol="XAUUSD", engine="wyckoff", timeframe="H4")
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-forensic", data_snapshot_id="snap-1", created_by="ops")

    mission = hm_storage.get_mission(result["mission_id"])
    assert mission["family_id"] == result["family_id"]
    assert mission["research_code_commit"] == "commit-forensic"
    assert mission["data_snapshot_id"] == "snap-1"
    assert mission["created_by"] == "ops"

    bindings = hm_storage.list_mission_bindings(result["mission_id"])
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding["hypothesis_id"] == h.hypothesis_id

    hypothesis_row = hf_storage.get_hypothesis(binding["hypothesis_id"])
    assert hypothesis_row["claim"] == h.claim
    assert hypothesis_row["symbol"] == "XAUUSD"

    cell = rm_storage.get_cell(binding["cell_id"])
    assert cell["family_id"] == mission["family_id"]
    assert cell["source_hypothesis_id"] == h.hypothesis_id
    assert cell["engine"] == "wyckoff"
    assert cell["timeframe"] == "H4"


def test_list_missions_for_hypothesis_is_the_reverse_forensic_lookup():
    h = _persist_hypothesis()
    result = hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")

    missions = hm_storage.list_missions_for_hypothesis(h.hypothesis_id)
    assert len(missions) == 1
    assert missions[0]["mission_id"] == result["mission_id"]


def test_list_missions_for_hypothesis_returns_empty_for_never_bound_hypothesis():
    assert hm_storage.list_missions_for_hypothesis("DISCOVERY-HYPOTHESIS-never-bound") == []


# --- storage-level persist_mission() idempotency ---------------------------


def test_persist_mission_bookkeeping_is_idempotent():
    first = hm_storage.persist_mission(
        "mission-1", "family-1",
        [{"hypothesis_id": "h1", "hypothesis_fingerprint": "fp1", "cell_id": "cell-1"}],
        research_code_commit="commit-A",
    )
    assert first == {"bindings_inserted": 1, "bindings_duplicate": 0}

    second = hm_storage.persist_mission(
        "mission-1", "family-1",
        [{"hypothesis_id": "h1", "hypothesis_fingerprint": "fp1", "cell_id": "cell-1"}],
        research_code_commit="commit-A",
    )
    assert second == {"bindings_inserted": 0, "bindings_duplicate": 1}
    assert len(hm_storage.list_mission_bindings("mission-1")) == 1


def test_mission_and_binding_tables_indexes_exist(fake_d1):
    hm_storage.list_mission_bindings("nope")  # triggers _init(con)
    idx_names = {
        r[0] for r in fake_d1.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert {"idx_rhm_family", "idx_rhmb_hypothesis", "idx_rhmb_cell"} <= idx_names


# --- config/registry isolation ---------------------------------------------


def test_record_mission_never_touches_config_or_registry():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    h = _persist_hypothesis()
    hm.record_mission([h.hypothesis_id], research_code_commit="commit-A")

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after record_mission()"
