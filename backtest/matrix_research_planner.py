"""
backtest/matrix_research_planner.py
---------------------------------------
Hypothesis Discovery Engine, Phase 3B — AI Research Orchestrator, Stage
3B.1: Evidence Context Builder.

Pure, read-only aggregation, same convention as backtest/matrix_
evidence.py (no D1 access here — every function takes rows its caller
already fetched) — this module ASSEMBLES matrix_evidence.py's own
already-computed outputs plus a few governance constants (the dead list,
frozen engines, symbol universe) into one compact JSON blob an AI planner
can read.

NON-NEGOTIABLE, same force as matrix_evidence.py's own rule and the
operator's own explicit Phase 3B boundary: this module computes ZERO
statistics, verdicts, or judgments of its own. It only re-presents
already-decided D1 evidence (via matrix_evidence.py's pure functions) in
one assembled shape. The AI Research Planner that reads this context is
strictly a PLANNER, never a JUDGE — see ai/prompts/matrix_research_plan.txt
and execution/routes/matrix_ai.py's own docstring for the enforced
boundary: it may propose WHERE to look next, never declare that
anything "has an edge" or should be promoted.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backtest import matrix_evidence as evidence


def build_evidence_context(
    families: list[dict[str, Any]],
    cells_by_family: dict[str, list[dict[str, Any]]],
    scoped_cell_evidence: list[dict[str, Any]],
    *,
    dead_list_text: str | None,
    frozen_engines: list[str],
    symbol_universe: list[str],
    focus_hint: str,
) -> dict[str, Any]:
    """Assembles the exact JSON blob ai/prompts/matrix_research_plan.txt
    consumes.

    `families`: already-fetched research_matrix_families rows (storage.
    research_matrix.get_family), one per family_id the caller scoped this
    recommendation to.
    `cells_by_family`: family_id -> its already-fetched cell rows
    (storage.research_matrix.list_cells(family_id=...)) — used for both
    the family's own evidence summary and its per-symbol/bundle/
    risk_preset coverage breakdown (matrix_evidence.py's own group-stats
    functions, reused verbatim, never reimplemented).
    `scoped_cell_evidence`: already-built full evidence joins (backtest.
    matrix_evidence.cell_evidence()) for specific cell_ids the caller
    wants explained (typically REJECTED/INSUFFICIENT_DATA cells) — empty
    when the recommendation is family-scoped only.
    `dead_list_text`: CLAUDE.md's own "## The dead list" section, or None
    if unavailable — degrades gracefully, never fabricated.
    `frozen_engines`: config/engines.yaml's currently-enabled engine keys.
    `symbol_universe`: every internal symbol name currently configured.
    `focus_hint`: the operator's own free-text steering input (may be
    empty) — same convention as ai/prompts/suggest_hypothesis.txt's own
    Operator focus hint.
    """
    family_blocks = []
    for family in families:
        family_id = family["family_id"]
        cells = cells_by_family.get(family_id, [])
        summary = evidence.family_evidence_summary(family, cells)
        family_blocks.append({
            "summary": summary,
            "coverage": {
                "by_symbol": evidence.per_symbol_stats(cells),
                "by_bundle": evidence.per_bundle_stats(cells),
                "by_risk_preset": evidence.per_risk_preset_stats(cells),
            },
        })

    return {
        "families": family_blocks,
        "scoped_cells": scoped_cell_evidence,
        "already_killed_ideas": dead_list_text or "Not available — see CLAUDE.md's dead list table directly.",
        "frozen_engines": sorted(frozen_engines),
        "symbol_universe": sorted(symbol_universe),
        "focus_hint": focus_hint or "none",
    }


def evidence_snapshot_hash(context: dict[str, Any]) -> str:
    """A stable sha256 over the exact context blob sent to the AI —
    persisted alongside every recommendation (storage.matrix_ai_
    recommendations) so that if the Matrix evidence changes later, an
    auditor can tell whether a given recommendation's snapshot still
    matches current evidence or was computed against a stale view.
    sort_keys=True makes this independent of dict insertion order, the
    only source of nondeterminism in an otherwise-plain-data structure.

    Verification procedure (documented per the Phase 3B-H audit's own LOW
    finding — storage/matrix_ai_recommendations.py's module docstring
    carries the same note): to verify a STORED evidence_snapshot_json
    against its evidence_snapshot_hash, re-canonicalize before hashing —
    `json.loads()` the stored text, then call THIS function again on the
    parsed dict. Do not sha256 the raw stored text directly; it was
    persisted without sort_keys, so its byte-for-byte serialization does
    not necessarily match this function's own canonical form even when
    the content is identical and untampered."""
    canonical = json.dumps(context, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
