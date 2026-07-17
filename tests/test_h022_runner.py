"""tests/test_h022_runner.py — H022 verdict logic.

The decision rule was pre-registered in registry.json (2026-07-17);
verdict_for() must apply it LITERALLY. These tests pin the runner to the
registry text so the logic cannot drift between registration and run.
"""
from __future__ import annotations

from research.experiments.H022_fx_cross_oos import (
    MIN_TEST_PF,
    MIN_TEST_TRADES,
    MIN_YEAR_PF,
    SPREADS_PIPS,
    verdict_for,
)

GOOD_YEARS = {"2024": {"trades": 20, "wr": 50.0, "pf": 1.3},
              "2025": {"trades": 22, "wr": 48.0, "pf": 1.25}}


def test_registered_constants_match_registry_text():
    assert MIN_TEST_TRADES == 40 and MIN_TEST_PF == 1.2 and MIN_YEAR_PF == 0.9
    assert SPREADS_PIPS == {"USDCNH": 2.3, "GBPAUD": 0.9, "EURAUD": 0.7}


def test_adopt_when_both_conditions_hold():
    verdict, reasons = verdict_for(1.35, 45, GOOD_YEARS)
    assert verdict == "ADOPT_TO_DEMO" and reasons == []


def test_boundaries_are_inclusive_as_registered():
    # "PF >= 1.2 with n >= 40" and "no year PF < 0.9" — exact values pass.
    years = {**GOOD_YEARS, "2023": {"trades": 10, "wr": 40.0, "pf": 0.9}}
    verdict, _ = verdict_for(1.2, 40, years)
    assert verdict == "ADOPT_TO_DEMO"


def test_insufficient_data_below_min_n():
    verdict, reasons = verdict_for(2.5, 39, GOOD_YEARS)
    assert verdict == "INSUFFICIENT_DATA"
    assert "n=39" in reasons[0]


def test_reject_on_low_test_pf():
    verdict, reasons = verdict_for(1.19, 60, GOOD_YEARS)
    assert verdict == "REJECT"
    assert any("TEST PF" in r for r in reasons)


def test_reject_on_any_bad_year_even_with_great_overall_pf():
    years = {**GOOD_YEARS, "2022": {"trades": 15, "wr": 30.0, "pf": 0.7}}
    verdict, reasons = verdict_for(1.8, 80, years)
    assert verdict == "REJECT"
    assert any("2022" in r for r in reasons)


def test_inf_pf_sentinel_counts_as_passing():
    # An all-wins TEST slice serializes PF as a sentinel string.
    verdict, _ = verdict_for("inf (no losses)", 41, GOOD_YEARS)
    assert verdict == "ADOPT_TO_DEMO"


def test_year_with_no_losses_sentinel_is_not_a_bad_year():
    years = {**GOOD_YEARS, "2023": {"trades": 5, "wr": 100.0, "pf": "inf (no losses)"}}
    verdict, _ = verdict_for(1.4, 50, years)
    assert verdict == "ADOPT_TO_DEMO"
