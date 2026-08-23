"""
storage/hypothesis_decision_gate.py
---------------------------------------
Hypothesis Discovery Engine, Phase 7 — D1 persistence for backtest.
hypothesis_decision_gate.evaluate_live_decision()'s audit trail. Same
`_DDL` + `_init(con)` idiom as storage/hypothesis_policy.py.

NON-NEGOTIABLE: this module is bookkeeping only. It never checks the
kill switch, never looks up a policy, never decides PROCEED/NO_TRADE.
Matching every other storage/*.py file in this codebase, this module
never imports backtest/*.py.

Unlike every OTHER ledger in this engine (research_hypothesis_missions,
research_hypothesis_promotions, research_symbol_policy_events), rows here
are DELIBERATELY NOT deduplicated or idempotent — every call to record_
decision() is an independent, real, point-in-time observation. A live
decision loop legitimately re-evaluates the exact same identity many
times over; each evaluation is separately meaningful audit evidence
("was this identity authorized at THIS moment"), never collapsed to "has
this combination ever been decided before." `seq` (INTEGER PRIMARY KEY
AUTOINCREMENT) is both a row's own true identity and the ledger's natural
chronological order; `decision_id` is a random, non-deterministic token
(there is nothing to make it idempotent FROM — no two calls are ever
"the same call retried" the way a grant/revoke can be).

`risk_verdict`/`pretrade_limits_verdict` are reserved, honestly-nullable
columns: this phase never calls risk/risk_engine.py or risk/pretrade_
limits.py (no live wiring exists yet — see backtest.hypothesis_decision_
gate's own module docstring for why), so these are always NULL today.
The columns exist so a future wiring phase can populate them without a
schema migration — the same "the field exists, the mechanism doesn't
yet" discipline Phase 4 already used for data_snapshot_id.

Never touches research/results/registry.json, config.yaml, config/
engines.yaml, or config/symbols.yaml.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_LIVE_DECISIONS = """
CREATE TABLE IF NOT EXISTS research_live_decisions (
    seq                        INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id                TEXT NOT NULL UNIQUE,
    symbol                      TEXT NOT NULL,
    engine                      TEXT NOT NULL,
    engine_version               TEXT NOT NULL,
    timeframe                   TEXT NOT NULL,
    risk_preset                  TEXT NOT NULL,
    decision                    TEXT NOT NULL,
    decision_reason              TEXT NOT NULL,
    kill_switch_state            TEXT NOT NULL,
    policy_lookup_result          TEXT NOT NULL,
    policy_event_id                TEXT,
    policy_seq                     INTEGER,
    promotion_id                    TEXT,
    mission_id                      TEXT,
    hypothesis_id                    TEXT,
    risk_verdict                     TEXT,
    pretrade_limits_verdict           TEXT,
    research_code_commit              TEXT,
    data_snapshot_id                  TEXT,
    created_at                       TEXT NOT NULL
)
"""
_DDL_LIVE_DECISIONS_IDENTITY_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rld_identity ON research_live_decisions"
    "(symbol, engine, engine_version, timeframe, risk_preset)"
)
_DDL_LIVE_DECISIONS_DECISION_IDX = "CREATE INDEX IF NOT EXISTS idx_rld_decision ON research_live_decisions(decision)"
_DDL_LIVE_DECISIONS_POLICY_EVENT_IDX = "CREATE INDEX IF NOT EXISTS idx_rld_policy_event ON research_live_decisions(policy_event_id)"


def _init(con) -> None:
    con.execute(_DDL_LIVE_DECISIONS)
    con.execute(_DDL_LIVE_DECISIONS_IDENTITY_IDX)
    con.execute(_DDL_LIVE_DECISIONS_DECISION_IDX)
    con.execute(_DDL_LIVE_DECISIONS_POLICY_EVENT_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def record_decision(
    *, symbol: str, engine: str, engine_version: str, timeframe: str, risk_preset: str,
    decision: str, decision_reason: str, kill_switch_state: str, policy_lookup_result: str,
    policy_event_id: str | None = None, policy_seq: int | None = None,
    promotion_id: str | None = None, mission_id: str | None = None, hypothesis_id: str | None = None,
    research_code_commit: str | None = None, data_snapshot_id: str | None = None,
) -> dict[str, Any]:
    """A plain INSERT — never OR IGNORE, never deduplicated (see this
    module's own docstring: there is no such thing as "the same live
    decision call retried," every call is its own real observation).
    `risk_verdict`/`pretrade_limits_verdict` are always persisted as NULL
    by this phase — reserved for a future wiring phase, never fabricated
    here."""
    decision_id = f"LIVE-DECISION-{uuid.uuid4().hex[:16]}"
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_live_decisions
               (decision_id, symbol, engine, engine_version, timeframe, risk_preset,
                decision, decision_reason, kill_switch_state, policy_lookup_result,
                policy_event_id, policy_seq, promotion_id, mission_id, hypothesis_id,
                risk_verdict, pretrade_limits_verdict, research_code_commit, data_snapshot_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision_id, symbol, engine, engine_version, timeframe, risk_preset,
                decision, decision_reason, kill_switch_state, policy_lookup_result,
                policy_event_id, policy_seq, promotion_id, mission_id, hypothesis_id,
                None, None, research_code_commit, data_snapshot_id, now,
            ),
        )
        row = con.execute("SELECT * FROM research_live_decisions WHERE decision_id=?", (decision_id,)).fetchone()
    return _row_to_dict(row)


def get_decision(decision_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute("SELECT * FROM research_live_decisions WHERE decision_id=?", (decision_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_decisions_for_identity(
    symbol: str, engine: str, engine_version: str, timeframe: str, risk_preset: str, limit: int = 50,
) -> list[dict[str, Any]]:
    """Full chronological history for one exact identity, newest first —
    every independent evaluation ever recorded, never collapsed to "the
    latest one" (unlike Phase 6's own get_symbol_policy(), which reads
    the SAME underlying policy ledger but for a different, current-state
    purpose)."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            """SELECT * FROM research_live_decisions
               WHERE symbol=? AND engine=? AND engine_version=? AND timeframe=? AND risk_preset=?
               ORDER BY seq DESC LIMIT ?""",
            (symbol, engine, engine_version, timeframe, risk_preset, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_recent_decisions(limit: int = 100) -> list[dict[str, Any]]:
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute("SELECT * FROM research_live_decisions ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]
