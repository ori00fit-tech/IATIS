"""
backtest/research_matrix.py
------------------------------
Hypothesis Discovery Engine, Phase 1 (Matrix Engine + Scheduler + Evidence
Gate). Pure functions + dataclasses only — no D1 access (see
storage/research_matrix.py), no engine run, no mission/validation
execution (see backtest/matrix_orchestrator.py).

RESEARCH-ONLY, MEASUREMENT/ADVISORY LAYER — same guarantee as every other
Mission Center module this codebase already ships (backtest/mission_
runner.py, backtest/mission_validator.py, backtest/price_benchmark.py,
...). Nothing in this module, or anything built on top of it, may ever
write research/results/registry.json, config.yaml, config/engines.yaml,
or config/symbols.yaml. A VALIDATED matrix cell is a LEAD, never a
hypothesis: promoting it to a real HXXX id with falsification criteria
written before any result exists remains a manual, human-governed step
(CLAUDE.md rule 1) — this module has no code path that could shortcut it.

Identity chain (deliberately kept separate at every stage, per an
explicit operator requirement):

    MATRIX-CELL-<fingerprint>   (this module — a fully-specified,
                                  fingerprinted hypothesis COMBINATION,
                                  never itself a hypothesis identity)
            |
      Stage A trial               (backtest.mission_runner.run_mission,
                                    UNCHANGED, reused as-is)
            |
      LEAD-<mission>-<trial>-<symbol>   (backtest.lead_id.lead_id,
                                          UNCHANGED, reused as-is)
            |
      Stage B validation           (backtest.mission_validator.
                                     run_validation, UNCHANGED, reused
                                     as-is)
            |
      Evidence / verdict
            |
      Human / research governance
            |
      HXXX                          (never assigned by any code in this
                                      module or its orchestrator)

Reuses, never rebuilds: MissionSearchSpace, run_mission(), run_validation(),
backtest.multiple_testing (Bonferroni correction), backtest.runner's
dataset-completeness/fingerprint primitives, storage.research_missions,
storage.research_mission_validations. Rebuilding any of those would be an
architectural regression per the operator's own explicit instruction.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Cell lifecycle
# ---------------------------------------------------------------------------

# QUEUED -> RUNNING -> SCREENED -> CANDIDATE -> VALIDATING -> VALIDATED
#                          \-> INSUFFICIENT_DATA / REJECTED (documented reason)
#                                       \-> REJECTED (documented reason)
#                                                        \-> REJECTED (documented reason)
# Never QUEUED -> VALIDATED directly — every intermediate gate is mandatory
# and every rejection carries a real reason (never silently dropped).
#
# VALIDATING (Phase 3A, Matrix Operational Validation) — mirrors RUNNING's
# own role but for Stage B: storage.claim_candidate_cells() atomically
# transitions CANDIDATE -> VALIDATING via the same compare-and-set pattern
# claim_queued_cells() already used for QUEUED -> RUNNING. Without this, two
# concurrent run_batch() calls against the same family could both SELECT
# and both run Stage B (run_validation) for the SAME candidate cell — a
# real race this phase's multi-worker stress test surfaced. A stale
# VALIDATING cell (its process died mid-Stage-B) is requeued back to
# CANDIDATE (never QUEUED — Stage A already ran) by storage.requeue_stale_
# validating_cells(), skipping a redundant re-screen.
QUEUED = "QUEUED"
RUNNING = "RUNNING"
SCREENED = "SCREENED"
CANDIDATE = "CANDIDATE"
VALIDATING = "VALIDATING"
VALIDATED = "VALIDATED"
REJECTED = "REJECTED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
FAILED = "FAILED"

CELL_STATUSES: tuple[str, ...] = (
    QUEUED, RUNNING, SCREENED, CANDIDATE, VALIDATING, VALIDATED, REJECTED, INSUFFICIENT_DATA, FAILED,
)

# Statuses a cell may still transition out of. VALIDATED/REJECTED/
# INSUFFICIENT_DATA/FAILED are terminal for that cell's fixed fingerprint —
# a fingerprint change (any change to the hypothesis) always produces a
# brand-new cell rather than mutating a terminal one's result.
_NON_TERMINAL_STATUSES: tuple[str, ...] = (QUEUED, RUNNING, SCREENED, CANDIDATE, VALIDATING)

# ---------------------------------------------------------------------------
# Named, mission-wide-only risk presets (operator's condition #1). A
# hypothesis bundle may NEVER carry its own risk profile — the preset name
# is resolved here, mission-wide, and becomes part of every cell's
# fingerprint (condition #1's second half). Each preset is a single FIXED
# point (not a range) so Stage A's screen for one matrix cell is exactly
# one deterministic, reproducible backtest — never an Optuna sweep hiding
# behind one cell.
RISK_PRESETS: dict[str, dict[str, float]] = {
    "conservative": {
        "sl_atr_multiplier": 2.5, "min_rr": 2.5, "risk_per_trade": 0.005,
    },
    "balanced": {
        "sl_atr_multiplier": 2.0, "min_rr": 2.0, "risk_per_trade": 0.01,
    },
    "aggressive": {
        "sl_atr_multiplier": 1.5, "min_rr": 1.5, "risk_per_trade": 0.02,
    },
}

RISK_PRESET_NAMES: tuple[str, ...] = tuple(RISK_PRESETS.keys())


class ResearchMatrixError(ValueError):
    """Raised on any malformed matrix-cell specification. Fail loud —
    never silently coerce an invalid hypothesis into a runnable one."""


# Forensic Audit hardening (Finding 2) — resolves the ACTUAL research
# repository commit for the fingerprint's research_code_commit field.
# Previously this field existed on compute_cell_fingerprint()/
# MatrixCellSpec but nothing in the real generation path ever populated
# it, so every cell fingerprinted identically regardless of engine-code
# changes -- a real code change (an engine bugfix, an engine v2 swap)
# could silently dedupe against a stale-code cell's old result.
#
# Reuses research.manifest.git_state() verbatim (the same primitive
# mission_runner.py/mission_validator.py already fingerprint with) --
# never a second git-shelling-out implementation. git_state() itself
# never raises; it degrades to {"commit": "unknown", "dirty": True} when
# git is unavailable (e.g. no .git directory in some runtime). This
# function deliberately does NOT special-case that: "unknown" is baked
# into the fingerprint like any other real value -- deterministic, not
# fabricated, and every cell generated in such an environment correctly
# shares one code-identity bucket rather than silently pretending code
# identity doesn't matter. A dirty working tree also flows through
# (commit unchanged, but the caller can see `dirty` on request) so an
# uncommitted local change doesn't merge into the last real commit's
# identity -- see resolve_research_code_commit()'s return shape.
def resolve_research_code_commit() -> dict[str, Any]:
    """Returns {"commit": str, "dirty": bool} straight from git_state().
    `commit` is what actually goes into the fingerprint; `dirty` is
    exposed for callers that want to warn an operator their working tree
    has uncommitted changes (not itself part of the fingerprint -- an
    uncommitted change already changes the file content the engines
    import, but "commit" being the last real commit plus a `dirty` flag
    the caller can log/display is the same convention research/manifest.py
    already uses everywhere else in this codebase, not a new one)."""
    from research.manifest import git_state
    state = git_state()
    return {"commit": state.get("commit", "unknown"), "dirty": bool(state.get("dirty", True))}


def risk_preset_to_grid(preset_name: str) -> dict[str, tuple[float, ...]]:
    """MissionSearchSpace.risk_param_grid shape for a single fixed risk
    preset point — one value per param, so sampler='grid' + n_trials=1
    is fully deterministic. Raises ResearchMatrixError on an unknown
    preset name (never silently falls back to a default preset)."""
    if preset_name not in RISK_PRESETS:
        raise ResearchMatrixError(
            f"unknown risk_preset {preset_name!r} — choose from {RISK_PRESET_NAMES}"
        )
    return {k: (v,) for k, v in RISK_PRESETS[preset_name].items()}


# ---------------------------------------------------------------------------
# Matrix cell specification + fingerprinting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatrixCellSpec:
    """One fully-specified, fingerprinted hypothesis combination. NOT a
    hypothesis identity (see this module's own header) — a research
    artifact tracked purely so the Matrix Engine can dedupe, resume, and
    apply evidence gates across thousands of candidates without ever
    re-running (or silently reusing a stale result for) a combination
    that has genuinely changed."""

    symbol: str
    bundle: dict[str, Any]  # hypothesis-bundle dict: name/timeframes/engines/indicators/context_filters
    risk_preset: str
    confluence_overrides: dict[str, float] | None = None
    engine_variants: dict[str, str] | None = None
    data_provider: str | None = None
    research_code_commit: str | None = None

    @property
    def fingerprint(self) -> str:
        return compute_cell_fingerprint(
            symbol=self.symbol, bundle=self.bundle, risk_preset=self.risk_preset,
            confluence_overrides=self.confluence_overrides, engine_variants=self.engine_variants,
            data_provider=self.data_provider, research_code_commit=self.research_code_commit,
        )

    @property
    def cell_id(self) -> str:
        return f"MATRIX-CELL-{self.fingerprint}"


def _canonicalize(value: Any) -> Any:
    """Deep, order-independent canonical form for fingerprinting: dict
    keys sorted, list-of-dicts sorted by their own canonical JSON so two
    logically-identical bundles built in different iteration order always
    fingerprint identically."""
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        items = [_canonicalize(v) for v in value]
        try:
            return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, default=str))
        except TypeError:
            return items
    return value


def compute_cell_fingerprint(
    *,
    symbol: str,
    bundle: dict[str, Any],
    risk_preset: str,
    confluence_overrides: dict[str, float] | None,
    engine_variants: dict[str, str] | None,
    data_provider: str | None,
    research_code_commit: str | None,
) -> str:
    """Deterministic identity for one matrix cell (operator's condition
    #2). Includes every dimension the hypothesis actually varies on:
    symbol, bundle (which already carries timeframes/engines/indicators/
    context_filters — direction and regime live inside context_filters,
    not as separate top-level fields), risk_preset, confluence overrides,
    engine variant selection, and data/provider + research-code identity.
    Any change to ANY of these produces a different hash, hence a
    different MATRIX-CELL-<fingerprint> — a changed hypothesis is always a
    NEW cell, never a silent reuse of an old cell's result."""
    payload = {
        "symbol": symbol,
        "bundle_name": bundle.get("name"),
        "timeframes": _canonicalize(bundle.get("timeframes", [])),
        "engines": _canonicalize(bundle.get("engines", [])),
        "indicators": _canonicalize(bundle.get("indicators", [])),
        "context_filters": _canonicalize(bundle.get("context_filters", [])),
        "risk_preset": risk_preset,
        "confluence_overrides": _canonicalize(confluence_overrides or {}),
        "engine_variants": _canonicalize(engine_variants or {}),
        "data_provider": data_provider,
        "research_code_commit": research_code_commit,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _validate_bundle(bundle: dict[str, Any]) -> None:
    if not isinstance(bundle, dict) or not bundle.get("name"):
        raise ResearchMatrixError(f"hypothesis bundle must be a dict with a non-blank 'name': {bundle!r}")


def generate_matrix_cells(
    *,
    symbols: list[str],
    bundles: list[dict[str, Any]],
    risk_presets: list[str] | None = None,
    confluence_overrides_choices: tuple[dict[str, float] | None, ...] = (None,),
    engine_variants_choices: tuple[dict[str, str] | None, ...] = (None,),
    data_provider: str | None = None,
    research_code_commit: str | None = None,
) -> list[MatrixCellSpec]:
    """Cartesian product of symbol x bundle x risk_preset x confluence
    override choice x engine variant choice, each becoming exactly one
    MatrixCellSpec. Pure — no D1, no dedup against already-persisted
    cells (that's storage.research_matrix.upsert_cells' job, keyed off
    each cell's own deterministic fingerprint)."""
    if not symbols:
        raise ResearchMatrixError("symbols must be non-empty")
    if not bundles:
        raise ResearchMatrixError("bundles must be non-empty")
    for bundle in bundles:
        _validate_bundle(bundle)
    presets = risk_presets or list(RISK_PRESET_NAMES)
    for preset in presets:
        if preset not in RISK_PRESETS:
            raise ResearchMatrixError(f"unknown risk_preset {preset!r} — choose from {RISK_PRESET_NAMES}")

    cells: list[MatrixCellSpec] = []
    for symbol in symbols:
        for bundle in bundles:
            for preset in presets:
                for confluence in confluence_overrides_choices:
                    for variants in engine_variants_choices:
                        cells.append(MatrixCellSpec(
                            symbol=symbol, bundle=bundle, risk_preset=preset,
                            confluence_overrides=confluence, engine_variants=variants,
                            data_provider=data_provider, research_code_commit=research_code_commit,
                        ))
    return cells


# ---------------------------------------------------------------------------
# Phase 1 — Research Matrix Normalization: ENGINE and TIMEFRAME as explicit,
# independent Cartesian axes (2026-08-23). generate_matrix_cells() above is
# UNCHANGED and remains the right tool for multi-engine confluence-research
# bundles (a bundle may legitimately combine several engines/timeframes to
# test THAT combination together — that is a different, still-valid
# research question). generate_discovery_cells() below is a NEW, PARALLEL
# generator for the different question this phase exists to answer:
# "does THIS single engine, on THIS single timeframe, have an independent,
# repeatable edge for THIS symbol" — never inferring an edge itself (see
# backtest/matrix_evidence.py's own NON-NEGOTIABLE rule), only producing
# the exhaustive set of cells whose evidence, once measured, could answer
# that question per (symbol, engine, timeframe) combination.
# ---------------------------------------------------------------------------


def single_engine_identity(
    bundle: dict[str, Any], engine_variants: dict[str, str] | None,
) -> tuple[str | None, str | None, str | None]:
    """If `bundle` unambiguously represents exactly ONE engine and exactly
    ONE timeframe, returns (engine, engine_version, timeframe) — the
    denormalized identity storage.research_matrix.upsert_cells() persists
    as its own indexed columns (Phase 1). Returns (None, None, None) for
    any bundle combining multiple engines and/or multiple timeframes
    (confluence-research bundles): such a cell genuinely has no single
    scalar engine/timeframe, so it is never coerced into one — its
    identity stays expressed only inside bundle_json, exactly as it was
    before this function existed. This is why backward compatibility
    holds by construction: applying this function to every PRE-EXISTING
    cell's own (already-immutable) bundle either correctly reveals an
    identity that was always implicitly true (a bundle that already
    happened to be single-engine/single-timeframe), or correctly yields
    None for one that genuinely isn't — never a reinterpretation of what
    the bundle actually specifies.

    engine_version defaults to "v1" when `engine_variants` doesn't name
    this engine — the same "omitted key = v1" convention
    compute_cell_fingerprint()/the Matrix Engine frontend already use."""
    engines = bundle.get("engines") or []
    timeframes = bundle.get("timeframes") or []
    if len(engines) != 1 or len(timeframes) != 1:
        return None, None, None
    engine = engines[0]
    timeframe = timeframes[0]
    engine_version = (engine_variants or {}).get(engine) or "v1"
    return engine, engine_version, timeframe


def generate_discovery_cells(
    *,
    symbols: list[str],
    engines: list[str],
    timeframes: list[str],
    engine_versions: dict[str, tuple[str, ...]] | None = None,
    risk_presets: list[str] | None = None,
    confluence_overrides_choices: tuple[dict[str, float] | None, ...] = (None,),
    data_provider: str | None = None,
    research_code_commit: str | None = None,
) -> list[MatrixCellSpec]:
    """Exhaustive Symbol x Engine x EngineVersion x Timeframe discovery
    matrix. Every cell is a genuinely single-engine, single-timeframe
    bundle — ENGINE and TIMEFRAME are independent, explicit axes here,
    never bundled opaquely, so a later evidence breakdown can cleanly
    answer "is the engine the reason it worked, or the timeframe, or only
    that exact combination" (backtest/matrix_evidence.py's new
    per_engine_stats/per_timeframe_stats/per_symbol_engine_stats/
    per_symbol_timeframe_stats/per_symbol_engine_timeframe_stats).

    Fingerprint-COMPATIBLE with generate_matrix_cells(): a cell this
    function produces for (symbol, engine, version, timeframe) fingerprints
    IDENTICALLY to a hand-constructed single-engine bundle passed through
    generate_matrix_cells() for the same combination — both flow through
    the exact same compute_cell_fingerprint()/MatrixCellSpec, and the
    bundle/engine_variants shape built here matches the Matrix Engine
    frontend's own established convention exactly (an engine absent from
    backtesting.backtest_engine.ENGINE_VARIANT_KEYS never appears as an
    engine_variants key). Old and new generation paths therefore dedupe
    against each other's evidence for the same logical hypothesis rather
    than silently duplicating it.

    Deliberately does NOT infer, rank, or select an edge — this function
    only enumerates cells; every cell starts QUEUED like any other, and
    whether one is ever promoted still runs through the exact same
    Stage A screen / Bonferroni-corrected Matrix Family evidence gate /
    Stage B validation this engine already has (never rebuilt here).

    engine_versions: optional {engine: (version, ...)} restricting which
    variant(s) of a variant-bearing engine to enumerate. An engine absent
    from this dict — or from ENGINE_VARIANT_KEYS entirely — defaults to
    EVERY real variant ENGINE_VARIANT_KEYS lists for it (or just ("v1",)
    if it has none): exhaustive by default, since narrowing the search is
    the caller's explicit choice to make, not this function's to assume.

    Symbols/engines/timeframes/per-engine versions are each deduplicated
    (order-preserving) before the Cartesian expansion, so a caller
    accidentally passing a repeated entry can never produce two cells for
    the same (symbol, engine, version, timeframe) identity tuple — the
    risk_preset/confluence_overrides axes still vary independently on top
    of that identity, exactly as generate_matrix_cells() already does,
    since a different risk preset is a genuinely different research
    question, not a duplicate of the same one."""
    from backtesting.backtest_engine import ENGINE_KEYS, ENGINE_VARIANT_KEYS

    if not symbols:
        raise ResearchMatrixError("symbols must be non-empty")
    if not engines:
        raise ResearchMatrixError("engines must be non-empty")
    if not timeframes:
        raise ResearchMatrixError("timeframes must be non-empty")
    unknown_engines = sorted(set(engines) - set(ENGINE_KEYS))
    if unknown_engines:
        raise ResearchMatrixError(f"unknown engine(s) {unknown_engines} — choose from {list(ENGINE_KEYS)}")
    presets = risk_presets or list(RISK_PRESET_NAMES)
    for preset in presets:
        if preset not in RISK_PRESETS:
            raise ResearchMatrixError(f"unknown risk_preset {preset!r} — choose from {RISK_PRESET_NAMES}")

    symbols = list(dict.fromkeys(symbols))
    engines = list(dict.fromkeys(engines))
    timeframes = list(dict.fromkeys(timeframes))

    versions_by_engine: dict[str, tuple[str, ...]] = {}
    for engine in engines:
        real_variants = ENGINE_VARIANT_KEYS.get(engine, ("v1",))
        requested = tuple(dict.fromkeys((engine_versions or {}).get(engine, real_variants)))
        unknown_versions = sorted(set(requested) - set(real_variants))
        if unknown_versions:
            raise ResearchMatrixError(
                f"engine {engine!r} has no variant(s) {unknown_versions} — choose from {list(real_variants)}"
            )
        versions_by_engine[engine] = requested

    cells: list[MatrixCellSpec] = []
    for symbol in symbols:
        for engine in engines:
            for version in versions_by_engine[engine]:
                engine_variants = {engine: version} if engine in ENGINE_VARIANT_KEYS else None
                for timeframe in timeframes:
                    bundle = {
                        "name": f"{engine}:{version} @ {timeframe}",
                        "timeframes": [timeframe], "engines": [engine],
                        "indicators": [], "context_filters": [],
                    }
                    for preset in presets:
                        for confluence in confluence_overrides_choices:
                            cells.append(MatrixCellSpec(
                                symbol=symbol, bundle=bundle, risk_preset=preset,
                                confluence_overrides=confluence, engine_variants=engine_variants,
                                data_provider=data_provider, research_code_commit=research_code_commit,
                            ))
    return cells


# ---------------------------------------------------------------------------
# Data quality gate — reused primitives only (core.data_validator,
# backtest.runner.build_dataset_descriptor), never a second implementation.
# Insufficient/invalid data must classify as INSUFFICIENT_DATA, never be
# silently converted into a real (bad) trading result.
# ---------------------------------------------------------------------------

MIN_ROWS_FOR_STAGE_A = 210  # matches backtest_engine's own NNFX warmup-bar floor elsewhere in this codebase
MIN_COMPLETENESS_PCT_FOR_STAGE_A = 80.0


@dataclass(frozen=True)
class DataQualityResult:
    ok: bool
    reason: str | None
    dataset_descriptor: dict[str, Any] | None
    dataset_fingerprint: str | None


def check_data_quality(
    symbol: str, timeframe: str, data_dir: Path, start: str | None, end: str | None,
    *, min_rows: int = MIN_ROWS_FOR_STAGE_A, min_completeness_pct: float = MIN_COMPLETENESS_PCT_FOR_STAGE_A,
) -> DataQualityResult:
    """Pre-flight gate run before Stage A even attempts a backtest.
    Reuses backtest.runner.find_symbol_csv/load_symbol_data/
    build_dataset_descriptor and core.data_validator.validate_ohlcv —
    zero new data-quality logic, only a threshold decision layered on
    top of what those already-tested functions compute. Never raises —
    every failure mode (missing file, unreadable data, structurally
    invalid OHLC, too few rows, incomplete coverage) degrades to a
    DataQualityResult(ok=False, reason=...) instead of propagating an
    exception into the orchestrator's batch loop."""
    from core.data_validator import validate_ohlcv
    from backtest.runner import build_dataset_descriptor, find_symbol_csv, load_symbol_data

    try:
        csv_path = find_symbol_csv(symbol, data_dir, timeframe=timeframe)
    except Exception as exc:  # noqa: BLE001 — any lookup failure is a data-quality verdict, not a crash
        return DataQualityResult(ok=False, reason=f"no dataset found: {exc}", dataset_descriptor=None, dataset_fingerprint=None)

    try:
        df = load_symbol_data(symbol, data_dir, start=start, end=end, timeframe=timeframe)
    except Exception as exc:  # noqa: BLE001
        return DataQualityResult(ok=False, reason=f"dataset could not be loaded: {exc}", dataset_descriptor=None, dataset_fingerprint=None)

    if len(df) < min_rows:
        return DataQualityResult(
            ok=False, reason=f"only {len(df)} rows, below the {min_rows}-row minimum for a Stage A backtest",
            dataset_descriptor=None, dataset_fingerprint=None,
        )

    try:
        validate_ohlcv(df)
    except Exception as exc:  # noqa: BLE001 — a structurally invalid dataset is INSUFFICIENT_DATA, not a crash
        return DataQualityResult(ok=False, reason=f"failed OHLCV structural validation: {exc}", dataset_descriptor=None, dataset_fingerprint=None)

    descriptor = build_dataset_descriptor(df, symbol, timeframe, csv_path)
    if descriptor["completeness_pct"] < min_completeness_pct:
        return DataQualityResult(
            ok=False,
            reason=f"dataset completeness {descriptor['completeness_pct']}% below the {min_completeness_pct}% minimum",
            dataset_descriptor=descriptor, dataset_fingerprint=descriptor["dataset_fingerprint"],
        )

    return DataQualityResult(ok=True, reason=None, dataset_descriptor=descriptor, dataset_fingerprint=descriptor["dataset_fingerprint"])


# ---------------------------------------------------------------------------
# Stage A screening — a cheap, generous sanity bar. Deliberately NOT the
# statistical-significance bar (that's matrix-wide Bonferroni correction,
# applied separately by the orchestrator across the whole SCREENED family
# — condition #3: mission-level correction never substitutes for it, and
# neither does this cheap screen).
# ---------------------------------------------------------------------------

STAGE_A_MIN_TRADES = 20
STAGE_A_MIN_PF = 1.0

# Forensic Audit hardening (Finding 5) — the deterministic terminal reason
# a cell gets when backtest.multiple_testing.trial_p_value() returns None
# because its trial's std_rr == 0 (every closed trade produced an
# identical R-multiple -- statistical significance is mathematically
# undefined, not merely small). Given a cell only reaches this check
# after already clearing STAGE_A_MIN_TRADES (>=20 >= trial_p_value's own
# n<2 floor), std_rr==0 is the ONLY remaining reason trial_p_value can
# return None here -- this reason code is never a guess.
REASON_ZERO_R_MULTIPLE_VARIANCE = (
    "ZERO_R_MULTIPLE_VARIANCE: all closed trades in this trial produced an "
    "identical R-multiple, so trial_p_value() is mathematically undefined "
    "(std_r == 0). This cell cannot be scored for statistical significance "
    "and is excluded from matrix-wide correction."
)


@dataclass(frozen=True)
class StageAScreenResult:
    passed: bool
    reason: str | None


def screen_stage_a(metrics: dict[str, Any] | None, trades: int) -> StageAScreenResult:
    """Cheap, documented pass/fail on ONE recorded Stage A trial's own
    metrics. Never a statistical-significance claim — purely "did this
    even trade enough, on the right side of breakeven, to be worth
    matrix-wide correction at all." Every rejection carries a real,
    specific reason (operator's condition #4: undocumented rejections are
    not allowed at any gate)."""
    if trades < STAGE_A_MIN_TRADES:
        return StageAScreenResult(False, f"only {trades} trade(s), below the Stage A minimum of {STAGE_A_MIN_TRADES}")
    if metrics is None:
        return StageAScreenResult(False, "no metrics recorded for this trial")
    pf = metrics.get("profit_factor")
    if pf is None:
        return StageAScreenResult(False, "no profit_factor recorded for this trial")
    if isinstance(pf, str):  # json_safe() sentinel for +/-inf/nan
        if pf == "Infinity":
            pf = float("inf")
        elif pf in ("-Infinity", "NaN"):
            return StageAScreenResult(False, f"profit_factor is {pf}, not a usable screening value")
    if pf < STAGE_A_MIN_PF:
        return StageAScreenResult(False, f"profit_factor {pf} below the Stage A minimum of {STAGE_A_MIN_PF}")
    return StageAScreenResult(True, None)
