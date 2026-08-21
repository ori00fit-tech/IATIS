"""
storage/pretrade_decisions.py
--------------------------------
A first-class, persisted record of every PRE-TRADE HARD LIMITS decision —
`risk/pretrade_limits.py::evaluate_pretrade()`'s verdict on one concrete
order, immediately before broker submission. Covers BOTH outcomes:
rejected orders (never reached the broker) and approved orders (the
hard-limit validation result attached to a real fill).

Mirrors `storage/execution_attempts.py`'s exact conventions (separate
table, best-effort/non-raising persistence, never mistaken for a live
position by any other consumer) — this table is a decision-audit log,
never read by `risk/live_portfolio_state.py` or any other exposure
calculation, so a row here can never inflate open-risk the way an
`outcomes` row would.

Never logs credentials/secrets — every field here is order/limit
arithmetic, never an API key, token, or broker credential.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from storage import d1_client
from utils.logger import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from risk.pretrade_limits import PendingOrder, PretradeContext, PretradeDecision


@contextmanager
def _conn():
    with d1_client.d1_connection() as con:
        yield con


def _init_db() -> None:
    with _conn() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS pretrade_decisions (
            id                        TEXT PRIMARY KEY,
            decision_id               TEXT NOT NULL,
            symbol                    TEXT NOT NULL,
            direction                 TEXT NOT NULL,
            requested_quantity        REAL,
            reference_price           REAL,
            estimated_notional_usd    REAL,
            current_position_notional REAL,
            projected_position_notional REAL,
            portfolio_notional        REAL,
            approved                  INTEGER NOT NULL,
            limits_violated           TEXT,
            violations_json           TEXT,
            checks_json               TEXT NOT NULL,
            strategy_engine           TEXT,
            kill_switch_active        INTEGER NOT NULL,
            created_at                TEXT NOT NULL
        )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_pretrade_decisions_symbol ON pretrade_decisions(symbol)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_pretrade_decisions_decision_id ON pretrade_decisions(decision_id)"
        )


def record_pretrade_decision(
    decision: "PretradeDecision",
    order: "PendingOrder",
    context: "PretradeContext",
    *,
    kill_switch_active: bool,
    strategy_engine: str | None = None,
) -> str:
    """Persist one pre-trade hard-limits verdict — approved or rejected.
    Never raises out to the caller on a storage hiccup (matches
    `storage/execution_attempts.py`'s own convention): a failure to
    PERSIST the audit trail must never itself block or corrupt the real
    trading decision the caller has already made.
    """
    current_position = None
    if context.open_positions_notional is not None:
        current_position = context.open_positions_notional.get(order.symbol, 0.0)

    projected_position = None
    if current_position is not None and decision.estimated_notional_usd is not None:
        projected_position = current_position + decision.estimated_notional_usd

    limits_violated = ",".join(v.check for v in decision.violations)
    violations_payload = [
        {"check": v.check, "detail": v.detail} for v in decision.violations
    ]

    row_id = uuid.uuid4().hex
    try:
        _init_db()
        with _conn() as con:
            con.execute(
                """
                INSERT INTO pretrade_decisions
                (id, decision_id, symbol, direction, requested_quantity,
                 reference_price, estimated_notional_usd,
                 current_position_notional, projected_position_notional,
                 portfolio_notional, approved, limits_violated,
                 violations_json, checks_json, strategy_engine,
                 kill_switch_active, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row_id, decision.decision_id, order.symbol, order.direction,
                    order.quantity, order.reference_price,
                    decision.estimated_notional_usd,
                    current_position, projected_position,
                    context.portfolio_notional,
                    1 if decision.approved else 0,
                    limits_violated,
                    json.dumps(violations_payload),
                    json.dumps(decision.checks),
                    strategy_engine,
                    1 if kill_switch_active else 0,
                    decision.generated_at,
                ),
            )
    except Exception as exc:
        logger.warning(
            f"pretrade_decisions: failed to persist decision {decision.decision_id} "
            f"for {order.symbol}: {exc}"
        )
    return row_id


def recent_decisions(symbol: str | None = None, limit: int = 50) -> list[dict]:
    """Most recent pre-trade decisions, newest first — optionally scoped
    to one symbol. Best-effort: returns [] on any storage failure rather
    than raising, matching `storage/execution_attempts.py::recent_
    attempts()`'s own read-side convention."""
    try:
        _init_db()
        with _conn() as con:
            if symbol:
                cur = con.execute(
                    "SELECT * FROM pretrade_decisions WHERE symbol=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (symbol, limit),
                )
            else:
                cur = con.execute(
                    "SELECT * FROM pretrade_decisions ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning(f"pretrade_decisions: failed to read recent decisions: {exc}")
        return []
