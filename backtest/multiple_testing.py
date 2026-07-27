"""
backtest/multiple_testing.py
-------------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — multiple-testing
/ data-dredging risk surfaced numerically, not just asserted.

No multiple-comparison-correction code (Bonferroni/FDR/Benjamini-
Hochberg) existed anywhere in this repo before this module (confirmed by
grep across the whole codebase). research/hypotheses/BACKLOG_2026-07-21.md
already states the operating principle this module exists to enforce
mechanically: "Sixty parallel experiments is how mirages get
manufactured; the registry's kill-rate exists because tests run one at a
time." A mission running hundreds/thousands of trials is exactly that
risk at scale — this module computes what pure noise would be expected
to produce at the same trial count, so a mission's leaderboard can never
be read as if it were validated evidence.

No scipy dependency — the normal-CDF approximation is `math.erf` only.
"""
from __future__ import annotations

import math
from typing import Any


def bonferroni_alpha(n_trials: int, family_alpha: float = 0.05) -> float:
    """Family-wise-corrected significance threshold for a single trial
    to be taken seriously out of n_trials tested."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    return family_alpha / n_trials


def expected_false_positives(n_trials: int, family_alpha: float = 0.05) -> float:
    """What pure noise would be expected to produce at the nominal
    (uncorrected) alpha — the number the report banner leads with."""
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    return n_trials * family_alpha


def normal_cdf(z: float) -> float:
    """Standard normal CDF via math.erf — no scipy dependency."""
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def trial_p_value(mean_r: float, std_r: float, n: int) -> float | None:
    """Two-tailed z-test of mean R-multiple vs 0 (normal approximation —
    approximate for n<30, same 'no p-hacking, report INSUFFICIENT below a
    floor' spirit as backtest/robustness.py's own min_trades gate).
    Returns None (not a fabricated 1.0/0.0) when undefined: n<2 or
    std_r==0."""
    if n < 2 or std_r == 0:
        return None
    se = std_r / math.sqrt(n)
    z = mean_r / se
    return 2 * (1 - normal_cdf(abs(z)))


def classify_significance(
    p_value: float | None, n_trials: int, family_alpha: float = 0.05
) -> str:
    """One of: INSUFFICIENT_DATA | SURVIVES_CORRECTION | NOMINAL_ONLY |
    NOT_SIGNIFICANT."""
    if p_value is None:
        return "INSUFFICIENT_DATA"
    if p_value < bonferroni_alpha(n_trials, family_alpha):
        return "SURVIVES_CORRECTION"
    if p_value < family_alpha:
        return "NOMINAL_ONLY"
    return "NOT_SIGNIFICANT"


def mission_significance_summary(
    trials: list[dict[str, Any]], family_alpha: float = 0.05
) -> dict[str, Any]:
    """trials: dicts each carrying at least `mean_r`, `std_r`, `n` (the
    trade-level R-multiple mean/std/count already present in a trial's
    stored metrics). Returns the report banner payload — always present
    on every mission report, never omitted."""
    n_trials = len(trials)
    if n_trials == 0:
        return {
            "n_trials": 0,
            "bonferroni_alpha": None,
            "expected_false_positives_at_p05": 0.0,
            "count_nominal_p_lt_05": 0,
            "count_surviving_bonferroni": 0,
            "banner": "EXPLORATORY — NOT EVIDENCE. No trials to summarize.",
        }

    alpha_corrected = bonferroni_alpha(n_trials, family_alpha)
    expected_fp = expected_false_positives(n_trials, family_alpha)

    count_nominal = 0
    count_corrected = 0
    for t in trials:
        p = trial_p_value(t.get("mean_r", 0.0), t.get("std_r", 0.0), t.get("n", 0))
        if p is None:
            continue
        if p < family_alpha:
            count_nominal += 1
        if p < alpha_corrected:
            count_corrected += 1

    banner = (
        f"EXPLORATORY — NOT EVIDENCE. Of {n_trials} trials, {count_nominal} "
        f"would look 'significant' at p<{family_alpha} by CHANCE ALONE "
        f"(expected ~{expected_fp:.1f}). Only {count_corrected} trial(s) "
        f"survive Bonferroni correction (p < alpha/N = {alpha_corrected:.6f}). "
        f"None of this is a validated result — a promising trial must be "
        f"manually re-registered with its own falsification criteria and "
        f"re-tested via the normal chronological-OOS pipeline before it can "
        f"ever be called PASSED (see CLAUDE.md rule 1)."
    )

    return {
        "n_trials": n_trials,
        "bonferroni_alpha": alpha_corrected,
        "expected_false_positives_at_p05": expected_fp,
        "count_nominal_p_lt_05": count_nominal,
        "count_surviving_bonferroni": count_corrected,
        "banner": banner,
    }
