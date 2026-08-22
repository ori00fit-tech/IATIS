"""
tests/test_kill_switch.py
----------------------------
storage/kill_switch.py — the operational kill switch (RTS 6 Art.12 /
PRA SS5/18 "kill functionality" style manual halt). Pure file-backed
storage tests; no D1, no network.
"""
from __future__ import annotations

import json

import pytest

from storage.kill_switch import activate, deactivate, get_state, is_active


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "kill_switch.json"


def test_default_state_is_inactive_when_no_file_exists(state_path):
    state = get_state(state_path)
    assert state["active"] is False
    assert state["reason"] is None
    assert is_active(state_path) is False


def test_activate_requires_a_reason(state_path):
    with pytest.raises(ValueError):
        activate("", path=state_path)
    with pytest.raises(ValueError):
        activate("   ", path=state_path)


def test_activate_sets_active_state_with_reason_and_timestamp(state_path):
    state = activate("Anomalous fill sequence on XAUUSD", activated_by="alice", path=state_path)
    assert state["active"] is True
    assert state["reason"] == "Anomalous fill sequence on XAUUSD"
    assert state["activated_by"] == "alice"
    assert state["activated_at"] is not None
    assert state["deactivated_at"] is None
    assert is_active(state_path) is True


def test_activated_state_persists_across_fresh_reads(state_path):
    """Simulates a restart / a different process: get_state() must read
    fresh from disk, not rely on any in-memory cache."""
    activate("Testing persistence", path=state_path)
    # A brand-new call, no prior state passed in-process — proves this
    # isn't relying on module-level caching.
    assert is_active(state_path) is True
    assert get_state(state_path)["reason"] == "Testing persistence"


def test_deactivate_requires_an_explicit_call_never_auto_clears(state_path):
    activate("halt for review", path=state_path)
    assert is_active(state_path) is True
    # Reading the state repeatedly must never clear it on its own.
    for _ in range(5):
        assert is_active(state_path) is True
    deactivate(deactivated_by="bob", path=state_path)
    assert is_active(state_path) is False


def test_deactivate_preserves_the_original_reason_for_the_audit_trail(state_path):
    activate("halt for review", activated_by="alice", path=state_path)
    state = deactivate(deactivated_by="bob", path=state_path)
    assert state["active"] is False
    assert state["reason"] == "halt for review"
    assert state["activated_by"] == "alice"
    assert state["deactivated_by"] == "bob"
    assert state["deactivated_at"] is not None


def test_deactivate_on_never_activated_state_is_a_harmless_noop(state_path):
    state = deactivate(deactivated_by="bob", path=state_path)
    assert state["active"] is False


def test_reason_is_truncated_and_stripped(state_path):
    state = activate("  padded  ", path=state_path)
    assert state["reason"] == "padded"

    long_reason = "x" * 1000
    state = activate(long_reason, path=state_path)
    assert len(state["reason"]) == 500


def test_corrupted_state_file_fails_closed(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{not valid json")
    state = get_state(state_path)
    assert state["active"] is True
    assert "unreadable" in (state["reason"] or "").lower()
    assert is_active(state_path) is True


def test_state_file_not_a_json_object_fails_closed(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps([1, 2, 3]))
    assert is_active(state_path) is True


def test_reactivating_an_already_active_switch_updates_reason(state_path):
    activate("first reason", activated_by="alice", path=state_path)
    state = activate("second reason", activated_by="carol", path=state_path)
    assert state["reason"] == "second reason"
    assert state["activated_by"] == "carol"
