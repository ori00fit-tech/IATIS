"""
backtest/hypothesis_mission.py
---------------------------------
Hypothesis Discovery Engine, Phase 4 — Mission Center Integration.

A "Mission" here is an ORCHESTRATION record, not a new research layer: it
is the durable, forensically-queryable answer to "why does this Matrix
Family/cell exist at all" — binding it back to the exact Hypothesis(es)
that requested it, the exact research code commit, and (when available)
the exact data snapshot, so the full chain is always answerable:

    Mission -> Hypothesis (original claim) -> code commit -> data
    snapshot -> Matrix Cell -> Stage A verdict -> Evidence Gate result

This is explicitly NOT backtest.mission_runner's own Optuna-search
"Mission" concept (a completely separate, UNCHANGED, pre-existing system
with its own research_missions storage table) — a Hypothesis Mission
never searches, samples, or optimizes anything; it only records that a
specific, already-verified set of Hypotheses was bound and persisted as
QUEUED Matrix cells via the SAME, UNCHANGED storage.research_matrix.
upsert_family()/upsert_cells() every other Matrix family already uses.

This module is the orchestration layer (it calls both backtest.* pure
logic AND storage.* persistence, exactly like backtest/matrix_
orchestrator.py already does for Stage A/Stage B — that file is this
one's own precedent for a backtest/*.py module that legitimately imports
storage/*.py; the ONE-WAY rule this codebase actually enforces is that
storage/*.py never imports backtest/*.py, never the other direction).

NON-NEGOTIABLE (operator's own explicit Phase 4 guardrail): no orphan
execution. record_mission() is the ONLY function in this engine that can
create a Mission, and it accepts EXCLUSIVELY a list of hypothesis_id
strings — never a symbol, engine, timeframe, or risk_preset. Every id is
independently re-fetched from storage.hypothesis_factory (an unknown id
is refused outright, loudly) and the resulting rows are re-verified as a
whole by calling backtest.hypothesis_execution.build_execution_request()
wholesale (never re-implemented) before anything is persisted. There is
no code path here — or anywhere downstream of it — that turns a raw
symbol/engine/timeframe/risk_preset combination directly into a Matrix
cell without first passing through a real, stored Hypothesis identity:

    Hypothesis  ->  ExecutionRequest  ->  Mission

never:

    arbitrary Mission config  ->  Matrix Cell

Reuses, never rebuilds: backtest.hypothesis_execution.build_execution_
request() for all identity re-verification and cell construction, and
storage.research_matrix.upsert_family()/upsert_cells() (both completely
UNCHANGED by this module) for all Matrix persistence. This module adds
zero new Cartesian-product, fingerprinting, Stage A/Stage B, promotion,
ranking, or optimizer logic of its own — see this module's own docstring
list of what it explicitly does NOT do, below.

Explicitly out of scope for this module (operator's own Phase 4 list):
no changes to generate_discovery_cells()/generate_hypotheses()/build_
execution_request()/Bonferroni/Stage A; no Promotion; no Symbol Policy
Registry; no live trading; no risk/exposure changes; no optimizer; no
ranking; no auto-selection. A Mission binds cells to QUEUED — exactly
where an ExecutionRequest already stops — and nothing more.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backtest.hypothesis_execution import HypothesisExecutionError, build_execution_request


def compute_mission_id(
    hypothesis_ids: tuple[str, ...] | list[str],
    research_code_commit: str | None,
    data_provider: str | None = None,
) -> str:
    """Deterministic identity for one Mission — the same set of hypothesis
    ids, bound at the same research code commit and data provider,
    always produces the SAME mission_id (idempotent re-binding, matching
    every other identity primitive in this engine: MATRIX-CELL-
    <fingerprint>, DISCOVERY-HYPOTHESIS-<fingerprint>). A different commit
    (the same reasoning Phase 3's own "same hypothesis + different
    fingerprint -> must not collide" guarantee already relies on)
    produces a genuinely different, coexisting mission_id.

    hypothesis_ids order does not affect the id — always sorted before
    hashing, so the SAME set bound via two different call orders still
    idempotently maps to one Mission."""
    payload = "|".join(sorted(hypothesis_ids)) + f"::{research_code_commit or ''}::{data_provider or ''}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"HYPOTHESIS-MISSION-{digest}"


def _mission_family_id(mission_id: str) -> str:
    """The Matrix family this Mission's cells belong to is itself derived
    (never randomly minted) from the Mission's own deterministic digest —
    unlike every OTHER family-creating caller in this codebase (which
    mints a random uuid4, since an ordinary /generate call has no natural
    stable identity of its own), a Mission already HAS one. Deriving
    family_id this way makes record_mission() crash-safe: if the process
    dies after the family/cells are persisted but before the Mission's own
    bookkeeping row is written, a retry recomputes the SAME mission_id and
    therefore the SAME family_id, finds it already exists, and simply
    finishes writing the missing Mission bookkeeping — never a duplicate,
    orphaned family."""
    digest = mission_id.rsplit("-", 1)[-1]
    return f"MISSION-FAM-{digest}"


def record_mission(
    hypothesis_ids: list[str], *,
    research_code_commit: str | None = None,
    data_provider: str | None = None,
    created_by: str | None = None,
    data_snapshot_id: str | None = None,
) -> dict[str, Any]:
    """The SOLE way to create a Hypothesis Mission. Accepts EXCLUSIVELY an
    explicit, non-empty list of already-proposed hypothesis_id strings —
    never a symbol, engine, timeframe, risk_preset, or any other raw
    Matrix-cell parameter (the "no orphan execution" guardrail, enforced
    structurally by this function's own signature, not by a runtime
    check that could be bypassed).

    For each hypothesis_id, fetches the CURRENT stored row fresh from
    storage.hypothesis_factory.get_hypothesis() — an id with no matching
    row raises HypothesisExecutionError immediately, loudly, before any
    persistence happens. The fetched rows are then bound via backtest.
    hypothesis_execution.build_execution_request() (reused wholesale),
    which independently re-verifies each hypothesis's own fingerprint —
    this function performs no identity verification of its own.

    Idempotent: calling this again with the same hypothesis_ids (any
    order) + the same research_code_commit + the same data_provider
    always resolves to the SAME mission_id and returns its EXISTING state
    (`created`: False) without re-verifying hypotheses, re-creating the
    family, or duplicating cells/bindings. A genuinely different commit or
    provider produces a genuinely different, coexisting Mission — never a
    collision, never an overwrite (the same guarantee Phase 3's
    ExecutionRequest already relies on for cell fingerprints).

    `data_snapshot_id` is accepted and persisted as an honestly-nullable
    field only — this function establishes no real data-versioning
    mechanism; it exists so that mechanism can be added later without a
    schema change (operator's own explicit Phase 4 note: research_code_
    commit alone is not sufficient for full reproducibility)."""
    # Local imports: keeps this orchestration module's own import graph
    # symmetric with backtest/matrix_orchestrator.py's precedent (storage
    # imported at module load time is fine too, but importing here keeps
    # this function's storage dependencies visible right next to their use).
    from storage import hypothesis_factory as storage_hypothesis_factory
    from storage import hypothesis_mission as storage_hypothesis_mission
    from storage import research_matrix as storage_research_matrix

    if not hypothesis_ids:
        raise HypothesisExecutionError(
            "record_mission: hypothesis_ids must be non-empty — a Mission is always built from an explicit, "
            "non-empty, human/caller-named list of hypothesis ids. This function accepts hypothesis ids "
            "exclusively; there is no orphan-execution path that accepts a raw symbol/engine/timeframe/"
            "risk_preset in its place."
        )

    mission_id = compute_mission_id(hypothesis_ids, research_code_commit, data_provider)

    existing_mission = storage_hypothesis_mission.get_mission(mission_id)
    if existing_mission is not None:
        return {
            "mission_id": mission_id,
            "family_id": existing_mission["family_id"],
            "created": False,
            "bindings": storage_hypothesis_mission.list_mission_bindings(mission_id),
        }

    hypothesis_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for hypothesis_id in hypothesis_ids:
        if hypothesis_id in seen_ids:
            continue  # idempotent within one call, matching build_execution_request()'s own dedup
        seen_ids.add(hypothesis_id)
        row = storage_hypothesis_factory.get_hypothesis(hypothesis_id)
        if row is None:
            raise HypothesisExecutionError(
                f"record_mission: unknown hypothesis_id {hypothesis_id!r} — refusing to create a Mission "
                f"referencing a hypothesis that does not exist in storage. Every Mission must be built "
                f"exclusively from real, already-proposed Hypothesis rows (see backtest.hypothesis_factory."
                f"generate_hypotheses() / storage.hypothesis_factory.record_hypotheses()); there is no "
                f"orphan-execution path here."
            )
        hypothesis_rows.append(row)

    request = build_execution_request(
        hypothesis_rows, research_code_commit=research_code_commit, data_provider=data_provider,
    )

    family_id = _mission_family_id(mission_id)
    if storage_research_matrix.get_family(family_id) is None:
        symbols = sorted({row["symbol"] for row in hypothesis_rows})
        storage_research_matrix.upsert_family(
            family_id, planned_n=len(request.cells), family_alpha=0.05, symbols_json=json.dumps(symbols),
        )
    storage_research_matrix.upsert_cells(
        list(request.cells), family_id, source_hypothesis_ids=request.source_hypothesis_ids_by_cell,
    )

    fingerprint_by_hypothesis = {row["hypothesis_id"]: row["matrix_cell_fingerprint"] for row in hypothesis_rows}
    bindings = [
        {
            "hypothesis_id": hypothesis_id,
            "hypothesis_fingerprint": fingerprint_by_hypothesis[hypothesis_id],
            "cell_id": cell_id,
        }
        for hypothesis_id, cell_id in request.cell_id_by_hypothesis.items()
    ]
    storage_hypothesis_mission.persist_mission(
        mission_id, family_id, bindings,
        research_code_commit=research_code_commit, data_snapshot_id=data_snapshot_id, created_by=created_by,
    )

    return {
        "mission_id": mission_id,
        "family_id": family_id,
        "created": True,
        # re-read from storage rather than returning the in-memory `bindings`
        # list built above -- keeps this branch's return shape identical to
        # the already-existing (`created`: False) branch above, which reads
        # the same rows back via list_mission_bindings().
        "bindings": storage_hypothesis_mission.list_mission_bindings(mission_id),
    }
