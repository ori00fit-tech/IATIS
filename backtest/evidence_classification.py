"""
backtest/evidence_classification.py
---------------------------------------
Hypothesis Discovery Engine, Phase 12 — Evidence Classification Layer.

A DESCRIPTIVE evidence-state classifier over ALREADY-COMPUTED signals from
three existing, unmodified modules:

  - backtest.multiple_testing.classify_significance() -- the Bonferroni-
    corrected significance classification (INSUFFICIENT_DATA /
    SURVIVES_CORRECTION / NOMINAL_ONLY / NOT_SIGNIFICANT).
  - backtest.mission_validator's own NO_EDGE / WEAK_LEAD / STRONG_LEAD
    cross-symbol verdict.
  - backtest.robustness's RobustnessResult.to_dict() -- the existing
    parameter-sensitivity sweep artifact, reused AS-IS (no change to
    backtest/robustness.py, no new parameter search, no optimizer).

NON-NEGOTIABLE (operator's own locked Phase 12 contract):

  1. This module computes NOTHING statistical itself. It never calls
     classify_significance(), never runs a mission validation, never
     calls run_robustness()/run_robustness_suite(). Every function here
     is a PURE function of already-computed inputs the caller supplies.
  2. Classification is DESCRIPTIVE EVIDENCE STATE, never a promotion
     decision, never a policy activation, never execution authorization.
     STRONG != PROMOTED (backtest.hypothesis_promotion). REJECTED != a
     broker block (execution.authorization). WEAK != an automatic
     NO_TRADE. This module writes to NOTHING -- no research_matrix_cells,
     no research_hypothesis_promotions, no research_policies, no
     research_execution_authorizations. It has no storage module at all.
  3. INSUFFICIENT_EVIDENCE is a SIXTH, SEPARATE value -- never one of the
     five descriptive tiers (REJECTED/WEAK/MIXED/PROMISING/STRONG). "we
     don't have enough data to judge" and "we judged it and it failed"
     are categorically different claims; collapsing them would be
     exactly the kind of silent, confident-sounding negative this
     engine's whole discipline refuses (CLAUDE.md: "negative results get
     committed with the same care as positive ones" -- which requires
     first being able to tell a negative apart from an absence of data).

LOCKED PRECEDENCE TABLE (first match wins, operator's own final wording):

    R1  significance == INSUFFICIENT_DATA                         -> INSUFFICIENT_EVIDENCE
    R2  significance == NOT_SIGNIFICANT                            -> REJECTED
    R3  mission_verdict == NO_EDGE                                 -> REJECTED
    R4  significance == SURVIVES_CORRECTION
         and mission_verdict == STRONG_LEAD and robustness == STABLE -> STRONG
    R5  significance == SURVIVES_CORRECTION and robustness == SENSITIVE -> MIXED
    R6  significance == SURVIVES_CORRECTION  (R4/R5 didn't match)    -> PROMISING
    R7  significance == NOMINAL_ONLY                                -> WEAK

R3 is only ever reached when R1/R2 did not already match. mission_verdict=None
and robustness=None (Stage B / robustness not yet run) never themselves
produce INSUFFICIENT_EVIDENCE -- only significance==INSUFFICIENT_DATA does;
an absent mission_verdict/robustness simply means R4/R5 cannot match, so a
SURVIVES_CORRECTION result falls through to PROMISING (R6), never a hidden
inference. NOMINAL_ONLY is capped at WEAK regardless of mission_verdict/
robustness -- not surviving Bonferroni correction is the weaker claim.
"""
from __future__ import annotations

from typing import Any

from backtest.mission_validator import NO_EDGE, STRONG_LEAD

# Significance classification values -- must match the literal return
# values of backtest.multiple_testing's own significance classifier
# exactly. Reused by string identity, never re-implemented or re-derived
# here.
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
SURVIVES_CORRECTION = "SURVIVES_CORRECTION"
NOMINAL_ONLY = "NOMINAL_ONLY"
NOT_SIGNIFICANT = "NOT_SIGNIFICANT"
_VALID_SIGNIFICANCE = (INSUFFICIENT_DATA, SURVIVES_CORRECTION, NOMINAL_ONLY, NOT_SIGNIFICANT)

# Per-sweep robustness verdict values -- must match backtest.robustness's
# own ParamSweepResult.verdict literal values exactly.
STABLE = "STABLE"
SENSITIVE = "SENSITIVE"
INSUFFICIENT = "INSUFFICIENT"

# The five descriptive evidence tiers, plus the separate sixth sentinel.
REJECTED = "REJECTED"
WEAK = "WEAK"
MIXED = "MIXED"
PROMISING = "PROMISING"
STRONG = "STRONG"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceClassificationError(Exception):
    """Structural misuse only (an unrecognized `significance` value) --
    never raised for an ordinary classification outcome, which is always
    one of the values above, returned, never an exception."""


def aggregate_robustness_verdict(robustness: dict[str, Any] | None) -> str | None:
    """Reduces a REAL backtest.robustness.RobustnessResult.to_dict() shape
    (`{"sweeps": [{"verdict": "STABLE"|"SENSITIVE"|"INSUFFICIENT", ...}, ...]}`)
    to one overall verdict -- never re-runs or re-fits any sweep.

    None (no artifact supplied, or an empty sweep list) -> None.
    Any sweep SENSITIVE                                 -> "SENSITIVE" (highest precedence).
    No SENSITIVE, every sweep INSUFFICIENT               -> "INSUFFICIENT".
    No SENSITIVE, at least one STABLE                    -> "STABLE"."""
    if not robustness:
        return None
    sweeps = robustness.get("sweeps") or []
    if not sweeps:
        return None
    verdicts = [s.get("verdict") for s in sweeps]
    if SENSITIVE in verdicts:
        return SENSITIVE
    if all(v == INSUFFICIENT for v in verdicts):
        return INSUFFICIENT
    return STABLE


def classify_evidence(
    *, significance: str, mission_verdict: str | None = None, robustness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The SOLE entry point. `significance` is REQUIRED -- the one axis
    every classification is anchored to. `mission_verdict` and
    `robustness` (a RobustnessResult.to_dict()-shaped artifact, reduced
    internally via aggregate_robustness_verdict()) are both optional --
    their absence never downgrades or upgrades anything; it only means
    the rules that reference them cannot match.

    Returns {"classification": ..., "reason": ..., "inputs": {...}} --
    `inputs` echoes exactly what was used, for full transparency (no
    hidden inference)."""
    if significance not in _VALID_SIGNIFICANCE:
        raise EvidenceClassificationError(
            f"classify_evidence: significance must be one of {_VALID_SIGNIFICANCE}, got {significance!r}."
        )

    robustness_verdict = aggregate_robustness_verdict(robustness)
    inputs = {"significance": significance, "mission_verdict": mission_verdict, "robustness": robustness_verdict}

    if significance == INSUFFICIENT_DATA:
        return {"classification": INSUFFICIENT_EVIDENCE,
                "reason": "statistical significance is undefined -- not enough evidence to judge, not a rejection",
                "inputs": inputs}
    if significance == NOT_SIGNIFICANT:
        return {"classification": REJECTED,
                "reason": "statistically indistinguishable from noise", "inputs": inputs}
    if mission_verdict == NO_EDGE:
        return {"classification": REJECTED,
                "reason": "cross-symbol mission validation found no edge", "inputs": inputs}
    if significance == SURVIVES_CORRECTION:
        if mission_verdict == STRONG_LEAD and robustness_verdict == STABLE:
            return {"classification": STRONG,
                    "reason": "survives Bonferroni correction, confirmed across symbols, stable under "
                              "cost/risk perturbation", "inputs": inputs}
        if robustness_verdict == SENSITIVE:
            return {"classification": MIXED,
                    "reason": "survives Bonferroni correction but fragile under cost/risk perturbation",
                    "inputs": inputs}
        return {"classification": PROMISING,
                "reason": "survives Bonferroni correction; no contradicting mission/robustness signal",
                "inputs": inputs}
    # significance == NOMINAL_ONLY (the only remaining valid value)
    return {"classification": WEAK,
            "reason": "significant only before correction -- never reaches Bonferroni-corrected significance",
            "inputs": inputs}
