"""
backtest/hypothesis_policy.py
---------------------------------
Hypothesis Discovery Engine, Phase 6 — Symbol Policy Registry.

Answers exactly one question, for an exact governance identity: "is there
a currently-active, explicitly human-authorized policy for THIS
(symbol, engine, engine_version, timeframe, risk_preset)?" Phase 5
answers "does this evidence package clear the governance gate for this
symbol" (a fact about EVIDENCE); this module answers "did an authorized
human explicitly grant this already-promoted identity as policy" (a fact
about AUTHORIZATION). Neither is execution:

    Hypothesis -> ExecutionRequest -> Mission -> Matrix Cell -> Promotion
        -> Policy Grant -> [STOP]

A GRANTED policy event means "this exact combination MAY be considered
by whatever live decision layer exists later" — nothing more. This
module contains no live wiring: it never imports, reads, or writes
config.yaml/config/engines.yaml/config/symbols.yaml, never touches
scheduler.py or any execution/risk/exposure path, and nothing outside
this module's own tests imports it (see this module's own tests for the
source-scan proof — Phase 7, a separate future spec, is required before
any policy entry can influence a live decision).

NON-NEGOTIABLE (operator's own explicit Phase 6 contract):

  PROMOTION: PROMOTED is necessary, never sufficient, for a grant.
  GRANT: only an explicit, human-authorized grant_policy(promotion_id,
      granted_by, reason) call creates a GRANTED event — there is no
      code path anywhere that auto-grants a policy the moment a
      promotion resolves to PROMOTED.
  IDENTITY: (symbol, engine, engine_version, timeframe, risk_preset) is
      exact and non-inheritable — never a symbol-only lookup, never a
      fallback across engine_version, never a "latest policy for this
      symbol regardless of engine."
  SOURCE: every identity field grant_policy() persists is RE-READ from
      the promotion's own identity chain (via backtest.hypothesis_
      promotion.evaluate_promotion(), reused wholesale, re-run fresh on
      every call) and from the hypothesis it references — grant_policy()
      itself accepts ONLY (promotion_id, granted_by, reason); a caller
      can never supply symbol/engine/engine_version/timeframe/
      risk_preset/hypothesis_id/mission_id/cell_id independently.
  STATE: append-only GRANTED/REVOKED ledger — no UPDATE, no DELETE, ever.
  READ: get_symbol_policy() reads the latest COMMITTED event (by the
      ledger's own deterministic `seq` — an INTEGER PRIMARY KEY
      AUTOINCREMENT column, never wall-clock `created_at` alone, which
      can collide or be unreliable) for the EXACT identity — no fallback
      of any kind.
  DEFAULT: no event for an identity => NO_POLICY (deny-by-default).
  FAIL CLOSED: an invalid/missing/non-PROMOTED promotion_id, an identity
      mismatch anywhere in the re-verified chain, a tampered promotion
      record, an anonymous/system/automatic actor, a missing reason, or
      revoking a grant that is not the identity's CURRENTLY active event
      — all refuse outright, before any write, never a partial one.
  REVOKE: explicit human action only, referencing the specific GRANTED
      event being revoked (never a free-form identity) — no automatic
      revocation, no un-revoke. A subsequent grant is always a NEW,
      distinct event, never a mutation of the original.
  NO AUTOMATION: no auto-grant, no ranking, no optimizer, no selection,
      no live wiring, no risk/exposure change, no legacy global panel
      fallback.

Reuses, never rebuilds: backtest.hypothesis_promotion.evaluate_promotion()
(the SAME full identity-chain re-verification Phase 5 already performs —
re-run here, fresh, on every grant, never re-implemented) and backtest.
hypothesis_execution.HypothesisExecutionError (the SAME exception class
Phases 3-5 already raise for governance/identity violations — no new
exception type invented here).
"""
from __future__ import annotations

import hashlib
from typing import Any

from backtest.hypothesis_execution import HypothesisExecutionError
from backtest.hypothesis_promotion import PROMOTED, evaluate_promotion

GRANTED = "GRANTED"
REVOKED = "REVOKED"
NO_POLICY = "NO_POLICY"

_IDENTITY_FIELDS = ("symbol", "engine", "engine_version", "timeframe", "risk_preset")

# Deliberately rejected as a policy actor: this is a HUMAN-authorization
# ledger, never a system/automated one. Matched case-insensitively against
# the caller's own stripped string -- an empty/whitespace-only string is
# rejected separately, before this set is even consulted.
_FORBIDDEN_ACTORS = frozenset({
    "system", "scheduler", "auto", "automatic", "bot", "none", "null", "n/a", "unknown", "anonymous",
})


def _require_real_human_identity(actor: str, *, action: str) -> None:
    if not actor or not actor.strip():
        raise HypothesisExecutionError(f"{action}: actioned_by must be a real, non-empty human identity.")
    if actor.strip().lower() in _FORBIDDEN_ACTORS:
        raise HypothesisExecutionError(
            f"{action}: actioned_by={actor!r} is not accepted as a real human identity — anonymous/system/"
            f"automatic actors may never author a policy event."
        )


def _require_reason(reason: str, *, action: str) -> None:
    if not reason or not reason.strip():
        raise HypothesisExecutionError(f"{action}: reason is required and may not be empty.")


def compute_policy_event_id(
    identity: dict[str, str], event_type: str, *,
    promotion_id: str | None, actioned_by: str, reason: str, chain_marker: str | None,
) -> str:
    """Deterministic identity for one policy ledger event.

    `chain_marker` disambiguates a genuine state transition from a mere
    retry, WITHOUT breaking retry-idempotency:

      - For a GRANTED event, chain_marker is the identity's most recent
        REVOKED event_id (or None if the identity has never been
        revoked). A plain retry of the SAME grant call, before anything
        else has happened to this identity, always recomputes the SAME
        chain_marker (nothing changed) -> the SAME event_id -> collapses
        via INSERT OR IGNORE (idempotent). A re-grant AFTER a revoke
        always sees a real, different chain_marker (that revoke's own
        event_id) -> a genuinely NEW, coexisting event_id -- this is
        exactly the operator's own required "re-grant is always a new
        event, never an update or un-revoke" guarantee, and it holds
        even if the actor and reason text are identical to the original
        grant.
      - For a REVOKED event, chain_marker is the grant_event_id being
        revoked — already unique per target, so no further
        disambiguation is needed."""
    payload = "|".join([
        identity["symbol"], identity["engine"], identity["engine_version"], identity["timeframe"],
        identity["risk_preset"], event_type, promotion_id or "", actioned_by, reason, chain_marker or "GENESIS",
    ])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"POLICY-EVENT-{digest}"


def grant_policy(promotion_id: str, granted_by: str, reason: str) -> dict[str, Any]:
    """The SOLE way to create a GRANTED policy event. Accepts EXCLUSIVELY
    (promotion_id, granted_by, reason) — never a symbol/engine/
    engine_version/timeframe/risk_preset/hypothesis_id/mission_id/
    cell_id supplied independently. Every identity field this function
    persists is re-derived from the promotion's own identity chain,
    re-verified fresh on every call — see evaluate_promotion() reuse
    below, which is the SAME re-verification Phase 5 performs, re-run
    here rather than trusted from the stored promotion row.

    Idempotent for a plain retry (same promotion_id/granted_by/reason,
    nothing else changed for this identity since); a genuine re-grant
    after a revoke is always a new, coexisting event — never an update."""
    from storage import hypothesis_factory as storage_hypothesis_factory
    from storage import hypothesis_policy as storage_hypothesis_policy
    from storage import hypothesis_promotion as storage_hypothesis_promotion

    _require_real_human_identity(granted_by, action="grant_policy")
    _require_reason(reason, action="grant_policy")

    promotion = storage_hypothesis_promotion.get_promotion(promotion_id)
    if promotion is None:
        raise HypothesisExecutionError(f"grant_policy: unknown promotion_id {promotion_id!r}.")
    if promotion["decision"] != PROMOTED:
        raise HypothesisExecutionError(
            f"grant_policy: promotion {promotion_id!r} has decision={promotion['decision']!r}, not PROMOTED — "
            f"only a PROMOTED promotion may be granted as symbol policy."
        )

    # Re-verify the FULL identity/evidence chain fresh — never trust the
    # stored promotion row alone. Matrix cells are terminal-status-
    # immutable once VALIDATED (storage.research_matrix.update_cell()'s
    # own guard), so a REAL PROMOTED promotion re-evaluates to PROMOTED
    # forever; a hand-tampered promotion row will disagree with what this
    # freshly re-derives, and is refused below.
    fresh = evaluate_promotion(promotion["hypothesis_id"], promotion["mission_id"], promotion["cell_id"])
    if fresh["decision"] != PROMOTED:
        raise HypothesisExecutionError(
            f"grant_policy: re-evaluating promotion {promotion_id!r}'s own identity chain no longer resolves "
            f"to PROMOTED (got {fresh['decision']!r}) — refusing to grant from stale or inconsistent evidence."
        )
    if fresh["symbol"] != promotion["symbol"]:
        raise HypothesisExecutionError(
            f"grant_policy: promotion {promotion_id!r}'s own stored symbol {promotion['symbol']!r} disagrees "
            f"with {fresh['symbol']!r}, freshly re-derived from its identity chain — refusing to grant from a "
            f"tampered promotion record."
        )
    if fresh["governance_snapshot"]["hypothesis_fingerprint"] != promotion["hypothesis_fingerprint"]:
        raise HypothesisExecutionError(
            f"grant_policy: promotion {promotion_id!r}'s own stored hypothesis_fingerprint "
            f"{promotion['hypothesis_fingerprint']!r} disagrees with "
            f"{fresh['governance_snapshot']['hypothesis_fingerprint']!r}, freshly re-derived — refusing to "
            f"grant from a tampered promotion record."
        )

    hypothesis = storage_hypothesis_factory.get_hypothesis(promotion["hypothesis_id"])
    identity = {
        "symbol": fresh["symbol"], "engine": hypothesis["engine"], "engine_version": hypothesis["engine_version"],
        "timeframe": hypothesis["timeframe"], "risk_preset": hypothesis["risk_preset"],
    }

    previous = storage_hypothesis_policy.get_latest_policy_event(**identity)
    chain_marker = previous["event_id"] if (previous is not None and previous["event_type"] == REVOKED) else None
    event_id = compute_policy_event_id(
        identity, GRANTED, promotion_id=promotion_id, actioned_by=granted_by, reason=reason,
        chain_marker=chain_marker,
    )

    existing = storage_hypothesis_policy.get_policy_event(event_id)
    if existing is not None:
        return dict(existing, created=False)

    storage_hypothesis_policy.persist_policy_event(
        event_id, identity, GRANTED,
        promotion_id=promotion_id, revokes_event_id=None,
        hypothesis_id=promotion["hypothesis_id"], mission_id=promotion["mission_id"], cell_id=promotion["cell_id"],
        reason=reason, actioned_by=granted_by,
        research_code_commit=promotion.get("research_code_commit"), data_snapshot_id=promotion.get("data_snapshot_id"),
    )
    record = storage_hypothesis_policy.get_policy_event(event_id)
    return dict(record, created=True)


def revoke_policy(grant_event_id: str, revoked_by: str, reason: str) -> dict[str, Any]:
    """The SOLE way to create a REVOKED policy event. References the
    SPECIFIC GRANTED event being revoked — never a free-form identity —
    so the identity fields persisted here are re-read from that grant
    event, exactly the same "identity comes from the governed object,
    never from caller-supplied dimensions" discipline grant_policy()
    itself follows.

    Refuses (raises, no write) unless grant_event_id is currently the
    identity's ACTIVE event — already revoked, or already superseded by
    a later grant, both refuse. A BYTE-IDENTICAL retry of an already-
    completed revoke of this SAME grant (same revoked_by + same reason)
    is idempotent (returns the existing REVOKED event, `created`: False);
    a DIFFERENT actor or reason targeting an already-revoked grant is
    refused exactly like any other inactive-grant attempt, never silently
    treated as a no-op."""
    from storage import hypothesis_policy as storage_hypothesis_policy

    _require_real_human_identity(revoked_by, action="revoke_policy")
    _require_reason(reason, action="revoke_policy")

    grant_event = storage_hypothesis_policy.get_policy_event(grant_event_id)
    if grant_event is None:
        raise HypothesisExecutionError(f"revoke_policy: unknown grant_event_id {grant_event_id!r}.")
    if grant_event["event_type"] != GRANTED:
        raise HypothesisExecutionError(
            f"revoke_policy: {grant_event_id!r} is not a GRANTED event (event_type="
            f"{grant_event['event_type']!r}) — only a GRANTED event may be revoked."
        )

    identity = {field: grant_event[field] for field in _IDENTITY_FIELDS}
    current = storage_hypothesis_policy.get_latest_policy_event(**identity)

    if current is not None and current["event_type"] == REVOKED and current.get("revokes_event_id") == grant_event_id:
        # This grant is already revoked. Collapse ONLY a byte-identical
        # retry of that SAME revoke (same actor + same reason -> the same
        # computed event_id) — never a DIFFERENT actor/reason "revoking
        # again," which is refused below like any other inactive-grant
        # attempt, not silently treated as a no-op.
        retry_event_id = compute_policy_event_id(
            identity, REVOKED, promotion_id=None, actioned_by=revoked_by, reason=reason, chain_marker=grant_event_id,
        )
        if retry_event_id == current["event_id"]:
            return dict(current, created=False)

    if current is None or current["event_id"] != grant_event_id:
        raise HypothesisExecutionError(
            f"revoke_policy: {grant_event_id!r} is not the currently active grant for {identity!r} "
            f"(current latest event: {current['event_id'] if current else None!r}) — refusing to revoke a "
            f"grant that is not active (already revoked or already superseded)."
        )

    event_id = compute_policy_event_id(
        identity, REVOKED, promotion_id=None, actioned_by=revoked_by, reason=reason, chain_marker=grant_event_id,
    )
    storage_hypothesis_policy.persist_policy_event(
        event_id, identity, REVOKED,
        promotion_id=None, revokes_event_id=grant_event_id,
        hypothesis_id=grant_event.get("hypothesis_id"), mission_id=grant_event.get("mission_id"),
        cell_id=grant_event.get("cell_id"), reason=reason, actioned_by=revoked_by,
        research_code_commit=grant_event.get("research_code_commit"), data_snapshot_id=grant_event.get("data_snapshot_id"),
    )
    record = storage_hypothesis_policy.get_policy_event(event_id)
    return dict(record, created=True)


def get_symbol_policy(symbol: str, engine: str, engine_version: str, timeframe: str, risk_preset: str) -> str:
    """The ONE read function this phase provides. Returns "GRANTED",
    "REVOKED", or "NO_POLICY" (deny-by-default) for the EXACT identity —
    no symbol-only fallback, no engine-version fallback, no "latest
    policy for this symbol regardless of engine," no legacy global-panel
    fallback. Nothing outside this module's own tests calls this
    function yet — wiring it into any live decision path is explicitly
    Phase 7, a separate future spec."""
    from storage import hypothesis_policy as storage_hypothesis_policy

    latest = storage_hypothesis_policy.get_latest_policy_event(symbol, engine, engine_version, timeframe, risk_preset)
    if latest is None:
        return NO_POLICY
    return latest["event_type"]
