"""
storage/calibration.py
-----------------------
Phase 4.1: Confidence Calibration
Phase 4.3: Regime Performance Matrix

Answers two critical questions:
1. "Does a score of 87 actually mean 87% win probability?"
   → Calibration: maps score buckets to actual win rates

2. "Which regime produces the best results?"
   → Regime matrix: WR/PF/expectancy by TRENDING/RANGING/VOLATILE

Both require real trade outcomes stored in decision_db.
With paper trading (current), uses backtest data from JSON files.
With live trading, uses decision_db outcomes.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from storage import d1_client
from utils.logger import get_logger

logger = get_logger(__name__)


@contextmanager
def _conn():
    """Yields a D1 connection. See storage/d1_client.py."""
    with d1_client.d1_connection() as con:
        yield con


# ---------------------------------------------------------------------------
# Calibration: Score → Actual Win Rate
# ---------------------------------------------------------------------------

SCORE_BUCKETS = [
    (55, 60,  "55-60"),
    (60, 65,  "60-65"),
    (65, 70,  "65-70"),
    (70, 75,  "70-75"),
    (75, 80,  "75-80"),
    (80, 85,  "80-85"),
    (85, 90,  "85-90"),
    (90, 101, "90-100"),
]


def calibration_from_db() -> list[dict[str, Any]]:
    """Compute calibration buckets from live decision_db outcomes.

    Requires 'outcome' column (win/loss) to be populated.
    Only available after live/paper trading with actual results.

    Reads from the ``outcomes`` table (storage/outcome_tracker.py), which
    is the only table that carries ``cf_score`` alongside a real
    win/loss/breakeven ``outcome`` for EXECUTE signals — the ``decisions``
    table (storage/decision_db.py) logs every verdict including NO_TRADE
    but has no ``outcome`` column at all. Querying ``decisions`` for
    ``outcome``/``final_verdict`` (neither of which exists there) used to
    fail every call and get swallowed by the except below, silently
    reporting "no data" instead of the real schema mismatch.
    """
    try:
        with _conn() as con:
            rows = con.execute("""
                SELECT cf_score, outcome
                FROM outcomes
                WHERE outcome NOT IN ('open')
                  AND cf_score IS NOT NULL
            """).fetchall()
    except Exception as exc:
        logger.warning(f"Calibration DB query failed: {exc}")
        return []

    if not rows:
        return []

    results = []
    for lo, hi, label in SCORE_BUCKETS:
        bucket = [r for r in rows if lo <= (r["cf_score"] or 0) < hi]
        if len(bucket) < 5:
            continue
        wins = sum(1 for r in bucket if r["outcome"] == "win")
        results.append({
            "score_range": label,
            "n": len(bucket),
            "actual_win_rate": round(wins / len(bucket) * 100, 1),
            "implied_win_rate": f"{(lo+hi)//2}%",
            "calibration_error": round(abs(wins/len(bucket)*100 - (lo+hi)/2), 1),
        })

    return results


def calibration_from_backtest(
    backtest_dir: Path = Path("storage"),
) -> list[dict[str, Any]]:
    """Compute calibration from backtest JSON files.

    Backtest doesn't store score-per-trade, so we bucket by symbol WR
    as a proxy. Not perfect but useful for Phase 4 development.
    """
    files = list(backtest_dir.glob("backtest_*_H1.json"))
    if not files:
        return []

    # Aggregate all trades with score info from backtest results
    all_trades = []
    for f in files:
        try:
            data = json.loads(f.read_text())
            symbol = data.get("symbol", "?")
            trades = data.get("trades", [])
            for t in trades:
                if t.get("entry_score"):
                    all_trades.append({
                        "score": t["entry_score"],
                        "won": t.get("pnl_usd", 0) > 0,
                        "symbol": symbol,
                    })
        except Exception:
            continue

    if not all_trades:
        return []

    results = []
    for lo, hi, label in SCORE_BUCKETS:
        bucket = [t for t in all_trades if lo <= t["score"] < hi]
        if len(bucket) < 5:
            continue
        wins = sum(1 for t in bucket if t["won"])
        results.append({
            "score_range": label,
            "n": len(bucket),
            "actual_win_rate": round(wins / len(bucket) * 100, 1),
        })

    return results


# ---------------------------------------------------------------------------
# Regime Performance Matrix
# ---------------------------------------------------------------------------

def regime_performance_matrix() -> list[dict[str, Any]]:
    """Compute WR, PF, and expectancy per regime.

    This is the Phase 4.3 'most important dashboard panel':
    Shows whether TRENDING regime actually produces better results
    than RANGING/VOLATILE — validating the regime-aware weight logic.

    Two tables, joined in Python by ``regime`` (no shared key exists to
    JOIN in SQL — ``decisions`` has no signal_id):
    - ``decisions`` (every verdict, EXECUTE + NO_TRADE) for
      total_decisions / executes / execute_rate. Column is ``verdict``,
      not ``final_verdict`` — the previous query used a column name
      that doesn't exist on this table and failed every call.
    - ``outcomes`` (EXECUTE signals only, with realized outcome) for
      wins / losses / PF / expectancy / avg score. The previous query
      read these off ``decisions``, which has no ``outcome`` column at
      all — that also failed every call. Both failures were swallowed
      by the except below and reported as an empty (not erroring)
      matrix.
    """
    try:
        with _conn() as con:
            decision_rows = con.execute("""
                SELECT
                    regime,
                    COUNT(*) as total,
                    SUM(CASE WHEN verdict='EXECUTE' THEN 1 ELSE 0 END) as executes
                FROM decisions
                WHERE regime IS NOT NULL
                GROUP BY regime
            """).fetchall()
            outcome_rows = con.execute("""
                SELECT
                    regime,
                    COUNT(*) as trades,
                    SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses,
                    AVG(cf_score) as avg_score,
                    SUM(CASE WHEN outcome='win' THEN pnl_usd ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN outcome='loss' THEN ABS(pnl_usd) ELSE 0 END) as gross_loss
                FROM outcomes
                WHERE regime IS NOT NULL AND outcome NOT IN ('open')
                GROUP BY regime
            """).fetchall()
    except Exception as exc:
        logger.warning(f"Regime matrix DB query failed: {exc}")
        return []

    by_regime: dict[str, dict[str, Any]] = {}
    for row in decision_rows:
        by_regime[row["regime"]] = {
            "regime": row["regime"],
            "total_decisions": row["total"],
            "executes": row["executes"] or 0,
        }
    for row in outcome_rows:
        entry = by_regime.setdefault(row["regime"], {
            "regime": row["regime"], "total_decisions": 0, "executes": 0,
        })
        entry.update({
            "trades": row["trades"] or 0,
            "wins": row["wins"] or 0,
            "losses": row["losses"] or 0,
            "avg_score": row["avg_score"] or 0,
            "gross_profit": row["gross_profit"] or 0,
            "gross_loss": row["gross_loss"] or 0,
        })

    results = []
    for regime, entry in sorted(by_regime.items(), key=lambda kv: kv[1]["executes"], reverse=True):
        trades = entry.get("trades", 0)
        wins = entry.get("wins", 0)
        losses = entry.get("losses", 0)
        gross_profit = entry.get("gross_profit", 0)
        gross_loss = entry.get("gross_loss", 0)

        wr = round(wins / trades * 100, 1) if trades > 0 else None
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
        expectancy = round((gross_profit - gross_loss) / trades, 2) if trades > 0 else None
        total = entry["total_decisions"] or 1

        results.append({
            "regime": regime,
            "total_decisions": entry["total_decisions"],
            "executes": entry["executes"],
            "execute_rate": round(entry["executes"] / total * 100, 1),
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "win_rate": wr,
            "profit_factor": pf,
            "expectancy_usd": expectancy,
            "avg_confluence_score": round(entry.get("avg_score", 0), 1),
        })

    return results


def regime_matrix_from_backtest(
    backtest_dir: Path = Path("storage"),
) -> list[dict[str, Any]]:
    """Regime matrix from backtest JSON files (uses per-trade regime if available)."""
    files = list(backtest_dir.glob("backtest_*_H1.json"))
    regime_data: dict[str, dict] = {}

    for f in files:
        try:
            data = json.loads(f.read_text())
            for trade in data.get("trades", []):
                regime = trade.get("regime", "UNKNOWN")
                if regime not in regime_data:
                    regime_data[regime] = {"wins": 0, "losses": 0, "pnl": []}
                pnl = trade.get("pnl_usd", 0)
                if pnl > 0:
                    regime_data[regime]["wins"] += 1
                else:
                    regime_data[regime]["losses"] += 1
                regime_data[regime]["pnl"].append(pnl)
        except Exception:
            continue

    results = []
    for regime, data in sorted(regime_data.items()):
        total = data["wins"] + data["losses"]
        if total == 0:
            continue
        gross_profit = sum(p for p in data["pnl"] if p > 0)
        gross_loss = abs(sum(p for p in data["pnl"] if p <= 0))
        results.append({
            "regime": regime,
            "trades": total,
            "win_rate": round(data["wins"] / total * 100, 1),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
            "expectancy_usd": round(sum(data["pnl"]) / total, 2),
        })

    return results


# ---------------------------------------------------------------------------
# Dynamic Weight Calculator (Phase 4, conservative)
# ---------------------------------------------------------------------------

def suggested_dynamic_weights(
    current_weights: dict[str, float],
    min_votes: int = 30,
    min_weight: float = 0.10,
    max_weight: float = 0.35,
    decay_factor: float = 0.7,
) -> dict[str, Any]:
    """Conservative dynamic weight adjustment per Phase 4 recommendations.

    Constraints (prevents chasing recent performance):
    - min_weight: no engine below 10% (prevents zeroing out)
    - max_weight: no engine above 35% (prevents domination)
    - decay_factor: 70% data-driven, 30% current weights (stability)
    - min_votes: only update if engine has sufficient data

    Rolling window: uses all data in engine_tracker (no time decay yet).
    Phase 6: add time-weighted rolling window (recent = higher weight).
    """
    from storage.engine_tracker import engine_stats

    stats = engine_stats(min_votes=min_votes)
    if not stats:
        return {
            "status": "insufficient_data",
            "message": f"Need {min_votes}+ votes per engine. Keep collecting data.",
            "weights": current_weights,
        }

    _ENGINE_TO_KEY = {
        "SMC": "smc", "PriceAction": "price_action", "ICT": "ict",
        "NNFX": "nnfx", "Quant": "quant", "Wyckoff": "wyckoff", "Macro": "macro",
        "Divergence": "divergence", "MarketStructure": "market_structure",
        "Sentiment": "sentiment",
    }

    # Compute data-driven scores
    engine_scores: dict[str, float] = {}
    for row in stats:
        key = _ENGINE_TO_KEY.get(row["engine"])
        if not key or key not in current_weights:
            continue
        agr = (row["agreement_rate"] or 50) / 100
        active_rate = 1 - (row["neutral_pct"] or 50) / 100
        # Score = agreement × active_rate × avg_score
        avg_score = (row["avg_score_when_voting"] or 50) / 100
        engine_scores[key] = agr * active_rate * avg_score

    if not engine_scores:
        return {
            "status": "insufficient_data",
            "message": "No engines with enough votes yet.",
            "weights": current_weights,
        }

    # Normalize data-driven scores to sum = total current weight
    total_current = sum(current_weights.values())
    total_score = sum(engine_scores.values()) or 1.0

    new_weights = dict(current_weights)
    for key, score in engine_scores.items():
        data_w = (score / total_score) * total_current
        # Blend: decay_factor × data + (1-decay) × current
        blended = decay_factor * data_w + (1 - decay_factor) * current_weights.get(key, 0)
        # Clamp to [min_weight, max_weight]
        new_weights[key] = round(max(min_weight, min(max_weight, blended)), 4)

    # Re-normalize to original total
    total_new = sum(new_weights.values())
    if total_new > 0:
        factor = total_current / total_new
        new_weights = {k: round(v * factor, 4) for k, v in new_weights.items()}

    changes = {
        k: round(new_weights.get(k, 0) - current_weights.get(k, 0), 4)
        for k in current_weights
    }

    return {
        "status": "ready",
        "weights": new_weights,
        "changes_from_current": changes,
        "constraints": {
            "min_weight": min_weight,
            "max_weight": max_weight,
            "decay_factor": decay_factor,
            "min_votes_required": min_votes,
        },
        "note": "Review changes before applying. Max change per engine is bounded by constraints.",
    }
