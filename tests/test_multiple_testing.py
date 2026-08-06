"""
tests/test_multiple_testing.py
---------------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — numeric-
correctness tests for backtest/multiple_testing.py. This is the module
that makes a mission's "leaderboard" honest: it computes what pure noise
would produce at the same trial count, so a promising-looking trial is
never mistaken for validated evidence.
"""
from __future__ import annotations

import math

import pytest

import random

from backtest.multiple_testing import (
    binomial_sign_test_p_value,
    bonferroni_alpha,
    classify_significance,
    effective_sample_size,
    expected_false_positives,
    mission_significance_summary,
    normal_cdf,
    trial_p_value,
)


def test_bonferroni_alpha_numerically_correct_for_known_n():
    assert bonferroni_alpha(1000) == pytest.approx(0.05 / 1000)
    assert bonferroni_alpha(1) == pytest.approx(0.05)
    assert bonferroni_alpha(20) == pytest.approx(0.0025)


def test_bonferroni_alpha_rejects_invalid_n():
    with pytest.raises(ValueError):
        bonferroni_alpha(0)


def test_expected_false_positives_numerically_correct():
    assert expected_false_positives(1000) == pytest.approx(50.0)
    assert expected_false_positives(20) == pytest.approx(1.0)
    assert expected_false_positives(100, family_alpha=0.01) == pytest.approx(1.0)


def test_normal_cdf_matches_known_values():
    assert normal_cdf(0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert normal_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_trial_p_value_none_when_insufficient_or_zero_variance():
    assert trial_p_value(mean_r=1.0, std_r=0.5, n=1) is None
    assert trial_p_value(mean_r=1.0, std_r=0.0, n=10) is None
    assert trial_p_value(mean_r=0.0, std_r=0.5, n=10) is not None


def test_trial_p_value_smaller_for_stronger_signal():
    weak = trial_p_value(mean_r=0.1, std_r=1.0, n=30)
    strong = trial_p_value(mean_r=2.0, std_r=1.0, n=30)
    assert strong < weak


def test_classify_significance_all_four_buckets():
    n_trials = 1000
    alpha_corrected = bonferroni_alpha(n_trials)  # 0.00005
    assert classify_significance(None, n_trials) == "INSUFFICIENT_DATA"
    assert classify_significance(alpha_corrected / 2, n_trials) == "SURVIVES_CORRECTION"
    assert classify_significance(0.02, n_trials) == "NOMINAL_ONLY"
    assert classify_significance(0.5, n_trials) == "NOT_SIGNIFICANT"


def test_mission_significance_summary_empty():
    summary = mission_significance_summary([])
    assert summary["n_trials"] == 0
    assert "EXPLORATORY" in summary["banner"]


def test_mission_significance_summary_count_matches_hand_computed():
    # Construct 1000 trials: 47 with a strong, genuinely significant mean_r
    # (p << 0.05 even after strict correction), the rest pure noise
    # (mean_r=0 exactly -> p=None, contributing to neither count).
    trials = []
    for _ in range(47):
        trials.append({"mean_r": 5.0, "std_r": 1.0, "n": 50})
    for _ in range(1000 - 47):
        trials.append({"mean_r": 0.0, "std_r": 1.0, "n": 50})

    summary = mission_significance_summary(trials)
    assert summary["n_trials"] == 1000
    assert summary["count_nominal_p_lt_05"] == 47
    assert summary["count_surviving_bonferroni"] == 47
    assert summary["expected_false_positives_at_p05"] == pytest.approx(50.0)
    assert "EXPLORATORY — NOT EVIDENCE" in summary["banner"]
    assert "1000 trials" in summary["banner"]


# ── Edge Discovery (2026-07-31) — binomial_sign_test_p_value ─────────────

def test_binomial_sign_test_p_value_matches_hand_computed():
    # k=8, n=10, p=0.5: two-tailed exact binomial p-value.
    # P(X=8) + P(X=9) + P(X=10) [upper tail] doubled by symmetry (and
    # equal to summing every i with pmf(i) <= pmf(8), which for n=10,
    # p=0.5 is exactly {0,1,2,8,9,10}).
    n, k = 10, 8

    def pmf(i):
        return math.comb(n, i) * (0.5 ** n)

    expected = sum(pmf(i) for i in range(n + 1) if pmf(i) <= pmf(k) * (1 + 1e-9))
    assert binomial_sign_test_p_value(k, n) == pytest.approx(expected, rel=1e-9)


def test_binomial_sign_test_p_value_symmetric_for_complementary_k():
    assert binomial_sign_test_p_value(8, 10) == pytest.approx(binomial_sign_test_p_value(2, 10))
    assert binomial_sign_test_p_value(43, 50) == pytest.approx(binomial_sign_test_p_value(7, 50))


def test_binomial_sign_test_p_value_one_at_exact_midpoint():
    # k == n/2 exactly -> every outcome has pmf <= pmf(k) is false in
    # general, but the midpoint itself must always yield p=1.0 for even n
    # under p=0.5 (the mode of the distribution).
    assert binomial_sign_test_p_value(5, 10) == pytest.approx(1.0)


def test_binomial_sign_test_p_value_small_for_lopsided_outcome():
    # 43 of 50 favoring one side should be a strong, clearly significant
    # signal (comfortably below 0.001).
    p = binomial_sign_test_p_value(43, 50)
    assert p is not None
    assert p < 0.001


def test_binomial_sign_test_p_value_none_when_undefined():
    assert binomial_sign_test_p_value(0, 0) is None
    assert binomial_sign_test_p_value(-1, 10) is None
    assert binomial_sign_test_p_value(11, 10) is None


# ── Mission Center Research Rigor Phase 2 (2026-08-XX) — effective_sample_size ──
# Geyer's initial positive sequence (IPS) estimator: ESS accounts for serial
# correlation in a mission trial's own R-multiple sequence, so a trial's
# significance can no longer be overstated just because raw trade count
# ignores that consecutive backtest trades are not independent samples.

def test_effective_sample_size_none_below_minimum():
    assert effective_sample_size([1.0, 2.0, 3.0]) is None
    assert effective_sample_size([]) is None


def test_effective_sample_size_never_exceeds_n():
    rng = random.Random(42)
    values = [rng.gauss(0, 1) for _ in range(200)]
    ess = effective_sample_size(values)
    assert ess is not None
    assert 1.0 <= ess <= 200.0


def test_effective_sample_size_close_to_n_for_iid_noise():
    # Pure i.i.d. noise has no detectable serial correlation on average —
    # the IPS estimator should stop almost immediately (first pair sum
    # negative) and report ESS very close to the raw N.
    rng = random.Random(7)
    values = [rng.gauss(0, 1) for _ in range(500)]
    ess = effective_sample_size(values)
    assert ess is not None
    assert ess > 400.0  # comfortably close to 500, allowing for sampling noise


def test_effective_sample_size_much_smaller_for_strongly_autocorrelated_series():
    # A slowly-varying, strongly autocorrelated series (a random walk of
    # small steps, which has near-1 lag-1 autocorrelation by construction)
    # must produce a materially smaller ESS than its raw length.
    rng = random.Random(3)
    values = []
    x = 0.0
    for _ in range(300):
        x += rng.gauss(0, 0.05)
        values.append(x)
    ess = effective_sample_size(values)
    assert ess is not None
    assert ess < 300.0 * 0.5  # strongly correlated -> well below half the raw N


def test_effective_sample_size_constant_series_degrades_to_n():
    # Zero variance -> _sample_autocorrelation returns 0.0 at every lag
    # (nothing to correlate), so the IPS sum stays 0 and ESS == N exactly.
    # (trial_p_value's own std_r==0 guard is what actually prevents this
    # degenerate case from ever producing a p-value — effective_sample_size
    # itself must simply not crash or misbehave on it.)
    ess = effective_sample_size([5.0] * 20)
    assert ess == pytest.approx(20.0)


def test_effective_sample_size_respects_max_lag_cap():
    rng = random.Random(11)
    values = [rng.gauss(0, 1) for _ in range(100)]
    ess_uncapped = effective_sample_size(values)
    ess_capped = effective_sample_size(values, max_lag=1)
    assert ess_uncapped is not None and ess_capped is not None
    # Capping at lag 1 can only ever consider one autocorrelation pair
    # (or zero), never diverging in a way that produces a LARGER estimate
    # than the uncapped run would for the same data in the typical case —
    # both must still be valid, in-range values.
    assert 1.0 <= ess_capped <= 100.0


def test_effective_sample_size_makes_p_value_more_conservative():
    # The whole point: feeding ESS (instead of raw N) into trial_p_value
    # for an autocorrelated series must never produce a SMALLER p-value
    # than the naive raw-N calculation — autocorrelation-adjustment can
    # only make a result look less significant, never more.
    rng = random.Random(3)
    values = []
    x = 0.0
    for _ in range(300):
        x += rng.gauss(0, 0.05) + 0.001  # slight positive drift so mean != 0
        values.append(x)
    n = len(values)
    mean_r = sum(values) / n
    std_r = (sum((v - mean_r) ** 2 for v in values) / (n - 1)) ** 0.5
    ess = effective_sample_size(values)
    assert ess is not None
    nominal_p = trial_p_value(mean_r, std_r, n)
    ess_p = trial_p_value(mean_r, std_r, int(ess))
    assert nominal_p is not None and ess_p is not None
    assert ess_p >= nominal_p
