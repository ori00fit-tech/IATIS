"""tests/test_migrations.py — storage/migrations.py runner semantics.

Runs against the autouse fake_d1 fixture (tests/conftest.py): real SQL
semantics on an in-memory sqlite3 standing in for D1, transport faked.
"""
from __future__ import annotations

import pytest

from storage import migrations
from storage.d1_client import D1Error


def _version() -> int:
    return migrations.current_version()


def test_fresh_apply_reaches_latest():
    applied = migrations.apply_migrations()
    assert applied  # at least baseline + decision_provenance
    assert _version() == migrations.LATEST_VERSION


def test_reapply_is_noop():
    migrations.apply_migrations()
    assert migrations.apply_migrations() == []
    assert _version() == migrations.LATEST_VERSION


def test_provenance_columns_exist_after_apply(fake_d1):
    migrations.apply_migrations()
    cols = {r[1] for r in fake_d1.execute("PRAGMA table_info(decisions)").fetchall()}
    assert {"git_commit", "config_hash", "data_versions"} <= cols


def test_duplicate_column_is_tolerated(fake_d1):
    """A table that already carries a migration's column (fresh install
    where the module DDL included it) must not break the migration."""
    from storage import decision_db
    fake_d1.execute(decision_db._CREATE_DECISIONS)
    # Simulate a fresh-install table that already has one of the columns.
    fake_d1.execute("ALTER TABLE decisions ADD COLUMN git_commit TEXT")
    fake_d1.commit()

    applied = migrations.apply_migrations()
    assert "decision_provenance" in applied
    assert _version() == migrations.LATEST_VERSION
    cols = {r[1] for r in fake_d1.execute("PRAGMA table_info(decisions)").fetchall()}
    assert {"git_commit", "config_hash", "data_versions"} <= cols


def test_failed_migration_is_not_stamped(monkeypatch):
    """A genuinely failing statement aborts WITHOUT stamping its version,
    so the migration retries in full on the next run."""
    bad = migrations.MIGRATIONS + [
        (migrations.LATEST_VERSION + 1, "broken", ["SELECT * FROM no_such_table_xyz"]),
    ]
    monkeypatch.setattr(migrations, "MIGRATIONS", bad)
    monkeypatch.setattr(migrations, "LATEST_VERSION", bad[-1][0])

    with pytest.raises(D1Error):
        migrations.apply_migrations()
    # Everything before the broken one is stamped; the broken one is not.
    assert _version() == bad[-2][0]


def test_apply_migrations_safe_never_raises(monkeypatch):
    bad = [(1, "broken", ["SELECT * FROM no_such_table_xyz"])]
    monkeypatch.setattr(migrations, "MIGRATIONS", bad)
    monkeypatch.setattr(migrations, "LATEST_VERSION", 1)
    assert migrations.apply_migrations_safe() == []


def test_version_zero_before_any_apply():
    assert _version() == 0
