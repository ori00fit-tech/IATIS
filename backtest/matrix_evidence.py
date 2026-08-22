"""
backtest/matrix_evidence.py
-------------------------------
Hypothesis Discovery Engine, Phase 2A — Evidence & Matrix Read Model.

Pure, read-only aggregation over already-persisted D1 rows: storage.
research_matrix (families/cells), storage.research_missions (Stage A
trials), storage.research_mission_validations (Stage B validations).
No D1 access happens IN this module — every function here takes rows
already fetched by its caller (execution/routes/research_matrix.py) and
returns a plain dict/list, matching backtest/meta_analysis.py's own
"pure functions over already-fetched rows" convention.

NON-NEGOTIABLE (operator's own explicit rule for this phase): this module
NEVER computes, infers, or overrides a verdict. Every status, p-value,
rejection reason, and Stage-B verdict it surfaces was already decided by
backtest/matrix_orchestrator.py (the Stage A screen, the Matrix Family
Bonferroni correction, Stage B validation) — this module only re-presents
that already-authoritative D1 evidence in aggregated/joined shapes that
are expensive or awkward to compute client-side. A future dashboard or
AI copilot consuming this module's output must treat every field as
already-decided evidence, never as something it can recompute.

RESEARCH-ONLY, same guarantee as every other module in this engine:
nothing here reads or writes research/results/registry.json, config.yaml,
config/engines.yaml, or config/symbols.yaml.
"""
from __future__ import annotations

import json
from typing import Any

from backtest import research_matrix as rm
from backtest.multiple_testing import bonferroni_alpha

# ---------------------------------------------------------------------------
# Family-level evidence
# ---------------------------------------------------------------------------


def family_status_breakdown(cells: list[dict[str, Any]]) -> dict[str, int]:
    """Count of cells per CELL_STATUSES value — every known status key is
    present (0 if unseen), so a caller never has to guess which keys might
    be missing."""
    counts = {status: 0 for status in rm.CELL_STATUSES}
    for cell in cells:
        status = cell.get("status")
        if status in counts:
            counts[status] += 1
        else:
            counts[status] = counts.get(status, 0) + 1
    return counts


def family_evidence_summary(family: dict[str, Any], cells: list[dict[str, Any]]) -> dict[str, Any]:
    """The one-call "how is this research family doing" view: the family's
    own fixed planned_n/family_alpha (see storage.research_matrix's own
    docstring — Finding 4's fixed-denominator design), the resulting
    Bonferroni-corrected alpha, a full status breakdown, and total
    resumptions (summed cell-level requeue_count) across the family."""
    planned_n = family["planned_n"]
    family_alpha = family["family_alpha"]
    breakdown = family_status_breakdown(cells)
    total_requeues = sum(int(c.get("requeue_count") or 0) for c in cells)
    symbols = []
    if family.get("symbols_json"):
        try:
            symbols = json.loads(family["symbols_json"])
        except (TypeError, ValueError):
            symbols = []
    return {
        "family_id": family["family_id"],
        "planned_n": planned_n,
        "family_alpha": family_alpha,
        "bonferroni_alpha": bonferroni_alpha(planned_n, family_alpha) if planned_n >= 1 else None,
        "symbols": symbols,
        "created_at": family.get("created_at"),
        "cells_generated": len(cells),
        "status_breakdown": breakdown,
        "total_requeues": total_requeues,
    }


# ---------------------------------------------------------------------------
# Group-by-status breakdowns (per-symbol / per-bundle / per-risk-preset)
# ---------------------------------------------------------------------------


def _group_stats(cells: list[dict[str, Any]], key_fn) -> dict[str, dict[str, int]]:
    """Generic groupby: {group_key: {status: count, ..., "total": n}}."""
    out: dict[str, dict[str, int]] = {}
    for cell in cells:
        key = key_fn(cell)
        bucket = out.setdefault(key, {status: 0 for status in rm.CELL_STATUSES})
        status = cell.get("status")
        bucket[status] = bucket.get(status, 0) + 1
        bucket["total"] = bucket.get("total", 0) + 1
    return out


def per_symbol_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return _group_stats(cells, lambda c: c.get("symbol") or "?")


def per_bundle_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Keyed by the bundle's own human-readable name (bundle_json.name),
    not its raw JSON — the fingerprint already captures the exact
    combination; this stat answers "how did hypothesis bundle X do," which
    is naturally per-name, not per-fingerprint."""

    def _bundle_name(cell: dict[str, Any]) -> str:
        raw = cell.get("bundle_json")
        if not raw:
            return "?"
        try:
            return json.loads(raw).get("name", "?")
        except (TypeError, ValueError):
            return "?"

    return _group_stats(cells, _bundle_name)


def per_risk_preset_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return _group_stats(cells, lambda c: c.get("risk_preset") or "?")


# ---------------------------------------------------------------------------
# Single-cell evidence (raw cell row + Stage A trial + Stage B validation)
# ---------------------------------------------------------------------------


def _safe_json_loads(raw: str | None) -> Any | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def cell_evidence(
    cell: dict[str, Any],
    trial: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full evidence chain for one cell in one call: the raw cell row
    (JSON columns decoded for readability) plus, when present, its Stage A
    trial detail (from storage.research_missions.get_trial) and its Stage B
    validation detail (from storage.research_mission_validations.
    get_validation) — the exact join a UI would otherwise need 3 separate
    round-trips to assemble. `trial`/`validation` are the caller's own
    already-fetched rows (this function performs no D1 access itself);
    either may be None when the cell hasn't reached that stage yet, or its
    stage_a_mission_id/stage_b_validation_id is unset."""
    return {
        "cell_id": cell["cell_id"],
        "family_id": cell.get("family_id"),
        "fingerprint": cell.get("fingerprint"),
        "symbol": cell.get("symbol"),
        "bundle": _safe_json_loads(cell.get("bundle_json")),
        "risk_preset": cell.get("risk_preset"),
        "confluence_overrides": _safe_json_loads(cell.get("confluence_overrides_json")),
        "engine_variants": _safe_json_loads(cell.get("engine_variants_json")),
        "data_provider": cell.get("data_provider"),
        "status": cell.get("status"),
        "rejection_reason": cell.get("rejection_reason"),
        "requeue_count": int(cell.get("requeue_count") or 0),
        "created_at": cell.get("created_at"),
        "updated_at": cell.get("updated_at"),
        "stage_a": {
            "mission_id": cell.get("stage_a_mission_id"),
            "trial_number": cell.get("stage_a_trial_number"),
            "metrics": _safe_json_loads(cell.get("stage_a_metrics_json")),
            "p_value": cell.get("stage_a_p_value"),
            "lead_id": cell.get("lead_id"),
            "trial_detail": trial,
        },
        "stage_b": {
            "validation_id": cell.get("stage_b_validation_id"),
            "verdict": cell.get("stage_b_verdict"),
            "validation_detail": validation,
        },
    }
