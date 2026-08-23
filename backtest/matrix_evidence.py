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

Phase 2C (Evidence Comparison) added `compare_cells_provenance()` and
threaded a cell's own `research_code_commit` + Stage B per-symbol
`validation_result` (Monte Carlo/Walk-Forward/robustness/regime-
robustness/stability/cost-stress) into `cell_evidence()`. The same rule
applies with extra force here: this module computes zero rankings,
scores, or "better than" judgments across cells — only identity
equality checks (same family? same commit? same provider? same
hypothesis?) and verbatim re-presentation of already-decided evidence.
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
# Phase 1 (Research Matrix Normalization, 2026-08-23) — per-engine and
# per-timeframe breakdowns, now that storage.research_matrix.upsert_cells()
# persists these as their own indexed columns (backtest.research_matrix.
# single_engine_identity()) rather than leaving them buried inside
# bundle_json. "?" groups every pre-existing/confluence-research cell whose
# bundle combines multiple engines or multiple timeframes — such a cell
# genuinely has no single scalar engine/timeframe, so it is never coerced
# into one; these functions do not attempt to attribute it to any single
# engine or timeframe. Same NON-NEGOTIABLE rule as every other function in
# this module: a plain tally of already-decided statuses, never a verdict,
# never a ranking, never "engine X is best."
# ---------------------------------------------------------------------------


def per_engine_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return _group_stats(cells, lambda c: c.get("engine") or "?")


def per_timeframe_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return _group_stats(cells, lambda c: c.get("timeframe") or "?")


def per_symbol_engine_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Keyed by "SYMBOL / engine" — a composite key rather than a nested
    dict, matching this module's own flat {key: {status: count}} shape so
    a caller (dashboard, AI copilot) doesn't need a different traversal
    pattern for the composite breakdowns than the single-dimension ones."""
    return _group_stats(cells, lambda c: f"{c.get('symbol') or '?'} / {c.get('engine') or '?'}")


def per_symbol_timeframe_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return _group_stats(cells, lambda c: f"{c.get('symbol') or '?'} / {c.get('timeframe') or '?'}")


def per_symbol_engine_timeframe_stats(cells: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return _group_stats(
        cells, lambda c: f"{c.get('symbol') or '?'} / {c.get('engine') or '?'} / {c.get('timeframe') or '?'}",
    )


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
    validation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full evidence chain for one cell in one call: the raw cell row
    (JSON columns decoded for readability) plus, when present, its Stage A
    trial detail (from storage.research_missions.get_trial), its Stage B
    validation-RUN detail (from storage.research_mission_validations.
    get_validation), and — Phase 2C — its Stage B per-SYMBOL validation
    result (from storage.research_mission_validations.validation_results,
    the row whose `symbol` matches this cell's own `symbol`; a CROSS_SYMBOL
    validation has one row per validated symbol, so the caller must pick
    the matching one, not just take the first) — the exact join a UI would
    otherwise need 4 separate round-trips to assemble. `trial`/
    `validation`/`validation_result` are the caller's own already-fetched
    rows (this function performs no D1 access itself); any may be None
    when the cell hasn't reached that stage yet, its stage_a_mission_id/
    stage_b_validation_id is unset, or the validation never covered this
    cell's own symbol."""
    return {
        "cell_id": cell["cell_id"],
        "family_id": cell.get("family_id"),
        "fingerprint": cell.get("fingerprint"),
        "symbol": cell.get("symbol"),
        "bundle": _safe_json_loads(cell.get("bundle_json")),
        "engine": cell.get("engine"),
        "engine_version": cell.get("engine_version"),
        "timeframe": cell.get("timeframe"),
        "risk_preset": cell.get("risk_preset"),
        "confluence_overrides": _safe_json_loads(cell.get("confluence_overrides_json")),
        "engine_variants": _safe_json_loads(cell.get("engine_variants_json")),
        "data_provider": cell.get("data_provider"),
        "research_code_commit": cell.get("research_code_commit"),
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
            "validation_result": _decode_validation_result(validation_result),
        },
    }


def _decode_validation_result(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Decodes every JSON column on a research_mission_validation_results
    row. Every one of these diagnostics (monte_carlo/walk_forward/
    robustness/significance/regime_robustness/stability/cost_stress/
    discovery_score) is, per that table's own DDL comments, diagnostic
    only — none of them is `passed`/`criteria_breakdown`, i.e. none ever
    participated in the Stage B verdict already carried on `validation`.
    This function only decodes; it adds no interpretation."""
    if row is None:
        return None
    return {
        "symbol": row.get("symbol"),
        "passed": bool(row.get("passed")),
        "metrics": _safe_json_loads(row.get("metrics_json")),
        "monte_carlo": _safe_json_loads(row.get("monte_carlo_json")),
        "walk_forward": _safe_json_loads(row.get("walk_forward_json")),
        "robustness": _safe_json_loads(row.get("robustness_json")),
        "criteria_breakdown": _safe_json_loads(row.get("criteria_breakdown_json")),
        "significance": _safe_json_loads(row.get("significance_json")),
        "regime_robustness": _safe_json_loads(row.get("regime_robustness_json")),
        "stability": _safe_json_loads(row.get("stability_json")),
        "cost_stress": _safe_json_loads(row.get("cost_stress_json")),
        "discovery_score": _safe_json_loads(row.get("discovery_score_json")),
        "error": row.get("error"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }


# ---------------------------------------------------------------------------
# Phase 2C — Evidence Comparison provenance (descriptive only, never a
# verdict). Every field below is a plain equality/membership check over
# identity columns already on each cell row — no statistic is computed, no
# score, no rank, no "better." The comparison UI built on this output must
# never collapse these flags into a single ranking; see this module's own
# NON-NEGOTIABLE rule at the top of the file.
# ---------------------------------------------------------------------------


def compare_cells_provenance(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Given 2+ already-fetched cell rows, reports whether they share the
    same Matrix Family (the statistically meaningful axis — same fixed
    planned_n/Bonferroni alpha), the same research code commit, and the
    same data provider — plus whether they form a "same-hypothesis
    lineage" (identical symbol + bundle name + risk_preset, varying only
    in commit/overrides/provider — the operator's own Comparison Type 3,
    useful for telling whether an apparent improvement came from a code
    change rather than the hypothesis itself). A single-cell list still
    returns a well-formed result (every same_* flag trivially True)."""
    family_ids = sorted({c.get("family_id") for c in cells if c.get("family_id") is not None})
    commits = sorted({c.get("research_code_commit") or "unknown" for c in cells})
    data_providers = sorted({c.get("data_provider") or "unspecified" for c in cells})

    def _bundle_name(cell: dict[str, Any]) -> str:
        raw = cell.get("bundle_json")
        if not raw:
            return "?"
        try:
            return json.loads(raw).get("name", "?")
        except (TypeError, ValueError):
            return "?"

    lineage_keys = {(c.get("symbol"), _bundle_name(c), c.get("risk_preset")) for c in cells}
    same_hypothesis_lineage = len(lineage_keys) == 1

    return {
        "cell_count": len(cells),
        "family_ids": family_ids,
        "same_family": len(family_ids) <= 1,
        "commits": commits,
        "same_commit": len(commits) <= 1,
        "data_providers": data_providers,
        "same_data_provider": len(data_providers) <= 1,
        "same_hypothesis_lineage": same_hypothesis_lineage,
        "lineage_key": (
            {"symbol": next(iter(lineage_keys))[0], "bundle": next(iter(lineage_keys))[1], "risk_preset": next(iter(lineage_keys))[2]}
            if same_hypothesis_lineage
            else None
        ),
    }
