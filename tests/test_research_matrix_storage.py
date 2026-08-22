"""tests/test_research_matrix_storage.py -- D1 round-trip tests for storage/research_matrix.py."""
from __future__ import annotations

import threading

from backtest import research_matrix as rm
from storage import research_matrix as storage

_BUNDLE = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}


def _cell(symbol="EURUSD", risk_preset="balanced") -> rm.MatrixCellSpec:
    return rm.MatrixCellSpec(symbol=symbol, bundle=_BUNDLE, risk_preset=risk_preset)


def _family(family_id="fam1", planned_n=10, family_alpha=0.05) -> str:
    storage.upsert_family(family_id, planned_n=planned_n, family_alpha=family_alpha)
    return family_id


# --- families (Finding 4) --------------------------------------------------


def test_upsert_family_and_get_family_round_trip():
    storage.upsert_family("famA", planned_n=42, family_alpha=0.05, symbols_json='["EURUSD"]')
    row = storage.get_family("famA")
    assert row is not None
    assert row["planned_n"] == 42
    assert row["family_alpha"] == 0.05
    assert row["symbols_json"] == '["EURUSD"]'


def test_get_family_returns_none_for_unknown_id():
    assert storage.get_family("nope") is None


# --- cells -------------------------------------------------------------


def test_upsert_cells_inserts_new_and_dedupes_existing():
    fam = _family()
    c1 = _cell()
    result_a = storage.upsert_cells([c1], fam)
    assert result_a == {"inserted": 1, "duplicate": 0}

    result_b = storage.upsert_cells([c1], fam)  # identical fingerprint again
    assert result_b == {"inserted": 0, "duplicate": 1}

    c2 = _cell(symbol="GBPUSD")
    result_c = storage.upsert_cells([c1, c2], fam)
    assert result_c == {"inserted": 1, "duplicate": 1}


def test_get_cell_returns_the_full_row():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    row = storage.get_cell(c1.cell_id)
    assert row is not None
    assert row["symbol"] == "EURUSD"
    assert row["status"] == rm.QUEUED
    assert row["fingerprint"] == c1.fingerprint
    assert row["family_id"] == fam


def test_get_cell_returns_none_for_unknown_id():
    assert storage.get_cell("MATRIX-CELL-doesnotexist") is None


def test_list_cells_filters_by_status_and_symbol():
    fam = _family()
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD")], fam)
    storage.update_cell(_cell(symbol="EURUSD").cell_id, status=rm.SCREENED)

    all_cells = storage.list_cells()
    assert len(all_cells) == 2

    screened = storage.list_cells(status=rm.SCREENED)
    assert len(screened) == 1
    assert screened[0]["symbol"] == "EURUSD"

    gbp_only = storage.list_cells(symbol="GBPUSD")
    assert len(gbp_only) == 1


def test_list_cells_filters_by_family_id():
    fam_a = _family("famA")
    fam_b = _family("famB")
    storage.upsert_cells([_cell(symbol="EURUSD")], fam_a)
    storage.upsert_cells([_cell(symbol="GBPUSD")], fam_b)

    only_a = storage.list_cells(family_id=fam_a)
    assert len(only_a) == 1
    assert only_a[0]["symbol"] == "EURUSD"

    only_b = storage.list_cells(family_id=fam_b)
    assert len(only_b) == 1
    assert only_b[0]["symbol"] == "GBPUSD"


def test_update_cell_rejects_unknown_field():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    import pytest
    with pytest.raises(ValueError):
        storage.update_cell(c1.cell_id, not_a_real_field="x")


def test_update_cell_persists_rejection_reason():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    storage.update_cell(c1.cell_id, status=rm.REJECTED, rejection_reason="only 3 trades")
    row = storage.get_cell(c1.cell_id)
    assert row["status"] == rm.REJECTED
    assert row["rejection_reason"] == "only 3 trades"


# --- claim_queued_cells (Finding 1 — atomic compare-and-set) ---------------


def test_claim_queued_cells_marks_them_running_and_returns_the_post_claim_row():
    fam = _family()
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD"), _cell(symbol="XAUUSD")], fam)
    claimed = storage.claim_queued_cells(fam, 2)
    assert len(claimed) == 2
    # Finding 1's atomic-claim rewrite re-SELECTs after the conditional
    # UPDATE, so the returned rows already reflect the real, post-claim
    # RUNNING status — a caller never has to re-fetch to know it won.
    assert all(c["status"] == rm.RUNNING for c in claimed)

    for c in claimed:
        assert storage.get_cell(c["cell_id"])["status"] == rm.RUNNING

    remaining = storage.list_cells(status=rm.QUEUED, family_id=fam)
    assert len(remaining) == 1


def test_claim_queued_cells_never_reclaims_an_already_running_cell():
    fam = _family()
    storage.upsert_cells([_cell(symbol="EURUSD")], fam)
    first = storage.claim_queued_cells(fam, 10)
    assert len(first) == 1
    second = storage.claim_queued_cells(fam, 10)
    assert second == []


def test_claim_queued_cells_is_scoped_to_its_family():
    fam_a = _family("famA")
    fam_b = _family("famB")
    storage.upsert_cells([_cell(symbol="EURUSD")], fam_a)
    storage.upsert_cells([_cell(symbol="GBPUSD")], fam_b)

    claimed_a = storage.claim_queued_cells(fam_a, 10)
    assert len(claimed_a) == 1
    assert claimed_a[0]["symbol"] == "EURUSD"
    # family B's cell is untouched by family A's claim
    assert storage.get_cell(_cell(symbol="GBPUSD").cell_id)["status"] == rm.QUEUED


def test_claim_queued_cells_real_concurrent_race_only_one_thread_wins_each_cell():
    """Forensic Audit Finding 1: two real Python threads racing
    claim_queued_cells() against the SAME small pool of QUEUED cells must
    never both claim the same cell_id — exactly one thread's atomic UPDATE
    wins per row, proven with real threads (not mocked/sequential calls)
    against the shared in-memory SQLite connection the fake_d1 fixture
    provides, which correctly serializes concurrent statement execution."""
    fam = _family(planned_n=20)
    cells = [_cell(symbol=f"SYM{i}") for i in range(20)]
    storage.upsert_cells(cells, fam)

    results: list[list[dict]] = [[], []]
    errors: list[Exception] = []

    def _worker(slot: int) -> None:
        try:
            results[slot] = storage.claim_queued_cells(fam, 20)
        except Exception as exc:  # pragma: no cover - failure surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"claim_queued_cells raised under concurrency: {errors}"

    claimed_ids_a = {c["cell_id"] for c in results[0]}
    claimed_ids_b = {c["cell_id"] for c in results[1]}
    # No cell_id was ever claimed by both threads.
    assert claimed_ids_a & claimed_ids_b == set()
    # Every cell was claimed by exactly one of the two threads (20 cells, no contention loss expected on a single in-memory DB).
    assert claimed_ids_a | claimed_ids_b == {c.cell_id for c in cells}

    for cell_id in claimed_ids_a | claimed_ids_b:
        assert storage.get_cell(cell_id)["status"] == rm.RUNNING


def test_requeue_stale_running_cells_only_touches_old_enough_rows():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    storage.claim_queued_cells(fam, 1)
    assert storage.get_cell(c1.cell_id)["status"] == rm.RUNNING

    fresh_requeued = storage.requeue_stale_running_cells(older_than_seconds=3600)
    assert fresh_requeued == 0
    assert storage.get_cell(c1.cell_id)["status"] == rm.RUNNING

    stale_requeued = storage.requeue_stale_running_cells(older_than_seconds=-1)  # "older than -1s" == everything
    assert stale_requeued == 1
    assert storage.get_cell(c1.cell_id)["status"] == rm.QUEUED


def test_requeued_stale_cell_can_be_reclaimed_within_its_own_family():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    storage.claim_queued_cells(fam, 1)
    storage.requeue_stale_running_cells(older_than_seconds=-1)
    reclaimed = storage.claim_queued_cells(fam, 1)
    assert len(reclaimed) == 1
    assert reclaimed[0]["cell_id"] == c1.cell_id


def test_cells_for_matrix_correction_only_returns_screened_with_a_p_value_within_the_family():
    fam_a = _family("famA")
    fam_b = _family("famB")
    storage.upsert_cells([_cell(symbol="EURUSD"), _cell(symbol="GBPUSD")], fam_a)
    storage.upsert_cells([_cell(symbol="XAUUSD")], fam_b)
    ids = [c["cell_id"] for c in storage.list_cells(family_id=fam_a)]
    storage.update_cell(ids[0], status=rm.SCREENED, stage_a_p_value=0.01)
    storage.update_cell(ids[1], status=rm.SCREENED)  # no p-value recorded

    other_id = storage.list_cells(family_id=fam_b)[0]["cell_id"]
    storage.update_cell(other_id, status=rm.SCREENED, stage_a_p_value=0.02)

    family_a_set = storage.cells_for_matrix_correction(fam_a)
    assert len(family_a_set) == 1
    assert family_a_set[0]["cell_id"] == ids[0]

    family_b_set = storage.cells_for_matrix_correction(fam_b)
    assert len(family_b_set) == 1
    assert family_b_set[0]["cell_id"] == other_id


# --- runs (batches) ---------------------------------------------------------


def test_run_lifecycle_round_trip():
    fam = _family()
    storage.upsert_run("run1", status="running", batch_size=10, family_id=fam)
    storage.set_run_status("run1", "running", started=True)
    row = storage.get_run("run1")
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["family_id"] == fam

    storage.set_run_status(
        "run1", "finished", finished=True, cells_claimed=10, cells_screened=8,
        cells_promoted=3, cells_validated=1, matrix_significance={"n_screened_this_pass": 8},
    )
    row = storage.get_run("run1")
    assert row["status"] == "finished"
    assert row["cells_validated"] == 1
    import json
    assert json.loads(row["matrix_significance_json"])["n_screened_this_pass"] == 8


def test_get_run_returns_none_for_unknown_id():
    assert storage.get_run("nope") is None
