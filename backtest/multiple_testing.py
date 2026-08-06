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


def _sample_autocorrelation(values: list[float], lag: int) -> float:
    """Sample autocorrelation of `values` at the given lag (Pearson-style,
    using the whole-series mean/variance — the standard time-series
    estimator). Returns 0.0 if the lag is out of range or the series has
    zero variance (nothing to correlate)."""
    n = len(values)
    if lag <= 0 or lag >= n:
        return 0.0
    mean = sum(values) / n
    denom = sum((v - mean) ** 2 for v in values)
    if denom == 0:
        return 0.0
    numer = sum((values[i] - mean) * (values[i + lag] - mean) for i in range(n - lag))
    return numer / denom


def effective_sample_size(values: list[float], max_lag: int | None = None) -> float | None:
    """Effective sample size under serial correlation — Geyer's (1992)
    'initial positive sequence' (IPS) estimator, the standard robust
    method for effective-N under autocorrelation (MCMC/time-series
    diagnostics): consecutive autocorrelation pairs (rho_{2m+1} + rho_{2m+2})
    are summed and the running total stops the FIRST time a pair sum goes
    negative, which guarantees a non-negative variance-inflation estimate.
    ESS = N / (1 + 2*sum(rho)).

    Mission Center Research Rigor Phase 2 (2026-08-XX): a mission trial's
    R-multiple sequence is NOT i.i.d. — consecutive trades on one symbol
    share regime/volatility conditions, so treating raw trade count as
    the sample size (as every trial_p_value() caller does today)
    understates the true standard error and can make a lucky,
    autocorrelated run look more significant than it is. ESS is always
    <= N (equality only when the series shows no detectable serial
    correlation), so using it in place of raw N is strictly more
    conservative, never more permissive — it can only make a result look
    LESS significant than the naive count.

    Returns None (never a fabricated number) for n<5 — too few points to
    estimate autocorrelation reliably. Result is clamped to [1, N].
    """
    n = len(values)
    if n < 5:
        return None
    cap = min(max_lag, n - 1) if max_lag is not None else n - 1
    total_rho = 0.0
    m = 0
    while True:
        k1 = 2 * m + 1
        if k1 > cap:
            break
        k2 = k1 + 1
        rho1 = _sample_autocorrelation(values, k1)
        rho2 = _sample_autocorrelation(values, k2) if k2 <= cap else 0.0
        pair_sum = rho1 + rho2
        if pair_sum < 0:
            break
        total_rho += pair_sum
        m += 1
    ess = n / (1.0 + 2.0 * total_rho)
    return max(1.0, min(float(n), ess))


def binomial_sign_test_p_value(k: int, n: int, p: float = 0.5) -> float | None:
    """Exact two-tailed binomial (sign) test p-value for observing k
    'successes' out of n independent trials under a null hypothesis of
    p (default 0.5) — via math.comb, no scipy. Distinct from
    trial_p_value() above, which is a z-test on a CONTINUOUS mean
    R-multiple; this is for trial-level count/consensus claims like
    'BUY win_rate exceeded SELL win_rate in 43 of 50 trials' (Edge
    Discovery, 2026-07-31). Returns None (never a fabricated value) when
    undefined: n<1 or k out of [0, n]."""
    if n < 1 or not (0 <= k <= n):
        return None

    def _pmf(i: int) -> float:
        return math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))

    p_k = _pmf(k)
    total = sum(_pmf(i) for i in range(n + 1) if _pmf(i) <= p_k * (1 + 1e-9))
    return min(1.0, total)


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
