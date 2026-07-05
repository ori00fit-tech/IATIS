"""
storage/decision_db.py
-----------------------
Cloudflare D1-backed decision store — replaces the flat JSONL log for queries.

Why a queryable DB:
    The JSONL log (decision_log.py) is kept for append-only streaming
    compatibility. This module adds a queryable layer on top so we can
    ask real questions:
        - What's the win rate by regime?
        - Which engine combination leads to EXECUTE most often?
        - How many NO_TRADEs are due to score vs engines vs contradiction?
        - What does performance look like over the last 7 days?

Schema:
    decisions table — one row per pipeline run
    engine_votes table — one row per engine per run (normalized)

Both tables are auto-created on first use. See storage/d1_client.py for
why every read/write here goes over HTTPS to the D1 proxy Worker
instead of a local file.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error
from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,             -- UTC ISO timestamp
    symbol      TEXT    NOT NULL DEFAULT '',
    verdict     TEXT    NOT NULL,             -- EXECUTE | NO_TRADE
    regime      TEXT,
    volatility  TEXT,
    trend_str   REAL,
    cf_score    REAL,
    cf_engines  INTEGER,
    risk_passed INTEGER,                       -- 1 | 0 | NULL
    fail_reason TEXT,                         -- primary fail reason
    summary     TEXT,
    raw_json    TEXT                          -- full report for drill-down
);
"""

_CREATE_ENGINE_VOTES = """
CREATE TABLE IF NOT EXISTS engine_votes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL REFERENCES decisions(id),
    engine      TEXT    NOT NULL,
    bias        TEXT    NOT NULL,
    score       REAL    NOT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);",
    "CREATE INDEX IF NOT EXISTS idx_decisions_verdict ON decisions(verdict);",
    "CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_engine_votes_did ON engine_votes(decision_id);",
]


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@contextmanager
def _conn():
    """Yields a D1 connection — same .execute()/.fetchone()/.fetchall()
    shape as sqlite3 used to have. See storage/d1_client.py."""
    with d1_client.d1_connection() as con:
        yield con


def init_db() -> None:
    """Create tables if they don't exist yet."""
    with _conn() as con:
        con.execute(_CREATE_DECISIONS)
        con.execute(_CREATE_ENGINE_VOTES)
        for idx in _CREATE_INDEXES:
            con.execute(idx)
    logger.debug("DB initialized (backend=d1)")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def log_decision_db(report: dict) -> None:
    """Insert one pipeline report into the DB. Never raises — failures are
    logged so the pipeline continues regardless of DB availability.
    """
    init_db()

    ts = datetime.now(timezone.utc).isoformat()
    symbol = report.get("symbol", "")
    verdict = report.get("final_verdict", "UNKNOWN")
    regime_d = report.get("regime", {})
    cf = report.get("confluence", {})
    risk = report.get("risk", {})

    fail_reasons = cf.get("fail_reasons", [])
    if not fail_reasons and verdict == "NO_TRADE":
        if risk and risk.get("passed") is False:
            fail_reasons = risk.get("reasons", [])
    primary_fail = fail_reasons[0] if fail_reasons else None

    decision_values = (
        ts, symbol, verdict,
        regime_d.get("state"),
        regime_d.get("volatility"),
        regime_d.get("trend_strength"),
        cf.get("score"),
        cf.get("engines_participating"),
        1 if risk and risk.get("passed") else 0 if risk else None,
        primary_fail,
        report.get("summary"),
        json.dumps(report, default=str),
    )
    engine_outputs = report.get("engine_outputs", [])

    try:
        # Originally this tried to keep the decision row and its N
        # engine_votes rows in one atomic /d1/batch call, using SQLite's
        # last_insert_rowid() so engine_votes could refer to a decision_id
        # it didn't have yet in Python. That hit a real D1 limitation in
        # production: last_insert_rowid() does not reliably carry over
        # between statements inside a single batch() call, so
        # engine_votes.decision_id came back as 0/NULL and every insert
        # failed its FOREIGN KEY constraint (confirmed live:
        # "D1_ERROR: FOREIGN KEY constraint failed").
        #
        # Fixed by using two round-trips instead: insert the decision row
        # alone first (its real id comes back directly in that exec
        # response's meta.last_row_id — reliable per-statement, unlike
        # last_insert_rowid() across a batch), then batch all engine_votes
        # inserts together using that concrete id. This keeps the N votes
        # atomic as a group but no longer atomic with the decision row
        # itself — see cloudflare/README.md's "Known limitation" section.
        with d1_client.d1_connection() as con:
            cur = con.execute(
                """INSERT INTO decisions
                   (ts, symbol, verdict, regime, volatility, trend_str,
                    cf_score, cf_engines, risk_passed, fail_reason, summary, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                decision_values,
            )
            decision_id = cur.lastrowid
        if engine_outputs and decision_id:
            d1_client.d1_batch([
                (
                    "INSERT INTO engine_votes (decision_id, engine, bias, score) VALUES (?, ?, ?, ?)",
                    (decision_id, e.get("engine"), e.get("bias"), e.get("score", 0)),
                )
                for e in engine_outputs
            ])

        logger.info(f"DB: logged {verdict} for {symbol}")
    except D1Error as exc:
        logger.warning(f"DB write failed (non-fatal): {exc}")


# ---------------------------------------------------------------------------
# Read / Analytics
# ---------------------------------------------------------------------------

def summary() -> dict[str, Any]:
    """Quick aggregate stats from the DB."""
    init_db()
    with _conn() as con:
        total = con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        execute = con.execute("SELECT COUNT(*) FROM decisions WHERE verdict='EXECUTE'").fetchone()[0]
        no_trade = con.execute("SELECT COUNT(*) FROM decisions WHERE verdict='NO_TRADE'").fetchone()[0]

        top_reasons = con.execute("""
            SELECT fail_reason, COUNT(*) as n
            FROM decisions
            WHERE verdict='NO_TRADE' AND fail_reason IS NOT NULL
            GROUP BY fail_reason
            ORDER BY n DESC
            LIMIT 5
        """).fetchall()

        by_regime = con.execute("""
            SELECT regime, verdict, COUNT(*) as n
            FROM decisions
            WHERE regime IS NOT NULL
            GROUP BY regime, verdict
            ORDER BY regime, verdict
        """).fetchall()

        by_engine_bias = con.execute("""
            SELECT d.verdict, ev.engine, ev.bias, COUNT(*) as n
            FROM decisions d
            JOIN engine_votes ev ON ev.decision_id = d.id
            GROUP BY d.verdict, ev.engine, ev.bias
            ORDER BY d.verdict, ev.engine
        """).fetchall()

    return {
        "total": total,
        "execute": execute,
        "no_trade": no_trade,
        "execute_rate": round(execute / total, 3) if total else 0,
        "top_no_trade_reasons": [
            {"reason": r["fail_reason"], "count": r["n"]} for r in top_reasons
        ],
        "by_regime": [
            {"regime": r["regime"], "verdict": r["verdict"], "count": r["n"]}
            for r in by_regime
        ],
        "engine_bias_breakdown": [
            {"verdict": r["verdict"], "engine": r["engine"],
             "bias": r["bias"], "count": r["n"]}
            for r in by_engine_bias
        ],
    }


def recent(limit: int = 20, verdict_filter: str | None = None) -> list[dict]:
    """Return recent decisions, newest first."""
    init_db()
    with _conn() as con:
        if verdict_filter:
            rows = con.execute(
                "SELECT * FROM decisions WHERE verdict=? ORDER BY id DESC LIMIT ?",
                (verdict_filter.upper(), limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def regime_performance() -> list[dict]:
    """EXECUTE rate broken down by regime — useful for tuning regime filters."""
    init_db()
    with _conn() as con:
        rows = con.execute("""
            SELECT
                regime,
                COUNT(*) as total,
                SUM(CASE WHEN verdict='EXECUTE' THEN 1 ELSE 0 END) as executes,
                ROUND(AVG(cf_score), 1) as avg_cf_score,
                ROUND(AVG(trend_str), 3) as avg_trend_strength
            FROM decisions
            WHERE regime IS NOT NULL
            GROUP BY regime
            ORDER BY regime
        """).fetchall()
    return [dict(r) for r in rows]


def execute_alert_exists_for_bar(symbol: str, bar_time: str) -> bool:
    """True if an EXECUTE decision for this symbol and decision-bar
    timestamp is already logged.

    Alert deduplication for slow decision timeframes: with a D1 decision
    TF and a 2-hourly scheduler, the same closed daily bar is re-evaluated
    ~12 times — the Telegram signal must go out once per bar, not twelve
    times. Fail-open by design: if D1 is unreachable, sending a duplicate
    alert is a better failure mode than silently dropping a signal.
    """
    if not bar_time:
        return False
    try:
        init_db()
        with _conn() as con:
            rows = con.execute(
                "SELECT raw_json FROM decisions "
                "WHERE symbol=? AND verdict='EXECUTE' ORDER BY id DESC LIMIT 12",
                (symbol,),
            ).fetchall()
        for r in rows:
            try:
                if json.loads(r["raw_json"] or "{}").get("bar_time") == bar_time:
                    return True
            except (ValueError, TypeError):
                continue
        return False
    except Exception as exc:
        logger.warning(f"Alert dedup check failed (fail-open, will send): {exc}")
        return False
