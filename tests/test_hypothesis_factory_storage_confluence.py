"""tests/test_hypothesis_factory_storage_confluence.py -- D1 round-trip
tests for storage/hypothesis_factory.py's Phase 8B CONFLUENCE columns
(decision_type/bundle_id/bundle_version/bundle_json). Hypothesis
Discovery Engine, Phase 8B — Confluence Governed Identity."""
from __future__ import annotations

import json

from backtest import hypothesis_factory as hf
from storage import hypothesis_factory as hf_storage


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


def test_confluence_hypothesis_round_trips_through_storage():
    h = hf.generate_confluence_hypotheses(
        symbols=["EURUSD"], decision_version="v1", bundle=_bundle(), bundle_version="v1", risk_presets=["balanced"],
    )[0]
    result = hf_storage.record_hypotheses([h])
    assert result == {"inserted": 1, "duplicate": 0}

    row = hf_storage.get_hypothesis(h.hypothesis_id)
    assert row["decision_type"] == hf.CONFLUENCE
    assert row["engine"] == hf.CONFLUENCE
    assert row["engine_version"] == "v1"
    assert row["bundle_id"] == "Prod4 Confluence Panel"
    assert row["bundle_version"] == "v1"
    assert json.loads(row["bundle_json"]) == _bundle()


def test_single_engine_hypothesis_confluence_columns_stay_null():
    h = hf.generate_hypotheses(
        symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"],
    )[0]
    hf_storage.record_hypotheses([h])

    row = hf_storage.get_hypothesis(h.hypothesis_id)
    assert row["decision_type"] == hf.SINGLE_ENGINE
    assert row["bundle_id"] is None
    assert row["bundle_version"] is None
    assert row["bundle_json"] is None


def test_re_proposing_the_same_confluence_hypothesis_is_idempotent():
    h = hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle())[0]
    first = hf_storage.record_hypotheses([h])
    second = hf_storage.record_hypotheses([h])
    assert first == {"inserted": 1, "duplicate": 0}
    assert second == {"inserted": 0, "duplicate": 1}


def test_list_hypotheses_filters_by_decision_type():
    single = hf.generate_hypotheses(symbols=["EURUSD"], engines=["price_action"], timeframes=["H1"], risk_presets=["balanced"])[0]
    confluence = hf.generate_confluence_hypotheses(symbols=["EURUSD"], decision_version="v1", bundle=_bundle())[0]
    hf_storage.record_hypotheses([single, confluence])

    confluence_only = hf_storage.list_hypotheses(decision_type=hf.CONFLUENCE)
    assert {r["hypothesis_id"] for r in confluence_only} == {confluence.hypothesis_id}

    single_only = hf_storage.list_hypotheses(decision_type=hf.SINGLE_ENGINE)
    assert {r["hypothesis_id"] for r in single_only} == {single.hypothesis_id}


def test_decision_type_index_exists(fake_d1):
    hf_storage.list_hypotheses()  # triggers _init(con)
    idx_names = {r[0] for r in fake_d1.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_rh_decision_type" in idx_names


def test_record_hypotheses_still_has_no_update_function():
    """Phase 8B must not weaken Phase 2's own immutability guarantee."""
    public_names = [n for n in dir(hf_storage) if not n.startswith("_")]
    forbidden_substrings = ("update_hypothesis", "delete_hypothesis", "edit_hypothesis")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower()
