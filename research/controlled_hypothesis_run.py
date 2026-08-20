"""
research/controlled_hypothesis_run.py
-----------------------------------------
Slice 5 (Hypothesis Contract Enforcement, 2026-08-19) -- the canonical,
hard-enforced entry point for the upcoming H001-H020 controlled rerun.

AUDIT CONCLUSION (see the Slice 5 commit message for the full 9-dimension
comparison table). Neither existing execution path is, by itself, correct
for this purpose:

  - research/experiments/H0*.py (bespoke, e.g. H101_smc_structural_bias_
    ab.py) already implements each hypothesis's own, UNIQUE, pre-
    registered decision rule (e.g. H101's smc_verdict(): "reject if mean
    dPF is not positive with win_fraction>=0.60"). CLAUDE.md rule 1
    requires THAT exact rule to be applied -- so this is the ONLY place a
    controlled rerun's verdict logic may correctly live. But Slice 4's
    audit confirmed it lacks: dataset-completeness gating, explicit OOS
    date boundaries, hypothesis-definition versioning, and any
    multiple-testing binding.
  - Mission Center (backtest/mission_runner.py + mission_validator.py)
    has all of those (Slices 1-3) plus deeper diagnostics (walk-forward/
    Monte Carlo/robustness/regime-robustness/stability/cost-stress). But
    its VALIDATION_CRITERIA (backtest/mission_validator.py) is ONE fixed,
    generic bar (PF>=1.25, trades>=300, ...) -- routing H101 through it
    would silently REPLACE H101's own pre-registered rule with a
    different one. That is a real rule-1 violation, not a convenience
    trade-off, so Mission Center's generic validator is disqualified as
    the verdict authority for a pre-registered hypothesis.

So the canonical path is neither of the above alone: it is a thin
ORCHESTRATOR wrapping the hypothesis's OWN execute callback (unmodified)
with the mandatory pre-flight contract gate, reusing already-existing,
already-shared, non-Mission-Center-exclusive primitives for every
non-hypothesis-specific step:
  - core.data_validator.validate_ohlcv()         (dataset structural integrity)
  - backtest.runner.build_dataset_descriptor()   (completeness + provider
    auto-resolution + a dataset_fingerprint that carries bar count/date
    range -- reused verbatim, already used by write_summary/walk_forward's
    /robustness's suite writers)
  - research.hypothesis_contract's own build_hypothesis_contract() /
    compute_execution_identity() / validate_for_new_run()
      (unmodified except one Slice-5-justified additive fix in
      hypothesis_contract.py itself: build_hypothesis_contract() gained
      an optional dataset_frames kwarg so its own dataset_fingerprints
      finally carry bar count/date range too -- closing, inside the
      contract Slice 4 shipped, the exact gap Slice 4's own audit found)
  - research.manifest.build_manifest()/write_manifest()  (persistence --
    reused as-is, never a second file format)

Nothing in this module re-implements engine logic, statistical
methodology, or optimizer sampling. Nothing here writes registry.json,
config.yaml, or config/engines.yaml.

Slice 6 (Close Controlled-Run Bypasses, 2026-08-19) closed the bypass
disclosed above. Every research/experiments/H0*.py entry point capable of
producing a NEW controlled result (15 of them -- 9 `main()`-shaped CLI
scripts plus 6 legacy `run_experiment()`-shaped callables with no CLI at
all, see the Slice 6 commit message for the full per-script audit table)
now calls require_controlled_execution(hypothesis_id) as its first
executable statement (after argument parsing, before any dataset
load/backtest/verdict computation). This is a FAIL-CLOSED guard, not a
routing change: none of those 13 scripts is retrofitted into a real
execute_fn adapter in this slice (that would mean actually running a
hypothesis, which is explicitly out of scope until after H001-H020's own
final review) -- calling one directly now raises DirectExecutionRefused
before it touches data, produces no result/manifest artifact, and its
message points back at this module. A script only stops refusing once
something calls it from *inside* run_controlled_hypothesis()'s own
execute_fn callback (see require_controlled_execution()'s docstring for
the exact mechanism) -- that is the one, deliberately narrow, door this
slice leaves open, and it is the SAME orchestrator every other controlled
run must already go through, not a second path.
"""
from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from backtest.runner import build_dataset_descriptor
from core.data_validator import DataValidationError, validate_ohlcv
from research.hypothesis_contract import (
    HypothesisContractError,
    build_hypothesis_contract,
    compute_execution_identity,
    resolve_hypothesis_definition_path,
    validate_for_new_run,
)


class ControlledRunAborted(Exception):
    """Raised (never silently swallowed, never a bare Exception) when the
    pre-flight contract gate rejects a controlled run. execute_fn is
    NEVER called and no evidence/result is ever produced when this is
    raised -- see run_controlled_hypothesis()'s own source for the single
    call site of execute_fn, positioned after every check below.

    `stage` names which of the mandatory 10 steps rejected the run
    (machine-readable, not just a prose message) -- one of
    "missing_dataset", "structural_validation", "contract_validation"."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class DirectExecutionRefused(Exception):
    """Raised by require_controlled_execution() (Slice 6) when a
    hypothesis script's own controlled-result-producing entry point is
    invoked directly -- e.g. `python -m research.experiments.
    H101_smc_structural_bias_ab` or calling `run_experiment(...)` from a
    driver script/REPL -- rather than through run_controlled_hypothesis().
    Never caught and silently downgraded anywhere in this codebase: a
    refusal here must always surface, since silently swallowing it would
    let a bypassed run appear to have "worked"."""


# Slice 6: a plain module-level flag would leak across threads/async
# tasks running different hypotheses concurrently (e.g. two Mission
# Center jobs); a contextvar scopes "am I currently inside THIS
# hypothesis_id's orchestrated execute_fn call" correctly per call stack.
_active_hypothesis_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "controlled_hypothesis_run_active_hypothesis_id", default=None,
)


def require_controlled_execution(hypothesis_id: str) -> None:
    """The Slice 6 bypass-closing guard. Call this as the FIRST executable
    statement of any research/experiments/H0*.py function that can
    produce a NEW controlled result (a `main()` CLI entry point, or a
    legacy `run_experiment()` callable with no CLI at all) -- before any
    dataset load, backtest, or verdict computation. Deliberately a single
    function call, never a copy-pasted pre-flight block, so no script
    duplicates this module's own validation logic (Slice 6 requirement:
    "Do NOT duplicate the pre-flight implementation into every script").

    Raises DirectExecutionRefused unless the CURRENT call stack is inside
    run_controlled_hypothesis()'s own execute_fn(contract) call for this
    EXACT hypothesis_id (checked by identity, not just "some hypothesis is
    running" -- H101's guard does not pass just because H102 is mid-run
    on another thread). This is fail-closed by construction: the
    contextvar defaults to None, so an entry point reached by any path
    other than run_controlled_hypothesis() itself -- direct CLI
    invocation, a bare Python import, a stray subprocess call -- always
    refuses, with zero special-casing needed per script.

    Read-only/historical operations (e.g. H103's own `--manifest-only`,
    which backfills a manifest from an ALREADY-existing result.json
    without recomputing a verdict) must call this AFTER their own early
    return, not before -- they read history, they don't produce a new
    controlled result, so they stay reachable directly (Slice 6
    requirement: "Legacy historical results remain readable")."""
    active = _active_hypothesis_id.get()
    if active != hypothesis_id:
        raise DirectExecutionRefused(
            f"{hypothesis_id}: direct execution refused. This entry point produces "
            f"a NEW controlled result and must be invoked through "
            f"research.controlled_hypothesis_run.run_controlled_hypothesis() as its "
            f"execute_fn, not directly via a CLI invocation, driver script, or bare "
            f"import. See research/controlled_hypothesis_run.py."
        )


def run_controlled_hypothesis(
    *,
    hypothesis_id: str,
    symbol: str,
    timeframe: str,
    dataset_paths: list[Path],
    datasets: dict[str, pd.DataFrame],
    train_period: dict[str, str | None],
    oos_period: dict[str, str | None],
    engine_config: dict[str, Any],
    risk_config: dict[str, Any],
    execute_fn: Callable[[dict[str, Any]], dict[str, Any]],
    hypothesis_definition_path: Path | None = None,
    provider: str | None = None,
    direction: str | None = None,
    regime: str | None = None,
    sampler: str | None = None,
    seed: int | None = None,
    trial_family_size: int = 1,
    config: dict[str, Any] | None = None,
    write_manifest_as: str | None = None,
) -> dict[str, Any]:
    """The mandatory 10-step pipeline. Steps 1-9 (resolve definition,
    resolve/validate/complete each dataset, build the contract, validate
    it) all happen BEFORE step 10 (execute_fn(contract) -- the
    hypothesis's own, unmodified backtest/decision-rule logic). Raises
    ControlledRunAborted on ANY pre-flight failure; execute_fn is
    unreachable in that case (see source: it is called exactly once, on
    the line immediately after validate_for_new_run() returns
    successfully -- no other call site exists in this module).

    datasets: {str(path): DataFrame} -- ALREADY LOADED by the caller.
    This module never re-implements CSV loading (every H0XX script
    already has its own, e.g. core.data_loader.load_from_csv) -- it only
    validates and fingerprints what the caller loaded.

    write_manifest_as: when given, persists the contract + the returned
    execution_identity alongside the result via the EXISTING research.
    manifest.build_manifest()/write_manifest() -- the same *_manifest.json
    convention 26 other call sites already use, never a second format.
    Omitted (default) performs no I/O -- useful for tests/dry runs.

    Returns {"contract": dict, "execution_identity": str, "result": dict}
    -- execution_identity is computed from the SAME contract dict
    execute_fn itself received (Requirement: "execution result contains
    the exact execution_identity used before execution").
    """
    definition_path = hypothesis_definition_path or resolve_hypothesis_definition_path(hypothesis_id)

    dataset_frames: dict[str, pd.DataFrame] = {}
    completeness_pcts: list[float] = []
    resolved_provider = provider

    for path in dataset_paths:
        df = datasets.get(str(path))
        if df is None:
            raise ControlledRunAborted(
                f"{hypothesis_id}: no loaded DataFrame supplied for dataset {path} -- "
                f"refusing to execute without a validated dataset.",
                stage="missing_dataset",
            )
        try:
            validate_ohlcv(df)
        except DataValidationError as exc:
            raise ControlledRunAborted(
                f"{hypothesis_id}: dataset {path} failed OHLCV structural validation: {exc}",
                stage="structural_validation",
            ) from exc
        dataset_frames[str(path)] = df
        descriptor = build_dataset_descriptor(df, symbol, timeframe, path, config=config)
        completeness_pcts.append(descriptor["completeness_pct"])
        if resolved_provider is None:
            resolved_provider = descriptor["provider"]

    contract = build_hypothesis_contract(
        hypothesis_id=hypothesis_id,
        hypothesis_definition_path=definition_path,
        symbol=symbol, timeframe=timeframe,
        dataset_paths=dataset_paths, dataset_frames=dataset_frames,
        train_period=train_period, oos_period=oos_period,
        engine_config=engine_config, risk_config=risk_config,
        trial_family_size=trial_family_size,
        dataset_completeness_pct=min(completeness_pcts) if completeness_pcts else None,
        provider=resolved_provider, direction=direction, regime=regime,
        sampler=sampler, seed=seed,
    )

    try:
        validate_for_new_run(contract)
    except HypothesisContractError as exc:
        raise ControlledRunAborted(
            f"{hypothesis_id}: contract validation failed: {exc}", stage="contract_validation",
        ) from exc

    execution_identity = compute_execution_identity(contract)

    # Slice 6: the ONE place _active_hypothesis_id is ever set -- every
    # require_controlled_execution() call anywhere in the codebase can
    # only pass while execute_fn(contract) is on the stack, right here,
    # for THIS hypothesis_id. reset() (not a bare set to None) restores
    # whatever the caller's own context held before this call, so nested/
    # sequential run_controlled_hypothesis() invocations on different
    # hypotheses never leak into each other.
    token = _active_hypothesis_id.set(hypothesis_id)
    try:
        result = execute_fn(contract)
    finally:
        _active_hypothesis_id.reset(token)

    if write_manifest_as is not None:
        from research.manifest import build_manifest, write_manifest
        from utils.helpers import load_config

        manifest = build_manifest(
            kind=f"controlled_hypothesis_{hypothesis_id}",
            config=config if config is not None else load_config(),
            params={"hypothesis_contract": contract, "execution_identity": execution_identity},
            datasets=contract["dataset_fingerprints"],
            results=result,
        )
        write_manifest(manifest, write_manifest_as)

    return {"contract": contract, "execution_identity": execution_identity, "result": result}
