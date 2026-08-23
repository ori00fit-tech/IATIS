"""tests/test_research_matrix_storage.py -- D1 round-trip tests for storage/research_matrix.py."""
from __future__ import annotations

import threading

import pytest

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


# --- Phase 3C: source_recommendation_id provenance --------------------------


def test_upsert_family_source_recommendation_id_defaults_to_none():
    storage.upsert_family("famA", planned_n=1, family_alpha=0.05)
    assert storage.get_family("famA")["source_recommendation_id"] is None


def test_upsert_family_persists_source_recommendation_id():
    storage.upsert_family("famA", planned_n=1, family_alpha=0.05, source_recommendation_id="MATRIX-AI-abc123")
    row = storage.get_family("famA")
    assert row["source_recommendation_id"] == "MATRIX-AI-abc123"


def test_get_family_by_source_recommendation_round_trip():
    storage.upsert_family("famA", planned_n=1, family_alpha=0.05, source_recommendation_id="MATRIX-AI-abc123")
    row = storage.get_family_by_source_recommendation("MATRIX-AI-abc123")
    assert row is not None
    assert row["family_id"] == "famA"


def test_get_family_by_source_recommendation_returns_none_when_absent():
    storage.upsert_family("famA", planned_n=1, family_alpha=0.05)  # ordinary family, no recommendation
    assert storage.get_family_by_source_recommendation("MATRIX-AI-abc123") is None


def test_source_recommendation_id_is_unique_across_families():
    """The second, independent half of Phase 3C's replay-prevention pair
    (the first half is the atomic APPROVED->CONVERTED CAS on the
    recommendation itself) -- even a bug that skipped the CAS could not
    silently attach a second family to one recommendation."""
    from storage.d1_client import D1Error

    storage.upsert_family("famA", planned_n=1, family_alpha=0.05, source_recommendation_id="MATRIX-AI-abc123")
    with pytest.raises(D1Error):
        storage.upsert_family("famB", planned_n=1, family_alpha=0.05, source_recommendation_id="MATRIX-AI-abc123")


def test_multiple_ordinary_families_can_all_have_a_null_source_recommendation_id():
    """A UNIQUE index must not treat multiple NULLs as duplicates of each
    other -- every ordinary, hand-typed generate call must keep working
    exactly as before Phase 3C."""
    storage.upsert_family("famA", planned_n=1, family_alpha=0.05)
    storage.upsert_family("famB", planned_n=1, family_alpha=0.05)
    assert storage.get_family("famA") is not None
    assert storage.get_family("famB") is not None


def test_list_families_empty_by_default():
    assert storage.list_families() == []


def test_list_families_respects_limit_and_returns_every_row():
    for i in range(5):
        storage.upsert_family(f"famL{i}", planned_n=1, family_alpha=0.05)
    assert len(storage.list_families(limit=2)) == 2
    ids = {f["family_id"] for f in storage.list_families(limit=10)}
    assert ids == {f"famL{i}" for i in range(5)}


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


def test_upsert_cells_persists_research_code_commit():
    """Phase 2C (Evidence Comparison): research_code_commit was already
    folded into the cell's fingerprint hash but never persisted as its
    own readable column — a comparison across code commits needs it as a
    real, queryable field, not something buried in an opaque hash."""
    fam = _family()
    c1 = rm.MatrixCellSpec(symbol="EURUSD", bundle=_BUNDLE, risk_preset="balanced", research_code_commit="abc1234")
    storage.upsert_cells([c1], fam)
    row = storage.get_cell(c1.cell_id)
    assert row["research_code_commit"] == "abc1234"


def test_upsert_cells_tolerates_missing_research_code_commit():
    fam = _family()
    c1 = _cell()  # no research_code_commit passed
    storage.upsert_cells([c1], fam)
    row = storage.get_cell(c1.cell_id)
    assert row["research_code_commit"] is None


# --- Phase 1: engine/engine_version/timeframe identity columns -------------


def test_upsert_cells_persists_engine_identity_for_single_engine_single_tf_bundle():
    fam = _family()
    single_bundle = {"name": "SMC only", "timeframes": ["H1"], "engines": ["smc"], "indicators": [], "context_filters": []}
    c1 = rm.MatrixCellSpec(symbol="EURUSD", bundle=single_bundle, risk_preset="balanced")
    storage.upsert_cells([c1], fam)
    row = storage.get_cell(c1.cell_id)
    assert row["engine"] == "smc"
    assert row["engine_version"] == "v1"
    assert row["timeframe"] == "H1"


def test_upsert_cells_persists_engine_version_from_engine_variants():
    fam = _family()
    single_bundle = {"name": "PA v2", "timeframes": ["H1"], "engines": ["price_action"], "indicators": [], "context_filters": []}
    c1 = rm.MatrixCellSpec(symbol="EURUSD", bundle=single_bundle, risk_preset="balanced", engine_variants={"price_action": "v2"})
    storage.upsert_cells([c1], fam)
    row = storage.get_cell(c1.cell_id)
    assert row["engine"] == "price_action"
    assert row["engine_version"] == "v2"


def test_upsert_cells_leaves_engine_identity_null_for_multi_engine_bundle():
    """Backward compatibility (item F): a confluence-research bundle
    combining multiple engines/timeframes must NEVER get a coerced
    single engine/timeframe value -- NULL, exactly as before Phase 1."""
    fam = _family(planned_n=1)
    multi = {"name": "confluence", "timeframes": ["H1", "H4"], "engines": ["smc", "nnfx"], "indicators": [], "context_filters": []}
    c1 = rm.MatrixCellSpec(symbol="EURUSD", bundle=multi, risk_preset="balanced")
    storage.upsert_cells([c1], fam)
    row = storage.get_cell(c1.cell_id)
    assert row["engine"] is None
    assert row["engine_version"] is None
    assert row["timeframe"] is None


def test_upsert_cells_engine_and_timeframe_indexes_exist(fake_d1):
    storage.list_cells()  # triggers _init(con) -- the table/indexes don't exist until first touched
    idx_names = {
        r[0] for r in fake_d1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_matrix_cells'"
        ).fetchall()
    }
    assert "idx_rmc_engine" in idx_names
    assert "idx_rmc_timeframe" in idx_names


def test_generate_discovery_cells_round_trips_through_storage():
    """End-to-end: a real generate_discovery_cells() batch, persisted via
    upsert_cells(), is queryable by engine/timeframe via list_cells()'s
    own row shape."""
    fam = _family(planned_n=4)
    cells = rm.generate_discovery_cells(
        symbols=["EURUSD"], engines=["price_action"], timeframes=["H1", "H4"],
        engine_versions={"price_action": ("v1", "v2")}, risk_presets=["balanced"],
    )
    assert len(cells) == 4
    result = storage.upsert_cells(cells, fam)
    assert result == {"inserted": 4, "duplicate": 0}

    rows = storage.list_cells(family_id=fam)
    engines_seen = {r["engine"] for r in rows}
    versions_seen = {r["engine_version"] for r in rows}
    timeframes_seen = {r["timeframe"] for r in rows}
    assert engines_seen == {"price_action"}
    assert versions_seen == {"v1", "v2"}
    assert timeframes_seen == {"H1", "H4"}


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


def test_update_cell_refuses_to_mutate_a_terminal_cell():
    """Phase 3A -- evidence immutability. Once a cell reaches a terminal
    status, no caller (dashboard, resumed batch, re-run) may ever mutate
    it again."""
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    storage.update_cell(c1.cell_id, status=rm.VALIDATED, stage_b_verdict="SAME_SYMBOL_CONFIRMED")
    with pytest.raises(ValueError, match="terminal"):
        storage.update_cell(c1.cell_id, status=rm.REJECTED, rejection_reason="trying to overwrite")
    # the original terminal evidence is untouched
    row = storage.get_cell(c1.cell_id)
    assert row["status"] == rm.VALIDATED
    assert row["stage_b_verdict"] == "SAME_SYMBOL_CONFIRMED"


@pytest.mark.parametrize("terminal_status", [rm.VALIDATED, rm.REJECTED, rm.INSUFFICIENT_DATA, rm.FAILED])
def test_update_cell_refuses_every_terminal_status(terminal_status):
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    storage.update_cell(c1.cell_id, status=terminal_status)
    with pytest.raises(ValueError, match="terminal"):
        storage.update_cell(c1.cell_id, status=rm.CANDIDATE)


def test_update_cell_still_allows_transitions_between_non_terminal_statuses():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    storage.update_cell(c1.cell_id, status=rm.SCREENED, stage_a_p_value=0.01)
    storage.update_cell(c1.cell_id, status=rm.CANDIDATE)
    assert storage.get_cell(c1.cell_id)["status"] == rm.CANDIDATE


def test_upsert_cells_rejects_an_unknown_family_id():
    c1 = _cell()
    with pytest.raises(ValueError):
        storage.upsert_cells([c1], "does-not-exist")


def test_upsert_cells_refuses_to_exceed_the_familys_fixed_planned_n():
    """Phase 3A -- family closure. A family's cell set is closed once its
    planned research space is generated; nothing may silently grow it
    beyond planned_n."""
    fam = _family(planned_n=1)
    storage.upsert_cells([_cell(symbol="EURUSD")], fam)  # fills planned_n exactly
    with pytest.raises(ValueError, match="planned_n"):
        storage.upsert_cells([_cell(symbol="GBPUSD")], fam)
    # the rejected cell was never inserted
    assert storage.list_cells(family_id=fam, symbol="GBPUSD") == []


def test_upsert_cells_allows_resubmitting_the_exact_same_cells_idempotently():
    """A retried /generate call (e.g. it crashed after upsert_family but
    before returning) resubmits the identical cell list -- every cell is
    already present, so it counts as duplicate, never against planned_n."""
    fam = _family(planned_n=1)
    c1 = _cell(symbol="EURUSD")
    storage.upsert_cells([c1], fam)
    result = storage.upsert_cells([c1], fam)
    assert result == {"inserted": 0, "duplicate": 1}


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


def test_requeue_stale_running_cells_increments_requeue_count():
    """Phase 2A Evidence Read Model: requeue_count is real, persisted
    evidence of how many times a cell was resumed after an interruption —
    not just a stale-count inferred from logs."""
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    assert storage.get_cell(c1.cell_id)["requeue_count"] == 0

    storage.claim_queued_cells(fam, 1)
    storage.requeue_stale_running_cells(older_than_seconds=-1)
    assert storage.get_cell(c1.cell_id)["requeue_count"] == 1

    # a second crash-and-resume cycle increments it again, not resets it
    storage.claim_queued_cells(fam, 1)
    storage.requeue_stale_running_cells(older_than_seconds=-1)
    assert storage.get_cell(c1.cell_id)["requeue_count"] == 2


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


def test_list_runs_empty_by_default():
    assert storage.list_runs() == []


def test_list_runs_scopes_by_family_and_respects_limit():
    fam_a = _family("famRunsA")
    fam_b = _family("famRunsB")
    storage.upsert_run("runA1", status="running", batch_size=5, family_id=fam_a)
    storage.upsert_run("runA2", status="finished", batch_size=5, family_id=fam_a)
    storage.upsert_run("runB1", status="running", batch_size=5, family_id=fam_b)

    all_runs = storage.list_runs(limit=50)
    assert {r["run_id"] for r in all_runs} == {"runA1", "runA2", "runB1"}

    fam_a_runs = storage.list_runs(family_id=fam_a, limit=50)
    assert {r["run_id"] for r in fam_a_runs} == {"runA1", "runA2"}

    assert len(storage.list_runs(limit=1)) == 1


# --- Phase 3: source_hypothesis_id provenance -------------------------------


def test_upsert_cells_source_hypothesis_id_defaults_to_none():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam)
    assert storage.get_cell(c1.cell_id)["source_hypothesis_id"] is None


def test_upsert_cells_persists_source_hypothesis_id_when_provided():
    fam = _family()
    c1 = _cell()
    storage.upsert_cells([c1], fam, source_hypothesis_ids={c1.cell_id: "DISCOVERY-HYPOTHESIS-abc123"})
    row = storage.get_cell(c1.cell_id)
    assert row["source_hypothesis_id"] == "DISCOVERY-HYPOTHESIS-abc123"


def test_upsert_cells_source_hypothesis_id_only_applies_to_named_cells():
    """A mapping covering only SOME cells in the batch must leave every
    other cell in that same call with a NULL source_hypothesis_id --
    never guessed, never applied to the wrong cell."""
    fam = _family(planned_n=2)
    c1 = _cell(symbol="EURUSD")
    c2 = _cell(symbol="GBPUSD")
    storage.upsert_cells([c1, c2], fam, source_hypothesis_ids={c1.cell_id: "DISCOVERY-HYPOTHESIS-abc123"})
    assert storage.get_cell(c1.cell_id)["source_hypothesis_id"] == "DISCOVERY-HYPOTHESIS-abc123"
    assert storage.get_cell(c2.cell_id)["source_hypothesis_id"] is None


def test_list_cells_filters_by_source_hypothesis_id():
    fam = _family(planned_n=2)
    c1 = _cell(symbol="EURUSD")
    c2 = _cell(symbol="GBPUSD")
    storage.upsert_cells([c1, c2], fam, source_hypothesis_ids={c1.cell_id: "DISCOVERY-HYPOTHESIS-abc123"})
    matching = storage.list_cells(source_hypothesis_id="DISCOVERY-HYPOTHESIS-abc123")
    assert len(matching) == 1
    assert matching[0]["cell_id"] == c1.cell_id


def test_source_hypothesis_id_permits_multiple_cells_for_the_same_hypothesis():
    """Deliberately NOT unique (unlike source_recommendation_id on
    families): the same hypothesis may be legitimately re-executed under
    a different research code commit, producing a second, coexisting
    cell that must never collide with or overwrite the first."""
    fam = _family(planned_n=2)
    c1 = rm.MatrixCellSpec(symbol="EURUSD", bundle=_BUNDLE, risk_preset="balanced", research_code_commit="commit-A")
    c2 = rm.MatrixCellSpec(symbol="EURUSD", bundle=_BUNDLE, risk_preset="balanced", research_code_commit="commit-B")
    assert c1.cell_id != c2.cell_id  # different commit -> different fingerprint
    result = storage.upsert_cells(
        [c1, c2], fam, source_hypothesis_ids={c1.cell_id: "DISCOVERY-HYPOTHESIS-abc123", c2.cell_id: "DISCOVERY-HYPOTHESIS-abc123"},
    )
    assert result == {"inserted": 2, "duplicate": 0}
    matching = storage.list_cells(source_hypothesis_id="DISCOVERY-HYPOTHESIS-abc123")
    assert len(matching) == 2


def test_source_hypothesis_id_index_exists(fake_d1):
    storage.list_cells()  # triggers _init(con)
    idx_names = {
        r[0] for r in fake_d1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_matrix_cells'"
        ).fetchall()
    }
    assert "idx_rmc_source_hypothesis" in idx_names
