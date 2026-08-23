"""tests/test_hypothesis_mission.py -- pure-function / structural tests
for backtest/hypothesis_mission.py (Hypothesis Discovery Engine, Phase 4 —
Mission Center Integration)."""
from __future__ import annotations

import inspect

import pytest

from backtest import hypothesis_execution as he
from backtest import hypothesis_mission as hm


# --- compute_mission_id: deterministic identity -------------------------


def test_compute_mission_id_is_deterministic():
    a = hm.compute_mission_id(["DISCOVERY-HYPOTHESIS-x", "DISCOVERY-HYPOTHESIS-y"], "commit-A", "dukascopy")
    b = hm.compute_mission_id(["DISCOVERY-HYPOTHESIS-x", "DISCOVERY-HYPOTHESIS-y"], "commit-A", "dukascopy")
    assert a == b
    assert a.startswith("HYPOTHESIS-MISSION-")


def test_compute_mission_id_is_order_independent():
    a = hm.compute_mission_id(["h1", "h2", "h3"], "commit-A")
    b = hm.compute_mission_id(["h3", "h1", "h2"], "commit-A")
    assert a == b


def test_compute_mission_id_different_commit_produces_different_id():
    a = hm.compute_mission_id(["h1"], "commit-A")
    b = hm.compute_mission_id(["h1"], "commit-B")
    assert a != b


def test_compute_mission_id_different_provider_produces_different_id():
    a = hm.compute_mission_id(["h1"], "commit-A", "dukascopy")
    b = hm.compute_mission_id(["h1"], "commit-A", "twelvedata")
    assert a != b


def test_compute_mission_id_different_hypothesis_set_produces_different_id():
    a = hm.compute_mission_id(["h1"], "commit-A")
    b = hm.compute_mission_id(["h1", "h2"], "commit-A")
    assert a != b


def test_compute_mission_id_none_commit_and_none_provider_are_stable():
    a = hm.compute_mission_id(["h1"], None)
    b = hm.compute_mission_id(["h1"], None, None)
    assert a == b


# --- record_mission: guardrail 3 equivalent -- never selects a default --


def test_record_mission_rejects_empty_hypothesis_ids():
    with pytest.raises(he.HypothesisExecutionError, match="non-empty"):
        hm.record_mission([])


def test_record_mission_rejects_unknown_hypothesis_id():
    with pytest.raises(he.HypothesisExecutionError, match="unknown hypothesis_id"):
        hm.record_mission(["DISCOVERY-HYPOTHESIS-does-not-exist"], research_code_commit="abc")


# --- "no orphan execution": structural, not just runtime ----------------


def test_record_mission_signature_accepts_only_hypothesis_identity_parameters():
    """The operator's own explicit Phase 4 requirement: there must be no
    code path that creates a Mission by passing symbol/engine/timeframe/
    risk_preset directly. Checked structurally, at the signature level --
    not merely by a runtime check that a determined caller could route
    around -- record_mission()'s ONLY positional/keyword surface is
    hypothesis_ids plus binding metadata (commit/provider/created_by/
    snapshot), never a raw Matrix-cell parameter."""
    params = set(inspect.signature(hm.record_mission).parameters)
    assert params == {
        "hypothesis_ids", "research_code_commit", "data_provider", "created_by", "data_snapshot_id",
    }
    forbidden = ("symbol", "engine", "timeframe", "risk_preset", "engines", "timeframes", "bundle", "cell")
    for name in params:
        for bad in forbidden:
            assert bad not in name.lower() or name == "hypothesis_ids", (
                f"record_mission() unexpectedly accepts a raw Matrix-cell parameter: {name!r}"
            )


def test_no_orphan_execution_source_scan_across_both_mission_modules():
    """No function anywhere in backtest.hypothesis_mission or storage.
    hypothesis_mission ever builds a MatrixCellSpec or calls generate_
    discovery_cells()/generate_matrix_cells() directly -- the ONLY route
    from a raw combination to a Matrix cell is via backtest.hypothesis_
    execution.build_execution_request(), called exclusively from inside
    record_mission(), which itself only ever accepts hypothesis_ids."""
    from storage import hypothesis_mission as storage_module

    for module in (hm, storage_module):
        source = inspect.getsource(module)
        for forbidden in ("generate_matrix_cells(", "MatrixCellSpec("):
            assert forbidden not in source, f"{module.__name__} unexpectedly references {forbidden!r}"


def test_storage_hypothesis_mission_never_imports_backtest():
    """Matches the established, load-bearing convention (storage/research_
    matrix.py's own module docstring): storage/*.py never imports
    backtest/*.py -- the orchestration/verification stays one layer up, in
    backtest.hypothesis_mission, which is the one that legitimately
    imports storage (matching backtest/matrix_orchestrator.py's own
    precedent)."""
    from storage import hypothesis_mission as storage_module

    source = inspect.getsource(storage_module)
    assert "import backtest" not in source
    assert "from backtest" not in source


def test_no_update_or_delete_path_exists_for_missions_or_bindings():
    """A Mission's own bookkeeping (and its bindings) are append-only,
    forensic records -- once record_mission() persists one, nothing in
    this engine may edit or remove it."""
    from storage import hypothesis_mission as storage_module

    public_names = [n for n in dir(storage_module) if not n.startswith("_")]
    forbidden_substrings = ("update_mission", "delete_mission", "edit_mission", "update_binding", "delete_binding")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"storage.hypothesis_mission unexpectedly exposes {name!r}"


# --- reuse, never rebuild -------------------------------------------------


def test_record_mission_reuses_build_execution_request():
    source = inspect.getsource(hm)
    assert "build_execution_request(" in source


def test_record_mission_never_touches_config_or_registry():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    with pytest.raises(he.HypothesisExecutionError):
        hm.record_mission([])

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after a failed record_mission() call"
