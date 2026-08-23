"""
storage/hypothesis_policy.py
---------------------------------
Hypothesis Discovery Engine, Phase 6 — D1 persistence for backtest.
hypothesis_policy.grant_policy()/revoke_policy()'s append-only Symbol
Policy ledger. Same `_DDL` + `_init(con)` idiom as storage/hypothesis_
promotion.py.

NON-NEGOTIABLE: this module is bookkeeping only. It never fetches a
Promotion/Hypothesis/Mission/Cell, never decides GRANTED/REVOKED, never
re-verifies a governance chain, and never writes to any other table in
this engine. Matching every other storage/*.py file in this codebase,
this module never imports backtest/*.py — the decision logic and
identity re-verification live one layer up, in backtest.hypothesis_
policy (which already imports THIS module).

One table: `research_symbol_policy_events` — an append-only ledger, one
row per GRANTED or REVOKED event. `seq` (INTEGER PRIMARY KEY AUTOINCREMENT)
is the deterministic ordering column get_latest_policy_event() actually
sorts by — never `created_at` alone, which can collide or be an
unreliable ordering signal across events written in the same instant.
No update/delete function exists here — "changing" a policy means
recording a NEW event, never mutating a prior one.

Never touches research/results/registry.json, config.yaml, config/
engines.yaml, or config/symbols.yaml. Policy != Live execution — this
module is Policy bookkeeping only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_POLICY_EVENTS = """
CREATE TABLE IF NOT EXISTS research_symbol_policy_events (
    seq                       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id                  TEXT NOT NULL UNIQUE,
    symbol                    TEXT NOT NULL,
    engine                    TEXT NOT NULL,
    engine_version            TEXT NOT NULL,
    timeframe                 TEXT NOT NULL,
    risk_preset                TEXT NOT NULL,
    event_type                TEXT NOT NULL,
    promotion_id                TEXT,
    revokes_event_id            TEXT,
    hypothesis_id                TEXT,
    mission_id                   TEXT,
    cell_id                      TEXT,
    reason                      TEXT NOT NULL,
    actioned_by                  TEXT NOT NULL,
    research_code_commit         TEXT,
    data_snapshot_id             TEXT,
    created_at                  TEXT NOT NULL
)
"""
# The primary lookup this whole phase is built around: the EXACT identity
# tuple, never a symbol-only or engine-only index — see this module's own
# docstring, "IDENTITY is exact and non-inheritable."
_DDL_POLICY_EVENTS_IDENTITY_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rspe_identity ON research_symbol_policy_events"
    "(symbol, engine, engine_version, timeframe, risk_preset)"
)
_DDL_POLICY_EVENTS_PROMOTION_IDX = "CREATE INDEX IF NOT EXISTS idx_rspe_promotion ON research_symbol_policy_events(promotion_id)"
_DDL_POLICY_EVENTS_REVOKES_IDX = "CREATE INDEX IF NOT EXISTS idx_rspe_revokes ON research_symbol_policy_events(revokes_event_id)"


def _init(con) -> None:
    con.execute(_DDL_POLICY_EVENTS)
    con.execute(_DDL_POLICY_EVENTS_IDENTITY_IDX)
    con.execute(_DDL_POLICY_EVENTS_PROMOTION_IDX)
    con.execute(_DDL_POLICY_EVENTS_REVOKES_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def persist_policy_event(
    event_id: str, identity: dict[str, str], event_type: str, *,
    promotion_id: str | None, revokes_event_id: str | None,
    hypothesis_id: str | None, mission_id: str | None, cell_id: str | None,
    reason: str, actioned_by: str,
    research_code_commit: str | None = None, data_snapshot_id: str | None = None,
) -> None:
    """INSERT OR IGNORE keyed by the event_id's own UNIQUE index — a
    repeat call with the same event_id (a retried grant_policy()/
    revoke_policy() after a crash, or a legitimate idempotent re-call) is
    always safe and writes nothing a second time."""
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT OR IGNORE INTO research_symbol_policy_events
               (event_id, symbol, engine, engine_version, timeframe, risk_preset, event_type,
                promotion_id, revokes_event_id, hypothesis_id, mission_id, cell_id,
                reason, actioned_by, research_code_commit, data_snapshot_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id, identity["symbol"], identity["engine"], identity["engine_version"],
                identity["timeframe"], identity["risk_preset"], event_type,
                promotion_id, revokes_event_id, hypothesis_id, mission_id, cell_id,
                reason, actioned_by, research_code_commit, data_snapshot_id, _now_iso(),
            ),
        )


def get_policy_event(event_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_symbol_policy_events WHERE event_id=?", (event_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_latest_policy_event(
    symbol: str, engine: str, engine_version: str, timeframe: str, risk_preset: str,
) -> dict[str, Any] | None:
    """The single read primitive `get_symbol_policy()` and both write
    paths (grant_policy()/revoke_policy()) are built on: the most
    recently COMMITTED event for this EXACT identity, ordered by the
    ledger's own deterministic `seq` (never `created_at`). None means no
    event has ever been recorded for this identity — deny-by-default."""
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            """SELECT * FROM research_symbol_policy_events
               WHERE symbol=? AND engine=? AND engine_version=? AND timeframe=? AND risk_preset=?
               ORDER BY seq DESC LIMIT 1""",
            (symbol, engine, engine_version, timeframe, risk_preset),
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_policy_events_for_identity(
    symbol: str, engine: str, engine_version: str, timeframe: str, risk_preset: str, limit: int = 50,
) -> list[dict[str, Any]]:
    """Full event history for one exact identity, newest first — the
    forensic ledger view (never collapsed to "the latest one")."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            """SELECT * FROM research_symbol_policy_events
               WHERE symbol=? AND engine=? AND engine_version=? AND timeframe=? AND risk_preset=?
               ORDER BY seq DESC LIMIT ?""",
            (symbol, engine, engine_version, timeframe, risk_preset, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_policy_events_for_promotion(promotion_id: str) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            "SELECT * FROM research_symbol_policy_events WHERE promotion_id=? ORDER BY seq DESC", (promotion_id,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
