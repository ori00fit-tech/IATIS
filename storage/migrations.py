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
