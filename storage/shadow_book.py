"""
storage/shadow_book.py
-----------------------
The Shadow Book — counterfactual outcomes for REJECTED signals.

The philosophy audit's single largest missing measurement: the pipeline
rejects ~98% of directional candidates and records the *reason* but never
the *outcome*. Without that counterfactual, every future threshold
calibration (min_score, quorum, info-share, exposure caps) is guesswork.

What this module does:
  - log_shadow_signal(report, config): whenever a decision is NO_TRADE but
    the vote had a direction, record a paper "shadow" trade with the SAME
    ATR-based levels the real trade would have used, tagged with the
    primary failing gate and the full fail-reason list.
  - auto_close_shadows(...): resolves shadows with the SAME mechanics as
    real outcomes (intrabar bar-range detection, SL-before-TP on
    both-touched, time stop) so shadow labels are comparable to real ones.
  - gate_ledger(): the payoff — per gate: how many shadows closed, their
    win rate, and total/average R. A gate with negative avg R is SAVING
    losses (working); a gate with positive avg R is REJECTING PROFIT
    (candidate for recalibration — after enough n, never before).

Shadow trades are hypothetical: no exposure caps, no correlation limits,
no execution. They measure what the GATES cost, not what a portfolio
would have earned — read avg R per gate, not total R as P&L.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error
from utils.logger import get_logger

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS shadow_signals (
    shadow_id    TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    direction    TEXT NOT NULL,
    entry_price  REAL NOT NULL,
    stop_loss    REAL NOT NULL,
    take_profit  REAL NOT NULL,
    cf_score     REAL,
    primary_gate TEXT,
    fail_reasons TEXT,
    outcome      TEXT DEFAULT 'open',
    exit_time    TEXT,
    exit_price   REAL,
    r_multiple   REAL
)
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_shadow_outcome ON shadow_signals(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_shadow_gate ON shadow_signals(primary_gate)",
]


def _init_db() -> None:
    try:
        with d1_client.d1_connection() as con:
            con.execute(_DDL)
            for idx in _INDEXES:
                con.execute(idx)
    except D1Error as exc:
        logger.warning(f"shadow_book init failed (non-fatal): {exc}")


def classify_gate(report: dict) -> str:
    """Primary rejecting gate, in pipeline order. One label per decision so
    the ledger's per-gate attribution is unambiguous."""
    summary = (report.get("summary") or "")
    cf = report.get("confluence", {}) or {}
    reasons = " ".join(cf.get("fail_reasons", []) or [])
    risk = report.get("risk", {}) or {}
    news = report.get("news", {}) or {}

    if "Market Quality" in summary:
        return "mqs"
    if "informative" in reasons or "mostly mute" in reasons:
        return "info_share"
    if "engine(s) agree" in reasons:
        return "quorum"
    if "below minimum required" in reasons:
        return "score"
    if cf.get("contradiction", {}).get("blocked"):
        return "contradiction"
    if (cf.get("reversal_veto") or {}).get("vetoed"):
        return "reversal_veto"
    if risk.get("passed") is False:
        return "risk"
    if news.get("blackout_active"):
        return "news"
    if report.get("downgrade_reason"):
        return "meta_or_regime"
    return "other"


def log_shadow_signal(report: dict, config: dict) -> str | None:
    """Record the counterfactual for a rejected directional decision.

    Levels replicate main._risk_gate's construction (entry = last close,
    SL = 2.5x ATR-estimate, TP = SL distance x per-symbol RR) so the
    shadow answers "what would THE SYSTEM'S trade have done", not some
    other trade. Returns shadow_id, or None when there is nothing to
    shadow (no direction / no price context / an EXECUTE)."""
    if report.get("final_verdict") != "NO_TRADE":
        return None
    bias = (report.get("confluence", {}).get("vote", {}) or {}).get("winning_bias")
    if bias not in ("BULLISH", "BEARISH"):
        return None
    price = report.get("current_price")
    bar_high, bar_low = report.get("bar_high"), report.get("bar_low")
    if not price or bar_high is None or bar_low is None:
        return None

    symbol = report.get("symbol", "UNKNOWN")
    # ATR estimate parity with main._risk_gate: mean H-L of the last 14
    # bars. The report carries only the last bar, so use its range as the
    # nearest available proxy; guarded against zero.
    atr_est = max(abs(float(bar_high) - float(bar_low)), float(price) * 1e-4)
    sym_cfg = next((s for s in config.get("data", {}).get("twelve_data_symbols", [])
                    if s.get("internal") == symbol), {})
    rr = sym_cfg.get("rr") or config.get("risk", {}).get("min_risk_reward", 2.0)
    sl_mult = config.get("risk", {}).get("sl_atr_multiplier", 2.5)
    direction = 1 if bias == "BULLISH" else -1
    entry = float(price)
    stop = entry - direction * atr_est * sl_mult
    target = entry + direction * atr_est * sl_mult * rr

    now = datetime.now(timezone.utc)
    shadow_id = f"S{now.strftime('%Y%m%dT%H%M%S')}_{symbol}"
    _init_db()
    try:
        with d1_client.d1_connection() as con:
            con.execute(
                """INSERT OR IGNORE INTO shadow_signals
                   (shadow_id, ts, symbol, direction, entry_price, stop_loss,
                    take_profit, cf_score, primary_gate, fail_reasons)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (shadow_id, now.isoformat(), symbol, bias, entry, stop, target,
                 report.get("confluence", {}).get("score"),
                 classify_gate(report),
                 json.dumps(report.get("confluence", {}).get("fail_reasons", [])
                            or [report.get("downgrade_reason") or
                                (report.get("risk", {}) or {}).get("reasons")])),
            )
        return shadow_id
    except D1Error as exc:
        logger.warning(f"shadow log failed (non-fatal): {exc}")
        return None


def get_open_shadows() -> list[dict]:
    _init_db()
    with d1_client.d1_connection() as con:
        rows = con.execute(
            "SELECT * FROM shadow_signals WHERE outcome='open'").fetchall()
    return [dict(zip(r.keys(), [r[k] for k in r.keys()]))
            if hasattr(r, "keys") else dict(r) for r in rows]


def _close(con, shadow_id: str, exit_price: float, outcome: str,
           entry: float, stop: float, direction: str) -> float:
    is_buy = direction == "BULLISH"
    diff = (exit_price - entry) if is_buy else (entry - exit_price)
    sl_dist = abs(entry - stop) or 1e-9
    r = round(diff / sl_dist, 3)
    con.execute(
        "UPDATE shadow_signals SET outcome=?, exit_time=?, exit_price=?, r_multiple=? "
        "WHERE shadow_id=?",
        (outcome, datetime.now(timezone.utc).isoformat(), exit_price, r, shadow_id),
    )
    return r


def auto_close_shadows(
    current_prices: dict[str, float],
    bar_ranges: dict[str, tuple[float, float]] | None = None,
    max_open_hours: float | None = None,
) -> int:
    """Resolve open shadows — identical conventions to
    outcome_tracker.auto_close_outcomes (bar-range touch detection,
    SL BEFORE TP when both are inside one bar, time stop at market).
    Returns how many were closed. Silent by design: shadows are
    measurements, not alerts."""
    from datetime import datetime as _dt
    bar_ranges = bar_ranges or {}
    now = datetime.now(timezone.utc)
    closed = 0
    _init_db()
    try:
        shadows = get_open_shadows()
        with d1_client.d1_connection() as con:
            for s in shadows:
                price = current_prices.get(s["symbol"])
                if price is None:
                    continue
                entry, sl, tp = s["entry_price"], s["stop_loss"], s["take_profit"]
                rng = bar_ranges.get(s["symbol"])
                hi, lo = (float(rng[0]), float(rng[1])) if rng else (float(price),) * 2
                is_buy = s["direction"] == "BULLISH"

                hit = None
                if is_buy:
                    if lo <= sl:
                        hit = ("loss", sl)
                    elif hi >= tp:
                        hit = ("win", tp)
                else:
                    if hi >= sl:
                        hit = ("loss", sl)
                    elif lo <= tp:
                        hit = ("win", tp)

                if hit is None and max_open_hours:
                    try:
                        opened = _dt.fromisoformat(str(s["ts"]))
                        if opened.tzinfo is None:
                            opened = opened.replace(tzinfo=timezone.utc)
                        age_h = (now - opened).total_seconds() / 3600
                    except (ValueError, TypeError):
                        age_h = None
                    if age_h is not None and age_h >= max_open_hours:
                        diff = (price - entry) if is_buy else (entry - price)
                        r = diff / (abs(entry - sl) or 1e-9)
                        outcome = ("win" if r > 0.1 else
                                   "loss" if r < -0.1 else "breakeven")
                        _close(con, s["shadow_id"], float(price), outcome,
                               entry, sl, s["direction"])
                        closed += 1
                    continue

                if hit:
                    _close(con, s["shadow_id"], hit[1], hit[0],
                           entry, sl, s["direction"])
                    closed += 1
    except D1Error as exc:
        logger.warning(f"shadow auto-close failed (non-fatal): {exc}")
    if closed:
        logger.info(f"Shadow book: closed {closed} counterfactual(s)")
    return closed


def gate_ledger() -> dict[str, Any]:
    """Per-gate counterfactual ledger — the number the whole module exists
    to produce. avg_r < 0: the gate saves losses (working). avg_r > 0:
    the gate rejects profit (recalibration candidate once n is adequate).
    """
    _init_db()
    with d1_client.d1_connection() as con:
        rows = con.execute(
            """SELECT primary_gate,
                      COUNT(*) AS n_closed,
                      SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins,
                      ROUND(AVG(r_multiple), 3) AS avg_r,
                      ROUND(SUM(r_multiple), 2) AS total_r
               FROM shadow_signals WHERE outcome != 'open'
               GROUP BY primary_gate ORDER BY n_closed DESC"""
        ).fetchall()
        open_count = con.execute(
            "SELECT COUNT(*) AS n FROM shadow_signals WHERE outcome='open'"
        ).fetchone()
    gates = []
    for r in rows:
        d = {k: r[k] for k in ("primary_gate", "n_closed", "wins", "avg_r", "total_r")}
        d["verdict"] = ("saving losses" if (d["avg_r"] or 0) < -0.05 else
                        "rejecting profit" if (d["avg_r"] or 0) > 0.05 else
                        "neutral")
        gates.append(d)
    return {
        "note": ("Counterfactuals of REJECTED signals, same exit mechanics as real "
                 "outcomes. Hypothetical: no exposure caps/correlation — read avg_r "
                 "per gate, not total_r as P&L. Do not recalibrate below n≈50/gate."),
        "open": open_count["n"] if open_count else 0,
        "gates": gates,
    }
