"""
backtest/policy_registry.py
---------------------------------------
Hypothesis Discovery Engine, Phase 10 — Policy Registry Enforcement
(domain layer).

Completes the operator's own locked architectural split:

    Policy Event Ledger (Phase 6, storage.hypothesis_policy, UNCHANGED)
        = "is THIS exact, already-identified identity authorized right
          now" -- an append-only GRANT/REVOKE history, never enumerated
          to pick what to run.
    Policy Registry (this module, Phase 10, NEW)
        = "what, if anything, is eligible to be SELECTED for
          (symbol, timeframe, regime) at all" -- a first-class entity
          with its own lifecycle, never a view over the ledger's columns.
    Execution Authorization (Phase 9, execution.authorization, UNCHANGED)
        = "may THIS specific, already-selected attempt be submitted to a
          broker" -- a third, separate layer again.

These three boundaries must never blur into each other (operator's own
explicit requirement) -- this module never writes to
research_symbol_policy_events or research_execution_authorizations, and
storage.policy_registry never imports storage.hypothesis_policy (the
FRESH re-verification against the real ledger happens HERE, in the
domain layer, exactly once per call site, never cached, never trusted
from a stored column).

LOCKED LIFECYCLE (operator's own final contract):

    DRAFT
     ├── VALIDATED
     │    ├── ACTIVE
     │    └── REVOKED
     └── REVOKED
    ACTIVE
     └── REVOKED

TOCTOU CLOSURE (the operator's own required correction): validate_policy()
checks the Phase 6 ledger's latest event is GRANTED for this policy's
exact (symbol, engine, engine_version, timeframe, risk_preset) identity.
activate_policy() REPEATS that exact same fresh check -- a GRANT revoked
between VALIDATED and the activation attempt denies the activation (the
row stays VALIDATED), it never silently proceeds on the earlier,
now-stale validation alone. `policy_event_id` (persisted at draft time)
is PROVENANCE ONLY, never trusted as a current authorization condition --
the current condition is always re-derived fresh via storage.
hypothesis_policy.get_latest_policy_event().

Non-negotiable regime rule (operator's own locked decision): a Policy's
own `regime_profile` is reused VERBATIM from regimes.regime_detector.
Regime's real values, restricted to the ones detect_regime() can
actually produce today (TRENDING, RANGING) -- NEVER UNKNOWN. A Policy
cannot be drafted/validated/activated FOR "unclassified" because no
regime match can ever be proven for it. At resolve time, an UNKNOWN
input regime resolves to NO_POLICY -> NO_TRADE directly, never a search
for "a policy with no regime" and never a fallback of any kind.

"Configured engine != Eligible policy" (operator's own locked rule):
resolve_eligible_policy() NEVER reads config.yaml, config/engines.yaml,
or any "enabled" engine flag -- proven by tests/test_policy_registry.py's
own source-scan, and behaviorally by its own "prod4 configured but no
ACTIVE policy -> still NO_TRADE, never prod4" test.
"""
from __future__ import annotations

import uuid
from typing import Any

from backtest.hypothesis_factory import CONFLUENCE, SINGLE_ENGINE
from backtest.hypothesis_policy import GRANTED
from regimes.regime_detector import Regime
from storage import policy_registry as storage_policy
from storage.hypothesis_policy import get_latest_policy_event
from storage.policy_registry import ACTIVE, DRAFT, REVOKED, VALIDATED

ELIGIBLE_POLICY = "ELIGIBLE_POLICY"
NO_POLICY = "NO_POLICY"
CONFLICT = "CONFLICT"

# Reused verbatim from regimes.regime_detector.Regime -- restricted to
# the values detect_regime() can actually produce today (its own
# docstring: ACCUMULATION/DISTRIBUTION/MANIPULATION/NEWS_DRIVEN
# "intentionally fall back to UNKNOWN rather than being guessed").
# UNKNOWN is deliberately EXCLUDED here -- see this module's own
# docstring for why a Policy can never be scoped to it.

VALID_REGIME_PROFILES = (Regime.TRENDING.value, Regime.RANGING.value)
_VALID_DECISION_TYPES = (SINGLE_ENGINE, CONFLUENCE)

# Re-exported status constants -- this module's own public vocabulary,
# so nothing outside storage/*.py needs to import storage.policy_registry
# directly (matching execution.authorization's own precedent).
__all__ = [
    "DRAFT", "VALIDATED", "ACTIVE", "REVOKED",
    "ELIGIBLE_POLICY", "NO_POLICY", "CONFLICT",
    "VALID_REGIME_PROFILES", "PolicyRegistryError",
    "draft_policy", "get_policy", "validate_policy", "activate_policy", "revoke_policy",
    "resolve_eligible_policy", "would_authorize_live_run",
]


class PolicyRegistryError(Exception):
    """Structural misuse only (unknown policy_id, invalid regime_profile
    at draft time, no Phase 6 event to draft from at all) -- NEVER raised
    for an ordinary validate/activate denial (a GRANT that isn't
    currently GRANTED, a lost uniqueness race), which are routine,
    expected outcomes the caller inspects via the returned row's own
    `status`."""


def draft_policy(
    *, policy_version: str, symbol: str, timeframe: str, regime_profile: str,
    hypothesis_id: str, engine: str, engine_version: str, decision_type: str,
    risk_preset: str, risk_definition_hash: str, bundle_id: str | None = None,
) -> dict[str, Any]:
    """Creates a new DRAFT policy. Requires at least ONE real Phase 6
    event to exist for this exact identity (any event_type -- GRANTED or
    REVOKED) so `policy_event_id` is always a real provenance pointer,
    never fabricated -- but does NOT require it to currently be GRANTED;
    that check belongs to validate_policy(), not here."""
    if regime_profile not in VALID_REGIME_PROFILES:
        raise PolicyRegistryError(
            f"draft_policy: regime_profile must be one of {VALID_REGIME_PROFILES}, got {regime_profile!r} "
            f"-- a Policy can never be scoped to an unclassified regime."
        )
    if decision_type not in _VALID_DECISION_TYPES:
        raise PolicyRegistryError(
            f"draft_policy: decision_type must be one of {_VALID_DECISION_TYPES}, got {decision_type!r}."
        )
    if not hypothesis_id or not engine or not engine_version or not risk_preset or not risk_definition_hash:
        raise PolicyRegistryError("draft_policy: hypothesis_id, engine, engine_version, risk_preset, and "
                                   "risk_definition_hash are all required.")

    latest_event = get_latest_policy_event(symbol, engine, engine_version, timeframe, risk_preset)
    if latest_event is None:
        raise PolicyRegistryError(
            f"draft_policy: no Phase 6 policy event exists for "
            f"(symbol={symbol!r}, engine={engine!r}, engine_version={engine_version!r}, "
            f"timeframe={timeframe!r}, risk_preset={risk_preset!r}) -- nothing to draft a policy from."
        )

    policy_id = f"POLICY-{uuid.uuid4().hex[:16]}"
    return storage_policy.insert_draft(
        policy_id=policy_id, policy_version=policy_version, symbol=symbol, timeframe=timeframe,
        regime_profile=regime_profile, hypothesis_id=hypothesis_id, engine=engine,
        engine_version=engine_version, decision_type=decision_type, risk_preset=risk_preset,
        risk_definition_hash=risk_definition_hash, bundle_id=bundle_id,
        policy_event_id=latest_event["event_id"],
    )


def get_policy(policy_id: str) -> dict[str, Any] | None:
    return storage_policy.get_policy(policy_id)


def _require_existing(policy_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        raise PolicyRegistryError(f"unknown policy_id {policy_id!r}.")
    return row


def _currently_granted(row: dict[str, Any]) -> bool:
    """The ONE fresh re-verification primitive both validate_policy() and
    activate_policy() call independently -- never cached, never shared
    between the two calls, so a GRANT revoked in between is always
    caught by whichever call happens next."""
    latest_event = get_latest_policy_event(
        row["symbol"], row["engine"], row["engine_version"], row["timeframe"], row["risk_preset"],
    )
    return latest_event is not None and latest_event["event_type"] == GRANTED


def validate_policy(policy_id: str) -> dict[str, Any]:
    """Atomic DRAFT -> VALIDATED, gated by a FRESH check that the Phase 6
    ledger's latest event for this policy's own identity is GRANTED.
    Returns the row UNCHANGED (still DRAFT) if not -- a denial, never an
    exception."""
    row = _require_existing(policy_id, storage_policy.get_policy(policy_id))
    if row["status"] != DRAFT:
        return row  # no-op -- not in a state validate_policy() can act on
    if not _currently_granted(row):
        return row  # denied -- stays DRAFT
    return storage_policy.try_set_validated(policy_id)


def activate_policy(policy_id: str) -> dict[str, Any]:
    """Atomic VALIDATED -> ACTIVE, gated by BOTH: (1) the SAME fresh
    GRANTED re-check validate_policy() already performed -- repeated
    here independently, closing the TOCTOU window the operator
    identified -- and (2) the DB-level active-scope uniqueness index
    (storage.policy_registry.try_set_active()). Either gate denying
    leaves the row at VALIDATED, never an exception."""
    row = _require_existing(policy_id, storage_policy.get_policy(policy_id))
    if row["status"] != VALIDATED:
        return row  # no-op
    if not _currently_granted(row):
        return row  # TOCTOU closure -- the GRANT backing this policy is gone; stays VALIDATED
    return storage_policy.try_set_active(policy_id)


def revoke_policy(policy_id: str, reason: str) -> dict[str, Any]:
    if not reason:
        raise PolicyRegistryError("revoke_policy: reason is required.")
    return _require_existing(policy_id, storage_policy.try_revoke(policy_id, reason))


def resolve_eligible_policy(symbol: str, timeframe: str, regime: str) -> dict[str, Any]:
    """The Policy Resolver's SOLE entry point. ONE query by exact scope
    (storage.policy_registry.find_active_policy()) -- never an
    enumeration of grants, hypotheses, or drafts. Never reads the global
    engine configuration file or any engine on/off flag within it (see
    this module's own docstring, "Configured engine != Eligible policy").

    An UNKNOWN (or any otherwise-unrecognized) regime resolves to
    NO_POLICY directly -- there is no "policy with no regime" to find,
    and no fallback. >1 ACTIVE rows for the same scope is CONFLICT,
    structurally prevented by the DB's own partial unique index but
    checked here defensively against corrupted/legacy data -- never a
    tie-breaker of any kind."""
    if regime not in VALID_REGIME_PROFILES:
        return {"result": NO_POLICY, "policy": None,
                "reason": f"regime {regime!r} is not a valid policy scope -- no regime match is possible."}

    rows = storage_policy.find_active_policy(symbol, timeframe, regime)
    if not rows:
        return {"result": NO_POLICY, "policy": None, "reason": "no ACTIVE policy for this exact scope."}
    if len(rows) > 1:
        return {"result": CONFLICT, "policy": None,
                "reason": f"{len(rows)} ACTIVE policies found for this exact scope -- registry integrity violation."}
    return {"result": ELIGIBLE_POLICY, "policy": rows[0], "reason": None}


def would_authorize_live_run(symbol: str, timeframe: str, regime: str, configured_engines: dict) -> dict[str, Any]:
    """A PURE, domain-only proof function -- NEVER imported from
    scheduler.py, main.py, or anything else outside this module's own
    tests (see tests/test_policy_registry.py's own source-scan). It
    exists ONLY to prove the Phase 10 contract at the domain level;
    whether/how a future cutover phase ever wires something like this
    into a live entry point is explicitly out of scope here.

    `configured_engines` is accepted and deliberately NEVER read anywhere
    in this function's body -- its presence in the signature, combined
    with the structural proof that it is never referenced, is itself
    part of the "configured engine has zero influence on this decision"
    proof.

    PROCEED_TO_GATE is NOT execution authorization and NOT even a live
    decision -- it means only "a Policy is eligible"; the next layers
    (data validity, a regime match re-check, the governed decision
    itself, and Execution Authorization) are unchanged and untouched by
    this function."""
    result = resolve_eligible_policy(symbol, timeframe, regime)
    if result["result"] != ELIGIBLE_POLICY:
        return {"decision": "NO_TRADE", "reason": result["result"]}
    return {"decision": "PROCEED_TO_GATE", "policy": result["policy"]}
