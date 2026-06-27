"""
storage/engine_tracker.py
---------------------------
Tracks per-engine performance over time.

The strategic gap identified in the Security Audit:
"No tracking of which engine performs well over time,
so weights remain static and unoptimized."

This module:
1. Records each engine's vote alongside the final verdict
2. Computes per-engine accuracy (when engine agreed with EXECUTE
   and the move was profitable vs when it disagreed)
3. Provides data for future Bayesian weight adjustment

Since we don't have real P&L data yet (paper trading), we track
a proxy: when the system says EXECUTE with N engines agreeing,
and later the price moved in the predicted direction for X pips,
which engines had the correct directional bias?

Phase 3: proxy tracking (directional accuracy vs price movement)
Phase 6: real P&L attribution once broker integration exists
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

TRACKER_DB = Path(__file__).resolve().parent / "engine_tracker.db"

_CREATE_ENGINE_PERFORMANCE = """
CREATE TABLE IF NOT EXISTS engine_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    engine          TEXT NOT NULL,
    bias            TEXT NOT NULL,   -- BULLISH | BEARISH | NEUTRAL
    score           REAL NOT NULL,
    final_verdict   TEXT NOT NULL,   -- EXECUTE | NO_TRADE
    agreed_with_majority INTEGER,    -- 1 = agreed, 0 = opposed, NULL = neutral
    confluence_score REAL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ep_engine ON engine_performance(engine);",
    "CREATE INDEX IF NOT EXISTS idx_ep_symbol ON engine_performance(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_ep_verdict ON engine_performance(final_verdict);",
]


@contextmanager
def _conn(path: Path = TRACKER_DB):
    path.parent.mkdir(parents=True, exist_ok=True)
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


def init_tracker(path: Path = TRACKER_DB) -> None:
    with _conn(path) as con:
        con.execute(_CREATE_ENGINE_PERFORMANCE)
        for idx in _CREATE_INDEXES:
            con.execute(idx)
    try:
        os.chmod(str(path), 0o600)
    except Exception:
        pass


def record_engine_votes(report: dict, path: Path = TRACKER_DB) -> None:
    """Record each engine's vote from a pipeline report.

    Called automatically after each pipeline run alongside log_decision_db().
    Never raises — tracking failure must not affect the pipeline.
    """
    init_tracker(path)
    ts = datetime.now(timezone.utc).isoformat()
    symbol = report.get("symbol", "")
    verdict = report.get("final_verdict", "UNKNOWN")
    cf_score = report.get("confluence", {}).get("score", 0)

    # Determine winning bias from the report
    winning_bias = None
    vote = report.get("confluence", {}).get("vote", {})
    if vote:
        winning_bias = vote.get("winning_bias")

    engine_outputs = report.get("engine_outputs", [])

    try:
        with _conn(path) as con:
            for e in engine_outputs:
                engine = e.get("engine", "?")
                bias = e.get("bias", "NEUTRAL")
                score = e.get("score", 0)

                # Did this engine agree with the majority?
                if bias == "NEUTRAL" or winning_bias is None or winning_bias == "NEUTRAL":
                    agreed = None
                elif bias == winning_bias:
                    agreed = 1
                else:
                    agreed = 0

                con.execute("""
                    INSERT INTO engine_performance
                    (ts, symbol, engine, bias, score, final_verdict,
                     agreed_with_majority, confluence_score)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (ts, symbol, engine, bias, score, verdict, agreed, cf_score))

        logger.debug(f"Engine tracker: recorded {len(engine_outputs)} votes for {symbol}")
    except Exception as exc:
        logger.warning(f"Engine tracker write failed (non-fatal): {exc}")


def engine_stats(
    min_votes: int = 10,
    symbol: str | None = None,
    path: Path = TRACKER_DB,
) -> list[dict[str, Any]]:
    """Return per-engine statistics.

    Args:
        min_votes: minimum votes for an engine to appear (filters low-data engines)
        symbol: filter to one symbol (None = all symbols)

    Returns list of dicts with:
        engine, total_votes, neutral_pct, bullish_pct, bearish_pct,
        agreement_rate (how often it agreed with majority on EXECUTE),
        avg_score_when_voting
    """
    init_tracker(path)
    with _conn(path) as con:
        sym_filter = "AND symbol = ?" if symbol else ""
        params = (symbol,) if symbol else ()

        rows = con.execute(f"""
            SELECT
                engine,
                COUNT(*) as total_votes,
                ROUND(AVG(CASE WHEN bias='NEUTRAL' THEN 1.0 ELSE 0.0 END)*100, 1) as neutral_pct,
                ROUND(AVG(CASE WHEN bias='BULLISH' THEN 1.0 ELSE 0.0 END)*100, 1) as bullish_pct,
                ROUND(AVG(CASE WHEN bias='BEARISH' THEN 1.0 ELSE 0.0 END)*100, 1) as bearish_pct,
                ROUND(AVG(CASE WHEN agreed_with_majority IS NOT NULL
                               THEN agreed_with_majority ELSE NULL END)*100, 1) as agreement_rate,
                ROUND(AVG(CASE WHEN bias != 'NEUTRAL' THEN score ELSE NULL END), 1) as avg_score_when_voting
            FROM engine_performance
            WHERE 1=1 {sym_filter}
            GROUP BY engine
            HAVING total_votes >= ?
            ORDER BY agreement_rate DESC NULLS LAST
        """, (*params, min_votes)).fetchall()

    return [dict(r) for r in rows]


def neutral_rate_by_engine(path: Path = TRACKER_DB) -> list[dict]:
    """Which engines abstain most often? High neutral rate = less useful."""
    init_tracker(path)
    with _conn(path) as con:
        rows = con.execute("""
            SELECT engine,
                   COUNT(*) as total,
                   ROUND(SUM(CASE WHEN bias='NEUTRAL' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as neutral_pct
            FROM engine_performance
            GROUP BY engine
            ORDER BY neutral_pct DESC
        """).fetchall()
    return [dict(r) for r in rows]


def suggested_weights(
    current_weights: dict[str, float],
    path: Path = TRACKER_DB,
) -> dict[str, float]:
    """Suggest adjusted weights based on engine performance data.

    Simple heuristic: engines with higher agreement rates and lower
    neutral rates get higher weights. This is NOT Bayesian optimization —
    it's a first-order approximation until we have real P&L data.

    Returns new weight dict (not applied automatically — review before using).
    """
    stats = engine_stats(min_votes=20, path=path)
    if not stats:
        return current_weights  # not enough data

    _ENGINE_TO_KEY = {
        "SMC": "smc", "PriceAction": "price_action", "ICT": "ict",
        "NNFX": "nnfx", "Quant": "quant", "Wyckoff": "wyckoff", "Macro": "macro",
        "Divergence": "divergence", "MarketStructure": "market_structure",
        "Sentiment": "sentiment",
    }

    # Compute score for each engine: agreement_rate × (1 - neutral_pct/100)
    engine_scores: dict[str, float] = {}
    for row in stats:
        key = _ENGINE_TO_KEY.get(row["engine"])
        if not key:
            continue
        agr = (row["agreement_rate"] or 50) / 100
        active_rate = 1 - (row["neutral_pct"] or 50) / 100
        engine_scores[key] = agr * active_rate

    if not engine_scores:
        return current_weights

    # Normalize to match total current weight
    total_current = sum(current_weights.values())
    total_score = sum(engine_scores.values()) or 1.0
    scale = total_current / total_score

    new_weights = dict(current_weights)
    for key, score in engine_scores.items():
        if key in new_weights:
            # Blend 70% data-driven + 30% current (conservative adjustment)
            data_driven = round(score * scale, 3)
            new_weights[key] = round(0.7 * data_driven + 0.3 * current_weights.get(key, 0), 3)

    # Re-normalize to sum to original total
    total_new = sum(new_weights.values())
    if total_new > 0:
        factor = total_current / total_new
        new_weights = {k: round(v * factor, 4) for k, v in new_weights.items()}

    return new_weights
