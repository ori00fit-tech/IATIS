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
    # A fresh-install table created from decision_db's DDL already carries
    # ALL of migration 2's columns — the migration must tolerate every one.
    fake_d1.execute(decision_db._CREATE_DECISIONS)
    fake_d1.commit()

    applied = migrations.apply_migrations()
    assert "decision_provenance" in applied
    assert _version() == migrations.LATEST_VERSION
    cols = {r[1] for r in fake_d1.execute("PRAGMA table_info(decisions)").fetchall()}
    assert {"git_commit", "config_hash", "data_versions"} <= cols


# --- Production incident regression (2026-08-23) -----------------------------
#
# Root cause: apply_migrations()'s guard blocks for research_matrix_cells/
# research_matrix_families/research_matrix_ai_recommendations used to call
# the module's FULL _init(con) — which, after Phase 3C, also creates
# idx_rmf_source_recommendation (a UNIQUE INDEX on research_matrix_families.
# source_recommendation_id). On a PRE-Phase-3C production table (this
# column doesn't exist yet), calling that from an EARLIER, unrelated
# migration's guard (18's own ALTER targets research_matrix_cells, nothing
# to do with families) tried to create that index BEFORE migration 21's own
# ALTER TABLE had a chance to add the column — "no such column:
# source_recommendation_id", a D1Error NOT in _ALREADY_APPLIED_MARKERS, so
# it aborted apply_migrations() entirely. Nothing from that point onward
# ever got stamped, and — because apply_migrations_safe() is (correctly)
# non-fatal at API boot — EVERY subsequent restart repeated the identical
# failure forever, while EVERY ordinary storage.research_matrix call's own
# _init(con) also independently hit the same missing-column error (GET
# /research/matrix/families -> 500 in production). Fixed by narrowing
# those three guards to the module's bare CREATE TABLE IF NOT EXISTS DDL
# only, matching every other guard's own established convention in this
# file (decision_db._CREATE_DECISIONS, reconciliation._DDL, ...).


def _legacy_pre_phase3c_research_matrix_families_schema(con) -> None:
    """The EXACT research_matrix_families shape from before Phase 3C —
    hand-written here (not imported from storage.research_matrix, whose
    _DDL_FAMILIES string already includes source_recommendation_id) so
    this test genuinely exercises migrating an OLD production table, not
    a fresh-install one that already has the column baked in."""
    con.execute("""
        CREATE TABLE research_matrix_families (
            family_id       TEXT PRIMARY KEY,
            planned_n       INTEGER NOT NULL,
            family_alpha    REAL NOT NULL,
            symbols_json    TEXT,
            created_at      TEXT NOT NULL
        )
    """)
    con.execute(
        "INSERT INTO research_matrix_families (family_id, planned_n, family_alpha, symbols_json, created_at) "
        "VALUES ('preexisting-fam', 5, 0.05, '[\"EURUSD\"]', '2026-01-01T00:00:00+00:00')"
    )
    con.commit()


def test_old_research_matrix_families_schema_migrates_to_current(fake_d1):
    """The exact incident, reproduced and proven fixed: an old production
    research_matrix_families table (no source_recommendation_id) migrates
    cleanly to LATEST_VERSION, ends up with the column AND the unique
    index, and its pre-existing row survives untouched with a NULL
    source_recommendation_id (never another value the migration never
    said to fabricate)."""
    _legacy_pre_phase3c_research_matrix_families_schema(fake_d1)

    applied = migrations.apply_migrations()
    assert "matrix_family_source_recommendation_column" in applied
    assert _version() == migrations.LATEST_VERSION

    cols = {r[1] for r in fake_d1.execute("PRAGMA table_info(research_matrix_families)").fetchall()}
    assert "source_recommendation_id" in cols

    idx_names = {
        r[0] for r in fake_d1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_matrix_families'"
        ).fetchall()
    }
    assert "idx_rmf_source_recommendation" in idx_names

    row = dict(fake_d1.execute("SELECT * FROM research_matrix_families WHERE family_id='preexisting-fam'").fetchone())
    assert row["planned_n"] == 5  # untouched
    assert row["source_recommendation_id"] is None  # migration never fabricates a value


def test_old_schema_migration_is_idempotent_on_reapply(fake_d1):
    _legacy_pre_phase3c_research_matrix_families_schema(fake_d1)
    migrations.apply_migrations()
    assert migrations.apply_migrations() == []
    assert _version() == migrations.LATEST_VERSION


def test_source_recommendation_id_unique_index_permits_many_nulls_after_migration(fake_d1):
    """Ordinary, hand-typed Matrix families (NULL source_recommendation_id)
    must keep working exactly as before Phase 3C after migrating an old
    table -- a UNIQUE index must never treat multiple NULLs as duplicates
    of each other."""
    _legacy_pre_phase3c_research_matrix_families_schema(fake_d1)
    migrations.apply_migrations()

    from storage import research_matrix as rm_storage
    rm_storage.upsert_family("famA", planned_n=1, family_alpha=0.05)
    rm_storage.upsert_family("famB", planned_n=1, family_alpha=0.05)
    assert rm_storage.get_family("famA") is not None
    assert rm_storage.get_family("famB") is not None


def test_source_recommendation_id_unique_index_rejects_duplicates_after_migration(fake_d1):
    """The other half: a converted family's non-NULL source_recommendation_
    id must be genuinely unique -- one recommendation maps to at most one
    family, enforced at the schema level even on a table that was just
    migrated from the old shape, not only on a fresh install."""
    from storage.d1_client import D1Error

    _legacy_pre_phase3c_research_matrix_families_schema(fake_d1)
    migrations.apply_migrations()

    from storage import research_matrix as rm_storage
    rm_storage.upsert_family("famA", planned_n=1, family_alpha=0.05, source_recommendation_id="MATRIX-AI-abc123")
    with pytest.raises(D1Error):
        rm_storage.upsert_family("famB", planned_n=1, family_alpha=0.05, source_recommendation_id="MATRIX-AI-abc123")


# --- Phase 1 (Research Matrix Normalization, 2026-08-23) regression ---------


def _legacy_pre_phase1_research_matrix_cells_schema(con) -> None:
    """The EXACT research_matrix_cells shape from before Phase 1 (no
    engine/engine_version/timeframe columns) — hand-written, not imported
    from storage.research_matrix's own _DDL_CELLS (which already includes
    them for fresh installs), so this test genuinely exercises migrating
    an OLD production table."""
    con.execute("""
        CREATE TABLE research_matrix_cells (
            cell_id TEXT PRIMARY KEY, family_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            symbol TEXT NOT NULL, bundle_json TEXT NOT NULL, risk_preset TEXT NOT NULL,
            confluence_overrides_json TEXT, engine_variants_json TEXT, data_provider TEXT,
            research_code_commit TEXT, status TEXT NOT NULL, rejection_reason TEXT,
            stage_a_mission_id TEXT, stage_a_trial_number INTEGER, stage_a_metrics_json TEXT,
            stage_a_p_value REAL, lead_id TEXT, stage_b_validation_id TEXT, stage_b_verdict TEXT,
            requeue_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    con.execute(
        "INSERT INTO research_matrix_cells (cell_id, family_id, fingerprint, symbol, bundle_json, risk_preset, "
        "status, created_at, updated_at) VALUES "
        "('MATRIX-CELL-preexisting', 'fam1', 'preexisting', 'EURUSD', "
        "'{\"name\":\"legacy confluence\",\"engines\":[\"smc\",\"nnfx\"],\"timeframes\":[\"H1\",\"H4\"]}', "
        "'balanced', 'QUEUED', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    con.commit()


def test_old_research_matrix_cells_schema_migrates_to_current(fake_d1):
    """Migration 22, reproduced and proven: an old production
    research_matrix_cells table (no engine/engine_version/timeframe)
    migrates cleanly to LATEST_VERSION, ends up with all three columns
    AND both indexes, and its pre-existing (multi-engine, confluence-
    research) row survives untouched with all three columns NULL — never
    coerced into a fabricated single engine/timeframe."""
    _legacy_pre_phase1_research_matrix_cells_schema(fake_d1)

    applied = migrations.apply_migrations()
    assert "matrix_cell_engine_timeframe_columns" in applied
    assert _version() == migrations.LATEST_VERSION

    cols = {r[1] for r in fake_d1.execute("PRAGMA table_info(research_matrix_cells)").fetchall()}
    assert {"engine", "engine_version", "timeframe"} <= cols

    idx_names = {
        r[0] for r in fake_d1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_matrix_cells'"
        ).fetchall()
    }
    assert {"idx_rmc_engine", "idx_rmc_timeframe"} <= idx_names

    row = dict(fake_d1.execute("SELECT * FROM research_matrix_cells WHERE cell_id='MATRIX-CELL-preexisting'").fetchone())
    assert row["symbol"] == "EURUSD"  # untouched
    assert row["engine"] is None
    assert row["engine_version"] is None
    assert row["timeframe"] is None


def test_old_research_matrix_cells_schema_migration_is_idempotent_on_reapply(fake_d1):
    _legacy_pre_phase1_research_matrix_cells_schema(fake_d1)
    migrations.apply_migrations()
    assert migrations.apply_migrations() == []
    assert _version() == migrations.LATEST_VERSION


# --- Phase 3 (Hypothesis Execution / Mission Binding, 2026-08-23) regression -


def test_old_research_matrix_cells_schema_migrates_source_hypothesis_column(fake_d1):
    """Migration 23, reproduced against the SAME pre-Phase-1 legacy schema
    (missing both the Phase 1 engine/timeframe columns AND the Phase 3
    source_hypothesis_id column) -- both migrations must apply cleanly in
    sequence, ending with all columns/indexes present and the pre-existing
    row's source_hypothesis_id NULL, never fabricated."""
    _legacy_pre_phase1_research_matrix_cells_schema(fake_d1)

    applied = migrations.apply_migrations()
    assert "matrix_cell_source_hypothesis_column" in applied
    assert _version() == migrations.LATEST_VERSION

    cols = {r[1] for r in fake_d1.execute("PRAGMA table_info(research_matrix_cells)").fetchall()}
    assert "source_hypothesis_id" in cols

    idx_names = {
        r[0] for r in fake_d1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_matrix_cells'"
        ).fetchall()
    }
    assert "idx_rmc_source_hypothesis" in idx_names

    row = dict(fake_d1.execute("SELECT * FROM research_matrix_cells WHERE cell_id='MATRIX-CELL-preexisting'").fetchone())
    assert row["source_hypothesis_id"] is None


def test_source_hypothesis_id_permits_multiple_cells_after_migration(fake_d1):
    """Not unique: after migrating an old table, the SAME hypothesis may
    still legitimately map to two different, coexisting cells (e.g. two
    different research code commits)."""
    _legacy_pre_phase1_research_matrix_cells_schema(fake_d1)
    migrations.apply_migrations()

    from backtest import research_matrix as rm
    from storage import research_matrix as rm_storage

    bundle = {"name": "PA v2", "timeframes": ["H1"], "engines": ["price_action"], "indicators": [], "context_filters": []}
    c1 = rm.MatrixCellSpec(symbol="EURUSD", bundle=bundle, risk_preset="balanced", research_code_commit="commit-A")
    c2 = rm.MatrixCellSpec(symbol="EURUSD", bundle=bundle, risk_preset="balanced", research_code_commit="commit-B")
    rm_storage.upsert_family("famX", planned_n=2, family_alpha=0.05)
    rm_storage.upsert_cells(
        [c1, c2], "famX",
        source_hypothesis_ids={c1.cell_id: "DISCOVERY-HYPOTHESIS-abc", c2.cell_id: "DISCOVERY-HYPOTHESIS-abc"},
    )
    matching = rm_storage.list_cells(source_hypothesis_id="DISCOVERY-HYPOTHESIS-abc")
    assert len(matching) == 2


def test_migration_touched_tables_includes_the_matrix_tables():
    tables = migrations._migration_touched_tables()
    assert "research_matrix_families" in tables
    assert "research_matrix_cells" in tables
    assert "research_matrix_ai_recommendations" in tables


def test_print_report_shows_version_columns_and_indexes(fake_d1, capsys):
    """The operational verification command (--report): confirms it reads
    the REAL schema, not a cached/assumed one -- run it before and after
    migrating an old table and check the reported columns/indexes change
    accordingly."""
    _legacy_pre_phase3c_research_matrix_families_schema(fake_d1)

    migrations._print_report()
    before = capsys.readouterr().out
    assert "source_recommendation_id" not in before
    assert "idx_rmf_source_recommendation" not in before

    migrations.apply_migrations()

    migrations._print_report()
    after = capsys.readouterr().out
    assert f"schema_version: {migrations.LATEST_VERSION}" in after
    assert "source_recommendation_id" in after
    assert "idx_rmf_source_recommendation" in after


def test_migration_guards_never_call_the_full_init_for_migrated_tables():
    """Static proof of the actual fix, not just an outcome-level test: the
    guard blocks for research_matrix_cells/research_matrix_families/
    research_matrix_ai_recommendations must reference the module's bare
    table DDL constant, never the module's own _init (which also builds
    indexes that can depend on a column an EARLIER, unrelated migration's
    guard would be running before that column's own ALTER TABLE has had a
    chance to execute)."""
    import inspect

    source = inspect.getsource(migrations.apply_migrations)
    guard_block = source[source.index('if any("ALTER TABLE research_matrix_cells"'):]
    assert "research_matrix._init(con)" not in guard_block.split("for sql in statements")[0]
    assert "matrix_ai_recommendations._init(con)" not in guard_block.split("for sql in statements")[0]


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


# --- Phase 8B (Confluence Governed Identity, 2026-08-24) regression --------


def _legacy_pre_phase8b_research_hypotheses_schema(con) -> None:
    """The EXACT research_hypotheses shape from before Phase 8B (no
    decision_type/bundle_id/bundle_version/bundle_json columns) —
    hand-written, not imported from storage.hypothesis_factory's own
    _DDL_HYPOTHESES (which already includes them for fresh installs), so
    this test genuinely exercises migrating an OLD production table."""
    con.execute("""
        CREATE TABLE research_hypotheses (
            hypothesis_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, engine TEXT NOT NULL,
            engine_version TEXT NOT NULL, timeframe TEXT NOT NULL, risk_preset TEXT NOT NULL,
            claim TEXT NOT NULL, matrix_cell_fingerprint TEXT NOT NULL, proposed_at TEXT NOT NULL
        )
    """)
    con.execute(
        "INSERT INTO research_hypotheses (hypothesis_id, symbol, engine, engine_version, timeframe, "
        "risk_preset, claim, matrix_cell_fingerprint, proposed_at) VALUES "
        "('DISCOVERY-HYPOTHESIS-preexisting', 'EURUSD', 'price_action', 'v2', 'H1', 'balanced', "
        "'pre-existing hypothesis claim', 'preexisting', '2026-01-01T00:00:00+00:00')"
    )
    con.commit()


def test_old_research_hypotheses_schema_migrates_to_current(fake_d1):
    """Migration 24, reproduced and proven: an old production research_
    hypotheses table (no decision_type/bundle_id/bundle_version/
    bundle_json) migrates cleanly to LATEST_VERSION, ends up with all
    four columns AND the new index, and its pre-existing (SINGLE_ENGINE,
    by construction — generate_confluence_hypotheses() did not exist
    before this migration) row survives untouched, correctly backfilled
    to decision_type='SINGLE_ENGINE', with the three bundle columns
    NULL — never fabricated."""
    _legacy_pre_phase8b_research_hypotheses_schema(fake_d1)

    applied = migrations.apply_migrations()
    assert "hypothesis_confluence_columns" in applied
    assert _version() == migrations.LATEST_VERSION

    cols = {r[1] for r in fake_d1.execute("PRAGMA table_info(research_hypotheses)").fetchall()}
    assert {"decision_type", "bundle_id", "bundle_version", "bundle_json"} <= cols

    idx_names = {
        r[0] for r in fake_d1.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='research_hypotheses'"
        ).fetchall()
    }
    assert "idx_rh_decision_type" in idx_names

    row = dict(fake_d1.execute(
        "SELECT * FROM research_hypotheses WHERE hypothesis_id='DISCOVERY-HYPOTHESIS-preexisting'"
    ).fetchone())
    assert row["symbol"] == "EURUSD"  # untouched
    assert row["decision_type"] == "SINGLE_ENGINE"
    assert row["bundle_id"] is None
    assert row["bundle_version"] is None
    assert row["bundle_json"] is None


def test_old_research_hypotheses_schema_migration_is_idempotent_on_reapply(fake_d1):
    _legacy_pre_phase8b_research_hypotheses_schema(fake_d1)
    migrations.apply_migrations()
    assert migrations.apply_migrations() == []
    assert _version() == migrations.LATEST_VERSION


def test_migration_touched_tables_includes_research_hypotheses():
    tables = migrations._migration_touched_tables()
    assert "research_hypotheses" in tables


def test_migration_guard_for_research_hypotheses_never_calls_the_full_init():
    """Same static proof as migration 22/23's own regression test,
    extended to the new guard: bare _DDL_HYPOTHESES only, never the
    module's full _init(con)."""
    import inspect

    source = inspect.getsource(migrations.apply_migrations)
    guard_block = source[source.index('if any("ALTER TABLE research_hypotheses"'):]
    assert "hypothesis_factory._init(con)" not in guard_block.split("for sql in statements")[0]
