"""
backtest/lead_id.py
---------------------
Mission Center Research Rigor Phase 7 (2026-08-XX) — a derived, cosmetic
identifier for tracing one Mission Center candidate (mission + trial +
symbol) across the leaderboard, a validation run, and an AI-drafted
hypothesis file, WITHOUT ever touching real hypothesis identity.

This is deliberately NOT a step toward "hypotheses as first-class objects
in registry.json" — CLAUDE.md rule 1 requires a real HXXX ID to be
assigned by a human, with falsification criteria written BEFORE any
result exists, at the point of manual registration. Minting IDs earlier
in the pipeline (at mission-trial time, before any human has looked at
the candidate) would be exactly the kind of automated identity-creation
that risks a lead being mistaken for a registered hypothesis. A Lead ID
carries no evidence status, no falsification criteria, and is never
written into research/results/registry.json.

Pure and deterministic: the same (mission_id, trial_number, symbol)
always produces the same Lead ID, computed on the fly wherever it's
needed — no new storage, no new database column. The 'LEAD-' prefix and
hyphenated shape are deliberately visually distinct from an HXXX
registry ID, so nobody mistakes one for the other at a glance.
"""
from __future__ import annotations


def lead_id(mission_id: str, trial_number: int, symbol: str) -> str:
    """LEAD-<first 8 chars of mission_id>-<trial_number>-<SYMBOL>."""
    short_mission = (mission_id or "")[:8] or "unknown"
    return f"LEAD-{short_mission}-{trial_number}-{(symbol or '').upper()}"
