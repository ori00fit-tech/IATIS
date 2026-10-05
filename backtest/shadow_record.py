"""
backtest/shadow_record.py
---------------------------------------
Hypothesis Discovery Engine -- the Full Observed Evidence Producer
(operator's own locked Implementation Design). The first, and only,
honest producer of backtest.promotion_gate's own Phase 13 `shadow_record`
contract: exactly `{"completed": bool, "diverged_catastrophically": bool}`.

THE THREE-CONJUNCT `completed` FORMULA (operator's own locked correction
to docs/SHADOW_EVIDENCE_UNIT_CLOSURE.md §2.4 -- not a silent edit of that
record, a textual strengthening agreed in this later Design Gate):

    completed(H, T) = (n_T(H) >= N_MIN_TERMINAL_CONFIRMED)
                       AND (baseline["divergence_statistic_valid"])
                       AND DIVERGENCE_VERDICT_COMPUTED

§2.4's original two-conjunct formula is necessary but was found NOT
sufficient: backtest.hypothesis_baseline_statistic.
evaluate_canonical_baseline_statistic() can now genuinely return
divergence_statistic_valid=True for a real hypothesis, which on its own
says nothing about whether any catastrophic-divergence VERDICT was ever
computed -- no observed-side win/loss statistic has ever been designed,
and the 0.65 (or any) threshold remains RESERVED (docs/
PHASE_15D_CLOSURE_RECORD.md). Without this third conjunct, `completed`
could become True the moment n_T(H) crosses 40 and a baseline happens to
be computable, with nothing downstream to honestly set
`diverged_catastrophically` from -- reproducing, by formula drift, the
exact fabricated-safety failure mode this whole engine's fail-closed
discipline exists to prevent. DIVERGENCE_VERDICT_COMPUTED is hardcoded
False, with this reason, until a future, separate Design Gate designs
the observed-side statistic AND locks a real threshold.

`diverged_catastrophically` is ALWAYS False in this module's current
output -- never read as "no catastrophe was found" (operator's own
locked §2.5 rule, reused verbatim): it is a structural placeholder,
inert whenever `completed` is False (which is every real case today,
since DIVERGENCE_VERDICT_COMPUTED is always False), because backtest.
promotion_gate.py's own `elif shadow_record.get("diverged_catastrophically")`
branch is never reached once `completed` is False.

RECORD SHAPE, locked minimal (operator's own decision): exactly the two
Phase 13 keys, nothing else. `n_T(H)` and the baseline evaluation are
intermediate evidence, not part of the contract -- adding diagnostic
fields is deferred to a future, separately-authorized Design Gate if
ever needed.

OBSERVED SIDE vs. BASELINE SIDE -- asymmetric I/O boundary, intentional:
this module performs I/O itself for the observed side (by calling
backtest.shadow_outcome_aggregate.evaluate_all_requests_for_hypothesis(),
which owns storage reads and a live network call). The baseline side is
caller-supplied (`promotions`, `cell`, `validation_results`) and never
fetched here -- preserving backtest.hypothesis_baseline_statistic.
evaluate_canonical_baseline_statistic()'s own locked purity boundary
unchanged.

FAIL-FAST (operator's own locked decision, same precedent as backtest.
shadow_outcome_aggregate.py): no try/except anywhere in this module. Any
exception from the observed-side evaluation propagates completely
unchanged -- no partial or fabricated shadow_record is ever returned.

NON-NEGOTIABLE (operator's own locked scope boundary): this module never
imports backtest.promotion_gate, backtest.policy_health, backtest.
execution_attribution, execution.authorization, execution.trade_executor,
storage.outcome_tracker, storage.shadow_book, scheduler.py, or main.py.
It never writes to storage. It never changes N_MIN_TERMINAL_CONFIRMED (40,
reused verbatim from D001/docs/SHADOW_EVIDENCE_UNIT_CLOSURE.md) or
evaluate_canonical_baseline_statistic()'s own eight guards.
"""
from __future__ import annotations

from typing import Any

from backtest.hypothesis_baseline_statistic import evaluate_canonical_baseline_statistic
from backtest.shadow_outcome_aggregate import (
    count_terminal_confirmed,
    evaluate_all_requests_for_hypothesis,
)

N_MIN_TERMINAL_CONFIRMED = 40

# Hardcoded False, with a documented reason (see module docstring): no
# observed-side win/loss statistic has ever been designed, and the
# catastrophic-divergence threshold remains RESERVED. This conjunct must
# never be flipped to True by editing this constant alone -- doing so
# honestly requires a separate, future Design Gate that designs the
# observed-side statistic AND locks a real threshold.
DIVERGENCE_VERDICT_COMPUTED = False

__all__ = ["N_MIN_TERMINAL_CONFIRMED", "DIVERGENCE_VERDICT_COMPUTED", "build_shadow_record"]


def build_shadow_record(
    hypothesis_id: str,
    *,
    base_config: dict[str, Any],
    promotions: list[dict[str, Any]],
    cell: dict[str, Any] | None,
    validation_results: list[dict[str, Any]],
    api_key: str | None = None,
) -> dict[str, Any]:
    """The SOLE entry point. Produces exactly backtest.promotion_gate's
    own Phase 13 shadow_record shape: {"completed": bool,
    "diverged_catastrophically": bool}.

    Observed side is evaluated fresh here (I/O: storage reads + a live
    network call, via backtest.shadow_outcome_aggregate.
    evaluate_all_requests_for_hypothesis()). Baseline side
    (`promotions`, `cell`, `validation_results`) is caller-supplied,
    exactly as backtest.hypothesis_baseline_statistic.
    evaluate_canonical_baseline_statistic() itself requires -- never
    fetched by this function.

    FAIL-FAST: any exception from the observed-side evaluation
    propagates completely unchanged. No partial or fabricated record is
    ever returned."""
    terminality_results = evaluate_all_requests_for_hypothesis(
        hypothesis_id, base_config=base_config, api_key=api_key,
    )
    n_t = count_terminal_confirmed(terminality_results)
    baseline = evaluate_canonical_baseline_statistic(hypothesis_id, promotions, cell, validation_results)

    completed = (
        n_t >= N_MIN_TERMINAL_CONFIRMED
        and baseline["divergence_statistic_valid"]
        and DIVERGENCE_VERDICT_COMPUTED
    )
    return {"completed": completed, "diverged_catastrophically": False}
