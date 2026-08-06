"""
storage/research_missions.py
-------------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — D1 storage for
research missions and their individual trials.

Two tables only, deliberately not the larger table list originally
proposed: `hypotheses` already exists as research/results/registry.json
(never written by this module — see backtest/mission_runner.py's own
header comment for the hard safety guarantee), individual mission
artifacts are also written as a plain reports/mission_{id}_{stamp}.json
file (same convention as backtest/robustness.py's run_robustness_suite),
and a per-symbol "edge library" is a future read-only aggregation over
registry.json, not a stored table. These two tables exist purely to make
a long-running mission resumable and to answer "how far along is this"
and "show me every trial" without re-reading thousands of report files.

Same `_DDL` + `_init(con)` idiom as core/data_confidence.py — idempotent
`CREATE TABLE IF NOT EXISTS`, called at the top of every public function.
No storage/migrations.py entry needed (that's only for ALTER TABLE on
already-shipped tables).

BUG FIX (2026-07-30, AI Research Lab Phase 3 design pass): the trials
table is named `research_mission_trials_v2`, not `research_mission_trials`
— a deliberate rename, not a typo. The original table's PRIMARY KEY was
`(mission_id, trial_number)`, omitting `symbol`. mission_runner.py gives
each symbol its own independent Optuna Study, so `trial.number` restarts
at 0 for every symbol — on any mission with 2+ symbols, the second
symbol's first trial collided on that primary key the instant it tried
to record, an unhandled D1 constraint error that propagated up and
marked the entire mission "failed". No multi-symbol mission has ever
completed past its first symbol as a result. `storage/migrations.py` is
additive-only (ADD COLUMN/CREATE TABLE/CREATE INDEX, never DROP/RENAME),
so it cannot widen an existing table's primary key — and `CREATE TABLE
IF NOT EXISTS` against the OLD table name would silently keep using the
broken schema on any deployment where it already exists. Renaming to a
new table name (correct PK: `(mission_id, symbol, trial_number)`) is the
only idiom-consistent way to force the fix to actually take effect
everywhere, old and new deployments alike. Nothing of value is lost: per
this module's own framing, a mission's trials are LEADS, never evidence
— the old table (if present on a given deployment) is simply dead and
unreferenced from here on.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_MISSIONS = """
CREATE TABLE IF NOT EXISTS research_missions (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    status              TEXT NOT NULL,   -- queued|running|finished|failed|cancelled
    sampler             TEXT NOT NULL,   -- grid|random|tpe|nsga2
    objective_metric    TEXT NOT NULL,
    symbols_json        TEXT NOT NULL,
    n_trials_per_symbol INTEGER NOT NULL,
    min_trades          INTEGER NOT NULL,
    seed                INTEGER NOT NULL,
    search_space_json   TEXT NOT NULL,
    config_json         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    finished_at         TEXT,
    error               TEXT
)
"""

_DDL_TRIALS = """
CREATE TABLE IF NOT EXISTS research_mission_trials_v2 (
    mission_id      TEXT NOT NULL,
    trial_number    INTEGER NOT NULL,
    symbol          TEXT NOT NULL,
    state           TEXT NOT NULL,      -- COMPLETE|PRUNED|FAIL|DUPLICATE
    objective_value REAL,
    params_json     TEXT NOT NULL,
    metrics_json    TEXT,
    trades          INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT NOT NULL,
    fingerprint_json TEXT,   -- Diagnostic Infrastructure Phase 1 (2026-08-02): git+dataset reproducibility fingerprint, see research/manifest.py
    PRIMARY KEY (mission_id, symbol, trial_number)
)
"""

# Mission Center Research Rigor Phase 1 (2026-08-06) — append-only
# "attempt started" marker, written right before a trial's backtest
# evaluation runs. Lets a resumed mission report how many trials were
# lost to a mid-flight process crash (never recorded to
# research_mission_trials_v2 because the process died before that
# INSERT). See storage/migrations.py version 8 for existing deployments.
_DDL_ATTEMPTS = """
CREATE TABLE IF NOT EXISTS research_mission_trial_attempts (
    mission_id      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    trial_number    INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    PRIMARY KEY (mission_id, symbol, trial_number)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_rmt_mission_symbol ON research_mission_trials_v2(mission_id, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_rmt_mission_objective ON research_mission_trials_v2(mission_id, objective_value)",
]


def _init(con) -> None:
    con.execute(_DDL_MISSIONS)
    con.execute(_DDL_TRIALS)
    con.execute(_DDL_ATTEMPTS)
    for idx in _INDEXES:
        con.execute(idx)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_mission(
    mission_id: str,
    name: str,
    sampler: str,
    objective_metric: str,
    symbols: list[str],
    n_trials_per_symbol: int,
    min_trades: int,
    seed: int,
    search_space: dict,
    config: dict,
    status: str = "queued",
) -> None:
    """Idempotent — used both at mission creation and at
    mission_runner.py startup (to flip status to "running" on a fresh or
    resumed run without erroring on a duplicate id)."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_missions
               (id, name, status, sampler, objective_metric, symbols_json,
                n_trials_per_symbol, min_trades, seed, search_space_json,
                config_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status""",
            (mission_id, name, status, sampler, objective_metric,
             json.dumps(symbols), n_trials_per_symbol, min_trades, seed,
             json.dumps(search_space), json.dumps(config), _now()),
        )


def set_mission_status(
    mission_id: str,
    status: str,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    with d1_client.d1_connection() as con:
        _init(con)
        if started:
            con.execute(
                "UPDATE research_missions SET status=?, started_at=? WHERE id=?",
                (status, _now(), mission_id),
            )
        elif finished:
            con.execute(
                "UPDATE research_missions SET status=?, finished_at=?, error=? WHERE id=?",
                (status, _now(), error, mission_id),
            )
        else:
            con.execute(
                "UPDATE research_missions SET status=?, error=? WHERE id=?",
                (status, error, mission_id),
            )


def get_mission(mission_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_missions WHERE id=?", (mission_id,)
        ).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def list_recent_missions(limit: int = 5, status: str | None = None) -> list[dict[str, Any]]:
    """Newest first, optionally filtered to one status (e.g. "finished").
    AI Research Lab Phase 4 (2026-07-30) — the accessor
    execution/routes/ai.py's _build_copilot_context() needs to ground a
    hypothesis suggestion in real, recent mission findings without
    requiring a caller to already know mission IDs (unlike every other
    function in this module)."""
    with d1_client.d1_connection() as con:
        _init(con)
        if status is not None:
            rows = con.execute(
                "SELECT * FROM research_missions WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM research_missions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def record_trial(
    mission_id: str,
    trial_number: int,
    symbol: str,
    state: str,
    objective_value: float | None,
    params: dict,
    metrics: dict | None,
    trades: int,
    error: str | None,
    started_at: str,
    finished_at: str,
    fingerprint: dict | None = None,
) -> None:
    """Always INSERT, never UPDATE — trial_number is assigned once by
    mission_runner.py (via Optuna's own trial.number, monotonic per
    Study) and never reused, so a resumed mission's replay + continue
    logic never produces a duplicate-key write.

    fingerprint (Diagnostic Infrastructure Phase 1, 2026-08-02): the
    research.manifest git+dataset reproducibility snapshot computed once
    per symbol by mission_runner.py, reused verbatim across every trial
    of that symbol. Informational only — never a criterion, never
    blocking. Absent (None) for a trial recorded before this shipped."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_mission_trials_v2
               (mission_id, trial_number, symbol, state, objective_value,
                params_json, metrics_json, trades, error, started_at, finished_at,
                fingerprint_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mission_id, trial_number, symbol, state, objective_value,
             json.dumps(params), json.dumps(metrics) if metrics is not None else None,
             trades, error, started_at, finished_at,
             json.dumps(fingerprint) if fingerprint is not None else None),
        )


def record_trial_attempt_start(mission_id: str, symbol: str, trial_number: int, started_at: str) -> None:
    """Mission Center Research Rigor Phase 1 (2026-08-06) — an append-only
    "this trial started evaluating" marker, written by mission_runner.py
    right before evaluate_point() runs, i.e. BEFORE record_trial() has
    anything to write. If the whole process crashes mid-evaluation, this
    row is the only surviving evidence that trial_number was ever
    attempted — see count_orphaned_attempts(). INSERT OR IGNORE: never
    UPDATE, and a duplicate call (should not happen — trial_number is
    unique per symbol per mission) is harmless, not an error."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT OR IGNORE INTO research_mission_trial_attempts
               (mission_id, symbol, trial_number, started_at) VALUES (?,?,?,?)""",
            (mission_id, symbol, trial_number, started_at),
        )


def count_orphaned_attempts(mission_id: str, symbol: str) -> int:
    """Attempts with no matching finished row in research_mission_trials_v2
    — i.e. trials that started evaluating but never got as far as being
    recorded (a mid-flight process crash). Purely informational: the
    resume loop's own remaining = n_target - len(existing) logic already
    naturally re-earns the lost trial via a fresh draw; this count only
    tells the operator how much compute a crash actually cost, not
    something mission_runner.py needs to act on."""
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            """SELECT COUNT(*) AS n FROM research_mission_trial_attempts a
               WHERE a.mission_id=? AND a.symbol=?
               AND NOT EXISTS (
                   SELECT 1 FROM research_mission_trials_v2 t
                   WHERE t.mission_id=a.mission_id AND t.symbol=a.symbol
                   AND t.trial_number=a.trial_number
               )""",
            (mission_id, symbol),
        ).fetchone()
    return int(row["n"]) if row and row["n"] is not None else 0


def existing_trials(mission_id: str, symbol: str) -> list[dict[str, Any]]:
    """Ordered by trial_number ASC — the resume/replay input."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            """SELECT * FROM research_mission_trials_v2
               WHERE mission_id=? AND symbol=? ORDER BY trial_number ASC""",
            (mission_id, symbol),
        ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def get_trial(mission_id: str, trial_number: int, symbol: str) -> dict[str, Any] | None:
    """Single-row lookup for one candidate trial — always symbol-scoped
    (never `(mission_id, trial_number)` alone): trial_number only means
    anything relative to the symbol whose Optuna Study produced it, since
    every symbol's numbering independently restarts at 0 (see this
    module's header comment on the primary-key fix). Used by
    backtest/mission_validator.py to re-resolve a candidate's exact point
    before validating it."""
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            """SELECT * FROM research_mission_trials_v2
               WHERE mission_id=? AND trial_number=? AND symbol=?""",
            (mission_id, trial_number, symbol),
        ).fetchone()
    return {k: row[k] for k in row.keys()} if row else None


def mission_progress(mission_id: str) -> dict[str, Any]:
    """One query: per-symbol, per-state trial counts. What GET
    /research/missions/{id} (Phase 2) reads — no log-line parsing."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            """SELECT symbol, state, COUNT(*) as n FROM research_mission_trials_v2
               WHERE mission_id=? GROUP BY symbol, state""",
            (mission_id,),
        ).fetchall()
    by_symbol: dict[str, dict[str, int]] = {}
    total = 0
    for r in rows:
        sym, state, n = r["symbol"], r["state"], int(r["n"])
        by_symbol.setdefault(sym, {})[state] = n
        total += n
    return {"by_symbol": by_symbol, "total": total}


def leaderboard(mission_id: str, symbol: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Every trial (COMPLETE/PRUNED/FAIL alike), never filtered to
    'winners' — mirrors backtest/robustness.py's report-every-point
    convention. No server-side 'best' selection."""
    with d1_client.d1_connection() as con:
        _init(con)
        if symbol is not None:
            rows = con.execute(
                """SELECT * FROM research_mission_trials_v2 WHERE mission_id=? AND symbol=?
                   ORDER BY objective_value IS NULL, objective_value DESC LIMIT ?""",
                (mission_id, symbol, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT * FROM research_mission_trials_v2 WHERE mission_id=?
                   ORDER BY objective_value IS NULL, objective_value DESC LIMIT ?""",
                (mission_id, limit),
            ).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]
