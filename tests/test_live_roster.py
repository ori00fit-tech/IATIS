"""tests/test_live_roster.py -- tests for backtest/live_roster.py
(Hypothesis Discovery Engine, Phase 15A — SHADOW Live Evaluation Roster,
domain layer): add/remove lifecycle, the locked "Roster membership !=
current eligibility" separation, and structural independence from
sibling-phase internals."""
from __future__ import annotations

import inspect

import pytest

from backtest import live_roster as lr
from backtest.promotion_gate import ELIGIBLE, LIMITED, NOT_ELIGIBLE, SHADOW


def _shadow_eligible(**overrides) -> dict:
    base = {"eligibility": ELIGIBLE, "target_stage": SHADOW, "reasons": []}
    base.update(overrides)
    return base


# --- add_to_roster -----------------------------------------------------


def test_add_to_roster_succeeds_for_a_shadow_eligible_result():
    result = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-a1", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="SHADOW-eligible per fresh gate",
    )
    assert result["added"] is True
    assert result["roster_entry"]["status"] == lr.ACTIVE
    assert result["roster_entry"]["hypothesis_id"] == "CONFLUENCE-HYPOTHESIS-a1"


def test_add_to_roster_persists_the_snapshot_verbatim():
    gate_result = _shadow_eligible(reasons=[])
    result = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-snap", promotion_gate_result=gate_result,
        added_by="alice", added_reason="ok",
    )
    import json
    snapshot = json.loads(result["roster_entry"]["promotion_gate_snapshot_json"])
    assert snapshot["eligibility"] == ELIGIBLE
    assert snapshot["target_stage"] == SHADOW


def test_add_to_roster_rejects_non_shadow_target_stage():
    result = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-a2",
        promotion_gate_result={"eligibility": ELIGIBLE, "target_stage": LIMITED, "reasons": []},
        added_by="alice", added_reason="wrong stage",
    )
    assert result["added"] is False
    assert "target_stage" in result["reason"]


def test_add_to_roster_rejects_not_eligible_result():
    result = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-a3",
        promotion_gate_result={"eligibility": NOT_ELIGIBLE, "target_stage": SHADOW, "reasons": ["too weak"]},
        added_by="alice", added_reason="not eligible",
    )
    assert result["added"] is False
    assert "eligibility" in result["reason"]


def test_add_to_roster_denies_a_second_active_entry_for_the_same_hypothesis():
    lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-dup", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="first",
    )
    second = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-dup", promotion_gate_result=_shadow_eligible(),
        added_by="bob", added_reason="second",
    )
    assert second["added"] is False
    assert "already exists" in second["reason"]


def test_add_to_roster_requires_hypothesis_id_added_by_and_added_reason():
    with pytest.raises(lr.LiveRosterError):
        lr.add_to_roster(hypothesis_id="", promotion_gate_result=_shadow_eligible(),
                          added_by="alice", added_reason="x")
    with pytest.raises(lr.LiveRosterError):
        lr.add_to_roster(hypothesis_id="H1", promotion_gate_result=_shadow_eligible(),
                          added_by="", added_reason="x")
    with pytest.raises(lr.LiveRosterError):
        lr.add_to_roster(hypothesis_id="H1", promotion_gate_result=_shadow_eligible(),
                          added_by="alice", added_reason="")


# --- remove_from_roster --------------------------------------------------


def test_remove_from_roster_flips_active_to_removed():
    added = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-rm1", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="ok",
    )
    roster_entry_id = added["roster_entry"]["roster_entry_id"]
    removed = lr.remove_from_roster(roster_entry_id=roster_entry_id, removed_by="bob", removed_reason="degraded")
    assert removed["status"] == lr.REMOVED
    assert removed["removed_by"] == "bob"


def test_remove_from_roster_raises_for_unknown_id():
    with pytest.raises(lr.LiveRosterError, match="unknown roster_entry_id"):
        lr.remove_from_roster(roster_entry_id="ROSTER-ghost", removed_by="bob", removed_reason="n/a")


def test_remove_from_roster_requires_removed_by_and_removed_reason():
    added = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-rm2", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="ok",
    )
    roster_entry_id = added["roster_entry"]["roster_entry_id"]
    with pytest.raises(lr.LiveRosterError):
        lr.remove_from_roster(roster_entry_id=roster_entry_id, removed_by="", removed_reason="x")
    with pytest.raises(lr.LiveRosterError):
        lr.remove_from_roster(roster_entry_id=roster_entry_id, removed_by="bob", removed_reason="")


# --- check_current_eligibility: the locked "Roster != eligibility" proof --


def test_check_current_eligibility_true_for_active_entry_and_fresh_eligible_result():
    added = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-ce1", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="ok",
    )
    result = lr.check_current_eligibility(
        roster_entry=added["roster_entry"], fresh_promotion_gate_result=_shadow_eligible(),
    )
    assert result == {"eligible_this_cycle": True, "reason": None}


def test_check_current_eligibility_false_when_fresh_result_is_not_eligible():
    """The locked scenario: the OLD snapshot (at add_to_roster time) was
    ELIGIBLE, but THIS cycle's fresh gate result is NOT_ELIGIBLE -- the
    stored snapshot must never be re-read as if it still proves current
    authorization."""
    added = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-ce2", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="ok",
    )
    fresh = {"eligibility": NOT_ELIGIBLE, "target_stage": SHADOW, "reasons": ["evidence degraded since admission"]}
    result = lr.check_current_eligibility(roster_entry=added["roster_entry"], fresh_promotion_gate_result=fresh)
    assert result["eligible_this_cycle"] is False
    assert "degraded" in result["reason"]


def test_check_current_eligibility_false_when_fresh_result_targets_a_different_stage():
    added = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-ce3", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="ok",
    )
    fresh = {"eligibility": ELIGIBLE, "target_stage": LIMITED, "reasons": []}
    result = lr.check_current_eligibility(roster_entry=added["roster_entry"], fresh_promotion_gate_result=fresh)
    assert result["eligible_this_cycle"] is False


def test_check_current_eligibility_false_for_a_removed_roster_entry():
    added = lr.add_to_roster(
        hypothesis_id="CONFLUENCE-HYPOTHESIS-ce4", promotion_gate_result=_shadow_eligible(),
        added_by="alice", added_reason="ok",
    )
    removed = lr.remove_from_roster(
        roster_entry_id=added["roster_entry"]["roster_entry_id"], removed_by="bob", removed_reason="gone",
    )
    result = lr.check_current_eligibility(roster_entry=removed, fresh_promotion_gate_result=_shadow_eligible())
    assert result["eligible_this_cycle"] is False
    assert "REMOVED" in result["reason"] or "status" in result["reason"]


def test_check_current_eligibility_never_reads_the_stored_snapshot():
    """Structural proof: the function's own signature takes
    `fresh_promotion_gate_result` as a SEPARATE argument and the roster
    entry passed here carries a snapshot that is the OPPOSITE of the
    fresh result -- proving the stored snapshot has zero influence on
    the returned verdict."""
    # add_to_roster() itself refuses a NOT_ELIGIBLE snapshot, so a roster
    # entry carrying a stale/mismatched snapshot is seeded via storage
    # directly for this one structural test.
    from storage import live_roster as storage_roster
    row = storage_roster.try_insert_active(
        roster_entry_id="ROSTER-ce5", hypothesis_id="CONFLUENCE-HYPOTHESIS-ce5",
        added_by="alice", added_reason="seeded directly for this structural test",
        promotion_gate_snapshot_json='{"eligibility": "NOT_ELIGIBLE", "target_stage": "SHADOW"}',
    )
    result = lr.check_current_eligibility(roster_entry=row, fresh_promotion_gate_result=_shadow_eligible())
    assert result["eligible_this_cycle"] is True  # fresh result wins, stale snapshot is ignored


# --- structural: no coupling with sibling-phase producers/consumers ------


def _source_without_docstrings() -> str:
    """Strips EVERY triple-quoted string (module AND per-function
    docstrings) before scanning -- several of this module's own
    docstrings legitimately explain, in prose, a call it must never make
    (e.g. 'never calls evaluate_promotion_gate() itself'), which would
    otherwise trip a bare-substring structural check against the prose
    itself rather than real code."""
    import re
    source = inspect.getsource(lr)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_hypothesis_live_request_shadow_observation_or_execution_import():
    body = _source_without_docstrings()
    forbidden = (
        "from storage.hypothesis_live_request", "import storage.hypothesis_live_request",
        "from backtest.hypothesis_live_request", "import backtest.hypothesis_live_request",
        "from backtest.shadow_observation", "import backtest.shadow_observation",
        "from backtest.policy_health", "import backtest.policy_health",
        "from execution", "import execution",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"live_roster unexpectedly references {pattern!r}"


def test_add_to_roster_never_calls_evaluate_promotion_gate():
    body = _source_without_docstrings()
    assert "evaluate_promotion_gate(" not in body
