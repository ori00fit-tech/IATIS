"""
storage/hypothesis_live_request.py
---------------------------------------
Hypothesis Discovery Engine, Phase 8C — D1 persistence for backtest.
hypothesis_live_request.evaluate_live_identity_request()'s governed risk
provenance snapshot. Same `_DDL` + `_init(con)` idiom as storage/
hypothesis_decision_gate.py (Phase 7) — this table is never registered in
storage/migrations.py, matching every other Hypothesis Discovery Engine
ledger (research_hypothesis_missions, research_hypothesis_promotions,
research_symbol_policy_events, research_live_decisions): it is created
lazily, on first use, by _init(con) inside d1_connection(), not by an
ALTER-TABLE migration (there is no pre-existing table to alter).

NON-NEGOTIABLE: this module is bookkeeping only. It never resolves a
governed identity, never builds a governed config, never calls
run_pipeline() or evaluate_live_decision() — those all happen in backtest.
hypothesis_live_request BEFORE this module is ever called. Matching every
other storage/*.py file in this codebase, this module never imports
backtest/*.py.

Like research_live_decisions (Phase 7) and UNLIKE every deduplicated
ledger in this engine (missions, promotions, policy events), rows here are
DELIBERATELY NOT deduplicated or idempotent — every call to record_live_
identity_request() is an independent, real, point-in-time observation of
one live identity request. The SAME hypothesis_id may legitimately be
evaluated many times over its life; each evaluation is separately
meaningful audit evidence, never collapsed to "has this ever been
requested before."

`preset_definition_hash` and `risk_parameters_used_json` are the
operator's own explicitly required risk provenance snapshot (Phase 8C
Contract, Point 3): the EXACT numeric definition of the risk_preset that
was ACTUALLY used for this specific computation, persisted verbatim at
record time. Never re-derived later by joining back to backtest.
research_matrix.RISK_PRESETS or config/risk.yaml — a future edit to what a
preset name means numerically can never reinterpret a row already written
here (proven by tests/test_hypothesis_live_request.py's own "changing the
live preset definition does not change an already-recorded snapshot"
test).

Never touches research/results/registry.json, config.yaml, config/
engines.yaml, or config/symbols.yaml.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from storage import d1_client

_DDL_LIVE_IDENTITY_REQUESTS = """
CREATE TABLE IF NOT EXISTS research_live_identity_requests (
    seq                        INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id                 TEXT NOT NULL UNIQUE,
    hypothesis_id               TEXT NOT NULL,
    decision_type                TEXT NOT NULL,
    symbol                        TEXT NOT NULL,
    engine                         TEXT NOT NULL,
    engine_version                  TEXT NOT NULL,
    timeframe                        TEXT NOT NULL,
    risk_preset                       TEXT NOT NULL,
    preset_definition_hash             TEXT NOT NULL,
    risk_parameters_used_json           TEXT NOT NULL,
    bundle_id                            TEXT,
    live_verdict                          TEXT,
    gate_decision                          TEXT,
    gate_policy_event_id                    TEXT,
    decision                                 TEXT NOT NULL,
    decision_reason                           TEXT NOT NULL,
    created_at                                TEXT NOT NULL
)
"""
_DDL_LIVE_IDENTITY_REQUESTS_HYPOTHESIS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rlir_hypothesis ON research_live_identity_requests(hypothesis_id)"
)
_DDL_LIVE_IDENTITY_REQUESTS_DECISION_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_rlir_decision ON research_live_identity_requests(decision)"
)


def _init(con) -> None:
    con.execute(_DDL_LIVE_IDENTITY_REQUESTS)
    con.execute(_DDL_LIVE_IDENTITY_REQUESTS_HYPOTHESIS_IDX)
    con.execute(_DDL_LIVE_IDENTITY_REQUESTS_DECISION_IDX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def record_live_identity_request(
    *, hypothesis_id: str, decision_type: str, symbol: str, engine: str, engine_version: str,
    timeframe: str, risk_preset: str, preset_definition_hash: str, risk_parameters_used_json: str,
    decision: str, decision_reason: str, bundle_id: str | None = None, live_verdict: str | None = None,
    gate_decision: str | None = None, gate_policy_event_id: str | None = None,
) -> dict[str, Any]:
    """A plain INSERT — never OR IGNORE, never deduplicated (see this
    module's own docstring). `preset_definition_hash`/`risk_parameters_
    used_json` are persisted exactly as computed by the caller — this
    function never recomputes or validates them against RISK_PRESETS
    itself; it is a pure recorder."""
    request_id = f"LIVE-IDENTITY-REQUEST-{uuid.uuid4().hex[:16]}"
    now = _now_iso()
    with d1_client.d1_connection() as con:
        _init(con)
        con.execute(
            """INSERT INTO research_live_identity_requests
               (request_id, hypothesis_id, decision_type, symbol, engine, engine_version, timeframe,
                risk_preset, preset_definition_hash, risk_parameters_used_json, bundle_id,
                live_verdict, gate_decision, gate_policy_event_id, decision, decision_reason, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                request_id, hypothesis_id, decision_type, symbol, engine, engine_version, timeframe,
                risk_preset, preset_definition_hash, risk_parameters_used_json, bundle_id,
                live_verdict, gate_decision, gate_policy_event_id, decision, decision_reason, now,
            ),
        )
        row = con.execute(
            "SELECT * FROM research_live_identity_requests WHERE request_id=?", (request_id,)
        ).fetchone()
    return _row_to_dict(row)


def get_live_identity_request(request_id: str) -> dict[str, Any] | None:
    with d1_client.d1_connection() as con:
        _init(con)
        row = con.execute(
            "SELECT * FROM research_live_identity_requests WHERE request_id=?", (request_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_live_identity_requests_for_hypothesis(hypothesis_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Full chronological history for one hypothesis_id, newest first —
    every independent evaluation ever recorded, never collapsed to "the
    latest one"."""
    with d1_client.d1_connection() as con:
        _init(con)
        rows = con.execute(
            """SELECT * FROM research_live_identity_requests
               WHERE hypothesis_id=? ORDER BY seq DESC LIMIT ?""",
            (hypothesis_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
