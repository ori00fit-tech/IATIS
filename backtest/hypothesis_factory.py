"""
backtest/hypothesis_factory.py
-------------------------------
Hypothesis Discovery Engine, Phase 2 — Hypothesis Factory. Pure functions
+ dataclasses only — no D1 access (see storage/hypothesis_factory.py), no
engine run, no evidence computation, no promotion.

NON-NEGOTIABLE (operator's own explicit Phase 2 guardrail): this module is
a DETERMINISTIC GENERATOR and nothing else. It never ranks, scores, or
selects a "best" combination; it never runs a backtest; it never reads or
writes evidence; it never promotes, approves, or converts anything. Every
Hypothesis this module produces says "this combination should be tested,"
never "this combination is the best, run it." Discovery != Evidence !=
Promotion — this module is Discovery only.

A Hypothesis here is a deterministically-generated, symbol-scoped,
PRE-REGISTERED testable claim about exactly one (symbol, engine,
engine_version, timeframe, risk_preset) combination — written down BEFORE
any result exists, matching CLAUDE.md rule 1's "pre-register before you
build" spirit for the research layer this phase operates in. It is NEVER
a hypothesis identity in the research/results/registry.json / HXXX sense
(see backtest/research_matrix.py's own identity chain docstring for that
namespace) — this module has no code path that could shortcut human
governance into creating, mutating, or promoting a real HXXX id. A
Hypothesis's own identifier (DISCOVERY-HYPOTHESIS-<fingerprint>)
deliberately shares its fingerprint suffix with the exact Matrix cell
(MATRIX-CELL-<fingerprint>) that would test it — the same identity-chain
convention this whole engine already uses (MATRIX-CELL- -> LEAD- -> HXXX),
extended one link earlier: DISCOVERY-HYPOTHESIS- (this module, the
pre-registered question) -> MATRIX-CELL- (backtest/research_matrix.py,
the fully-specified test of that question) -> ... -> HXXX (human/research
governance, never assigned here).

Reuses, never rebuilds: backtest.research_matrix.generate_discovery_cells()
(the exhaustive Symbol x Engine x EngineVersion x Timeframe enumeration
and all of its validation), single_engine_identity(), MatrixCellSpec's own
fingerprint. This module adds nothing to the Cartesian-product logic
itself — it only wraps each resulting cell into a named, claim-bearing
Hypothesis record.

Phase 8B (Confluence Governed Identity, 2026-08-24) — a SECOND, PARALLEL
kind of Hypothesis: `decision_type` is the real discriminator (never
inferred from `engine`'s string value):

    decision_type == SINGLE_ENGINE (default, unchanged historical shape)
        engine          -- a real engine name (backtesting.backtest_
                             engine.ENGINE_KEYS member)
        engine_version   -- that engine's own version

    decision_type == CONFLUENCE
        engine          -- literally the constant CONFLUENCE, never a
                             real engine name
        engine_version   -- the confluence DECISION MECHANISM's own
                             version (never any one engine's version)
        bundle_id        -- the exact bundle's own name (already part of
                             compute_cell_fingerprint()'s payload as
                             "bundle_name" — the canonical, fingerprinted
                             identity)
        bundle_version    -- provenance metadata ONLY. NOT independently
                             part of the cryptographic fingerprint (see
                             generate_confluence_hypotheses()'s own
                             docstring) — a caller who needs a version
                             bump to produce a genuinely new, non-
                             colliding identity must encode it into the
                             bundle's own `name`, exactly like changing
                             bundle content already does.
        bundle_json       -- the FULL bundle dict this hypothesis was
                             generated from, persisted verbatim. Required
                             for confluence re-verification (see
                             backtest.hypothesis_execution.build_
                             confluence_execution_request()'s own
                             docstring for why: unlike a single-engine
                             bundle, which generate_discovery_cells()
                             deterministically reconstructs from just
                             (engine, timeframe), a confluence bundle's
                             full composition — engines/indicators/
                             context_filters — is genuinely independent
                             information that cannot be re-derived from
                             scalar identity fields alone.)

generate_hypotheses()/single_engine_identity()/generate_discovery_cells()
are UNCHANGED — see generate_confluence_hypotheses() below for the new,
PARALLEL generator. Consumers must never assume `engine`/`engine_version`
mean "an engine" — decision_type says which shape applies.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backtest.research_matrix import (
    ResearchMatrixError,
    generate_discovery_cells,
    generate_matrix_cells,
    single_engine_identity,
)

SINGLE_ENGINE = "SINGLE_ENGINE"
CONFLUENCE = "CONFLUENCE"

# Deliberately generic and unconditional -- this template never states a
# specific numeric threshold, PF target, or verdict. The actual bar a
# combination must clear to be taken seriously as evidence is, and stays,
# entirely the job of the ALREADY-EXISTING Stage A screen / Bonferroni-
# corrected Matrix Family evidence gate / Stage B validation (backtest/
# research_matrix.py, backtest/matrix_orchestrator.py, backtest/
# mission_validator.py) -- never re-invented or duplicated here.
_CLAIM_TEMPLATE = (
    "Does {engine}:{engine_version} on {symbol} at {timeframe} (risk preset: "
    "{risk_preset}) provide an independent, repeatable, risk-adjusted edge? "
    "Answered ONLY by this hypothesis's own Matrix cell evidence (Stage A "
    "screen, Bonferroni-corrected Matrix Family gate, Stage B validation) — "
    "never by this factory, which proposes the question and nothing else."
)

# Phase 8B — same discipline as _CLAIM_TEMPLATE above: generic, no numeric
# thresholds, never a verdict. "the confluence decision mechanism" replaces
# a single engine's name; the bundle's own composition is the question.
_CONFLUENCE_CLAIM_TEMPLATE = (
    "Does the CONFLUENCE decision mechanism ({decision_version}) computed over "
    "bundle {bundle_id!r} on {symbol} at {timeframe} (risk preset: {risk_preset}) "
    "provide an independent, repeatable, risk-adjusted edge? Answered ONLY by "
    "this hypothesis's own Matrix cell evidence (Stage A screen, Bonferroni-"
    "corrected Matrix Family gate, Stage B validation) — never by this factory, "
    "which proposes the question and nothing else."
)


@dataclass(frozen=True)
class Hypothesis:
    """One pre-registered, symbol-scoped testable claim. NOT a hypothesis
    identity in the registry.json/HXXX sense (see module docstring) — a
    research artifact that exists purely so a (symbol, engine, engine_
    version, timeframe, risk_preset) combination is written down as a
    question BEFORE any test of it runs, never as an answer.

    Phase 8B: `decision_type` is the real discriminator between the two
    shapes this class now carries (see module docstring) — `bundle_id`/
    `bundle_version`/`bundle_json` are None for every SINGLE_ENGINE
    hypothesis (the historical, unchanged shape) and populated only for
    a CONFLUENCE one. All four new fields default to the SINGLE_ENGINE-
    compatible values, so generate_hypotheses()'s own existing
    construction call is completely unaffected by this widening."""

    symbol: str
    engine: str
    engine_version: str
    timeframe: str
    risk_preset: str
    claim: str
    matrix_cell_fingerprint: str
    decision_type: str = SINGLE_ENGINE
    bundle_id: str | None = None
    bundle_version: str | None = None
    bundle_json: str | None = None

    @property
    def hypothesis_id(self) -> str:
        prefix = "CONFLUENCE-HYPOTHESIS" if self.decision_type == CONFLUENCE else "DISCOVERY-HYPOTHESIS"
        return f"{prefix}-{self.matrix_cell_fingerprint}"


def generate_hypotheses(
    *,
    symbols: list[str],
    engines: list[str],
    timeframes: list[str],
    engine_versions: dict[str, tuple[str, ...]] | None = None,
    risk_presets: list[str] | None = None,
) -> list[Hypothesis]:
    """Deterministic Symbol x Engine x EngineVersion x Timeframe x
    RiskPreset hypothesis enumeration — exactly the same combinations
    backtest.research_matrix.generate_discovery_cells() would generate
    Matrix cells for (this function calls it directly, reusing its own
    validation and Cartesian-expansion logic verbatim), each wrapped into
    a named Hypothesis whose id shares its underlying cell's fingerprint.

    Deliberately does NOT vary confluence_overrides/data_provider/
    research_code_commit — a Hypothesis is a code/commit/provider-agnostic
    RESEARCH QUESTION about a combination, not a specific backtest run's
    provenance (that provenance is stamped onto the actual Matrix cell
    separately, at generation time, by the existing POST /research/
    matrix/generate path).

    No ranking, no scoring, no selection: the returned list is exactly the
    deterministic enumeration this function's own inputs describe, in
    generate_discovery_cells()'s own iteration order (symbol -> engine ->
    engine_version -> timeframe -> risk_preset) — never reordered by any
    property of the combination itself."""
    cells = generate_discovery_cells(
        symbols=symbols, engines=engines, timeframes=timeframes,
        engine_versions=engine_versions, risk_presets=risk_presets,
    )
    hypotheses: list[Hypothesis] = []
    for cell in cells:
        engine, engine_version, timeframe = single_engine_identity(cell.bundle, cell.engine_variants)
        # generate_discovery_cells() only ever produces single-engine,
        # single-timeframe bundles -- see its own docstring/tests.
        assert engine is not None and engine_version is not None and timeframe is not None
        hypotheses.append(Hypothesis(
            symbol=cell.symbol, engine=engine, engine_version=engine_version,
            timeframe=timeframe, risk_preset=cell.risk_preset,
            claim=_CLAIM_TEMPLATE.format(
                engine=engine, engine_version=engine_version, symbol=cell.symbol,
                timeframe=timeframe, risk_preset=cell.risk_preset,
            ),
            matrix_cell_fingerprint=cell.fingerprint,
        ))
    return hypotheses


def generate_confluence_hypotheses(
    *,
    symbols: list[str],
    decision_version: str,
    bundle: dict[str, Any],
    bundle_version: str | None = None,
    risk_presets: list[str] | None = None,
) -> list[Hypothesis]:
    """Phase 8B — the PARALLEL confluence counterpart to generate_
    hypotheses() above. generate_hypotheses()/generate_discovery_cells()/
    single_engine_identity() are completely UNCHANGED by this function's
    existence — this is an ADDITIVE extension, never a modification of
    the locked single-engine path.

    Reuses backtest.research_matrix.generate_matrix_cells() (UNCHANGED
    since before Phase 1, already fully confluence-bundle-capable) rather
    than generate_discovery_cells() (which validates against ENGINE_KEYS
    and would reject a "CONFLUENCE" engine name outright) — see this
    engine's own Phase 8B feasibility audit for why that's the correct,
    minimal reuse point.

    NO INFERENCE (operator's own explicit Phase 8B guardrail): `bundle`
    is a REQUIRED, fully-formed dict the caller must already have
    assembled and named — this function never reads config/engines.yaml,
    never queries the Policy Registry, and never constructs a bundle on
    its own. `decision_version` is a REQUIRED, non-empty string — never
    defaulted, never inferred from confluence/voting_system.py's current
    code state.

    Every engine named inside `bundle["engines"]` must be a real,
    registered engine identity (backtesting.backtest_engine.ENGINE_KEYS)
    — never an arbitrary string, and never "CONFLUENCE" itself (that
    name is reserved for the DECISION's own identity, never a bundle
    input). `bundle["timeframes"]` must name exactly one timeframe — a
    confluence hypothesis is still scoped to a single decision
    timeframe, matching Policy's own exact-5-field identity lookup.

    `bundle_id` is the bundle's own `name` — already part of compute_
    cell_fingerprint()'s payload ("bundle_name") via generate_matrix_
    cells(), so it is a REAL, cryptographically fingerprinted identity
    component, unchanged, reused as-is. `decision_version` is folded
    into the SAME fingerprint via generate_matrix_cells()'s existing
    `engine_variants_choices` parameter — `{CONFLUENCE: decision_
    version}` — the exact mechanism single-engine hypotheses already use
    to fingerprint engine_version, reused here rather than reinvented.

    `bundle_version`, in contrast, is provenance metadata ONLY — it is
    NOT independently part of the fingerprint (compute_cell_fingerprint()
    only reads specific known keys off the bundle dict: name/timeframes/
    engines/indicators/context_filters — an arbitrary extra key such as
    bundle["version"] would be silently invisible to it). A caller who
    needs a version bump to produce a genuinely new, non-colliding
    hypothesis identity must encode it into the bundle's own `name`
    (e.g. "Prod4 Confluence Panel v3") — this function does not do that
    automatically, so as never to create an undocumented, silently-
    assumed naming convention.

    Traceability without a fake registry: there is no existing concept
    of a "confluence mechanism version registry" anywhere in this
    codebase (confluence/voting_system.py carries no version tag of its
    own today), so this function cannot validate that a given
    decision_version is semantically compatible with a given bundle's
    intended mechanism — inventing such a registry now would be
    fabricating a mechanism that doesn't exist. What IS structurally
    guaranteed: decision_version and the bundle's full composition are
    BOTH part of the same cryptographic fingerprint, so any (bundle,
    decision_version) pairing is a genuinely distinct, forensically
    traceable identity — a wrong pairing is always detectable after the
    fact (a different fingerprint than a correct pairing would have
    produced), never silently misattributed. Resolving decision_version
    to an actual confluence/*.py code state (mirroring how research_code_
    commit is already resolved elsewhere in this engine) is real future
    hardening, not fabricated here.

    No ranking, no scoring, no selection — same guarantee as generate_
    hypotheses(): the returned list is exactly the deterministic
    enumeration of symbols x risk_presets this call's own inputs
    describe."""
    from backtesting.backtest_engine import ENGINE_KEYS

    if not decision_version or not decision_version.strip():
        raise ResearchMatrixError(
            "generate_confluence_hypotheses: decision_version must be a real, non-empty confluence "
            "decision-mechanism version — never defaulted, never inferred."
        )
    bundle_engines = bundle.get("engines") or []
    if not bundle_engines:
        raise ResearchMatrixError(
            "generate_confluence_hypotheses: bundle must name at least one real engine in 'engines' — an "
            "empty list is not a valid confluence bundle."
        )
    unknown_engines = sorted(set(bundle_engines) - set(ENGINE_KEYS))
    if unknown_engines:
        raise ResearchMatrixError(
            f"generate_confluence_hypotheses: unknown engine(s) in bundle {unknown_engines} — choose from "
            f"{list(ENGINE_KEYS)}. A confluence bundle's engines must each be a real, registered engine "
            f"identity, never an arbitrary string."
        )
    bundle_timeframes = bundle.get("timeframes") or []
    if len(bundle_timeframes) != 1:
        raise ResearchMatrixError(
            f"generate_confluence_hypotheses: bundle must specify exactly one timeframe, got "
            f"{bundle_timeframes!r} — a confluence hypothesis is still scoped to a single decision "
            f"timeframe, matching Policy's own exact-identity lookup."
        )
    timeframe = bundle_timeframes[0]
    bundle_id = bundle.get("name")
    if not bundle_id:
        raise ResearchMatrixError("generate_confluence_hypotheses: bundle must have a non-blank 'name' (becomes bundle_id).")

    cells = generate_matrix_cells(
        symbols=symbols, bundles=[bundle], risk_presets=risk_presets,
        engine_variants_choices=({CONFLUENCE: decision_version},),
    )
    bundle_json = json.dumps(bundle, sort_keys=True)
    hypotheses: list[Hypothesis] = []
    for cell in cells:
        hypotheses.append(Hypothesis(
            symbol=cell.symbol, engine=CONFLUENCE, engine_version=decision_version,
            timeframe=timeframe, risk_preset=cell.risk_preset,
            claim=_CONFLUENCE_CLAIM_TEMPLATE.format(
                decision_version=decision_version, bundle_id=bundle_id, symbol=cell.symbol,
                timeframe=timeframe, risk_preset=cell.risk_preset,
            ),
            matrix_cell_fingerprint=cell.fingerprint,
            decision_type=CONFLUENCE, bundle_id=bundle_id, bundle_version=bundle_version, bundle_json=bundle_json,
        ))
    return hypotheses
