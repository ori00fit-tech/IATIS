"""
tests/test_ops_tier1.py
------------------------
Tier-1 operational gap closures: the watchdog (scheduler/API/disk checks,
alert cooldown, recovery notice) and the D1 backup (dump-verify-rotate).
All offline: fake D1 fixture, tmp paths, monkeypatched notifier.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

import scripts.backup_d1 as bkp
import scripts.watchdog as wd
from storage import d1_client
from storage.decision_db import init_db


# ── Watchdog ─────────────────────────────────────────────────────────────

@pytest.fixture
def wd_env(tmp_path, monkeypatch):
    jsonl = tmp_path / "decisions.jsonl"
    monkeypatch.setattr(wd, "DECISIONS_JSONL", jsonl)
    monkeypatch.setattr(wd, "STATE_PATH", tmp_path / "watchdog_state.json")
    sent: list[str] = []
    monkeypatch.setattr(wd, "_notify", lambda text: sent.append(text))
    monkeypatch.setattr(wd, "_check_api", lambda: None)   # network-free
    monkeypatch.setattr(wd, "_check_disk", lambda: None)
    return jsonl, sent


def test_fresh_decisions_file_is_all_clear(wd_env):
    jsonl, sent = wd_env
    jsonl.write_text("{}\n")
    assert wd.main() == 0
    assert sent == []


def test_stale_scheduler_alerts_once_within_cooldown(wd_env, monkeypatch):
    jsonl, sent = wd_env
    jsonl.write_text("{}\n")
    stale = time.time() - (wd.MAX_SILENCE_MIN + 60) * 60
    import os
    os.utime(jsonl, (stale, stale))

    assert wd.main() == 1
    assert len(sent) == 1 and "scheduler" in sent[0]
    assert wd.main() == 1                 # second run inside cooldown
    assert len(sent) == 1                 # no spam


def test_recovery_notice_sent_once(wd_env):
    jsonl, sent = wd_env
    jsonl.write_text("{}\n")
    import os
    stale = time.time() - (wd.MAX_SILENCE_MIN + 60) * 60
    os.utime(jsonl, (stale, stale))
    wd.main()                              # firing
    jsonl.write_text("{}\n{}\n")           # scheduler wrote again
    assert wd.main() == 0
    assert any("recovered" in m for m in sent)
    assert wd.main() == 0
    assert sum("recovered" in m for m in sent) == 1


def test_missing_decisions_file_is_a_failure(wd_env):
    _, sent = wd_env
    assert wd.main() == 1
    assert "missing" in sent[0]


# ── Backup ───────────────────────────────────────────────────────────────

def _seed_decisions(n=3):
    init_db()
    with d1_client.d1_connection() as con:
        for i in range(n):
            con.execute(
                "INSERT INTO decisions (ts, symbol, verdict) VALUES (?,?,?)",
                (f"2026-07-10T0{i}:00:00", "EURUSD", "NO_TRADE"),
            )


def test_backup_dumps_verifies_and_reports_counts(tmp_path, monkeypatch, capsys):
    _seed_decisions(3)
    monkeypatch.setattr(bkp, "PROJECT_ROOT", tmp_path)   # no real jsonl
    import sys
    monkeypatch.setattr(sys, "argv", ["backup_d1", "--out-dir", str(tmp_path / "bk")])
    assert bkp.main() == 0

    files = list((tmp_path / "bk").glob("d1_*.json.gz"))
    assert len(files) == 1
    with gzip.open(files[0], "rt") as f:
        dump = json.load(f)
    assert dump["tables"]["decisions"]["count"] == 3
    assert dump["tables"]["decisions"]["columns"]  # named columns preserved
    assert "backup OK" in capsys.readouterr().out


def test_backup_rotation_keeps_newest(tmp_path):
    out = tmp_path / "bk"
    out.mkdir()
    for i in range(20):
        (out / f"d1_202607{i:02d}_0000.json.gz").write_bytes(b"x")
    removed = bkp.rotate(out, "d1_*.json.gz", keep=14)
    assert removed == 6
    remaining = sorted(p.name for p in out.glob("d1_*.json.gz"))
    assert len(remaining) == 14
    assert remaining[0] == "d1_20260706_0000.json.gz"   # oldest kept


def test_backup_failure_alerts_and_exits_nonzero(tmp_path, monkeypatch):
    sent = []
    import execution.telegram_bot as tb
    monkeypatch.setattr(tb, "send_raw", lambda text, **k: sent.append(text) or True)

    def boom():
        raise RuntimeError("D1 unreachable")
    monkeypatch.setattr(d1_client, "d1_connection", boom)
    import sys
    monkeypatch.setattr(sys, "argv", ["backup_d1", "--out-dir", str(tmp_path / "bk")])
    assert bkp.main() == 1
    assert sent and "backup failed" in sent[0].lower()
