"""
storage/experience_db.py
---------------------------
Experience Database — MROS Level 1.

Records COMPLETE context for every pipeline decision (EXECUTE and NO_TRADE),
enabling the system to learn from its own history.

Every decision stores:
  1. Market DNA — regime, volatility, session, ATR, MQS, trend strength
  2. Engine Analysis — each engine's vote, score, and primary reason
  3. Decision Metadata — confluence score, confidence, veto status, fail reasons
  4. Outcome (for EXECUTE) — PnL, duration, MFE, MAE, exit reason

This enables queries like:
  "What happens when Wyckoff agrees with SMC during London in Trending?"
  "What's the WR when score > 70 and ATR percentile is 50-75?"
  "Which engine predicts reversals best in XAUUSD?"

Design:
  - SQLite with WAL mode (concurrent reads)
  - JSON columns for variable-length data (engines, reasons)
  - Indexed on symbol, regime, session, verdict for fast queries
  - experience_id links decisions to outcomes
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error
from utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent / "experience.db"

_CREATE_EXPERIENCES = """
CREATE TABLE IF NOT EXISTS experiences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id   TEXT    UNIQUE NOT NULL,
    ts              TEXT    NOT NULL,

    -- Symbol & Market
    symbol          TEXT    NOT NULL,
    session         TEXT,
    day_of_week     INTEGER,
    hour_utc        INTEGER,

    -- Market DNA
    regime          TEXT,
    regime_confidence REAL,
    volatility      TEXT,
    trend_strength  REAL,
    mqs_score       REAL,
    mqs_grade       TEXT,
    atr_percentile  REAL,
    d1_bias         TEXT,
    d1_adx          REAL,

    -- Decision
    verdict         TEXT    NOT NULL,
    direction       TEXT,
    confluence_score REAL,
    raw_score       REAL,
    mtf_adjustment  REAL,
    agree_count     INTEGER,
    total_engines   INTEGER,
    bull_conviction REAL,
    bear_conviction REAL,

    -- Confidence & Quality
    confidence      REAL,
    stability       REAL,
    data_quality    REAL,
    position_multiplier REAL,

    -- Filters
    contradiction_blocked  INTEGER DEFAULT 0,
    reversal_vetoed        INTEGER DEFAULT 0,
    news_blocked           INTEGER DEFAULT 0,
    news_risk_score        REAL,
    regime_filtered        INTEGER DEFAULT 0,
    meta_blocked           INTEGER DEFAULT 0,

    -- Fail reason (for NO_TRADE)
    fail_reason     TEXT,

    -- Trade levels (for EXECUTE only)
    entry_price     REAL,
    stop_loss       REAL,
    take_profit     REAL,
    risk_reward     TEXT,

    -- Engine details (JSON)
    engines_json    TEXT,

    -- Outcome (filled after trade closes)
    outcome         TEXT,
    exit_price      REAL,
    pnl_pips        REAL,
    pnl_usd         REAL,
    pnl_r           REAL,
    duration_bars   INTEGER,
    exit_reason     TEXT,
    outcome_ts      TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_exp_symbol ON experiences(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_exp_regime ON experiences(regime);",
    "CREATE INDEX IF NOT EXISTS idx_exp_session ON experiences(session);",
    "CREATE INDEX IF NOT EXISTS idx_exp_verdict ON experiences(verdict);",
    "CREATE INDEX IF NOT EXISTS idx_exp_ts ON experiences(ts);",
    "CREATE INDEX IF NOT EXISTS idx_exp_outcome ON experiences(outcome);",
    "CREATE INDEX IF NOT EXISTS idx_exp_score ON experiences(confluence_score);",
]


@contextmanager
def _conn(path: Path = DB_PATH):
    """Yields a connection to either D1 (IATIS_STORAGE_BACKEND=d1) or the
    local SQLite file at `path`. See storage/d1_client.py."""
    if d1_client.is_d1_enabled():
        with d1_client.d1_connection() as con:
            yield con
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _init_db(path: Path = DB_PATH) -> None:
    with _conn(path) as con:
        con.execute(_CREATE_EXPERIENCES)
        for idx in _CREATE_INDEXES:
            con.execute(idx)


def _detect_session(hour_utc: int) -> str:
    """Detect active trading session from UTC hour."""
    if 7 <= hour_utc < 12:
        return "London"
    elif 12 <= hour_utc < 16:
        return "London+NY"
    elif 16 <= hour_utc < 21:
        return "NewYork"
    elif 23 <= hour_utc or hour_utc < 8:
        return "Asian"
    else:
        return "Transition"


# ─── Record Experience ──────────────────────────────────────────────

def record_experience(report: dict, path: Path = DB_PATH) -> str:
    """Record a complete pipeline decision as an experience.

    Called after EVERY run_pipeline() — both EXECUTE and NO_TRADE.
    Returns the experience_id.
    """
    _init_db(path)

    now = datetime.now(timezone.utc)
    symbol = report.get("symbol", "UNKNOWN")
    experience_id = f"exp_{now.strftime('%Y%m%dT%H%M%S')}_{symbol}"
    verdict = report.get("final_verdict", "UNKNOWN")

    # Market DNA
    regime_info = report.get("regime", {})
    mqs_info = report.get("market_quality", {})
    confluence = report.get("confluence", {})
    vote = confluence.get("vote", {})
    mtf = confluence.get("mtf", {})
    risk = report.get("risk", {})
    news = report.get("news", {})
    meta = report.get("meta_decision") or {}
    reversal = confluence.get("reversal_veto", {})
    contradiction = confluence.get("contradiction", {})

    # Engine details
    engines = report.get("engine_outputs", [])
    engines_data = []
    for e in engines:
        engines_data.append({
            "engine": e.get("engine", "?"),
            "bias": e.get("bias", "NEUTRAL"),
            "score": e.get("score", 0),
            "reason": (e.get("reasons") or [""])[0][:100] if e.get("reasons") else "",
        })

    # Fail reason
    fail_reasons = confluence.get("fail_reasons", [])
    if not fail_reasons and verdict == "NO_TRADE":
        fail_reasons = [report.get("summary", "")]

    try:
        with _conn(path) as con:
            con.execute("""
                INSERT OR IGNORE INTO experiences (
                    experience_id, ts, symbol, session, day_of_week, hour_utc,
                    regime, regime_confidence, volatility, trend_strength,
                    mqs_score, mqs_grade, atr_percentile, d1_bias, d1_adx,
                    verdict, direction, confluence_score, raw_score, mtf_adjustment,
                    agree_count, total_engines, bull_conviction, bear_conviction,
                    confidence, stability, data_quality, position_multiplier,
                    contradiction_blocked, reversal_vetoed, news_blocked,
                    news_risk_score, meta_blocked, fail_reason,
                    entry_price, stop_loss, take_profit, risk_reward,
                    engines_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                experience_id,
                now.isoformat(),
                symbol,
                _detect_session(now.hour),
                now.weekday(),
                now.hour,
                # Market DNA
                regime_info.get("state"),
                regime_info.get("confidence"),
                regime_info.get("volatility"),
                regime_info.get("trend_strength"),
                mqs_info.get("mqs_score"),
                mqs_info.get("grade"),
                mqs_info.get("atr_percentile"),
                mtf.get("d1_bias"),
                mtf.get("d1_adx"),
                # Decision
                verdict,
                vote.get("winning_bias"),
                confluence.get("score"),
                confluence.get("raw_score"),
                mtf.get("adjustment"),
                vote.get("agree_count"),
                vote.get("total_engines"),
                vote.get("bull_conviction"),
                vote.get("bear_conviction"),
                # Confidence
                meta.get("confidence"),
                meta.get("stability"),
                meta.get("data_quality"),
                meta.get("position_multiplier"),
                # Filters
                1 if contradiction.get("blocked") else 0,
                1 if reversal.get("vetoed") else 0,
                1 if news.get("blackout_active") else 0,
                news.get("news_risk_score"),
                1 if meta.get("verdict") == "BLOCK" else 0,
                "; ".join(fail_reasons)[:500] if fail_reasons else None,
                # Trade levels
                report.get("entry_price"),
                report.get("stop_loss"),
                report.get("take_profit"),
                report.get("risk_reward"),
                # Engines
                json.dumps(engines_data),
            ))
        logger.debug(f"Experience recorded: {experience_id} → {verdict}")
    except (sqlite3.Error, D1Error) as exc:
        logger.warning(f"Experience DB write failed (non-fatal): {exc}")

    return experience_id


# ─── Record Outcome ────────────────────────────────────────────────

def record_outcome(
    symbol: str,
    outcome: str,
    exit_price: float,
    pnl_pips: float = 0,
    pnl_usd: float = 0,
    pnl_r: float = 0,
    duration_bars: int = 0,
    exit_reason: str = "",
    path: Path = DB_PATH,
) -> bool:
    """Update the most recent EXECUTE experience for this symbol with outcome."""
    _init_db(path)
    try:
        with _conn(path) as con:
            row = con.execute("""
                SELECT experience_id FROM experiences
                WHERE symbol = ? AND verdict = 'EXECUTE' AND outcome IS NULL
                ORDER BY ts DESC LIMIT 1
            """, (symbol,)).fetchone()

            if not row:
                logger.debug(f"No open experience for {symbol}")
                return False

            con.execute("""
                UPDATE experiences
                SET outcome = ?, exit_price = ?, pnl_pips = ?, pnl_usd = ?,
                    pnl_r = ?, duration_bars = ?, exit_reason = ?,
                    outcome_ts = ?
                WHERE experience_id = ?
            """, (
                outcome, exit_price, pnl_pips, pnl_usd,
                pnl_r, duration_bars, exit_reason,
                datetime.now(timezone.utc).isoformat(),
                row["experience_id"],
            ))
            logger.info(f"Experience outcome recorded: {row['experience_id']} → {outcome}")
            return True
    except (sqlite3.Error, D1Error) as exc:
        logger.warning(f"Experience outcome update failed: {exc}")
        return False


# ─── Query Interface ────────────────────────────────────────────────

def query_experiences(
    symbol: str | None = None,
    regime: str | None = None,
    session: str | None = None,
    verdict: str | None = None,
    min_score: float | None = None,
    limit: int = 100,
    path: Path = DB_PATH,
) -> list[dict]:
    """Query experiences with filters."""
    _init_db(path)
    conditions = []
    params = []

    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if regime:
        conditions.append("regime = ?")
        params.append(regime)
    if session:
        conditions.append("session = ?")
        params.append(session)
    if verdict:
        conditions.append("verdict = ?")
        params.append(verdict)
    if min_score is not None:
        conditions.append("confluence_score >= ?")
        params.append(min_score)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with _conn(path) as con:
        rows = con.execute(f"""
            SELECT * FROM experiences {where}
            ORDER BY ts DESC LIMIT ?
        """, params).fetchall()

    return [dict(r) for r in rows]


def pattern_analysis(
    filters: dict[str, Any],
    path: Path = DB_PATH,
) -> dict:
    """Analyze win rate for a specific pattern combination.

    Example:
        pattern_analysis({
            "regime": "TRENDING",
            "session": "London+NY",
            "min_score": 65,
        })

    Returns: {"trades": 42, "wins": 23, "wr": 54.8, "avg_pnl_r": 0.8}
    """
    _init_db(path)
    conditions = ["verdict = 'EXECUTE'", "outcome IS NOT NULL"]
    params = []

    for key, value in filters.items():
        if key == "min_score":
            conditions.append("confluence_score >= ?")
            params.append(value)
        elif key == "max_score":
            conditions.append("confluence_score <= ?")
            params.append(value)
        elif key == "engine_agrees":
            # Check if specific engine voted in same direction
            conditions.append(f"engines_json LIKE ?")
            params.append(f'%"engine": "{value}"%')
        elif key in ("symbol", "regime", "session", "direction"):
            conditions.append(f"{key} = ?")
            params.append(value)

    where = f"WHERE {' AND '.join(conditions)}"

    with _conn(path) as con:
        rows = con.execute(f"""
            SELECT outcome, pnl_r, pnl_usd, pnl_pips
            FROM experiences {where}
        """, params).fetchall()

    if not rows:
        return {"trades": 0, "message": "No matching experiences"}

    total = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "win")
    pnl_r_values = [r["pnl_r"] for r in rows if r["pnl_r"] is not None]

    return {
        "trades": total,
        "wins": wins,
        "losses": total - wins,
        "wr": round(wins / total * 100, 1),
        "avg_pnl_r": round(sum(pnl_r_values) / max(len(pnl_r_values), 1), 2) if pnl_r_values else None,
        "total_pnl_usd": round(sum(r["pnl_usd"] or 0 for r in rows), 2),
    }


def experience_summary(path: Path = DB_PATH) -> dict:
    """High-level summary of all experiences."""
    _init_db(path)
    with _conn(path) as con:
        total = con.execute("SELECT COUNT(*) as n FROM experiences").fetchone()["n"]
        executes = con.execute(
            "SELECT COUNT(*) as n FROM experiences WHERE verdict='EXECUTE'"
        ).fetchone()["n"]
        closed = con.execute(
            "SELECT COUNT(*) as n FROM experiences WHERE outcome IS NOT NULL"
        ).fetchone()["n"]
        wins = con.execute(
            "SELECT COUNT(*) as n FROM experiences WHERE outcome='win'"
        ).fetchone()["n"]

        # By regime
        regime_rows = con.execute("""
            SELECT regime,
                   COUNT(*) as total,
                   SUM(CASE WHEN verdict='EXECUTE' THEN 1 ELSE 0 END) as executes,
                   SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins
            FROM experiences
            GROUP BY regime
        """).fetchall()

        # By session
        session_rows = con.execute("""
            SELECT session,
                   COUNT(*) as total,
                   SUM(CASE WHEN verdict='EXECUTE' THEN 1 ELSE 0 END) as executes,
                   SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins
            FROM experiences
            WHERE session IS NOT NULL
            GROUP BY session
        """).fetchall()

        # Top fail reasons
        fail_rows = con.execute("""
            SELECT fail_reason, COUNT(*) as n
            FROM experiences
            WHERE verdict='NO_TRADE' AND fail_reason IS NOT NULL
            GROUP BY fail_reason
            ORDER BY n DESC LIMIT 10
        """).fetchall()

    return {
        "total_experiences": total,
        "execute_count": executes,
        "closed_count": closed,
        "open_count": executes - closed,
        "win_count": wins,
        "wr": round(wins / max(closed, 1) * 100, 1),
        "execute_rate": round(executes / max(total, 1) * 100, 1),
        "by_regime": [dict(r) for r in regime_rows],
        "by_session": [dict(r) for r in session_rows],
        "top_fail_reasons": [dict(r) for r in fail_rows],
    }


def find_similar(
    current_report: dict,
    top_n: int = 10,
    path: Path = DB_PATH,
) -> list[dict]:
    """Find historically similar decisions (Market Memory - Level 9).

    Matches on: symbol + regime + similar score range + same direction.
    Returns past experiences with outcomes for pattern comparison.
    """
    _init_db(path)

    symbol = current_report.get("symbol", "")
    regime = current_report.get("regime", {}).get("state", "")
    score = current_report.get("confluence", {}).get("score", 0)
    direction = current_report.get("confluence", {}).get("vote", {}).get("winning_bias", "")

    with _conn(path) as con:
        rows = con.execute("""
            SELECT experience_id, ts, symbol, regime, confluence_score,
                   direction, verdict, outcome, pnl_r, pnl_usd,
                   confidence, session
            FROM experiences
            WHERE symbol = ?
              AND regime = ?
              AND direction = ?
              AND confluence_score BETWEEN ? AND ?
              AND outcome IS NOT NULL
            ORDER BY ts DESC
            LIMIT ?
        """, (symbol, regime, direction,
              score - 10, score + 10, top_n)).fetchall()

    results = [dict(r) for r in rows]

    if results:
        wins = sum(1 for r in results if r["outcome"] == "win")
        total = len(results)
        return {
            "similar_count": total,
            "historical_wr": round(wins / total * 100, 1),
            "matches": results,
            "recommendation": (
                "FAVORABLE" if wins / total > 0.45 else
                "NEUTRAL" if wins / total > 0.35 else
                "UNFAVORABLE"
            ),
        }

    return {"similar_count": 0, "message": "No similar experiences found yet"}
