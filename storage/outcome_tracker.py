"""
storage/outcome_tracker.py
----------------------------
Track actual trade outcomes for IATIS signals.

Purpose:
  - During paper trading: manually log outcomes from cTrader Demo
  - Feeds Confidence Calibration (Phase 4.1)
  - Feeds Regime Performance Matrix (Phase 4.3)
  - Feeds Engine Performance tracking

Schema:
  signal_id   TEXT  — timestamp_symbol (e.g. "20260625T2131_USOIL")
  symbol      TEXT
  direction   TEXT  — BUY / SELL
  entry_price REAL
  stop_loss   REAL
  take_profit REAL
  entry_time  TEXT  — ISO UTC
  exit_time   TEXT  — ISO UTC (NULL if open)
  exit_price  REAL  — NULL if open
  outcome     TEXT  — win / loss / breakeven / open
  pnl_pips    REAL
  pnl_usd     REAL
  cf_score    REAL  — confluence score at signal time
  regime      TEXT  — TRENDING / RANGING / VOLATILE
  news_risk   REAL  — news_risk_score at signal time
  engines     TEXT  — JSON: which engines voted and how
  notes       TEXT  — manual notes
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent / "outcomes.db"


@contextmanager
def _conn(path: Path = DB_PATH):
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
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
        con.execute("""
        CREATE TABLE IF NOT EXISTS outcomes (
            signal_id   TEXT PRIMARY KEY,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            entry_price REAL,
            stop_loss   REAL,
            take_profit REAL,
            entry_time  TEXT NOT NULL,
            exit_time   TEXT,
            exit_price  REAL,
            outcome     TEXT DEFAULT 'open',
            pnl_pips    REAL,
            pnl_usd     REAL,
            cf_score    REAL,
            regime      TEXT,
            news_risk   REAL,
            engines     TEXT,
            notes       TEXT
        )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON outcomes(symbol)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_outcome ON outcomes(outcome)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_regime ON outcomes(regime)")
    os.chmod(str(path), 0o600)


# ─── Write ─────────────────────────────────────────────────────────────────

def log_signal(report: dict, path: Path = DB_PATH) -> str:
    """Log an EXECUTE signal for outcome tracking.

    Called automatically when IATIS generates an EXECUTE verdict.
    Returns signal_id.
    """
    _init_db(path)

    now = datetime.now(timezone.utc)
    symbol = report.get("symbol", "UNKNOWN")
    signal_id = f"{now.strftime('%Y%m%dT%H%M')}_{symbol}"

    confluence = report.get("confluence", {})
    vote = confluence.get("vote", {})
    engines_data = {
        e.get("engine", "?"): {
            "bias": e.get("bias", "?"),
            "score": e.get("score", 0),
        }
        for e in report.get("engine_outputs", [])
    }

    try:
        with _conn(path) as con:
            con.execute("""
            INSERT OR IGNORE INTO outcomes
            (signal_id, symbol, direction, entry_price, stop_loss, take_profit,
             entry_time, outcome, cf_score, regime, news_risk, engines)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                signal_id,
                symbol,
                vote.get("winning_bias", "?"),
                report.get("entry_price"),
                report.get("stop_loss"),
                report.get("take_profit"),
                now.isoformat(),
                "open",
                confluence.get("score"),
                report.get("regime", {}).get("state"),
                report.get("news", {}).get("news_risk_score", 0),
                json.dumps(engines_data),
            ))
        logger.info(f"Outcome tracker: logged signal {signal_id}")
    except Exception as exc:
        logger.warning(f"Outcome tracker log failed: {exc}")

    return signal_id


def close_signal(
    signal_id: str,
    exit_price: float,
    outcome: str,           # "win" / "loss" / "breakeven"
    exit_time: str | None = None,
    notes: str = "",
    path: Path = DB_PATH,
) -> bool:
    """Record the outcome of a completed trade.

    Args:
        signal_id: from log_signal()
        exit_price: actual exit price
        outcome: "win", "loss", or "breakeven"
        exit_time: ISO UTC string (default: now)
        notes: any manual observations
    """
    _init_db(path)

    if exit_time is None:
        exit_time = datetime.now(timezone.utc).isoformat()

    # Fetch signal to calculate P&L
    with _conn(path) as con:
        row = con.execute(
            "SELECT * FROM outcomes WHERE signal_id=?", (signal_id,)
        ).fetchone()

        if not row:
            logger.warning(f"Signal {signal_id} not found in outcome_tracker")
            return False

        entry = row["entry_price"] or 0
        direction = row["direction"]
        symbol = row["symbol"]

        # Calculate pip P&L
        pip_size = 0.01 if "JPY" in symbol else (
            0.01 if symbol in ("XAUUSD",) else
            0.001 if symbol in ("XAGUSD",) else
            0.0001
        )
        price_diff = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
        pnl_pips = round(price_diff / pip_size, 1)

        # Approximate USD P&L (1 lot basis)
        if symbol in ("XAUUSD",):
            pnl_usd = round(price_diff * 100, 2)
        elif symbol in ("BTCUSD", "ETHUSD"):
            pnl_usd = round(price_diff, 2)
        else:
            pnl_usd = round(pnl_pips * 10, 2)  # ~$10/pip standard lot

        con.execute("""
        UPDATE outcomes
        SET exit_time=?, exit_price=?, outcome=?, pnl_pips=?, pnl_usd=?, notes=?
        WHERE signal_id=?
        """, (exit_time, exit_price, outcome, pnl_pips, pnl_usd, notes, signal_id))

    logger.info(
        f"Outcome recorded: {signal_id} → {outcome} "
        f"(pips={pnl_pips}, usd≈${pnl_usd})"
    )
    return True


# ─── Read ──────────────────────────────────────────────────────────────────

def get_open_signals(path: Path = DB_PATH) -> list[dict]:
    """Get all signals still awaiting outcome."""
    _init_db(path)
    with _conn(path) as con:
        rows = con.execute(
            "SELECT * FROM outcomes WHERE outcome='open' ORDER BY entry_time DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def performance_summary(path: Path = DB_PATH) -> dict:
    """Overall performance statistics from closed signals."""
    _init_db(path)
    with _conn(path) as con:
        total = con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE outcome != 'open'"
        ).fetchone()[0]
        wins = con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE outcome='win'"
        ).fetchone()[0]
        losses = con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE outcome='loss'"
        ).fetchone()[0]
        total_pips = con.execute(
            "SELECT SUM(pnl_pips) FROM outcomes WHERE outcome != 'open'"
        ).fetchone()[0] or 0
        open_count = con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE outcome='open'"
        ).fetchone()[0]

        # Calibration: score bucket vs actual win rate
        calibration_rows = con.execute("""
        SELECT
            CASE
                WHEN cf_score >= 90 THEN '90-100'
                WHEN cf_score >= 80 THEN '80-90'
                WHEN cf_score >= 70 THEN '70-80'
                WHEN cf_score >= 60 THEN '60-70'
                ELSE '55-60'
            END as bucket,
            COUNT(*) as n,
            SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins
        FROM outcomes
        WHERE outcome != 'open' AND cf_score IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket DESC
        """).fetchall()

        # Regime breakdown
        regime_rows = con.execute("""
        SELECT regime, COUNT(*) as n,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
               AVG(pnl_pips) as avg_pips
        FROM outcomes
        WHERE outcome != 'open' AND regime IS NOT NULL
        GROUP BY regime
        """).fetchall()

    win_rate = round(wins / total * 100, 1) if total > 0 else 0
    return {
        "total_closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pips": round(total_pips, 1),
        "open_signals": open_count,
        "calibration": [dict(r) for r in calibration_rows],
        "by_regime": [dict(r) for r in regime_rows],
        "note": f"Need 200+ trades for statistical significance (current: {total})",
    }


def recent_signals(limit: int = 10, path: Path = DB_PATH) -> list[dict]:
    """Get most recent signals."""
    _init_db(path)
    with _conn(path) as con:
        rows = con.execute(
            "SELECT * FROM outcomes ORDER BY entry_time DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
