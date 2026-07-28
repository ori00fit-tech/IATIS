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

from backtest.multiple_testing import (
    bonferroni_alpha,
    classify_significance,
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
