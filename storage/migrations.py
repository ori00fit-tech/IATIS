"""
storage/migrations.py
----------------------
Versioned, additive schema migrations for the D1 backend (production
audit item M6; institutional gap analysis S4).

Why this exists:
    Every storage module creates its own tables at runtime with
    `CREATE TABLE IF NOT EXISTS` — fine for NEW tables, but useless for
    evolving an EXISTING one: `IF NOT EXISTS` never alters a table that
    is already there, so a column added to a module's DDL string silently
    exists on fresh installs and silently does NOT exist in production.
    That drift is exactly what a version table prevents.

Contract:
    - `schema_version` records every applied migration (version, name,
      applied_at). The table itself is created here on first use.
    - MIGRATIONS is an append-only, ordered list. Never edit or reorder
      an entry that has shipped — append a new version instead.
    - Migrations must be ADDITIVE (CREATE TABLE / ADD COLUMN / CREATE
      INDEX). D1 executes each statement independently over HTTP, so a
      multi-statement migration is not atomic as a group; additive
      statements are individually safe to re-run because the runner
      tolerates "duplicate column name" / "already exists" errors.
      Destructive changes (DROP/RENAME) are deliberately unsupported.
    - A migration's version is stamped only after ALL of its statements
      succeeded (or were tolerated as already-applied). Any other error
      aborts before stamping, so a partial migration is retried in full
      on the next run — which additivity makes safe.

Usage:
    apply_migrations()                      # from code (scheduler boot)
    python -m storage.migrations            # apply, from a deploy step
    python -m storage.migrations --status   # current vs latest, no writes
    python -m storage.migrations --sql      # print SQL for manual wrangler use
    python -m storage.migrations --report   # version + real columns/indexes
                                             # of every migration-touched
                                             # table, read directly from
                                             # whatever D1_WORKER_URL points
                                             # at right now — the operational
                                             # verification command for
                                             # confirming a deploy's schema
                                             # actually matches the code
                                             # (production incident 2026-08-23)
"""
from __future__ import annotations

from datetime import datetime, timezone

from storage import d1_client
from storage.d1_client import D1Error
from utils.logger import get_logger

logger = get_logger(__name__)

_DDL_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""

# Error fragments that mean "this additive statement already ran" — safe
# to treat as success. Matched case-insensitively against the D1 error.
_ALREADY_APPLIED_MARKERS = (
    "duplicate column name",
    "already exists",
)

# ---------------------------------------------------------------------------
# The migration ledger — append-only. (version, name, [statements])
# ---------------------------------------------------------------------------

MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "baseline",
        # Stamp-only: the pre-existing schema (decisions, engine_votes,
        # outcomes, engine_performance, experiences, shadow_signals, ...)
        # is created by each module's runtime DDL and cloudflare/schema.sql.
        # This entry marks "the schema as of 2026-07-16" as version 1.
        [],
    ),
    (
        2,
        "decision_provenance",
        # Gap analysis M2: every decision carries the exact code version,
        # config fingerprint, and per-timeframe data version that produced
        # it. Fresh installs get these columns from decision_db's DDL;
        # this migration brings the existing production table up to match.
        [
            "ALTER TABLE decisions ADD COLUMN git_commit TEXT",
            "ALTER TABLE decisions ADD COLUMN config_hash TEXT",
            "ALTER TABLE decisions ADD COLUMN data_versions TEXT",
        ],
    ),
    (
        3,
        "journal_tags",
        # Trade Journal (dashboard): operator-assigned tags on an outcome
        # row, stored as a JSON array of short strings. Annotation only —
        # never read by any gate, weight, or measurement (storage/journal.py).
        [
            "ALTER TABLE outcomes ADD COLUMN tags TEXT",
        ],
    ),
    (
        4,
        "feature_mining_column",
        # Feature Mining / Hypothesis Discovery Phase 1 (2026-07-30):
        # backtest/mission_validator.py now records a per-symbol
        # backtest.feature_mining.FeatureMiningResult alongside the existing
        # monte_carlo/walk_forward/robustness blobs. Diagnostic only — never
        # a criterion, never registry.json/config.yaml evidence.
        [
            "ALTER TABLE research_mission_validation_results ADD COLUMN feature_mining_json TEXT",
        ],
    ),
    (
        5,
        "mission_reproducibility_fingerprint",
        # Diagnostic Infrastructure Phase 1 (2026-08-02): reuses the existing
        # research/manifest.py reproducibility module (previously only wired
        # into standalone research scripts) for Mission Center trials/
        # validations. All three columns are informational — never block a
        # trial or validation, never write to registry.json/config.yaml.
        [
            "ALTER TABLE research_mission_trials_v2 ADD COLUMN fingerprint_json TEXT",
            "ALTER TABLE research_mission_validations ADD COLUMN candidate_lock_json TEXT",
            "ALTER TABLE research_mission_validations ADD COLUMN date_overlap_json TEXT",
        ],
    ),
    (
        6,
        "shadow_book_regime_column",
        # Diagnostic Infrastructure Phase 1 (2026-08-02): gate_ledger() can
        # now break down rejections by regime, not just primary_gate.
        [
            "ALTER TABLE shadow_signals ADD COLUMN regime TEXT",
        ],
    ),
    (
        7,
        "validation_mode_column",
        # Forensic Audit Phase 1, item D (2026-08-02): SAME_SYMBOL (default
        # going forward, via the API) vs. explicit CROSS_SYMBOL. The DDL/
        # migration default is deliberately 'CROSS_SYMBOL' — the OPPOSITE of
        # the new API default — because it honestly describes what every
        # pre-existing row structurally was (validation_symbols always
        # excluded trial_symbol, always >=2 entries), not a behavior change
        # for historical rows.
        [
            "ALTER TABLE research_mission_validations ADD COLUMN validation_mode TEXT DEFAULT 'CROSS_SYMBOL'",
        ],
    ),
    (
        8,
        "mission_trial_attempts",
        # Mission Center Research Rigor Phase 1 (2026-08-06): an append-only
        # "attempt started" marker, written right before a trial's backtest
        # evaluation runs (mission_runner.py), so a resumed mission can
        # report how many trials were lost to a mid-flight process crash
        # (never recorded to research_mission_trials_v2 because the process
        # died before that INSERT). Purely informational — never blocks a
        # trial or a resume, never a criterion.
        [
            """CREATE TABLE IF NOT EXISTS research_mission_trial_attempts (
                mission_id TEXT NOT NULL, symbol TEXT NOT NULL, trial_number INTEGER NOT NULL,
                started_at TEXT NOT NULL, PRIMARY KEY (mission_id, symbol, trial_number)
            )""",
        ],
    ),
    (
        9,
        "validation_significance_column",
        # Mission Center Research Rigor Phase 2 (2026-08-XX): each
        # validation result now also records an autocorrelation-adjusted
        # (effective sample size) significance check alongside the
        # existing feature_mining blob — same insertion point (raw trade
        # records are only available at validation time). Diagnostic
        # only — never a VALIDATION_CRITERIA entry.
        [
            "ALTER TABLE research_mission_validation_results ADD COLUMN significance_json TEXT",
        ],
    ),
    (
        10,
        "validation_regime_robustness_column",
        # Mission Center Research Rigor Phase 3 (2026-08-XX): does the
        # candidate's edge hold across regimes it actually traded in, or
        # is it concentrated entirely in one (the same curve-fitting risk
        # MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD already guards for
        # symbols)? Reuses BacktestMetrics.by_regime, already computed —
        # no new trade-level access needed. Diagnostic only.
        [
            "ALTER TABLE research_mission_validation_results ADD COLUMN regime_robustness_json TEXT",
        ],
    ),
    (
        11,
        "validation_stability_column",
        # Mission Center Research Rigor Phase 4 (2026-08-XX): a fractional
        # companion to the existing all-or-nothing "robustness_all_stable"
        # criterion — what share of the candidate's own swept risk params
        # are STABLE, reusing backtest.robustness.run_robustness()'s
        # already-computed per-parameter verdicts. Diagnostic only.
        [
            "ALTER TABLE research_mission_validation_results ADD COLUMN stability_json TEXT",
        ],
    ),
    (
        12,
        "validation_cost_stress_column",
        # Mission Center Research Rigor Phase 5 (2026-08-XX): does the
        # candidate's edge survive commission/slippage/swap costs scaled
        # 1.5x/2x/3x above the candidate's own real, measured baseline
        # values? Re-runs backtest.optimizer.evaluate_point() at each
        # stress level — the same primitive every other evaluation in
        # this pipeline uses. Diagnostic only.
        [
            "ALTER TABLE research_mission_validation_results ADD COLUMN cost_stress_json TEXT",
        ],
    ),
    (
        13,
        "validation_discovery_score_column",
        # Mission Center Research Rigor Phase 6 (2026-08-XX): an equally-
        # weighted average of the significance/regime_robustness/stability/
        # cost_stress diagnostics above, for at-a-glance triage. Every
        # component is read verbatim from an already-computed diagnostic —
        # no new backtest evaluation, no invented weighting. Diagnostic
        # only, never a criterion, never used to rank/select a candidate.
        [
            "ALTER TABLE research_mission_validation_results ADD COLUMN discovery_score_json TEXT",
        ],
    ),
    (
        14,
        "provider_benchmark_evidence_series_column",
        # Provider Benchmark & Data Quality Lab Phase 1b (2026-08-XX) — the
        # capped (close, consensus_close, diff_pct) per-bar series an
        # Evidence drill-down chart overlays, computed once per (provider,
        # symbol, timeframe) point alongside the existing aggregate scores.
        # Advisory/diagnostic only, same as every other column on this table.
        [
            "ALTER TABLE provider_benchmark_results ADD COLUMN evidence_series_json TEXT",
        ],
    ),
    (
        15,
        "fills_latency_column",
        # TCA async-fill fix (2026-08-17) — storage/execution_quality.py's
        # resolve_pending_fill() records how long a fill sat PENDING before
        # the broker confirmed its real price (async path only; the
        # synchronous log_fill() path leaves this NULL, since the concept
        # doesn't apply the same way there).
        [
            "ALTER TABLE fills ADD COLUMN fill_latency_seconds REAL",
        ],
    ),
    (
        16,
        "validation_mission_family_significance_column",
        # Evidence Integrity / Multiple Testing (Slice 3, 2026-08-19) — a
        # validated candidate's own Bonferroni-corrected significance
        # against how many configurations its ORIGINAL mission actually
        # searched (reuses backtest.multiple_testing.bonferroni_alpha/
        # trial_p_value/classify_significance verbatim, no new statistical
        # methodology). Distinct from the existing significance_json
        # column on research_mission_validation_results (autocorrelation-
        # within-one-candidate) — this corrects for selection-among-many-
        # candidates. Unlike every other diagnostic column, this ONE gates
        # the top-tier overall_verdict (STRONG_LEAD/SAME_SYMBOL_CONFIRMED)
        # — see backtest/mission_validator.py.
        [
            "ALTER TABLE research_mission_validations ADD COLUMN mission_family_significance_json TEXT",
        ],
    ),
    (
        17,
        "reconciliation_checks_skip_reason_kind_column",
        # Unified Post-Trade Control / Incident Register (2026-08-XX) —
        # execution/reconciliation.py's reconcile() now tags a "skipped"
        # result with WHY it skipped ("not_live" = healthy paper mode,
        # "control_failure" = the control itself is genuinely unavailable),
        # so execution/post_trade_monitor.py's scan can tell the two apart
        # without string-matching `reason`. Advisory/diagnostic only.
        [
            "ALTER TABLE reconciliation_checks ADD COLUMN skip_reason_kind TEXT",
        ],
    ),
    (
        18,
        "matrix_cell_requeue_count",
        # Hypothesis Discovery Engine Phase 2A — Evidence Read Model
        # (2026-08-XX): storage.research_matrix.requeue_stale_running_cells()
        # now increments this counter every time a crashed/stale RUNNING
        # cell is returned to QUEUED, so "how many times was this cell
        # resumed" is real, queryable evidence (backtest/matrix_evidence.py)
        # instead of only inferable from logs. Diagnostic only — never a
        # gate, never registry.json/config.yaml.
        [
            "ALTER TABLE research_matrix_cells ADD COLUMN requeue_count INTEGER NOT NULL DEFAULT 0",
        ],
    ),
    (
        19,
        "matrix_cell_research_code_commit",
        # Hypothesis Discovery Engine Phase 2C — Evidence Comparison
        # (2026-08-XX): backtest.research_matrix.MatrixCellSpec has always
        # carried research_code_commit in memory (it's already folded into
        # the cell's fingerprint hash, per compute_cell_fingerprint()) but
        # the human-readable commit string was never persisted as its own
        # column — only buried, unrecoverable, inside the opaque hash. A
        # cross-cell/cross-family comparison needs this as a real,
        # queryable field to detect "did this result change because of a
        # code change, not the hypothesis." Diagnostic only — never a
        # gate, never registry.json/config.yaml.
        [
            "ALTER TABLE research_matrix_cells ADD COLUMN research_code_commit TEXT",
        ],
    ),
    (
        20,
        "matrix_ai_recommendation_converted_at_column",
        # Hypothesis Discovery Engine Phase 3C — Controlled Recommendation
        # Conversion (2026-08-XX): a recommendation's status vocabulary
        # gains a fourth, terminal value, CONVERTED (DRAFT -> APPROVED ->
        # CONVERTED), set only by storage.matrix_ai_recommendations.
        # convert_recommendation()'s own atomic CAS. converted_at is a
        # denormalized "latest state" cache on the main row, mirroring how
        # reviewed_at already sits there — the authoritative detail lives
        # in the new research_matrix_ai_recommendation_conversions table
        # (a brand-new table, no migration needed for it). Never a gate,
        # never registry.json/config.yaml.
        [
            "ALTER TABLE research_matrix_ai_recommendations ADD COLUMN converted_at TEXT",
        ],
    ),
    (
        21,
        "matrix_family_source_recommendation_column",
        # Hypothesis Discovery Engine Phase 3C — links a family that was
        # created via a controlled AI-recommendation conversion back to
        # the recommendation it came from, WITHOUT ever creating an HXXX
        # identity or touching registry.json (source_recommendation_id is
        # a pure provenance pointer, exactly like research_matrix_cells.
        # research_code_commit already is). NULL for every family created
        # the normal way (a human typing symbols/bundles by hand) — this
        # migration changes no existing family's meaning. The UNIQUE index
        # is the second, independent half of Phase 3C's replay-prevention
        # pair (the first half is the atomic APPROVED->CONVERTED CAS on
        # the recommendation itself): even a bug that skipped the CAS
        # could not silently attach a second family to one recommendation.
        [
            "ALTER TABLE research_matrix_families ADD COLUMN source_recommendation_id TEXT",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_rmf_source_recommendation ON research_matrix_families(source_recommendation_id)",
        ],
    ),
]

LATEST_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def current_version(con: "d1_client.D1Connection | None" = None) -> int:
    """Highest applied version; 0 when no migration has ever run."""
    def _read(c) -> int:
        c.execute(_DDL_VERSION_TABLE)
        row = c.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"]) if row and row["v"] is not None else 0

    if con is not None:
        return _read(con)
    with d1_client.d1_connection() as c:
        return _read(c)


def _tolerable(exc: D1Error) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _ALREADY_APPLIED_MARKERS)


def apply_migrations() -> list[str]:
    """Apply every migration above the current version, in order.

    Returns the list of applied migration names (empty = already current).
    Raises D1Error if a statement fails for a non-"already applied" reason
    — in that case the failing version is NOT stamped and the whole
    migration re-runs (safely, being additive) on the next call.
    """
    applied: list[str] = []
    with d1_client.d1_connection() as con:
        ver = current_version(con)
        for version, name, statements in MIGRATIONS:
            if version <= ver:
                continue
            # Module-owned tables may not exist yet on a fresh deploy where
            # the pipeline never ran — ALTER TABLE would fail on them. Ensure
            # they exist before altering.
            if any("ALTER TABLE decisions" in s for s in statements):
                from storage import decision_db
                con.execute(decision_db._CREATE_DECISIONS)
            if any("ALTER TABLE outcomes" in s for s in statements):
                from storage import outcome_tracker
                outcome_tracker._init_db()
            if any("ALTER TABLE research_mission_validation_results" in s for s in statements):
                from storage import research_mission_validations
                research_mission_validations._init(con)
            if any("ALTER TABLE research_mission_trials_v2" in s for s in statements):
                from storage import research_missions
                research_missions._init(con)  # creates both research_missions + _trials_v2
            if any("ALTER TABLE research_mission_validations " in s for s in statements):
                # trailing space excludes "research_mission_validation_results"
                from storage import research_mission_validations
                research_mission_validations._init(con)
            if any("ALTER TABLE shadow_signals" in s for s in statements):
                from storage import shadow_book
                shadow_book._init_db()
            if any("ALTER TABLE provider_benchmark_results" in s for s in statements):
                from storage import provider_benchmark
                provider_benchmark._init(con)
            if any("ALTER TABLE fills" in s for s in statements):
                from storage import execution_quality
                execution_quality._init(con)
            if any("ALTER TABLE reconciliation_checks" in s for s in statements):
                from execution import reconciliation
                con.execute(reconciliation._DDL)
            if any("ALTER TABLE research_matrix_cells" in s for s in statements):
                from storage import research_matrix
                con.execute(research_matrix._DDL_CELLS)
            if any("ALTER TABLE research_matrix_ai_recommendations " in s for s in statements):
                # trailing space excludes "_reviews"/"_conversions"
                from storage import matrix_ai_recommendations
                con.execute(matrix_ai_recommendations._DDL_RECOMMENDATIONS)
            if any("ALTER TABLE research_matrix_families" in s for s in statements):
                from storage import research_matrix
                con.execute(research_matrix._DDL_FAMILIES)
            # NOTE: these guards call the bare CREATE TABLE IF NOT EXISTS
            # DDL only -- matching every other guard's own convention in
            # this function (decision_db._CREATE_DECISIONS, reconciliation.
            # _DDL, ...) -- deliberately NEVER the module's full _init(con).
            # Root cause of a real production incident (2026-08-23):
            # research_matrix._init() also creates idx_rmf_source_
            # recommendation, a UNIQUE INDEX on research_matrix_families.
            # source_recommendation_id -- a column ONLY migration 21's own
            # ALTER TABLE (below, in this SAME migration's statements list)
            # adds. Calling the full _init() from an EARLIER migration's
            # guard (18/19, whose own ALTER targets research_matrix_cells,
            # nothing to do with families) tried to create that index
            # against a still-unmigrated families table -- "no such column:
            # source_recommendation_id", a D1Error NOT in _ALREADY_APPLIED_
            # MARKERS, so it aborted apply_migrations() entirely, migration
            # 21 (and everything from wherever it first broke) never got
            # stamped, and EVERY subsequent boot repeated the identical
            # failure -- migrations permanently stuck, and every ordinary
            # storage.research_matrix call's own _init(con) hit the same
            # missing-column error on every request (GET /research/matrix/
            # families -> 500). Confirmed via direct reproduction against a
            # simulated pre-migration schema. Bare-table-only guards avoid
            # this entirely: a table's own indexes are only ever created by
            # _init() once its migration-added columns genuinely exist.
            for sql in statements:
                try:
                    con.execute(sql)
                except D1Error as exc:
                    if _tolerable(exc):
                        logger.info(
                            f"migration {version} '{name}': statement already "
                            f"applied, continuing ({sql.split()[0]}...)"
                        )
                        continue
                    logger.error(
                        f"migration {version} '{name}' FAILED (version not "
                        f"stamped, will retry next run): {exc}"
                    )
                    raise
            con.execute(
                "INSERT INTO schema_version (version, name, applied_at) VALUES (?,?,?)",
                (version, name, datetime.now(timezone.utc).isoformat()),
            )
            logger.info(f"migration {version} '{name}' applied")
            applied.append(name)
    return applied


def apply_migrations_safe() -> list[str]:
    """Boot-time wrapper: never raises. A migration failure must not stop
    the scheduler — the pipeline keeps running on the old schema (all
    consumers tolerate the missing columns) and the failure is logged
    loudly for the operator."""
    try:
        return apply_migrations()
    except Exception as exc:  # noqa: BLE001 — boot path must survive anything
        logger.error(f"schema migrations skipped (non-fatal at boot): {exc}")
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_sql() -> None:
    print("-- schema_version bookkeeping")
    print(_DDL_VERSION_TABLE.strip() + ";")
    for version, name, statements in MIGRATIONS:
        print(f"\n-- migration {version}: {name}")
        for sql in statements:
            print(sql.strip() + ";")
        print(
            "INSERT INTO schema_version (version, name, applied_at) "
            f"VALUES ({version}, '{name}', datetime('now'));"
        )


def _migration_touched_tables() -> list[str]:
    """Every table name any migration's own statements reference (ALTER
    TABLE / CREATE TABLE), in first-seen order, deduplicated. Used by
    --report so a NEW migration's table is picked up automatically
    without a second, hand-maintained list to keep in sync."""
    import re

    pattern = re.compile(r"(?:ALTER TABLE|CREATE TABLE(?:\s+IF NOT EXISTS)?)\s+(\w+)")
    seen: list[str] = []
    for _version, _name, statements in MIGRATIONS:
        for sql in statements:
            m = pattern.search(sql)
            if m and m.group(1) not in seen:
                seen.append(m.group(1))
    return seen


def _print_report() -> None:
    """Operational verification (production incident 2026-08-23): prints
    exactly what an operator needs to confirm a deploy's schema actually
    matches the code — migration version, and for every table any
    migration has ever touched, its real columns and real indexes,
    queried directly against whatever D1 D1_WORKER_URL currently points
    at (production when run there). Never trust `applied` from a prior
    apply_migrations() call alone; this reads the schema itself."""
    ver = current_version()
    state = "current" if ver >= LATEST_VERSION else f"BEHIND (latest {LATEST_VERSION})"
    print(f"schema_version: {ver} — {state}")
    print()
    with d1_client.d1_connection() as con:
        for table in _migration_touched_tables():
            try:
                cols = con.execute(f"PRAGMA table_info({table})").fetchall()
            except D1Error as exc:
                print(f"[{table}] ERROR reading schema: {exc}")
                continue
            if not cols:
                print(f"[{table}] does not exist")
                continue
            col_names = ", ".join(c["name"] for c in cols)
            print(f"[{table}] columns: {col_names}")
            idx_rows = con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
            ).fetchall()
            if idx_rows:
                for idx in idx_rows:
                    print(f"[{table}] index: {idx['name']}")
            print()


def main() -> int:
    import sys

    # CLI runs outside systemd (which is where the services get their
    # environment) — load .env exactly like scheduler.py does, so
    # `python -m storage.migrations --status` works from a plain shell
    # on the VPS without a manual `set -a; source .env` dance.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # --sql needs no environment at all

    if "--sql" in sys.argv:
        _print_sql()
        return 0
    if "--report" in sys.argv:
        _print_report()
        return 0
    if "--status" in sys.argv:
        ver = current_version()
        state = "current" if ver >= LATEST_VERSION else f"BEHIND (latest {LATEST_VERSION})"
        print(f"schema_version: {ver} — {state}")
        return 0 if ver >= LATEST_VERSION else 1
    applied = apply_migrations()
    print(f"applied: {applied or 'nothing — already at latest'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
