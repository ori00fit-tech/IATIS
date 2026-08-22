"""tests/test_research_matrix_storage.py -- D1 round-trip tests for storage/research_matrix.py."""
from __future__ import annotations

from backtest import research_matrix as rm
from storage import research_matrix as storage

_BUNDLE = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}


def _cell(symbol="EURUSD", risk_preset="balanced") -> rm.MatrixCellSpec:
    return rm.MatrixCellSpec(symbol=symbol, bundle=_BUNDLE, risk_preset=risk_preset)


def test_upsert_cells_inserts_new_and_dedupes_existing():
    c1 = _cell()
    result_a = storage.upsert_cells([c1])
    assert result_a == {"inserted": 1, "duplicate": 0}

    result_b = storage.upsert_cells([c1])  # identical fingerprint again
    assert result_b == {"inserted": 0, "duplicate": 1}

    c2 = _cell(symbol="GBPUSD")
    result_c = storage.upsert_cells([c1, c2])
    assert result_c == {"inserted": 1, "duplicate": 1}


def test_get_cell_returns_the_full_row():
    c1 = _cell()
    storage.upsert_cells([c1])
    row = storage.get_cell(c1.cell_id)
    assert row is not None
    assert row["symbol"] == "EURUSD"
    assert row["status"] == rm.QUEUED
    assert row["fingerprint"] == c1.fingerprint


def test_get_cell_returns_none_for_unknown_id():
    assert storage.get_cell("MATRIX-CELL-doesnotexist") is None


def test_list_cells_filters_by_status_and_symbol():
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD")])
    storage.update_cell(_cell(symbol="EURUSD").cell_id, status=rm.SCREENED)

    all_cells = storage.list_cells()
    assert len(all_cells) == 2

    screened = storage.list_cells(status=rm.SCREENED)
    assert len(screened) == 1
    assert screened[0]["symbol"] == "EURUSD"

    gbp_only = storage.list_cells(symbol="GBPUSD")
    assert len(gbp_only) == 1


def test_update_cell_rejects_unknown_field():
    c1 = _cell()
    storage.upsert_cells([c1])
    import pytest
    with pytest.raises(ValueError):
        storage.update_cell(c1.cell_id, not_a_real_field="x")


def test_update_cell_persists_rejection_reason():
    c1 = _cell()
    storage.upsert_cells([c1])
    storage.update_cell(c1.cell_id, status=rm.REJECTED, rejection_reason="only 3 trades")
    row = storage.get_cell(c1.cell_id)
    assert row["status"] == rm.REJECTED
    assert row["rejection_reason"] == "only 3 trades"


def test_claim_queued_cells_marks_them_running_and_returns_pre_claim_snapshot():
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD"), _cell(symbol="XAUUSD")])
    claimed = storage.claim_queued_cells(2)
    assert len(claimed) == 2
    assert all(c["status"] == rm.QUEUED for c in claimed)  # pre-claim snapshot, not post

    for c in claimed:
        assert storage.get_cell(c["cell_id"])["status"] == rm.RUNNING

    remaining = storage.list_cells(status=rm.QUEUED)
    assert len(remaining) == 1


def test_claim_queued_cells_never_reclaims_an_already_running_cell():
    storage.upsert_cells([_cell(symbol="EURUSD")])
    first = storage.claim_queued_cells(10)
    assert len(first) == 1
    second = storage.claim_queued_cells(10)
    assert second == []


def test_requeue_stale_running_cells_only_touches_old_enough_rows():
    c1 = _cell()
    storage.upsert_cells([c1])
    storage.claim_queued_cells(1)
    assert storage.get_cell(c1.cell_id)["status"] == rm.RUNNING

    fresh_requeued = storage.requeue_stale_running_cells(older_than_seconds=3600)
    assert fresh_requeued == 0
    assert storage.get_cell(c1.cell_id)["status"] == rm.RUNNING

    stale_requeued = storage.requeue_stale_running_cells(older_than_seconds=-1)  # "older than -1s" == everything
    assert stale_requeued == 1
    assert storage.get_cell(c1.cell_id)["status"] == rm.QUEUED


def test_cells_for_matrix_correction_only_returns_screened_with_a_p_value():
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD"), _cell(symbol="XAUUSD")])
    ids = [c["cell_id"] for c in storage.list_cells()]
    storage.update_cell(ids[0], status=rm.SCREENED, stage_a_p_value=0.01)
    storage.update_cell(ids[1], status=rm.SCREENED)  # no p-value recorded
    storage.update_cell(ids[2], status=rm.REJECTED, stage_a_p_value=0.9)

    family = storage.cells_for_matrix_correction()
    assert len(family) == 1
    assert family[0]["cell_id"] == ids[0]


def test_run_lifecycle_round_trip():
    storage.upsert_run("run1", status="running", batch_size=10)
    storage.set_run_status("run1", "running", started=True)
    row = storage.get_run("run1")
    assert row["status"] == "running"
    assert row["started_at"] is not None

    storage.set_run_status(
        "run1", "finished", finished=True, cells_claimed=10, cells_screened=8,
        cells_promoted=3, cells_validated=1, matrix_significance={"n_trials": 8},
    )
    row = storage.get_run("run1")
    assert row["status"] == "finished"
    assert row["cells_validated"] == 1
    import json
    assert json.loads(row["matrix_significance_json"])["n_trials"] == 8


def test_get_run_returns_none_for_unknown_id():
    assert storage.get_run("nope") is None
