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

from storage import d1_client
from utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent / "outcomes.db"

# Default per-trade risk budget in USD, matching config.yaml defaults:
# risk.risk_per_trade_max (0.01) × risk.starting_balance (10 000).
# Callers with different sizing should pass ``risk_usd`` explicitly.
DEFAULT_RISK_USD: float = 100.0


@contextmanager
def _conn(path: Path = DB_PATH):
    """Yields a connection to either D1 (IATIS_STORAGE_BACKEND=d1) or the
    local SQLite file at `path`. See storage/d1_client.py."""
    if d1_client.is_d1_enabled():
        with d1_client.d1_connection() as con:
            yield con
        return
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
    if not d1_client.is_d1_enabled():
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
    risk_usd: float = DEFAULT_RISK_USD,
) -> bool:
    """Record the outcome of a completed trade.

    Args:
        signal_id: from log_signal()
        exit_price: actual exit price
        outcome: "win", "loss", or "breakeven"
        exit_time: ISO UTC string (default: now)
        notes: any manual observations
        risk_usd: USD risked per trade; pnl_usd is recorded as
            R-multiple × risk_usd. Default matches config
            (risk_per_trade_max × starting_balance = 0.01 × 10 000).
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
        is_buy = direction in ("BUY", "BULLISH")
        price_diff = (exit_price - entry) if is_buy else (entry - exit_price)
        pnl_pips = round(price_diff / pip_size, 1)

        # Risk-normalized USD P&L (R-multiple × per-trade risk budget).
        #
        # The risk layer (risk/live_portfolio_state.py) assumes every
        # trade risks a FIXED fraction of the account
        # (risk.risk_per_trade_max × risk.starting_balance). The old
        # "1 standard lot" approximation was inconsistent with that
        # assumption and inflated the equity curve by orders of
        # magnitude (e.g. crypto price_diff counted 1:1 in USD),
        # corrupting balance/drawdown inputs to the risk gate.
        #
        # pnl_usd = R × risk_usd, where R = price_diff / |entry − SL|.
        # A full SL hit ≈ −risk_usd; a 2R take-profit ≈ +2 × risk_usd.
        # If no stop-loss was stored we cannot size the trade — record
        # NULL rather than invent a lot size.
        sl = row["stop_loss"]
        sl_distance = abs(entry - sl) if sl else 0.0
        if sl_distance > 0:
            r_multiple = price_diff / sl_distance
            pnl_usd = round(r_multiple * risk_usd, 2)
        else:
            pnl_usd = None
            logger.warning(
                f"{signal_id}: no stop_loss stored — pnl_usd left NULL "
                f"(cannot compute R-multiple)"
            )

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


# ─── Auto-Close ────────────────────────────────────────────────────────────

def auto_close_outcomes(
    current_prices: dict[str, float], path: Path = DB_PATH
) -> list[dict]:
    """Check open signals against current prices and auto-close if TP/SL hit.

    Called automatically at the end of each scheduler run.

    Args:
        current_prices: {symbol: current_price} from latest pipeline data
        path: DB path

    Returns:
        One record per closed signal:
        ``{"signal_id", "symbol", "direction", "outcome", "exit_price"}``.
        Empty list when nothing closed (``len()`` gives the old count).
    """
    _init_db(path)
    open_signals = get_open_signals(path)
    closed: list[dict] = []

    for sig in open_signals:
        symbol = sig.get("symbol", "")
        price = current_prices.get(symbol)
        if price is None:
            continue

        entry = sig.get("entry_price") or 0
        sl = sig.get("stop_loss") or 0
        tp = sig.get("take_profit") or 0
        direction = sig.get("direction", "")
        sig_id = sig.get("signal_id", "")

        if not all([entry, sl, tp, sig_id]):
            continue

        # Check TP/SL hit.
        # log_signal() stores direction as the vote's winning bias
        # (BULLISH/BEARISH); broker paths may store BUY/SELL. Accept
        # both — previously only BUY/SELL matched, so signals logged by
        # the pipeline could NEVER auto-close.
        hit = None
        if direction in ("BUY", "BULLISH"):
            if price >= tp:
                hit = ("win", tp)
            elif price <= sl:
                hit = ("loss", sl)
        elif direction in ("SELL", "BEARISH"):
            if price <= tp:
                hit = ("win", tp)
            elif price >= sl:
                hit = ("loss", sl)

        if hit:
            outcome, exit_px = hit
            success = close_signal(
                signal_id=sig_id,
                exit_price=exit_px,
                outcome=outcome,
                notes=f"auto_close: price={price:.5f} hit {'TP' if outcome=='win' else 'SL'}",
                path=path,
            )
            if success:
                closed.append({
                    "signal_id": sig_id,
                    "symbol": symbol,
                    "direction": direction,
                    "outcome": outcome,
                    "exit_price": exit_px,
                })
                logger.info(
                    f"Auto-closed {sig_id} ({symbol} {direction}): "
                    f"{outcome} @ {exit_px:.5f} (price={price:.5f})"
                )

    return closed
