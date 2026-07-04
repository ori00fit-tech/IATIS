"""
storage/symbol_health.py
--------------------------
Symbol Health Index (SHI): 0-100 per symbol

Tracks live performance of each symbol and auto-pauses
underperformers. Updates after every closed outcome.

Factors (from outcome_tracker data):
  Win Rate (last 20 trades):    40 pts max
  Profit Factor (last 20):      30 pts max
  Regime Stability:             15 pts max
  Recent Trend (last 5):        15 pts max

Thresholds:
  SHI >= 65: HEALTHY  — trade normally
  SHI 45-65: CAUTION  — reduce position size (0.5×)
  SHI < 45:  PAUSED   — skip this symbol

Auto-pauses symbols with:
  WR < 45% over last 20 trades
  PF < 1.0 (net losing)
  3+ consecutive losses
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger(__name__)

SHI_HEALTHY = 65
SHI_CAUTION = 45


@dataclass
class SymbolHealth:
    symbol: str
    shi_score: float
    status: str          # HEALTHY / CAUTION / PAUSED
    win_rate: float | None
    profit_factor: float | None
    trades_count: int
    consecutive_losses: int
    last_updated: str
    reason: str

    @property
    def position_multiplier(self) -> float:
        """Position size multiplier based on health."""
        if self.status == "HEALTHY":
            return 1.0
        elif self.status == "CAUTION":
            return 0.5
        else:  # PAUSED
            return 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "shi_score": round(self.shi_score, 1),
            "status": self.status,
            "win_rate": round(self.win_rate * 100, 1) if self.win_rate else None,
            "profit_factor": round(self.profit_factor, 2) if self.profit_factor else None,
            "trades_count": self.trades_count,
            "consecutive_losses": self.consecutive_losses,
            "position_multiplier": self.position_multiplier,
            "last_updated": self.last_updated,
            "reason": self.reason,
        }


@contextmanager
def _conn():
    """Yields a D1 connection to the same `outcomes` table outcome_tracker.py
    uses — reusing its own connection here instead of maintaining a second,
    independent path to that table (a prior bug: this module used to open a
    local outcomes.db file directly via sqlite3, so under D1 that file never
    existed on the VPS and the auto-pause safety gate silently fell back to
    "no data yet — assuming healthy" for every symbol, forever)."""
    from storage.outcome_tracker import _conn as _outcome_conn, _init_db as _init_outcomes_db
    _init_outcomes_db()
    with _outcome_conn() as con:
        yield con


def get_symbol_health(symbol: str, lookback: int = 20) -> SymbolHealth:
    """Calculate health score for a symbol from outcome history."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    with _conn() as con:
        # Get last N closed trades for this symbol
        rows = con.execute("""
            SELECT outcome, pnl_pips, pnl_usd, regime
            FROM outcomes
            WHERE symbol=? AND outcome != 'open'
            ORDER BY entry_time DESC
            LIMIT ?
        """, (symbol, lookback)).fetchall()

    if len(rows) < 5:
        return SymbolHealth(
            symbol=symbol, shi_score=70.0, status="HEALTHY",
            win_rate=None, profit_factor=None, trades_count=len(rows),
            consecutive_losses=0, last_updated=now,
            reason=f"Insufficient data ({len(rows)}/5 minimum trades)"
        )

    # Win rate score (40 pts)
    wins = sum(1 for r in rows if r["outcome"] == "win")
    total = len(rows)
    wr = wins / total
    if wr >= 0.60:    wr_pts = 40.0
    elif wr >= 0.55:  wr_pts = 34.0
    elif wr >= 0.50:  wr_pts = 28.0
    elif wr >= 0.45:  wr_pts = 20.0
    else:             wr_pts = 8.0

    # Profit factor (30 pts)
    gross_profit = sum(r["pnl_pips"] for r in rows if (r["pnl_pips"] or 0) > 0) or 0
    gross_loss = abs(sum(r["pnl_pips"] for r in rows if (r["pnl_pips"] or 0) < 0)) or 0.001
    pf = gross_profit / gross_loss
    pf_score: float | None = pf
    if pf >= 2.0:    pf_pts = 30.0
    elif pf >= 1.5:  pf_pts = 24.0
    elif pf >= 1.2:  pf_pts = 18.0
    elif pf >= 1.0:  pf_pts = 12.0
    else:            pf_pts = 4.0

    # Consecutive losses (15 pts)
    consec = 0
    for r in rows:  # most recent first
        if r["outcome"] == "loss":
            consec += 1
        else:
            break
    if consec == 0:    consec_pts = 15.0
    elif consec == 1:  consec_pts = 12.0
    elif consec == 2:  consec_pts = 7.0
    elif consec == 3:  consec_pts = 2.0
    else:              consec_pts = 0.0

    # Recent trend — last 5 trades (15 pts)
    recent = list(rows[:5])
    recent_wins = sum(1 for r in recent if r["outcome"] == "win")
    if recent_wins >= 4:   trend_pts = 15.0
    elif recent_wins >= 3: trend_pts = 10.0
    elif recent_wins >= 2: trend_pts = 5.0
    else:                  trend_pts = 0.0

    score = min(100.0, wr_pts + pf_pts + consec_pts + trend_pts)

    # Status
    reasons = []
    if wr < 0.45: reasons.append(f"WR={wr:.0%} below 45%")
    if pf < 1.0:  reasons.append(f"PF={pf:.2f} (net losing)")
    if consec >= 3: reasons.append(f"{consec} consecutive losses")

    if score >= SHI_HEALTHY and not reasons:
        status = "HEALTHY"
        reason = f"WR={wr:.0%} PF={pf:.2f} — performing well"
    elif score >= SHI_CAUTION and len(reasons) <= 1:
        status = "CAUTION"
        reason = "; ".join(reasons) if reasons else f"WR={wr:.0%} — below average"
    else:
        status = "PAUSED"
        reason = "; ".join(reasons) if reasons else f"SHI={score:.0f} below threshold"

    logger.info(f"Symbol health {symbol}: SHI={score:.0f} ({status}) — {reason}")

    return SymbolHealth(
        symbol=symbol,
        shi_score=round(score, 1),
        status=status,
        win_rate=wr,
        profit_factor=pf_score,
        trades_count=total,
        consecutive_losses=consec,
        last_updated=now,
        reason=reason,
    )


def get_all_symbol_health(symbols: list[str]) -> list[dict]:
    """Get health for all symbols, sorted by score."""
    results = [get_symbol_health(sym).to_dict() for sym in symbols]
    return sorted(results, key=lambda x: x["shi_score"], reverse=True)
