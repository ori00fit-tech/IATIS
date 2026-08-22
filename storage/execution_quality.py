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
    git_commit        TEXT,                      -- provenance tie-in (M2)
    fill_latency_seconds REAL                    -- accept->confirmed-fill gap (async-fill path only; NULL when N/A)
)
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_fills_symbol ON fills(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_fills_session ON fills(session)",
    "CREATE INDEX IF NOT EXISTS idx_fills_ts ON fills(ts)",
]

# The backtest's cost assumption these measurements exist to verify.
BACKTEST_SLIPPAGE_ASSUMPTION_PIPS = 0.5

# ---------------------------------------------------------------------------
# Pending fills — durable queue for a broker fill whose real price isn't
# known yet at accept time (2026-08-17, TCA async-fill fix).
#
# Root cause this closes: cTrader's synchronous ProtoOANewOrderReq response
# is a ProtoOAExecutionEvent with executionType=ORDER_ACCEPTED (2), which
# frequently carries position.price == 0 — the real fill lands moments
# later on an ORDER_FILLED (3) / ORDER_PARTIAL_FILL (11) event pushed
# asynchronously on the reactor thread (execution/ctrader_client.py's
# _on_execution_event), which the old synchronous log_fill() call never
# saw. Every such fill was previously dropped with "TCA: fill ... missing
# intended/fill price — not recorded" and NEVER completed — a silent,
# permanent gap in the slippage ledger for exactly the fills that most
# needed measuring (the ones the fast synchronous path couldn't price).
#
# Durable (D1), not in-memory: survives a process restart between accept
# and fill — _on_reconcile_res's broker-truth position price on the next
# (re)connect can resolve a pending row just as well as a live
# _on_execution_event can (see execution/ctrader_client.py's
# _record_fill_update).
#
# Never fabricates: a row here carries NO fill_price at all until
# resolve_pending_fill() is called with a REAL broker-reported price.
# sweep_stale_pending_fills() marks a fill UNAVAILABLE after a bounded
# wait rather than leaving PENDING forever, but never invents a price to
# get there.
# ---------------------------------------------------------------------------

PENDING = "PENDING"
RESOLVED = "RESOLVED"
UNAVAILABLE = "UNAVAILABLE"
_PENDING_FILL_STATUSES = (PENDING, RESOLVED, UNAVAILABLE)

_DDL_PENDING = """
CREATE TABLE IF NOT EXISTS pending_fills (
    position_id    TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    ts_queued      TEXT NOT NULL,
    symbol         TEXT NOT NULL,
    direction      TEXT NOT NULL,
    broker         TEXT,
    trade_id       TEXT,
    intended_price REAL NOT NULL,
    stop_loss      REAL,
    volume         REAL,
    bar_time       TEXT,
    git_commit     TEXT
)
"""


def _init_fills_table(con) -> None:
    con.execute(_DDL)
    for idx in _INDEXES:
        con.execute(idx)


def _init_pending_table(con) -> None:
    con.execute(_DDL_PENDING)
    con.execute("CREATE INDEX IF NOT EXISTS idx_pending_fills_status ON pending_fills(status)")


def _init(con) -> None:
    """Ensures BOTH `fills` and `pending_fills` exist, regardless of which
    path (synchronous log_fill vs. the async pending-fill queue) a caller
    happens to exercise first — the two tables are one subsystem and a
    caller touching only one side must never hit a missing-table error on
    the other."""
    _init_fills_table(con)
    _init_pending_table(con)


def _init_pending(con) -> None:
    _init(con)


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

def _current_session() -> str | None:
    try:
        from regimes.session_context import detect_session
        return detect_session().primary_session
    except Exception:
        return None


def _insert_fill_row(
    con,
    *,
    symbol: str,
    direction: str,
    broker: str | None,
    trade_id: str,
    session: str | None,
    intended: float,
    fill: float,
    stop: float | None,
    volume: float | None,
    pip: float,
    slip_price: float,
    slip_pips: float,
    slip_r: float | None,
    bar_time: str | None,
    git_commit: str | None,
    fill_latency_seconds: float | None,
) -> None:
    con.execute(
        """INSERT INTO fills
           (ts, symbol, direction, broker, trade_id, session,
            intended_price, fill_price, stop_loss, volume, pip_size,
            slippage_price, slippage_pips, slippage_r,
            decision_bar_time, git_commit, fill_latency_seconds)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            symbol,
            direction.upper(),
            broker,
            trade_id,
            session,
            intended,
            fill,
            stop,
            volume,
            pip,
            round(slip_price, 8),
            round(slip_pips, 3),
            round(slip_r, 5) if slip_r is not None else None,
            bar_time,
            git_commit,
            round(fill_latency_seconds, 3) if fill_latency_seconds is not None else None,
        ),
    )


def log_fill(report: dict, exec_result: Any, broker: str | None = None) -> bool:
    """Record one real broker fill whose price is ALREADY known
    synchronously (the common case: OANDA, Dukascopy JForex, or a cTrader
    order-accept response that happened to carry a real price). Never
    raises — a TCA write failure must not disturb the trade that just
    executed.

    Args:
        report: the pipeline report the trade came from (intended price,
                bar time, provenance).
        exec_result: execution.trade_executor.ExecutionResult (or any
                object with the same attributes).
        broker: which broker filled it ("ctrader" | "oanda"), from the
                caller's execution config.

    Returns True if a row was written. Callers with a real position_id
    but no price yet should call queue_pending_fill() instead — or just
    call record_or_queue_fill(), which picks the right one automatically.
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
        sl_val = float(stop) if stop else None
        if sl_val:
            sl_dist = abs(intended - sl_val)
            if sl_dist > 0:
                slip_r = slip_price / sl_dist

        session = _current_session()
        provenance = report.get("provenance") or {}

        with d1_client.d1_connection() as con:
            _init(con)
            _insert_fill_row(
                con,
                symbol=symbol, direction=direction, broker=broker,
                trade_id=str(getattr(exec_result, "trade_id", "") or ""),
                session=session, intended=intended, fill=fill, stop=sl_val,
                volume=float(getattr(exec_result, "units", 0) or 0) or None,
                pip=pip, slip_price=slip_price, slip_pips=slip_pips, slip_r=slip_r,
                bar_time=str(report.get("bar_time", "") or "") or None,
                git_commit=provenance.get("git_commit"),
                fill_latency_seconds=None,  # synchronous path — the concept doesn't apply
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


def queue_pending_fill(report: dict, exec_result: Any, broker: str | None = None) -> bool:
    """Called when a broker order was accepted but its real fill price is
    NOT yet known (cTrader's synchronous ORDER_ACCEPTED response — see
    this module's pending-fills docstring above). Persists a durable
    PENDING row keyed by position_id; resolve_pending_fill() completes it
    once execution/ctrader_client.py reports a real broker price. Never
    writes a fill_price here at all — nothing to fabricate. Never raises.
    """
    try:
        if not getattr(exec_result, "executed", False):
            return False
        if getattr(exec_result, "dry_run", True):
            return False

        symbol = getattr(exec_result, "symbol", "") or report.get("symbol", "")
        direction = getattr(exec_result, "direction", "")
        intended = report.get("entry_price")
        position_id = str(getattr(exec_result, "trade_id", "") or "")
        if not symbol or not direction or not intended:
            logger.warning(f"TCA: cannot queue pending fill for {symbol!r} — missing symbol/direction/intended price")
            return False
        if not position_id:
            logger.warning(
                f"TCA: fill for {symbol!r} has no fill price AND no position_id "
                f"— cannot queue for async resolution, not recorded"
            )
            return False

        stop = report.get("stop_loss")
        provenance = report.get("provenance") or {}

        with d1_client.d1_connection() as con:
            _init_pending(con)
            con.execute(
                """INSERT OR IGNORE INTO pending_fills
                   (position_id, status, ts_queued, symbol, direction, broker,
                    trade_id, intended_price, stop_loss, volume, bar_time, git_commit)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    position_id, PENDING, datetime.now(timezone.utc).isoformat(),
                    symbol, direction.upper(), broker, position_id,
                    float(intended), float(stop) if stop else None,
                    float(getattr(exec_result, "units", 0) or 0) or None,
                    str(report.get("bar_time", "") or "") or None,
                    provenance.get("git_commit"),
                ),
            )
        logger.info(
            f"TCA: {direction} {symbol} pos_id={position_id} queued as PENDING "
            f"— fill price not yet confirmed by broker"
        )
        return True
    except D1Error as exc:
        logger.warning(f"TCA pending-fill queue write failed (non-fatal): {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 — must never disturb execution
        logger.warning(f"TCA pending-fill queue error (non-fatal): {exc}")
        return False


def record_or_queue_fill(report: dict, exec_result: Any, broker: str | None = None) -> str:
    """Single entry point a caller should use after every real (non-dry-
    run) broker execution. Returns 'RECORDED' (log_fill wrote a completed
    row immediately — the common case for a broker/response that DOES
    carry a synchronous real price), 'QUEUED' (price not yet known, a
    position_id is — queued for resolve_pending_fill() to complete
    later), or 'DROPPED' (neither a price nor a position_id — nothing
    durable to do, matches the pre-2026-08-17 warn-and-drop behavior)."""
    if not getattr(exec_result, "executed", False) or getattr(exec_result, "dry_run", True):
        return "DROPPED"
    fill = getattr(exec_result, "entry_price", 0.0)
    if fill:
        return "RECORDED" if log_fill(report, exec_result, broker=broker) else "DROPPED"
    return "QUEUED" if queue_pending_fill(report, exec_result, broker=broker) else "DROPPED"


def resolve_pending_fill(position_id: str, fill_price: float) -> bool:
    """Complete a pending fill once the broker's real fill price is known
    (execution/ctrader_client.py's in-memory fill-update registry,
    take_fill_update() — polled and resolved from the caller's own main
    thread, never from the reactor thread). Idempotent: a position_id
    that is already RESOLVED/UNAVAILABLE, or was never queued at all, is
    a no-op (returns False) — a duplicate resolution attempt (e.g. two
    execution events reporting the same fill) can never write two `fills`
    rows for the same position. `fill_price` must be a real, broker-
    reported value — this function never validates that on its own, so
    the caller (never anything client-supplied) is the trust boundary."""
    try:
        if fill_price <= 0:
            return False
        with d1_client.d1_connection() as con:
            _init_pending(con)
            _init(con)
            row = con.execute(
                "SELECT * FROM pending_fills WHERE position_id=?", (position_id,)
            ).fetchone()
            if row is None or row["status"] != PENDING:
                return False

            intended = float(row["intended_price"])
            direction = row["direction"]
            stop = row["stop_loss"]
            pip = pip_size_for(row["symbol"])
            slip_price = compute_slippage(direction, intended, fill_price)
            slip_pips = slip_price / pip
            slip_r = None
            if stop:
                sl_dist = abs(intended - float(stop))
                if sl_dist > 0:
                    slip_r = slip_price / sl_dist

            queued_at = datetime.fromisoformat(row["ts_queued"])
            latency = (datetime.now(timezone.utc) - queued_at).total_seconds()

            _insert_fill_row(
                con,
                symbol=row["symbol"], direction=direction, broker=row["broker"],
                trade_id=row["trade_id"] or position_id, session=_current_session(),
                intended=intended, fill=fill_price, stop=float(stop) if stop else None,
                volume=row["volume"], pip=pip, slip_price=slip_price,
                slip_pips=slip_pips, slip_r=slip_r,
                bar_time=row["bar_time"], git_commit=row["git_commit"],
                fill_latency_seconds=latency,
            )
            con.execute(
                "UPDATE pending_fills SET status=? WHERE position_id=? AND status=?",
                (RESOLVED, position_id, PENDING),
            )
        logger.info(
            f"TCA: {direction} {row['symbol']} pos_id={position_id} resolved — "
            f"intended={intended} fill={fill_price} slippage={slip_pips:+.2f} pips "
            f"(pending for {latency:.1f}s)"
        )
        return True
    except D1Error as exc:
        logger.warning(f"TCA pending-fill resolve failed (non-fatal): {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 — must never disturb execution
        logger.warning(f"TCA pending-fill resolve error (non-fatal): {exc}")
        return False


def mark_pending_fill_unavailable(position_id: str, reason: str = "") -> bool:
    """Idempotent: only a currently-PENDING row transitions to
    UNAVAILABLE; already-RESOLVED/UNAVAILABLE/unknown position_ids are a
    no-op. Never writes a `fills` row — this position's slippage is
    honestly unmeasured, not fabricated as zero."""
    try:
        with d1_client.d1_connection() as con:
            _init_pending(con)
            cur = con.execute(
                "UPDATE pending_fills SET status=? WHERE position_id=? AND status=?",
                (UNAVAILABLE, position_id, PENDING),
            )
            changed = getattr(cur, "rowcount", 0) or 0
        if changed:
            logger.warning(f"TCA: pos_id={position_id} fill price never confirmed by broker — marked UNAVAILABLE ({reason or 'no reason given'})")
        return bool(changed)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"TCA pending-fill mark-unavailable failed (non-fatal): {exc}")
        return False


def sweep_stale_pending_fills(max_age_seconds: float = 900.0) -> list[str]:
    """Marks every still-PENDING row older than max_age_seconds as
    UNAVAILABLE. Called periodically (the scheduler's per-tick pending-
    fill resolution pass) so a lost/never-arriving fill confirmation
    doesn't leave TCA silently waiting forever — the row just stops being
    a candidate for resolution, it is never converted into a fabricated
    slippage-free fill. Returns the position_ids that were swept."""
    try:
        cutoff = (datetime.now(timezone.utc).timestamp() - max_age_seconds)
        with d1_client.d1_connection() as con:
            _init_pending(con)
            rows = con.execute(
                "SELECT position_id, ts_queued FROM pending_fills WHERE status=?", (PENDING,)
            ).fetchall()
            stale = [
                r["position_id"] for r in rows
                if datetime.fromisoformat(r["ts_queued"]).timestamp() < cutoff
            ]
            for position_id in stale:
                con.execute(
                    "UPDATE pending_fills SET status=? WHERE position_id=? AND status=?",
                    (UNAVAILABLE, position_id, PENDING),
                )
        if stale:
            logger.warning(f"TCA: {len(stale)} pending fill(s) exceeded {max_age_seconds:.0f}s without broker confirmation — marked UNAVAILABLE: {stale}")
        return stale
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"TCA stale pending-fill sweep failed (non-fatal): {exc}")
        return []


def pending_fill_position_ids() -> list[str]:
    """position_ids currently PENDING — what a caller polls
    execution/ctrader_client.py's take_fill_update() against each tick."""
    try:
        with d1_client.d1_connection() as con:
            _init_pending(con)
            rows = con.execute(
                "SELECT position_id FROM pending_fills WHERE status=?", (PENDING,)
            ).fetchall()
        return [r["position_id"] for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"TCA pending-fill listing failed (non-fatal): {exc}")
        return []


def unavailable_fill_count() -> int:
    """Count of pending_fills rows that hit the bounded wait
    (sweep_stale_pending_fills) or an explicit mark_pending_fill_
    unavailable() without ever being resolved with a real broker price.
    Post-trade monitoring's evidence source for FILL_UNAVAILABLE
    incidents (execution/post_trade_monitor.py) — never fabricates a
    slippage figure for these, matches this module's own convention."""
    try:
        with d1_client.d1_connection() as con:
            _init_pending(con)
            row = con.execute(
                "SELECT COUNT(*) AS n FROM pending_fills WHERE status=?", (UNAVAILABLE,)
            ).fetchone()
        return int(row["n"]) if row else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"TCA unavailable-fill count failed (non-fatal): {exc}")
        return 0


def latency_stats() -> dict[str, Any]:
    """Aggregate fill_latency_seconds across every recorded fill (async
    path only — synchronous fills always have fill_latency_seconds=NULL,
    excluded here the same way summary()'s _bucket() excludes rows
    lacking the field in question). Read-only aggregation over the same
    `fills` table summary() already reads — no new measurement, no new
    table."""
    try:
        with d1_client.d1_connection() as con:
            _init(con)
            rows = con.execute(
                "SELECT fill_latency_seconds FROM fills WHERE fill_latency_seconds IS NOT NULL"
            ).fetchall()
        values = sorted(r["fill_latency_seconds"] for r in rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"TCA latency stats failed (non-fatal): {exc}")
        return {"n": 0}
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean_seconds": round(statistics.fmean(values), 3),
        "p95_seconds": round(values[min(len(values) - 1, int(0.95 * len(values)))], 3),
        "worst_seconds": round(max(values), 3),
    }


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
