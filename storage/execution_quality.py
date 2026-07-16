"""
storage/execution_quality.py
-----------------------------
Execution-quality (TCA) ledger — the implementation-shortfall record
(institutional gap analysis M1).

The measurement this closes: the pipeline knows the price it decided at
(the report's `entry_price`) and the broker reports the price it filled
at (`ExecutionResult.entry_price` from the deal's executionPrice), but
nothing ever compared them. The backtest ASSUMES 0.5 pips of slippage
(`backtesting/backtest_engine.py BacktestConfig.slippage_pips`); this
ledger is what verifies or refutes that assumption from real fills
(Perold 1988 — the paper-vs-real gap is the part of the edge nobody
backtests).

Units are deliberately identical to the backtest engine's pip convention
(0.01 for JPY pairs / metals / crypto / indices, 0.0001 for other FX) so
`summary()`'s numbers are directly comparable to `slippage_pips=0.5`
without any conversion.

Sign convention: slippage is ADVERSE-positive.
    BUY : slippage = fill − intended   (paid more than planned → +)
    SELL: slippage = intended − fill   (received less than planned → +)
A negative value is price improvement.

`slippage_r` normalizes the cost by the trade's risk (SL distance):
the number that plugs straight into expectancy math — a mean of +0.02
means every trade starts 0.02 R behind the backtest.

Only real broker fills are recorded. Dry-run "fills" echo the intended
price back (slippage ≡ 0 by construction) and would only dilute the
statistic.

Per-session dimensions (gap analysis addendum A1): every fill is tagged
with the active session so the report can show that, e.g., London-open
fills cost 3× the assumption — microstructure as measurement, never as
a gate.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.d1_client import D1Error
from utils.logger import get_logger

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS fills (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    direction         TEXT NOT NULL,             -- BUY | SELL
    broker            TEXT,
    trade_id          TEXT,
    session           TEXT,                      -- Asia | London | NewYork | Overlap | Off
    intended_price    REAL NOT NULL,             -- report entry_price (decision-sized price)
    fill_price        REAL NOT NULL,             -- broker executionPrice
    stop_loss         REAL,
    volume            REAL,
    pip_size          REAL NOT NULL,
    slippage_price    REAL NOT NULL,             -- signed, adverse-positive, price units
    slippage_pips     REAL NOT NULL,             -- same, in backtest pip units
    slippage_r        REAL,                      -- same, as fraction of SL distance
    spread_at_fill    REAL,                      -- reserved: broker event doesn't expose it yet
    decision_bar_time TEXT,                      -- ties the fill to its decision bar
    git_commit        TEXT                       -- provenance tie-in (M2)
)
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_fills_symbol ON fills(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_fills_session ON fills(session)",
    "CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts)",
]

# The backtest's cost assumption these measurements exist to verify.
BACKTEST_SLIPPAGE_ASSUMPTION_PIPS = 0.5


def _init(con) -> None:
    con.execute(_DDL)
    for idx in _INDEXES:
        con.execute(idx)


# ---------------------------------------------------------------------------
# Pure math (unit-tested directly)
# ---------------------------------------------------------------------------

def pip_size_for(symbol: str) -> float:
    """The backtest engine's pip convention, verbatim
    (backtesting/backtest_engine.py config_for_symbol): 0.0001 for FX,
    0.01 for JPY pairs, metals, energy, indices and crypto."""
    sym = symbol.upper()
    try:
        from core.asset_profiles import get_profile
        ac = get_profile(sym).asset_class.lower()
    except Exception:
        ac = "forex"
    if ac == "forex":
        return 0.01 if "JPY" in sym else 0.0001
    return 0.01


def compute_slippage(direction: str, intended: float, fill: float) -> float:
    """Signed slippage in PRICE units, adverse-positive (see module doc)."""
    if direction.upper() in ("BUY", "BULLISH"):
        return fill - intended
    return intended - fill


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def log_fill(report: dict, exec_result: Any, broker: str | None = None) -> bool:
    """Record one real broker fill. Never raises — a TCA write failure
    must not disturb the trade that just executed.

    Args:
        report: the pipeline report the trade came from (intended price,
                bar time, provenance).
        exec_result: execution.trade_executor.ExecutionResult (or any
                object with the same attributes).
        broker: which broker filled it ("ctrader" | "oanda"), from the
                caller's execution config.

    Returns True if a row was written.
    """
    try:
        if not getattr(exec_result, "executed", False):
            return False
        if getattr(exec_result, "dry_run", True):
            logger.debug("TCA: dry-run fill ignored (slippage ≡ 0 by construction)")
            return False

        symbol = getattr(exec_result, "symbol", "") or report.get("symbol", "")
        direction = getattr(exec_result, "direction", "")
        intended = report.get("entry_price")
        fill = getattr(exec_result, "entry_price", 0.0)
        if not symbol or not direction or not intended or not fill:
            logger.warning(
                f"TCA: fill for {symbol!r} missing intended/fill price — not recorded"
            )
            return False

        intended = float(intended)
        fill = float(fill)
        pip = pip_size_for(symbol)
        slip_price = compute_slippage(direction, intended, fill)
        slip_pips = slip_price / pip

        stop = report.get("stop_loss")
        slip_r = None
        if stop:
            sl_dist = abs(intended - float(stop))
            if sl_dist > 0:
                slip_r = slip_price / sl_dist

        try:
            from regimes.session_context import detect_session
            session = detect_session().primary_session
        except Exception:
            session = None

        provenance = report.get("provenance") or {}

        with d1_client.d1_connection() as con:
            _init(con)
            con.execute(
                """INSERT INTO fills
                   (ts, symbol, direction, broker, trade_id, session,
                    intended_price, fill_price, stop_loss, volume, pip_size,
                    slippage_price, slippage_pips, slippage_r,
                    decision_bar_time, git_commit)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    symbol,
                    direction.upper(),
                    broker,
                    str(getattr(exec_result, "trade_id", "") or ""),
                    session,
                    intended,
                    fill,
                    float(stop) if stop else None,
                    float(getattr(exec_result, "units", 0) or 0) or None,
                    pip,
                    round(slip_price, 8),
                    round(slip_pips, 3),
                    round(slip_r, 5) if slip_r is not None else None,
                    str(report.get("bar_time", "") or "") or None,
                    provenance.get("git_commit"),
                ),
            )
        logger.info(
            f"TCA: {direction} {symbol} intended={intended} fill={fill} "
            f"slippage={slip_pips:+.2f} pips"
            + (f" ({slip_r:+.4f} R)" if slip_r is not None else "")
        )
        return True
    except D1Error as exc:
        logger.warning(f"TCA write failed (non-fatal): {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 — must never disturb execution
        logger.warning(f"TCA logging error (non-fatal): {exc}")
        return False


# ---------------------------------------------------------------------------
# Read / report
# ---------------------------------------------------------------------------

def _bucket(rows: list[dict]) -> dict[str, Any]:
    pips = [r["slippage_pips"] for r in rows if r["slippage_pips"] is not None]
    rs = [r["slippage_r"] for r in rows if r["slippage_r"] is not None]
    if not pips:
        return {"n": 0}
    pips_sorted = sorted(pips)
    return {
        "n": len(pips),
        "mean_slippage_pips": round(statistics.fmean(pips), 3),
        "median_slippage_pips": round(statistics.median(pips), 3),
        "p90_slippage_pips": round(pips_sorted[min(len(pips_sorted) - 1, int(0.9 * len(pips_sorted)))], 3),
        "worst_slippage_pips": round(max(pips), 3),
        "best_slippage_pips": round(min(pips), 3),
        "mean_slippage_r": round(statistics.fmean(rs), 5) if rs else None,
    }


def summary() -> dict[str, Any]:
    """The TCA report: overall / per-symbol / per-session slippage vs the
    backtest assumption. Read `mean_slippage_pips` against
    `backtest_assumption_pips` — sustained live slippage above it means
    the backtested edge is overstated by the difference."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = [
            {k: r[k] for k in ("symbol", "session", "slippage_pips", "slippage_r")}
            for r in con.execute(
                "SELECT symbol, session, slippage_pips, slippage_r FROM fills"
            ).fetchall()
        ]
        recent_cols = ("ts", "symbol", "direction", "session", "intended_price",
                       "fill_price", "slippage_pips", "slippage_r", "trade_id")
        recent = [
            {k: r[k] for k in recent_cols}
            for r in con.execute(
                "SELECT ts, symbol, direction, session, intended_price, "
                "fill_price, slippage_pips, slippage_r, trade_id "
                "FROM fills ORDER BY id DESC LIMIT 20"
            ).fetchall()
        ]

    by_symbol: dict[str, list[dict]] = {}
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)
        by_session.setdefault(r["session"] or "unknown", []).append(r)

    return {
        "backtest_assumption_pips": BACKTEST_SLIPPAGE_ASSUMPTION_PIPS,
        "overall": _bucket(rows),
        "by_symbol": {s: _bucket(v) for s, v in sorted(by_symbol.items())},
        "by_session": {s: _bucket(v) for s, v in sorted(by_session.items())},
        "recent": recent,
        "note": (
            "Adverse-positive, in backtest pip units — directly comparable "
            "to BacktestConfig.slippage_pips. mean_slippage_r is the cost "
            "as a fraction of each trade's risk (expectancy haircut). "
            "Real broker fills only; dry-run signals are excluded."
        ),
    }
