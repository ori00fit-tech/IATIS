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

# QUEUED -> RUNNING -> SCREENED -> CANDIDATE -> VALIDATED
#                          \-> INSUFFICIENT_DATA / REJECTED (documented reason)
#                                       \-> REJECTED (documented reason)
# Never QUEUED -> VALIDATED directly — every intermediate gate is mandatory
# and every rejection carries a real reason (never silently dropped).
QUEUED = "QUEUED"
RUNNING = "RUNNING"
SCREENED = "SCREENED"
CANDIDATE = "CANDIDATE"
VALIDATED = "VALIDATED"
REJECTED = "REJECTED"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
FAILED = "FAILED"

CELL_STATUSES: tuple[str, ...] = (
    QUEUED, RUNNING, SCREENED, CANDIDATE, VALIDATED, REJECTED, INSUFFICIENT_DATA, FAILED,
)

# Statuses a cell may still transition out of. VALIDATED/REJECTED/
# INSUFFICIENT_DATA/FAILED are terminal for that cell's fixed fingerprint —
# a fingerprint change (any change to the hypothesis) always produces a
# brand-new cell rather than mutating a terminal one's result.
_NON_TERMINAL_STATUSES: tuple[str, ...] = (QUEUED, RUNNING, SCREENED, CANDIDATE)

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
