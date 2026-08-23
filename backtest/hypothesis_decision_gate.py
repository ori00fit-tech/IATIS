"""
backtest/hypothesis_decision_gate.py
---------------------------------------
Hypothesis Discovery Engine, Phase 7 — Live Decision Gate (BUILT, TESTED,
NOT WIRED).

NON-NEGOTIABLE (the operator's own explicit finding this module exists
to respect): this gate MUST NOT manufacture engine attribution.
scheduler.py's CURRENT live verdict is produced by a multi-engine
confluence pipeline (main.py::run_pipeline(), against config/engines.
yaml's single global `enabled:` panel) with NO single (engine, engine_
version) identity attached to it anywhere in the resulting report dict.
There is no "highest-scoring engine," "dominant engine," "first
contributing engine," "majority engine," "best engine," or synthetic
panel alias (e.g. a made-up "CONFLUENCE PANEL PROD4"-style engine name)
anywhere in this module, and there never may be —  inventing one would
let every engine in the current global panel operate under a single
grant, exactly the per-engine authorization Phases 4-6 were built to
prevent. evaluate_live_decision() accepts (symbol, engine, engine_
version, timeframe, risk_preset) as REQUIRED, CALLER-SUPPLIED facts —
the SAME identity Phase 6's own get_symbol_policy() already requires —
and derives nothing about WHICH engine a decision is about; it only
decides, GIVEN an already-attributed identity, whether that identity is
currently authorized.

Because of this gap, this module is NOT called from scheduler.py or
main.py in this phase — see this module's own tests for the source-scan
proof. It exists so the full Kill-Switch -> Policy -> Decision -> Audit
contract can be built and proven correct NOW, against synthetic/
controlled attributed identities, without pretending the live pipeline
can supply one yet. Wiring this into a real live decision path requires
a SEPARATE, later phase that first solves engine attribution — either by
making the live pipeline itself produce a single-engine decision, or by
defining an explicit, honestly-named governance identity for confluence
itself. That decision is out of scope here.

DECISION CHAIN THIS COMPLETES (read only, up to PROCEED/NO_TRADE):

    Hypothesis -> ExecutionRequest -> Mission -> Matrix Cell -> Promotion
        -> Policy Grant -> Live Decision Gate -> [STOP — risk/pretrade/
                                                    broker are separate,
                                                    UNCHANGED, existing
                                                    live modules this
                                                    phase never calls]

ORDER (the operator's own required correction to this phase's first
draft): Kill Switch is checked FIRST — a global, cheap, already-fail-
closed override (storage.kill_switch.get_state(), reused verbatim,
never reimplemented) — THEN Policy (storage.hypothesis_policy.
get_latest_policy_event(), Phase 6, reused verbatim). An unauthorized
identity must never even reach a risk computation; Kill Switch must
never be second-guessed by anything downstream. Both facts are read
FRESH on every single call — no cache, no TTL, no memoization —
matching kill_switch.py's own existing "read fresh from disk on every
check" discipline exactly, and Phase 6's own append-only "current state
= latest committed event" discipline. Both reads happen ONCE each, at
the top of the SAME call, so the returned audit record is always
internally consistent with the decision it explains.

The orchestration function owns the ordering. There is no parameter on
evaluate_live_decision() through which a caller can supply or override a
stage's result — no `policy=`, no `kill_switch=` argument exists — every
fact is derived here, internally, from its own authoritative source, on
every single call.

FAIL CLOSED: "missing" and "unreadable" are distinct audit reasons but
IDENTICAL authorization outcomes — every non-GRANTED, non-kill-switch-
clear state resolves to NO_TRADE, and the specific reason is always
recorded (decision_reason, policy_lookup_result, kill_switch_state),
never collapsed into one generic denial.

INDEPENDENCE: every call is a fresh, independent observation — nothing
about one call's result is ever carried into, or assumed by, another.
There is no cache to go stale, so there is nothing to invalidate.

Reuses, never rebuilds: storage.kill_switch.get_state() (unmodified),
storage.hypothesis_policy.get_latest_policy_event() (Phase 6,
unmodified), and the SAME GRANTED/REVOKED/NO_POLICY vocabulary backtest.
hypothesis_policy already defines.

OUT OF SCOPE (operator's own explicit list): no scheduler.py change, no
main.py change, no panel attribution, no engine selection/ranking, no
execution, no call into risk/risk_engine.py, no call into risk/
pretrade_limits.py, no broker call, no change to Phase 5/6 semantics, no
new statistical thresholds, no risk-limit or portfolio-allocation
redesign. "PROCEED" means "eligible to be considered by the (unchanged)
Risk Gate" — it is explicitly NOT execution authorization.
"""
from __future__ import annotations

from typing import Any

from backtest.hypothesis_policy import GRANTED, NO_POLICY, REVOKED

PROCEED = "PROCEED"
NO_TRADE = "NO_TRADE"

KILL_SWITCH_ACTIVE = "ACTIVE"
KILL_SWITCH_INACTIVE = "INACTIVE"


def evaluate_live_decision(
    symbol: str, engine: str, engine_version: str, timeframe: str, risk_preset: str,
) -> dict[str, Any]:
    """The SOLE entry point for this gate. Accepts EXCLUSIVELY the
    already-attributed identity tuple — see this module's own docstring
    for why that attribution is a required, honest input this gate never
    computes itself, not a violation of the "identity comes from the
    governed object" discipline the rest of this engine follows (there is
    no governed object one layer up to derive it from here — this IS the
    primary key of the Kill-Switch/Policy lookup).

    Every call is independent and persists its own audit row via storage.
    hypothesis_decision_gate.record_decision() — deliberately NOT
    deduplicated, unlike every other ledger in this engine (see that
    function's own docstring for why: a live decision loop legitimately
    re-evaluates the same identity many times, and EVERY evaluation is
    separately meaningful evidence of "was this authorized at THIS
    moment," never collapsed to "has this ever been decided before")."""
    from storage import hypothesis_decision_gate as storage_decision_gate
    from storage import hypothesis_policy as storage_hypothesis_policy
    from storage import kill_switch as storage_kill_switch

    ks_state = storage_kill_switch.get_state()
    kill_switch_active = bool(ks_state.get("active"))
    kill_switch_state_label = KILL_SWITCH_ACTIVE if kill_switch_active else KILL_SWITCH_INACTIVE

    # ONE read, used for both the classification below AND the audit
    # record — never re-queried a second time, so the two can never
    # disagree under a concurrent policy change mid-call.
    policy_event = storage_hypothesis_policy.get_latest_policy_event(symbol, engine, engine_version, timeframe, risk_preset)
    policy_lookup_result = policy_event["event_type"] if policy_event is not None else NO_POLICY

    if kill_switch_active:
        decision = NO_TRADE
        decision_reason = (
            f"kill switch ACTIVE ({ks_state.get('reason')!r}) — checked before policy, always authoritative, "
            f"never overridden by a GRANTED policy."
        )
    elif policy_lookup_result == NO_POLICY:
        decision = NO_TRADE
        decision_reason = (
            f"NO_POLICY for (symbol={symbol!r}, engine={engine!r}, engine_version={engine_version!r}, "
            f"timeframe={timeframe!r}, risk_preset={risk_preset!r}) — deny-by-default, no fallback of any kind."
        )
    elif policy_lookup_result == REVOKED:
        decision = NO_TRADE
        decision_reason = (
            f"policy REVOKED (event_id={policy_event['event_id']!r}) — the most recently committed event for "
            f"this exact identity."
        )
    elif policy_lookup_result == GRANTED:
        decision = PROCEED
        decision_reason = (
            f"policy GRANTED (event_id={policy_event['event_id']!r}) and kill switch inactive — eligible to "
            f"proceed to the existing, unchanged Risk Gate; this is NOT execution authorization."
        )
    else:
        decision = NO_TRADE
        decision_reason = f"unrecognized policy_lookup_result={policy_lookup_result!r} — fail closed."

    return storage_decision_gate.record_decision(
        symbol=symbol, engine=engine, engine_version=engine_version, timeframe=timeframe, risk_preset=risk_preset,
        decision=decision, decision_reason=decision_reason,
        kill_switch_state=kill_switch_state_label,
        policy_lookup_result=policy_lookup_result,
        policy_event_id=policy_event["event_id"] if policy_event is not None else None,
        policy_seq=policy_event["seq"] if policy_event is not None else None,
        promotion_id=policy_event.get("promotion_id") if policy_event is not None else None,
        mission_id=policy_event.get("mission_id") if policy_event is not None else None,
        hypothesis_id=policy_event.get("hypothesis_id") if policy_event is not None else None,
        research_code_commit=policy_event.get("research_code_commit") if policy_event is not None else None,
        data_snapshot_id=policy_event.get("data_snapshot_id") if policy_event is not None else None,
    )
