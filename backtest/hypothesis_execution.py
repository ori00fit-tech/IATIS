"""
backtest/hypothesis_execution.py
-----------------------------------
Hypothesis Discovery Engine, Phase 3 — Hypothesis Execution / Mission
Binding. Pure functions + dataclasses only — no D1 access (see
storage/research_matrix.py's upsert_family()/upsert_cells() for the
persistence half), no engine run, no Stage A/Stage B execution.

Answers the governance question the operator posed explicitly: how does a
SPECIFIC, already-proposed backtest.hypothesis_factory.Hypothesis become a
real, testable Matrix cell, without Mission Center (or anything else)
gaining the ability to edit the hypothesis's own definition or cherry-pick
which result it prefers?

    DISCOVERY-HYPOTHESIS-<fp>
            |
    build_execution_request()   (THIS module — pure, verifies identity,
            |                     never selects which hypotheses to bind)
            v
    ExecutionRequest             (immutable: hypothesis_ids/cells/mapping
            |                     are frozen the moment this call returns)
            v
    storage.research_matrix.upsert_family()/upsert_cells()
    (the SAME, UNCHANGED persistence path every other Matrix family/cell
    already goes through — reused, never rebuilt)
            v
    QUEUED Matrix cell(s)
            |
    Stage A screen -> Bonferroni-corrected Matrix Family evidence gate ->
    Stage B validation
    (backtest/matrix_orchestrator.py, backtest/mission_runner.py,
    backtest/mission_validator.py — ALL UNCHANGED, ALL reused as-is, and
    ALL still only reachable via the existing, separate, human-triggered
    POST /research/matrix/run-batch action — binding a hypothesis stops
    at QUEUED, exactly like Phase 3C's AI recommendation conversion stops
    at QUEUED. This module contains no code path that runs a backtest,
    computes a verdict, or promotes anything.)

NON-NEGOTIABLE guardrails (operator's own explicit Phase 3 requirements):
  1. A hypothesis can never be modified after being bound — this module
     has no update path, and neither does storage.hypothesis_factory
     (record_hypotheses() is INSERT OR IGNORE only). build_execution_
     request() re-derives each hypothesis's canonical fingerprint from
     its OWN stored identity fields and refuses (raises
     HypothesisExecutionError) if it doesn't match the fingerprint the
     hypothesis was originally proposed with — a structural proof, not
     an assumption, that nothing has drifted.
  2. Symbol/engine/version/timeframe/risk_preset can never change after
     an execution request is built — every one of these is read directly
     from the hypothesis's own immutable stored row, never accepted as a
     separate, override-able argument to this module.
  3. Never auto-selects which hypothesis to bind — build_execution_
     request() requires an explicit, non-empty list of already-fetched
     hypothesis rows; there is no code path here that queries, ranks, or
     picks "the best" one.
  4. Never touches research/results/registry.json, config.yaml, config/
     engines.yaml, or config/symbols.yaml — this module makes no file or
     network call of any kind.
  5. No promotion, no live execution, no risk-exposure change — this
     module produces QUEUED cells only, nothing more.
  6. Never bypasses Stage A or the Bonferroni-corrected Matrix Family
     evidence gate — an ExecutionRequest's cells are ordinary
     MatrixCellSpec objects, persisted via the SAME upsert_family()/
     upsert_cells() every other Matrix family uses, so they are screened
     and corrected identically, with no special-cased shortcut.
  7. Never treats a backtest result as evidence of edge by itself — this
     module computes no result at all; it only requests that a cell
     EXISTS to be tested later.
  8. Structurally cannot reuse a hypothesis_id with a different
     fingerprint: hypothesis_id IS DISCOVERY-HYPOTHESIS-<fingerprint> (see
     backtest.hypothesis_factory.Hypothesis.hypothesis_id) — two different
     fingerprints mechanically produce two different ids, never a
     collision.

Reuses, never rebuilds: backtest.research_matrix.generate_discovery_cells()
for ALL bundle construction/validation (both to re-derive a hypothesis's
own canonical fingerprint for verification, and to build the real,
commit-stamped MatrixCellSpec actually persisted) — this module adds zero
new Cartesian-product or bundle-shaping logic of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backtest.research_matrix import MatrixCellSpec, generate_discovery_cells


class HypothesisExecutionError(ValueError):
    """Raised on a malformed binding request or a hypothesis whose stored
    identity fails to re-verify. Fail loud — never silently bind a
    tampered or inconsistent hypothesis."""


@dataclass(frozen=True)
class ExecutionRequest:
    """An IMMUTABLE bundle of (hypothesis_id -> real, commit-stamped
    MatrixCellSpec) built once, from already-verified hypothesis rows.
    Nothing in this engine ever mutates an ExecutionRequest after
    build_execution_request() returns it — persisting it (storage.
    research_matrix.upsert_family()/upsert_cells()) is a separate,
    explicit next step the caller takes with this object, unchanged."""

    hypothesis_ids: tuple[str, ...]
    cells: tuple[MatrixCellSpec, ...]
    cell_id_by_hypothesis: dict[str, str]

    @property
    def source_hypothesis_ids_by_cell(self) -> dict[str, str]:
        """The {cell_id: hypothesis_id} shape storage.research_matrix.
        upsert_cells()'s own source_hypothesis_ids parameter expects — the
        inverse of cell_id_by_hypothesis, computed once here so a caller
        persisting this request never has to invert the mapping itself
        (and risk getting the direction backwards)."""
        return {cell_id: hypothesis_id for hypothesis_id, cell_id in self.cell_id_by_hypothesis.items()}


def _canonical_cell_for_hypothesis(h: dict[str, Any]) -> MatrixCellSpec:
    """Reconstructs the hypothesis's own canonical (commit/provider-
    agnostic) Matrix cell — the SAME cell backtest.hypothesis_factory.
    generate_hypotheses() itself derived this hypothesis's matrix_cell_
    fingerprint from — by calling generate_discovery_cells() again with
    the hypothesis's own stored identity fields, and nothing else (no
    commit, no data_provider — matching generate_hypotheses()'s own
    defaults exactly). Reuses ALL of that function's bundle-construction
    and validation logic; never a second implementation of it."""
    cells = generate_discovery_cells(
        symbols=[h["symbol"]], engines=[h["engine"]], timeframes=[h["timeframe"]],
        engine_versions={h["engine"]: (h["engine_version"],)}, risk_presets=[h["risk_preset"]],
    )
    if len(cells) != 1:
        raise HypothesisExecutionError(
            f"hypothesis {h.get('hypothesis_id')!r}: expected exactly one canonical cell, got {len(cells)} — "
            f"malformed hypothesis row."
        )
    return cells[0]


def _verify_hypothesis_identity(h: dict[str, Any]) -> None:
    """The structural immutability proof (guardrail 1): re-derives what
    this hypothesis's fingerprint SHOULD be, right now, from its own
    stored fields, and refuses to proceed if it disagrees with the
    fingerprint the hypothesis was actually proposed with. There is no
    code path anywhere in this engine that could make these disagree
    (storage.hypothesis_factory has no update function), but this check
    makes that guarantee explicit and loud rather than merely assumed."""
    canonical = _canonical_cell_for_hypothesis(h)
    if canonical.fingerprint != h.get("matrix_cell_fingerprint"):
        raise HypothesisExecutionError(
            f"hypothesis {h.get('hypothesis_id')!r} fingerprint mismatch: stored "
            f"{h.get('matrix_cell_fingerprint')!r}, re-derived {canonical.fingerprint!r} — refusing to bind a "
            f"hypothesis whose own identity appears to have drifted since it was proposed."
        )


def build_execution_request(
    hypotheses: list[dict[str, Any]], *,
    research_code_commit: str | None = None,
    data_provider: str | None = None,
) -> ExecutionRequest:
    """Binds an EXPLICIT, human/caller-named list of already-fetched
    Hypothesis rows (as returned by storage.hypothesis_factory.
    get_hypothesis()/list_hypotheses()) into an immutable ExecutionRequest
    — one real MatrixCellSpec per hypothesis, stamped with the CURRENT
    research_code_commit (the caller's own responsibility to resolve, via
    backtest.research_matrix.resolve_research_code_commit(), exactly like
    every other Matrix cell generation path already does — never resolved
    internally here, keeping this function fully deterministic and
    testable without touching git).

    Raises HypothesisExecutionError for an empty list (guardrail 3 — this
    function never selects a default/best set on its own) or for any
    hypothesis whose stored identity fails to re-verify (guardrail 1).

    Idempotent within one call: a duplicate hypothesis_id in the input
    list is bound once, not twice. Idempotent ACROSS calls at the same
    research_code_commit: the resulting cell's fingerprint (and hence
    cell_id) is fully deterministic, so persisting it twice via storage.
    research_matrix.upsert_cells() is safely deduplicated there exactly
    like any other repeated cell. A DIFFERENT research_code_commit
    produces a genuinely different, coexisting cell — never a collision,
    never an overwrite of the earlier one (guardrail 8's corollary)."""
    if not hypotheses:
        raise HypothesisExecutionError(
            "hypotheses must be non-empty — an execution request always binds at least one explicitly-named "
            "hypothesis; this function never selects one on its own."
        )

    cells: list[MatrixCellSpec] = []
    cell_id_by_hypothesis: dict[str, str] = {}
    for h in hypotheses:
        hypothesis_id = h["hypothesis_id"]
        if hypothesis_id in cell_id_by_hypothesis:
            continue  # idempotent within one request
        _verify_hypothesis_identity(h)
        real_cells = generate_discovery_cells(
            symbols=[h["symbol"]], engines=[h["engine"]], timeframes=[h["timeframe"]],
            engine_versions={h["engine"]: (h["engine_version"],)}, risk_presets=[h["risk_preset"]],
            data_provider=data_provider, research_code_commit=research_code_commit,
        )
        spec = real_cells[0]
        cells.append(spec)
        cell_id_by_hypothesis[hypothesis_id] = spec.cell_id

    return ExecutionRequest(
        hypothesis_ids=tuple(cell_id_by_hypothesis.keys()),
        cells=tuple(cells),
        cell_id_by_hypothesis=cell_id_by_hypothesis,
    )
