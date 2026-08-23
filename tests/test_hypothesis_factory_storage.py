"""tests/test_hypothesis_factory_storage.py -- D1 round-trip tests for
storage/hypothesis_factory.py (Hypothesis Discovery Engine, Phase 2)."""
from __future__ import annotations

from backtest import hypothesis_factory as hf
from storage import hypothesis_factory as storage


def _hyp(**overrides) -> hf.Hypothesis:
    base = dict(
        symbol="EURUSD", engine="price_action", engine_version="v2", timeframe="H1",
        risk_preset="balanced", claim="does it have edge?", matrix_cell_fingerprint="abc123",
    )
    base.update(overrides)
    return hf.Hypothesis(**base)


def test_record_and_get_hypothesis_round_trip():
    h = _hyp()
    result = storage.record_hypotheses([h])
    assert result == {"inserted": 1, "duplicate": 0}

    row = storage.get_hypothesis(h.hypothesis_id)
    assert row is not None
    assert row["symbol"] == "EURUSD"
    assert row["engine"] == "price_action"
    assert row["engine_version"] == "v2"
    assert row["timeframe"] == "H1"
    assert row["risk_preset"] == "balanced"
    assert row["claim"] == "does it have edge?"
    assert row["matrix_cell_fingerprint"] == "abc123"
    assert row["proposed_at"] is not None


def test_get_hypothesis_returns_none_for_unknown_id():
    assert storage.get_hypothesis("DISCOVERY-HYPOTHESIS-nope") is None


def test_record_hypotheses_is_idempotent_on_re_proposal():
    """Re-generating an overlapping batch (the same deterministic
    combination -> the same hypothesis_id) is always safe -- a no-op
    duplicate, never a second row, never an error."""
    h = _hyp()
    first = storage.record_hypotheses([h])
    assert first == {"inserted": 1, "duplicate": 0}
    second = storage.record_hypotheses([h])
    assert second == {"inserted": 0, "duplicate": 1}

    rows = storage.list_hypotheses(symbol="EURUSD")
    assert len(rows) == 1


def test_record_hypotheses_batch_with_real_generator():
    hyps = hf.generate_hypotheses(symbols=["EURUSD", "GBPUSD"], engines=["smc", "price_action"], timeframes=["H1", "H4"])
    result = storage.record_hypotheses(hyps)
    assert result["inserted"] == len(hyps)
    assert result["duplicate"] == 0

    # re-running the exact same generation call is idempotent
    hyps_again = hf.generate_hypotheses(symbols=["EURUSD", "GBPUSD"], engines=["smc", "price_action"], timeframes=["H1", "H4"])
    result2 = storage.record_hypotheses(hyps_again)
    assert result2 == {"inserted": 0, "duplicate": len(hyps)}


def test_list_hypotheses_empty_by_default():
    assert storage.list_hypotheses() == []


def test_list_hypotheses_filters_by_symbol():
    storage.record_hypotheses([
        _hyp(symbol="EURUSD", matrix_cell_fingerprint="fp1"),
        _hyp(symbol="GBPUSD", matrix_cell_fingerprint="fp2"),
    ])
    eurusd = storage.list_hypotheses(symbol="EURUSD")
    assert len(eurusd) == 1
    assert eurusd[0]["symbol"] == "EURUSD"


def test_list_hypotheses_filters_by_engine():
    storage.record_hypotheses([
        _hyp(engine="price_action", matrix_cell_fingerprint="fp1"),
        _hyp(engine="smc", matrix_cell_fingerprint="fp2"),
    ])
    assert len(storage.list_hypotheses(engine="smc")) == 1


def test_list_hypotheses_filters_by_timeframe():
    storage.record_hypotheses([
        _hyp(timeframe="H1", matrix_cell_fingerprint="fp1"),
        _hyp(timeframe="H4", matrix_cell_fingerprint="fp2"),
    ])
    assert len(storage.list_hypotheses(timeframe="H4")) == 1


def test_list_hypotheses_respects_limit():
    storage.record_hypotheses([_hyp(matrix_cell_fingerprint=f"fp{i}") for i in range(5)])
    assert len(storage.list_hypotheses(limit=2)) == 2


def test_hypothesis_indexes_exist(fake_d1):
    storage.list_hypotheses()  # triggers _init(con)
    idx_names = {
        r[0] for r in fake_d1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_hypotheses'"
        ).fetchall()
    }
    assert {"idx_rh_symbol", "idx_rh_engine", "idx_rh_timeframe", "idx_rh_fingerprint"} <= idx_names


def test_record_hypotheses_never_touches_config_or_registry():
    from pathlib import Path

    watched = [Path("config.yaml"), Path("config/engines.yaml"), Path("config/symbols.yaml"), Path("research/results/registry.json")]
    before = {p: p.read_bytes() for p in watched if p.exists()}

    storage.record_hypotheses([_hyp()])

    for p in watched:
        if p in before:
            assert p.read_bytes() == before[p], f"{p} changed after record_hypotheses()"


def test_record_hypotheses_never_touches_research_matrix_cells_or_families():
    """This module must never write research_matrix_cells/families -- a
    Hypothesis's evidence lives entirely in its own Matrix cell, joined
    by matrix_cell_fingerprint, never duplicated or shadowed here."""
    import inspect

    from storage import hypothesis_factory as module

    source = inspect.getsource(module)
    forbidden_calls = ("upsert_cells(", "upsert_family(", "update_cell(")
    for call in forbidden_calls:
        assert call not in source, f"storage.hypothesis_factory references forbidden Matrix-state mutator {call!r}"
