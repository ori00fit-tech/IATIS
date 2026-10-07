#!/usr/bin/env python3
"""
scripts/add_hypothesis_to_shadow_roster.py
------------------------------------------
Roster Population Tooling (operator's own locked Design Gate, 2026-10,
"Roster Population Tooling"). The human-curation entry point for
admitting a hypothesis_id to the SHADOW Live Evaluation Roster
(backtest.live_roster, Phase 15A) -- the explicit, deliberate action
that was the one remaining dependency the whole External Shadow Observe
Runner chain (9288ba6) left as "a human curation action, not
automatable" (backtest.hypothesis_live_request's own locked Phase 8C
scope note).

NO NEW BACKEND LOGIC (operator's own locked principle): this script is
pure composition of three already-existing, already-tested pieces,
called in exactly the same way backtest.shadow_roster_composer.
compose_shadow_observe_entries() already calls the first two for the
SAME fresh-eligibility check:

    backtest.shadow_live_evidence_resolver.resolve_live_shadow_evidence(hypothesis_id)
            |
            v
    backtest.promotion_gate.evaluate_promotion_gate(target_stage=SHADOW, cross_symbol_confirmed=False)
            |
            v
    backtest.live_roster.add_to_roster(hypothesis_id, promotion_gate_result, added_by, added_reason)

`--dry-run` stops after printing the fresh eligibility result -- never
calls add_to_roster() at all. Without it, add_to_roster() is still only
called when the fresh check came back ELIGIBLE -- an ineligible result
is reported and the script exits non-zero without attempting a write
(add_to_roster() would itself refuse it anyway; short-circuiting here
only avoids a pointless write attempt and gives a clearer message, it
changes no behavior). add_to_roster() is already idempotent against an
existing ACTIVE entry for the same hypothesis_id -- this script adds no
duplicate-prevention logic of its own.

OUT OF SCOPE (operator's own locked boundary, this Design Gate): no
roster listing/removal tooling (a separate, later script if ever
needed), no change to add_to_roster()/evaluate_promotion_gate()/
resolve_live_shadow_evidence(), no robustness producer, no LIMITED/
ACTIVE path, no execution/broker path.

Usage (VPS, run manually by a human operator -- never scheduled):

    python3 -m scripts.add_hypothesis_to_shadow_roster HYPOTHESIS-ID \\
        --added-by "operator-name" --added-reason "reason text" [--dry-run]
"""
from __future__ import annotations

import argparse

from utils.logger import get_logger

logger = get_logger(__name__)


def main() -> int:
    from backtest.promotion_gate import ELIGIBLE, SHADOW, evaluate_promotion_gate
    from backtest.shadow_evidence_assembly import ASSEMBLED
    from backtest.shadow_live_evidence_resolver import resolve_live_shadow_evidence
    from backtest.live_roster import add_to_roster

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hypothesis_id", help="the hypothesis_id to admit to the SHADOW roster")
    parser.add_argument("--added-by", required=True, help="operator name/identifier")
    parser.add_argument("--added-reason", required=True, help="free-text reason for this admission")
    parser.add_argument("--dry-run", action="store_true", help="show fresh SHADOW eligibility, never write")
    args = parser.parse_args()

    assembly = resolve_live_shadow_evidence(args.hypothesis_id)
    if assembly["assembly_state"] != ASSEMBLED:
        print(f"Evidence assembly failed: assembly_state={assembly['assembly_state']!r} -- nothing to evaluate.")
        return 1

    gate_result = evaluate_promotion_gate(
        target_stage=SHADOW, evidence=assembly["evidence"], cross_symbol_confirmed=False,
    )
    print(
        f"hypothesis_id={args.hypothesis_id!r} classification={assembly['evidence']['classification']!r} "
        f"eligibility={gate_result['eligibility']!r} reasons={gate_result['reasons']!r}"
    )

    if args.dry_run:
        print("(dry-run -- no write attempted)")
        return 0

    if gate_result["eligibility"] != ELIGIBLE:
        print("Not SHADOW-eligible -- add_to_roster() was not called.")
        return 1

    result = add_to_roster(
        hypothesis_id=args.hypothesis_id, promotion_gate_result=gate_result,
        added_by=args.added_by, added_reason=args.added_reason,
    )
    print(result)
    return 0 if result["added"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
