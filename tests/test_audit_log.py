"""tests/test_audit_log.py — append-only action audit trail (Mission
Control module 15)."""
from __future__ import annotations

from storage.audit_log import _mask_actor, log_action, read_actions


def test_mask_actor_api_key_never_logs_raw_key():
    assert _mask_actor("super-secret-key-value", None) == "api_key"


def test_mask_actor_session_truncates():
    masked = _mask_actor(None, "abcdefghijklmnopqrstuvwxyz")
    assert masked.startswith("session:abcdefgh")
    assert "ijklmnopqrstuvwxyz" not in masked


def test_mask_actor_unknown_when_neither_present():
    assert _mask_actor(None, None) == "unknown"


def test_log_action_writes_and_reads_back(tmp_path):
    path = tmp_path / "audit.jsonl"
    log_action("test_action", x_api_key="key", detail="something happened", path=path)

    entries = read_actions(path=path)
    assert len(entries) == 1
    assert entries[0]["action"] == "test_action"
    assert entries[0]["actor"] == "api_key"
    assert entries[0]["success"] is True
    assert entries[0]["detail"] == "something happened"


def test_log_action_never_leaks_raw_api_key_into_the_log_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    log_action("login", x_api_key="THIS-IS-THE-SECRET-KEY", path=path)
    raw_text = path.read_text()
    assert "THIS-IS-THE-SECRET-KEY" not in raw_text


def test_read_actions_returns_newest_first(tmp_path):
    path = tmp_path / "audit.jsonl"
    log_action("first", path=path)
    log_action("second", path=path)
    log_action("third", path=path)

    entries = read_actions(path=path)
    assert [e["action"] for e in entries] == ["third", "second", "first"]


def test_read_actions_respects_limit(tmp_path):
    path = tmp_path / "audit.jsonl"
    for i in range(10):
        log_action(f"action_{i}", path=path)

    entries = read_actions(limit=3, path=path)
    assert len(entries) == 3
    assert entries[0]["action"] == "action_9"


def test_read_actions_empty_when_no_file(tmp_path):
    assert read_actions(path=tmp_path / "does_not_exist.jsonl") == []


def test_log_action_failure_recorded(tmp_path):
    path = tmp_path / "audit.jsonl"
    log_action("login", success=False, path=path)
    entries = read_actions(path=path)
    assert entries[0]["success"] is False
