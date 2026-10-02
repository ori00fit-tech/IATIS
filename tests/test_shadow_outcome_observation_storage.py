"""tests/test_shadow_outcome_observation_storage.py -- storage-layer
tests for storage/shadow_outcome_observation.py (Phase 15E): the
append-only event-log insert shape, the DB-level (request_id,
evaluated_at) collision guard, and the deliberate ABSENCE of a
request_id-alone uniqueness constraint (many observations per
request_id is the whole point of this table)."""
from __future__ import annotations

import inspect

from storage import shadow_outcome_observation as storage_observation


def _insert(**overrides) -> dict | None:
    base = dict(
        observation_id="SHADOW-OUTCOME-OBSERVATION-test0001",
        request_id="LIVE-IDENTITY-REQUEST-test0001", hypothesis_id="CONFLUENCE-HYPOTHESIS-x",
        outcome="NOT_YET_ASSESSABLE", resolved_bar_time=None,
        evaluated_at="2026-01-01T05:00:00+00:00",
    )
    base.update(overrides)
    return storage_observation.try_insert(**base)


def test_try_insert_creates_a_row_with_all_fields():
    row = _insert()
    assert row["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-x"
    assert row["outcome"] == "NOT_YET_ASSESSABLE"
    assert row["resolved_bar_time"] is None
    assert row["evaluated_at"] == "2026-01-01T05:00:00+00:00"
    assert row["created_at"] is not None
    assert row["created_at"] != row["evaluated_at"]  # distinct clocks, never conflated


def test_get_observation_by_request_id_and_evaluated_at_returns_none_when_absent():
    assert storage_observation.get_observation_by_request_id_and_evaluated_at(
        "LIVE-IDENTITY-REQUEST-ghost", "2026-01-01T05:00:00+00:00"
    ) is None


def test_request_id_alone_is_deliberately_not_unique():
    """The core proof this table is NOT the 15C/15D 1:1-by-request_id
    pattern: two observations for the SAME request_id at DIFFERENT
    evaluated_at both persist successfully."""
    first = _insert(observation_id="SHADOW-OUTCOME-OBSERVATION-m1", request_id="LIVE-IDENTITY-REQUEST-multi",
                     evaluated_at="2026-01-01T05:00:00+00:00")
    second = _insert(observation_id="SHADOW-OUTCOME-OBSERVATION-m2", request_id="LIVE-IDENTITY-REQUEST-multi",
                      outcome="TP_HIT", resolved_bar_time="2026-01-01T08:00:00+00:00",
                      evaluated_at="2026-01-01T09:00:00+00:00")
    assert first is not None
    assert second is not None
    assert storage_observation.list_observations_for_request("LIVE-IDENTITY-REQUEST-multi").__len__() == 2


def test_duplicate_request_id_and_evaluated_at_blocked_by_the_real_db_level_unique_index():
    """The collision guard, proven at the DB level -- not a Python
    len()-check. A second row for the EXACT SAME (request_id,
    evaluated_at) pair fails to insert."""
    first = _insert(observation_id="SHADOW-OUTCOME-OBSERVATION-c1", request_id="LIVE-IDENTITY-REQUEST-coll",
                     evaluated_at="2026-01-01T05:00:00+00:00")
    second = _insert(observation_id="SHADOW-OUTCOME-OBSERVATION-c2", request_id="LIVE-IDENTITY-REQUEST-coll",
                      evaluated_at="2026-01-01T05:00:00+00:00", outcome="SL_HIT")
    assert first is not None
    assert second is None  # denied -- nothing inserted
    assert storage_observation.get_observation_by_request_id_and_evaluated_at(
        "LIVE-IDENTITY-REQUEST-coll", "2026-01-01T05:00:00+00:00"
    )["observation_id"] == "SHADOW-OUTCOME-OBSERVATION-c1"


def test_list_observations_for_hypothesis_returns_newest_first_within_window():
    hyp = "CONFLUENCE-HYPOTHESIS-window"
    _insert(observation_id="SHADOW-OUTCOME-OBSERVATION-w1", request_id="LIVE-IDENTITY-REQUEST-w1",
            hypothesis_id=hyp, evaluated_at="2026-01-01T01:00:00+00:00")
    _insert(observation_id="SHADOW-OUTCOME-OBSERVATION-w2", request_id="LIVE-IDENTITY-REQUEST-w2",
            hypothesis_id=hyp, evaluated_at="2026-01-01T02:00:00+00:00")
    rows = storage_observation.list_observations_for_hypothesis(hyp, limit=10)
    assert len(rows) == 2
    assert rows[0]["observation_id"] == "SHADOW-OUTCOME-OBSERVATION-w2"  # newest first (seq DESC)


# --- structural: no UPDATE statement exists anywhere in this module ------


def test_no_update_statement_anywhere_in_this_module():
    """Strips the module's own docstrings first -- they legitimately
    discuss, in prose, that no UPDATE exists, which would otherwise trip
    a bare-substring check against the prose itself rather than real
    code."""
    import re
    source = inspect.getsource(storage_observation)
    body = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    assert "UPDATE " not in body
    assert "UPDATE\n" not in body
