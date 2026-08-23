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
"""
from __future__ import annotations

from dataclasses import dataclass

from backtest.research_matrix import generate_discovery_cells, single_engine_identity

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


@dataclass(frozen=True)
class Hypothesis:
    """One pre-registered, symbol-scoped testable claim. NOT a hypothesis
    identity in the registry.json/HXXX sense (see module docstring) — a
    research artifact that exists purely so a (symbol, engine, engine_
    version, timeframe, risk_preset) combination is written down as a
    question BEFORE any test of it runs, never as an answer."""

    symbol: str
    engine: str
    engine_version: str
    timeframe: str
    risk_preset: str
    claim: str
    matrix_cell_fingerprint: str

    @property
    def hypothesis_id(self) -> str:
        return f"DISCOVERY-HYPOTHESIS-{self.matrix_cell_fingerprint}"


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
