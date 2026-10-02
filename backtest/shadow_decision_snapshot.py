"""
backtest/shadow_decision_snapshot.py
---------------------------------------
Hypothesis Discovery Engine, Phase 15D — SHADOW Decision Snapshot Capture
(domain layer).

Captures the IMMUTABLE entry/stop/target snapshot backtest.hypothesis_
live_request.evaluate_live_identity_request() (Phase 8C) now exposes via
its own `decision_snapshot` return key (a Phase 15D-authorized controlled
extension of Phase 8C, locked by the operator's own Governance Decision --
not a formal reopening of that phase). This module never computes that
snapshot itself, never calls run_pipeline(), and never re-derives
entry_price/stop_loss/take_profit/side/bar_time from anything -- it only
persists, verbatim, whatever Phase 8C's own function already validated
and returned.

capture_decision_snapshot() takes the FULL evaluate_live_identity_
request() return dict (or, equivalently, one result's own
`live_identity_request` field from backtest.shadow_observation.
run_shadow_cycle()'s own output -- that function already forwards this
dict unchanged, so it required NO modification for this phase) -- never
separate request_id/decision_snapshot parameters, so there is no
parameter shape through which a caller could supply a mismatched pair.

NON-NEGOTIABLE (operator's own locked Phase 15D scope boundary): this
module never resolves an outcome, never reads a market bar, never
computes divergence, and never calls backtest.promotion_gate, backtest.
policy_health, backtest.execution_attribution, execution.authorization,
execution.trade_executor, storage.engine_tracker, scheduler.py, or
main.py. It never mutates backtest.shadow_observation.run_shadow_cycle()
or storage.hypothesis_live_request in any way -- this is a purely
additive, one-way consumer of their already-existing output shape.

A storage failure here is NEVER conflated with "no snapshot to capture" --
see ShadowDecisionSnapshotError's own docstring.
"""
from __future__ import annotations

import uuid
from typing import Any

from storage import shadow_decision_snapshot as storage_snapshot
from storage.d1_client import D1Error

__all__ = ["ShadowDecisionSnapshotError", "capture_decision_snapshot"]


class ShadowDecisionSnapshotError(Exception):
    """A genuine storage failure while persisting a decision snapshot --
    NEVER raised merely because `decision_snapshot` is None (that is an
    ordinary, valid "nothing to capture" outcome, returned as None, not
    an exception). A caller must never read this exception, or its
    absence, as having any bearing on the underlying decision's own
    final_verdict/decision/gate_decision -- those were already final and
    persisted by Phase 8C before this function was ever called."""


def capture_decision_snapshot(live_identity_request_result: dict[str, Any]) -> dict[str, Any] | None:
    """The SOLE entry point. `live_identity_request_result` is the WHOLE
    dict evaluate_live_identity_request() returns (request_id,
    hypothesis_id, symbol, timeframe, ..., decision_snapshot) -- reading
    every field it needs from this ONE trusted object, never from
    separately-supplied arguments that could drift apart.

    Returns None, performing NO write at all, when `decision_snapshot` is
    None (the original decision had nothing to evaluate -- confluence did
    not pass). Otherwise persists the snapshot and returns the stored row.

    Idempotent: a repeat call for the same request_id returns the
    EXISTING row unchanged (never a duplicate, never an error) -- the
    same "only one honest snapshot per request_id" discipline Phase 15C's
    own create_attribution_record() already established.

    Raises ShadowDecisionSnapshotError only for a genuine storage failure
    -- never for an ordinary None input."""
    decision_snapshot = live_identity_request_result.get("decision_snapshot")
    if decision_snapshot is None:
        return None

    request_id = live_identity_request_result["request_id"]
    existing = storage_snapshot.get_snapshot_by_request_id(request_id)
    if existing is not None:
        return existing

    snapshot_id = f"SHADOW-SNAPSHOT-{uuid.uuid4().hex[:16]}"
    try:
        row = storage_snapshot.try_insert(
            snapshot_id=snapshot_id, request_id=request_id,
            hypothesis_id=live_identity_request_result["hypothesis_id"],
            symbol=live_identity_request_result["symbol"],
            timeframe=live_identity_request_result["timeframe"],
            bar_time=decision_snapshot["bar_time"], side=decision_snapshot["side"],
            entry_price=decision_snapshot["entry_price"], stop_loss=decision_snapshot["stop_loss"],
            take_profit=decision_snapshot["take_profit"],
        )
    except D1Error as exc:
        raise ShadowDecisionSnapshotError(
            f"capture_decision_snapshot: storage failure persisting snapshot for "
            f"request_id {request_id!r}: {exc}"
        ) from exc

    if row is None:
        # Lost a race against a concurrent capture for the same request_id --
        # the other call's row is the real one; fetch and return it.
        row = storage_snapshot.get_snapshot_by_request_id(request_id)
    return row
