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
from contextlib import contextmanager
from datetime import datetime, timezone

from storage import d1_client
from utils import trade_math
from utils.logger import get_logger

logger = get_logger(__name__)

# Default per-trade risk budget in USD, matching config.yaml defaults:
# risk.risk_per_trade_max (0.01) × risk.starting_balance (10 000).
# Callers with different sizing should pass ``risk_usd`` explicitly.
DEFAULT_RISK_USD: float = 100.0

# Broker-confirmed pip sizes by asset class (IC Markets cTrader
# ProtoOASymbolById, verified 2026-07-16 via scripts.ctrader_inspect_symbols).
# pip_size = 10^-(pip position) from the live symbol spec:
#   FX (EURUSD)   digits=5 pip=4 → 0.0001   (JPY pairs digits=3 pip=2 → 0.01)
#   METALS (XAU)  digits=2 pip=2 → 0.01     (XAG digits=3 pip=2 → 0.01)
#   ENERGY (XTI)  digits=2 pip=2 → 0.01
#   CRYPTO (BTC)  digits=2 pip=2 → 0.01
#   INDICES (US30/US500/USTEC) digits=2 pip=1 → 0.1
# The previous inline table sent CRYPTO/INDICES/ENERGY to the FX default
# (0.0001), so e.g. a BTC move (thousands of USD) / 0.0001 produced millions of
# phantom "pips" that dominated total_pips; it also had XAGUSD at 0.001 when the
# broker reports 0.01.
_PIP_SIZE_BY_CLASS: dict[str, float] = {
    "FOREX": 0.0001,
    "METALS": 0.01,
    "ENERGY": 0.01,
    "CRYPTO": 0.01,
    "INDICES": 0.1,   # US30/US500/USTEC: broker pip=1
}


def _pip_size(symbol: str) -> float:
    """Price increment of one pip for ``symbol``, matching the broker's spec.

    JPY forex pairs use 0.01; all other forex 0.0001. Non-forex classes follow
    the broker's pip position (0.01). Unknown symbols fall back to the forex
    default so a mislabeled symbol never re-triggers the millions-of-pips bug.
    """
    if "JPY" in symbol.upper():
        return 0.01
    try:
        from core.asset_profiles import get_profile
        asset_class = get_profile(symbol).asset_class
    except KeyError:
        return 0.0001
    return _PIP_SIZE_BY_CLASS.get(asset_class, 0.0001)


@contextmanager
def _conn():
    """Yields a D1 connection. See storage/d1_client.py."""
    with d1_client.d1_connection() as con:
        yield con


def _init_db() -> None:
    with _conn() as con:
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


# ─── Write ─────────────────────────────────────────────────────────────────

def log_signal(report: dict) -> str:
    """Log an EXECUTE signal for outcome tracking.

    Called automatically when IATIS generates an EXECUTE verdict.
    Returns signal_id.
    """
    _init_db()

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
        with _conn() as con:
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
    _init_db()

    if exit_time is None:
        exit_time = datetime.now(timezone.utc).isoformat()

    # Fetch signal to calculate P&L
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM outcomes WHERE signal_id=?", (signal_id,)
        ).fetchone()

        if not row:
            logger.warning(f"Signal {signal_id} not found in outcome_tracker")
            return False

        entry = row["entry_price"] or 0
        direction = row["direction"]
        symbol = row["symbol"]

        # Pip P&L — pip_size MUST match the broker's pip definition per asset
        # class (see _pip_size / _PIP_SIZE_BY_CLASS above). The old inline table
        # defaulted crypto/indices/energy to 0.0001 and produced millions of
        # phantom pips; this routes by measured broker spec instead.
        pip_size = _pip_size(symbol)
        price_diff = trade_math.price_diff(entry, exit_price, direction)
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

def get_open_signals() -> list[dict]:
    """Get all signals still awaiting outcome."""
    _init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM outcomes WHERE outcome='open' ORDER BY entry_time DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def performance_summary() -> dict:
    """Overall performance statistics from closed signals."""
    _init_db()
    with _conn() as con:
        total = con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE outcome != 'open'"
        ).fetchone()[0]
        wins = con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE outcome='win'"
        ).fetchone()[0]
        losses = con.execute(
            "SELECT COUNT(*) FROM outcomes WHERE outcome='loss'"
        ).fetchone()[0]
        # NOTE: total_pips is recomputed below from per-row prices — the
        # stored pnl_pips column is poisoned on legacy rows written before
        # the 2026-07-16 pip-size fix (crypto/indices went through the FX
        # pip size, producing millions of phantom pips that made the
        # dashboard's expectancy read −857k pips/trade).
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

        # Regime breakdown (avg_pips recomputed from prices below — the
        # stored pnl_pips column is poisoned on legacy rows).
        regime_rows = con.execute("""
        SELECT regime, COUNT(*) as n,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins
        FROM outcomes
        WHERE outcome != 'open' AND regime IS NOT NULL
        GROUP BY regime
        """).fetchall()
        regime_px_rows = con.execute("""
        SELECT regime, symbol, entry_price, exit_price, direction
        FROM outcomes
        WHERE outcome != 'open' AND regime IS NOT NULL
          AND entry_price IS NOT NULL AND exit_price IS NOT NULL
        """).fetchall()

        # Per-row prices for every derived figure below. Recomputed exactly
        # from each row's own entry/stop/exit (never from the stored
        # pnl_pips/pnl_usd columns): legacy rows carry both a poisoned
        # pnl_pips (pre-2026-07-16 pip-size bug) and a pnl_usd written with
        # the old inflated "1 standard lot" approximation — this repo's own
        # rule is real evidence over convenient shortcuts.
        r_rows = con.execute("""
        SELECT symbol, entry_price, stop_loss, exit_price, direction
        FROM outcomes
        WHERE outcome != 'open' AND entry_price IS NOT NULL
          AND exit_price IS NOT NULL
        """).fetchall()

    r_multiples: list[float] = []
    total_pips = 0.0
    for row in r_rows:
        entry, exit_px = row["entry_price"], row["exit_price"]
        diff = trade_math.price_diff(entry, exit_px, row["direction"])
        total_pips += diff / _pip_size(row["symbol"] or "")
        sl = row["stop_loss"]
        sl_distance = abs(entry - sl) if sl is not None else 0.0
        if sl_distance > 0:
            r_multiples.append(diff / sl_distance)
    avg_r_multiple = round(sum(r_multiples) / len(r_multiples), 3) if r_multiples else None
    total_r = round(sum(r_multiples), 2) if r_multiples else None

    # Profit factor — same gross-win/gross-loss definition as
    # scripts/forward_review.py's _bucket_stats, applied to risk-normalized
    # R-multiples (equivalent ordering to the pnl_usd version when pnl_usd
    # is intact, immune to the legacy corruption when it is not).
    #
    # trade_math.profit_factor returns the JSON-safe "Infinity" sentinel
    # (a bare float("inf") is not valid JSON; a browser's fetch().json()
    # throws on it) for wins-with-zero-losses, and None — not "Infinity"
    # — for an all-breakeven book (0/0 is undefined, not infinite; this
    # exact case was a real bug in this function's own former inline copy
    # of this formula until the audit's P2-9 consolidation caught it by
    # writing shared tests for storage/journal.py's sibling copy first).
    pf = trade_math.profit_factor(r_multiples)
    profit_factor: float | str | None = round(pf, 3) if isinstance(pf, float) else pf

    win_rate = round(wins / total * 100, 1) if total > 0 else 0
    return {
        "total_closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pips": round(total_pips, 1),
        "profit_factor": profit_factor,
        "avg_r_multiple": avg_r_multiple,
        "total_r": total_r,
        "open_signals": open_count,
        "calibration": [dict(r) for r in calibration_rows],
        "by_regime": _regime_breakdown(regime_rows, regime_px_rows),
        "note": f"Need 200+ trades for statistical significance (current: {total})",
    }


def _regime_breakdown(regime_rows, regime_px_rows) -> list[dict]:
    """Per-regime stats with avg_pips recomputed from row prices (the
    stored pnl_pips is unreliable on legacy rows — see _PIP_SIZE_BY_CLASS)."""
    pips_by_regime: dict[str, list[float]] = {}
    for row in regime_px_rows:
        entry, exit_px = row["entry_price"], row["exit_price"]
        diff = trade_math.price_diff(entry, exit_px, row["direction"])
        pips_by_regime.setdefault(row["regime"], []).append(
            diff / _pip_size(row["symbol"] or "")
        )
    out = []
    for r in regime_rows:
        d = dict(r)
        pips = pips_by_regime.get(d.get("regime"), [])
        d["avg_pips"] = round(sum(pips) / len(pips), 1) if pips else None
        out.append(d)
    return out


def recent_signals(limit: int = 10) -> list[dict]:
    """Get most recent signals."""
    _init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM outcomes ORDER BY entry_time DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Auto-Close ────────────────────────────────────────────────────────────

def auto_close_outcomes(
    current_prices: dict[str, float],
    bar_ranges: dict[str, tuple[float, float]] | None = None,
    max_open_hours: float | None = None,
) -> list[dict]:
    """Check open signals against current prices and auto-close if TP/SL hit.

    Called automatically at the end of each scheduler run.

    Open-outcome hygiene (philosophy audit, priority 4): stale open paper
    trades saturate the 5%% exposure cap (risk_per_trade_max=0.01 × 5 slots)
    and were observed blocking new signals live. Two mechanisms fix that:

    1. INTRABAR detection via ``bar_ranges``: the old check compared only
       the tick-time close, so a TP/SL touched inside the bar and retraced
       was never detected — the trade stayed open (and its eventual label
       was wrong). With the decision bar's (high, low) the touch is seen.
       Convention parity with backtesting/backtest_engine.check_exit():
       when BOTH levels are touched within one bar, SL is assumed first
       (conservative — counts as a loss).
    2. TIME STOP via ``max_open_hours``: a signal that never reaches TP or
       SL now force-closes at the current price after this many hours
       (outcome by realized R: > +0.1R win, < −0.1R loss, else breakeven),
       so the paper book cannot stay saturated indefinitely.

    Args:
        current_prices: {symbol: current_price} from latest pipeline data
        bar_ranges: optional {symbol: (bar_high, bar_low)} of the latest
            closed decision bar; falls back to close-only checks if absent
        max_open_hours: force-close signals open longer than this
            (None/0 disables — old behavior)

    Returns:
        One record per closed signal:
        ``{"signal_id", "symbol", "direction", "outcome", "exit_price"}``.
        Empty list when nothing closed (``len()`` gives the old count).
    """
    _init_db()
    open_signals = get_open_signals()
    closed: list[dict] = []
    bar_ranges = bar_ranges or {}
    now = datetime.now(timezone.utc)

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

        # Effective extremes for this tick: the decision bar's range when
        # provided, else the close price for both (old behavior).
        rng = bar_ranges.get(symbol)
        if rng and rng[0] is not None and rng[1] is not None:
            hi, lo = float(rng[0]), float(rng[1])
        else:
            hi = lo = float(price)

        # Check TP/SL hit.
        # log_signal() stores direction as the vote's winning bias
        # (BULLISH/BEARISH); broker paths may store BUY/SELL. Accept
        # both — previously only BUY/SELL matched, so signals logged by
        # the pipeline could NEVER auto-close.
        # SL BEFORE TP when both are inside the bar (backtest parity).
        hit = None
        if direction in ("BUY", "BULLISH"):
            if lo <= sl:
                hit = ("loss", sl)
            elif hi >= tp:
                hit = ("win", tp)
        elif direction in ("SELL", "BEARISH"):
            if hi >= sl:
                hit = ("loss", sl)
            elif lo <= tp:
                hit = ("win", tp)

        # Time stop: neither level reached but the signal is stale.
        if hit is None and max_open_hours:
            age_h = _open_age_hours(sig.get("entry_time"), now)
            if age_h is not None and age_h >= max_open_hours:
                diff = trade_math.price_diff(entry, price, direction)
                sl_dist = abs(entry - sl)
                r = diff / sl_dist if sl_dist > 0 else 0.0
                outcome = "win" if r > 0.1 else "loss" if r < -0.1 else "breakeven"
                success = close_signal(
                    signal_id=sig_id,
                    exit_price=float(price),
                    outcome=outcome,
                    notes=f"time_stop: open {age_h:.0f}h >= {max_open_hours:.0f}h, "
                          f"closed at market ({r:+.2f}R)",
                )
                if success:
                    closed.append({
                        "signal_id": sig_id,
                        "symbol": symbol,
                        "direction": direction,
                        "outcome": outcome,
                        "exit_price": float(price),
                    })
                    logger.info(
                        f"Time-stopped {sig_id} ({symbol} {direction}): "
                        f"{outcome} @ {price:.5f} after {age_h:.0f}h"
                    )
            continue

        if hit:
            outcome, exit_px = hit
            success = close_signal(
                signal_id=sig_id,
                exit_price=exit_px,
                outcome=outcome,
                notes=f"auto_close: bar range [{lo:.5f}, {hi:.5f}] "
                      f"hit {'TP' if outcome == 'win' else 'SL'}",
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
                    f"{outcome} @ {exit_px:.5f} (bar=[{lo:.5f}, {hi:.5f}])"
                )

    return closed


def _open_age_hours(entry_time: str | None, now: datetime) -> float | None:
    """Age of an open signal in hours; None when entry_time is unparseable."""
    if not entry_time:
        return None
    try:
        opened = datetime.fromisoformat(str(entry_time))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return (now - opened).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None
