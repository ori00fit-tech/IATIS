"""
storage/journal.py
-------------------
Trade Journal — a read/annotate layer over the ``outcomes`` table
(storage/outcome_tracker.py) that turns the raw paper-trading ledger into
the detailed, filterable record the dashboard's Journal tab renders.

Scope, deliberately narrow (CLAUDE.md):
  - READ-ONLY over trade economics. This module never creates, closes,
    or re-prices a signal — that stays in outcome_tracker (write path)
    and the scheduler's auto-close. The only column the journal may
    write is the operator annotation (``notes``/``tags``), which has no
    effect on any measurement or gate.
  - All derived figures (realized R, duration, equity curve) are
    recomputed from each row's own stored prices — never from the
    convenience ``pnl_*`` columns, which legacy rows corrupted with the
    pre-2026-07-16 pip-size bug (see outcome_tracker._PIP_SIZE_BY_CLASS).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from storage import d1_client
from storage.outcome_tracker import _init_db, _pip_size
from utils import trade_math
from utils.logger import get_logger

logger = get_logger(__name__)

# Whitelisted values for filter parameters — anything else is ignored
# rather than interpolated into SQL.
_OUTCOME_VALUES = {"win", "loss", "breakeven", "open"}
_DIRECTION_VALUES = {"BUY", "SELL", "BULLISH", "BEARISH"}

# Thin aliases kept for this module's existing call sites — the real
# implementation lives in utils/trade_math.py, shared with
# storage/outcome_tracker.py and scripts/repair_outcome_pips.py (audit
# docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-9).
_is_buy = trade_math.is_buy_direction


def _realized_r(row: dict) -> float | None:
    """R-multiple recomputed from the row's own prices (never pnl_usd)."""
    r = trade_math.realized_r(
        row.get("entry_price"), row.get("stop_loss"), row.get("exit_price"),
        row.get("direction"),
    )
    return round(r, 4) if r is not None else None


def _planned_rr(row: dict) -> float | None:
    """Planned reward:risk from entry/SL/TP at signal time."""
    entry, sl, tp = row.get("entry_price"), row.get("stop_loss"), row.get("take_profit")
    if entry is None or sl is None or tp is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    return round(abs(tp - entry) / risk, 2)


def _clean_pips(row: dict) -> float | None:
    """pnl_pips recomputed from prices with the broker-verified pip size.

    The stored pnl_pips column is untrustworthy for legacy rows (the old
    inline table sent crypto/indices/energy through the FX pip size and
    produced millions of phantom pips); recomputing costs nothing and is
    always right.
    """
    entry, exit_px = row.get("entry_price"), row.get("exit_price")
    if entry is None or exit_px is None:
        return None
    diff = trade_math.price_diff(entry, exit_px, row.get("direction"))
    return round(diff / _pip_size(row.get("symbol", "")), 1)


def _duration_hours(row: dict) -> float | None:
    entry_t, exit_t = row.get("entry_time"), row.get("exit_time")
    if not entry_t or not exit_t:
        return None
    try:
        t0 = datetime.fromisoformat(str(entry_t))
        t1 = datetime.fromisoformat(str(exit_t))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        if t1.tzinfo is None:
            t1 = t1.replace(tzinfo=timezone.utc)
        return round((t1 - t0).total_seconds() / 3600.0, 1)
    except (ValueError, TypeError):
        return None


def _parse_engines(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _enrich(row: dict) -> dict[str, Any]:
    """One journal entry: the raw row plus every derived field the
    dashboard needs, computed once server-side so all consumers agree."""
    out = dict(row)
    out["engines"] = _parse_engines(row.get("engines"))
    out["realized_r"] = _realized_r(row)
    out["planned_rr"] = _planned_rr(row)
    out["pnl_pips_clean"] = _clean_pips(row)
    out["duration_hours"] = _duration_hours(row)
    tags_raw = row.get("tags")
    try:
        tags = json.loads(tags_raw) if tags_raw else []
        out["tags"] = tags if isinstance(tags, list) else []
    except (ValueError, TypeError):
        out["tags"] = []
    return out


def _has_tags_column(con) -> bool:
    """The ``tags`` column arrives via migration 3 — production may not
    have applied it yet, and additive-only migrations mean we tolerate
    both shapes rather than failing the whole journal."""
    try:
        cols = con.execute("PRAGMA table_info(outcomes)").fetchall()
        return any(c["name"] == "tags" for c in cols)
    except Exception:
        return False


def list_trades(
    symbol: str | None = None,
    outcome: str | None = None,
    direction: str | None = None,
    regime: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Filterable, paginated journal listing (newest first)."""
    _init_db()
    where: list[str] = []
    params: list[Any] = []

    if symbol:
        where.append("symbol = ?")
        params.append(symbol.upper())
    if outcome and outcome in _OUTCOME_VALUES:
        where.append("outcome = ?")
        params.append(outcome)
    if direction and direction.upper() in _DIRECTION_VALUES:
        # BUY and BULLISH are the same trade seen from two write paths.
        pair = ("BUY", "BULLISH") if _is_buy(direction.upper()) else ("SELL", "BEARISH")
        where.append("direction IN (?,?)")
        params.extend(pair)
    if regime:
        where.append("regime = ?")
        params.append(regime.upper())
    if date_from:
        where.append("entry_time >= ?")
        params.append(date_from)
    if date_to:
        where.append("entry_time <= ?")
        params.append(date_to)
    if search:
        where.append("(notes LIKE ? OR signal_id LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with d1_client.d1_connection() as con:
        total = con.execute(
            f"SELECT COUNT(*) AS n FROM outcomes {clause}", tuple(params)
        ).fetchone()["n"]
        rows = con.execute(
            f"SELECT * FROM outcomes {clause} ORDER BY entry_time DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)),
        ).fetchall()

    return {
        "total": total,
        "returned": len(rows),
        "offset": offset,
        "trades": [_enrich(dict(r)) for r in rows],
    }


def trade_detail(signal_id: str) -> dict[str, Any] | None:
    """Full journal entry for one signal, or None if unknown."""
    _init_db()
    with d1_client.d1_connection() as con:
        row = con.execute(
            "SELECT * FROM outcomes WHERE signal_id = ?", (signal_id,)
        ).fetchone()
    if not row:
        return None
    return _enrich(dict(row))


def annotate(
    signal_id: str, notes: str | None = None, tags: list[str] | None = None
) -> tuple[bool, bool]:
    """Operator annotation — the ONLY write this module performs.

    Notes/tags never feed any gate, weight, or measurement; they exist so
    a human can attach context ("news spike", "reviewed 07-22") to a
    trade for later reading. Absent fields are left unchanged.

    Returns (found, applied). `found` is False only when signal_id doesn't
    exist (callers should 404). `applied` is False when the signal_id was
    found but nothing was actually written — e.g. tags were requested but
    the tags-column migration (3) hasn't run yet and no notes were given.
    Previously this returned a single bool that was True whenever the
    signal_id existed, even if nothing was persisted — a silent no-op
    reported as success (audit docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-5).
    """
    _init_db()
    sets: list[str] = []
    params: list[Any] = []
    if notes is not None:
        sets.append("notes = ?")
        params.append(str(notes)[:2000])
    with d1_client.d1_connection() as con:
        exists = con.execute(
            "SELECT 1 FROM outcomes WHERE signal_id = ?", (signal_id,)
        ).fetchone()
        if not exists:
            return False, False
        if tags is not None:
            if _has_tags_column(con):
                cleaned = [str(t).strip()[:40] for t in tags if str(t).strip()][:12]
                sets.append("tags = ?")
                params.append(json.dumps(cleaned))
            else:
                # Migration 3 not applied yet — save what we can, loudly.
                logger.warning(
                    "Journal tags requested but outcomes.tags is missing — "
                    "run `python -m storage.migrations` (migration 3)."
                )
        if not sets:
            logger.warning(
                f"Journal annotation for {signal_id} applied nothing "
                "(no notes given, and tags could not be saved — see warning above if tags were requested)."
            )
            return True, False
        con.execute(
            f"UPDATE outcomes SET {', '.join(sets)} WHERE signal_id = ?",
            (*params, signal_id),
        )
    logger.info(f"Journal annotation saved for {signal_id}")
    return True, True


def journal_stats() -> dict[str, Any]:
    """Aggregate statistics for the Journal tab — every figure recomputed
    from row prices, chronological by exit time."""
    _init_db()
    with d1_client.d1_connection() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM outcomes ORDER BY entry_time ASC"
        ).fetchall()]

    closed = [r for r in rows if r.get("outcome") not in (None, "open")]
    open_rows = [r for r in rows if r.get("outcome") == "open"]

    # Chronological equity curve in R (exit-time order; unresolvable rows skipped)
    curve_rows = sorted(
        (r for r in closed if r.get("exit_time")),
        key=lambda r: str(r.get("exit_time")),
    )
    equity_curve: list[dict[str, Any]] = []
    cum_r = 0.0
    peak = 0.0
    max_dd = 0.0
    win_streak = loss_streak = cur_streak = 0
    last_sign = 0
    for r in curve_rows:
        rr = _realized_r(r)
        if rr is None:
            continue
        cum_r += rr
        peak = max(peak, cum_r)
        max_dd = max(max_dd, peak - cum_r)
        equity_curve.append({
            "signal_id": r.get("signal_id"),
            "exit_time": r.get("exit_time"),
            "r": round(rr, 3),
            "cum_r": round(cum_r, 3),
        })
        sign = 1 if rr > 0 else -1 if rr < 0 else 0
        if sign != 0 and sign == last_sign:
            cur_streak += 1
        elif sign != 0:
            cur_streak = 1
        last_sign = sign if sign != 0 else last_sign
        if sign > 0:
            win_streak = max(win_streak, cur_streak)
        elif sign < 0:
            loss_streak = max(loss_streak, cur_streak)

    r_values = [x["r"] for x in equity_curve]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    pf = trade_math.profit_factor(r_values)
    profit_factor: float | str | None = round(pf, 3) if isinstance(pf, float) else pf

    def _bucket(rows_subset: list[dict], key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict]] = {}
        for r in rows_subset:
            if key == "direction":
                # Normalize BUY/BULLISH and SELL/BEARISH into one bucket
                # each via the same _is_buy() helper _realized_r() already
                # uses — production only ever writes BULLISH/BEARISH
                # today, but a future write path storing BUY/SELL must
                # not silently fragment "by direction" into 4 buckets
                # instead of 2 (audit
                # docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P3-3).
                k = "BUY" if _is_buy(r.get(key)) else "SELL"
            else:
                k = str(r.get(key) or "—")
            groups.setdefault(k, []).append(r)
        out = []
        for k, grp in sorted(groups.items()):
            rs = [x for x in (_realized_r(g) for g in grp) if x is not None]
            n_wins = sum(1 for g in grp if g.get("outcome") == "win")
            out.append({
                key: k,
                "n": len(grp),
                "wins": n_wins,
                "win_rate": round(n_wins / len(grp) * 100, 1) if grp else None,
                "total_r": round(sum(rs), 2) if rs else None,
                "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            })
        return out

    durations = [d for d in (_duration_hours(r) for r in closed) if d is not None]
    best = max(equity_curve, key=lambda x: x["r"], default=None)
    worst = min(equity_curve, key=lambda x: x["r"], default=None)

    return {
        "total": len(rows),
        "closed": len(closed),
        "open": len(open_rows),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(closed) - len(wins) - len(losses),
        "win_rate": round(len(wins) / len(r_values) * 100, 1) if r_values else None,
        "total_r": round(sum(r_values), 2) if r_values else None,
        "avg_r": round(sum(r_values) / len(r_values), 3) if r_values else None,
        "profit_factor": profit_factor,
        "max_drawdown_r": round(max_dd, 2) if equity_curve else None,
        "longest_win_streak": win_streak,
        "longest_loss_streak": loss_streak,
        "avg_duration_hours": round(sum(durations) / len(durations), 1) if durations else None,
        "best_trade": best,
        "worst_trade": worst,
        "equity_curve": equity_curve,
        "by_symbol": _bucket(closed, "symbol"),
        "by_regime": _bucket(closed, "regime"),
        "by_direction": _bucket(closed, "direction"),
        "note": "All figures recomputed from per-row prices (R-based); "
                "stored pnl columns are ignored for legacy safety.",
    }


_CSV_FIELDS = [
    "signal_id", "symbol", "direction", "outcome",
    "entry_time", "exit_time", "duration_hours",
    "entry_price", "stop_loss", "take_profit", "exit_price",
    "planned_rr", "realized_r", "pnl_pips_clean",
    "cf_score", "regime", "news_risk", "notes",
]


# Leading characters Excel/Sheets/LibreOffice treat as the start of a
# formula when a CSV cell is opened — a classic CSV-injection vector via
# any operator-controlled free-text field (in practice, `notes`).
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """Prefix a leading formula-trigger character with `'` so the cell is
    always treated as text on open, never evaluated (audit
    docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P3-1)."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def export_csv() -> str:
    """The whole journal as CSV (newest first) for offline analysis."""
    listing = list_trades(limit=100_000)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for t in listing["trades"]:
        writer.writerow({k: _csv_safe(t.get(k)) for k in _CSV_FIELDS})
    return buf.getvalue()
