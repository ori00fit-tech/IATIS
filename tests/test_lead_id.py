"""
tests/test_lead_id.py
------------------------
Mission Center Research Rigor Phase 7 — backtest/lead_id.py. Purely
cosmetic traceability, not a registry ID: pins the exact deterministic
formula (also mirrored in mission-center/MissionCenter.tsx's leadId()).
"""
from __future__ import annotations

from backtest.lead_id import lead_id


def test_lead_id_deterministic_formula():
    assert lead_id("abcdef1234567890", 7, "eurusd") == "LEAD-abcdef12-7-EURUSD"


def test_lead_id_same_inputs_produce_same_id():
    assert lead_id("mission-xyz", 3, "GBPUSD") == lead_id("mission-xyz", 3, "GBPUSD")


def test_lead_id_uppercases_symbol():
    assert lead_id("m1", 0, "xauusd").endswith("-XAUUSD")


def test_lead_id_truncates_mission_id_to_eight_chars():
    assert lead_id("0123456789abcdef", 1, "BTCUSD").startswith("LEAD-01234567-")


def test_lead_id_handles_short_or_empty_mission_id():
    assert lead_id("", 2, "ETHUSD") == "LEAD-unknown-2-ETHUSD"
    assert lead_id("abc", 2, "ETHUSD") == "LEAD-abc-2-ETHUSD"


def test_lead_id_never_looks_like_a_registry_id():
    # A real registry ID is HNNN (e.g. H013) — the "LEAD-" prefix and
    # hyphenated shape must never collide with that pattern.
    result = lead_id("m1", 5, "EURUSD")
    assert result.startswith("LEAD-")
    assert not result.startswith("H")
