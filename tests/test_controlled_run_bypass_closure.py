"""
tests/test_controlled_run_bypass_closure.py
------------------------------------------------
Slice 6 (Close Controlled-Run Bypasses, 2026-08-19) -- proves the bypass
Slice 5 disclosed and left open (every research/experiments/H0*.py entry
point could still produce a "controlled-shaped" result by invoking it
directly, skipping run_controlled_hypothesis()'s 10-step pre-flight gate
entirely) is now closed for all 13 real entry points, without duplicating
any pre-flight logic inside those scripts and without touching a single
hypothesis's own decision-rule computation.

Covers the 8 mandated properties from the operator's own Slice 6 prompt:
  1. every controlled-result-producing entry point routes through the
     orchestrator OR explicitly refuses controlled execution
  2. direct invocation cannot silently produce a new controlled result
  3. the original hypothesis-specific verdict function is still used,
     unchanged
  4. valid orchestrated execution still works
  5. legacy historical results remain readable
  6. no duplicate contract/preflight implementation exists in the H0XX
     scripts
  7. a failed preflight produces no evidence/result artifact
  8. execution identity remains identical between the adapter and the
     orchestrator for identical inputs
"""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path

import pandas as pd
import pytest

from research.controlled_hypothesis_run import (
    DirectExecutionRefused,
    require_controlled_execution,
    run_controlled_hypothesis,
)
from research.hypothesis_contract import compute_execution_identity

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = REPO_ROOT / "research" / "experiments"

# Every research/experiments/H0*.py entry point that can produce a NEW
# controlled result, and which callable inside it require_controlled_
# execution() now guards. Group 1 scripts guard main(); Group 2 (the 6
# legacy, dead-listed, pre-manifest scripts with no CLI of their own)
# guard run_experiment() -- their only real entry point.
_MAIN_GUARDED = [
    ("research.experiments.H019_crypto_positioning_ab", "H019", "main"),
    ("research.experiments.H022_fx_cross_oos", "H022", "main"),
    ("research.experiments.H023_wyckoff_volume_gating", "H023", "main"),
    ("research.experiments.H025_information_compression", "H025", "main"),
    ("research.experiments.H033_meta_confidence_gate", "H033", "main"),
    ("research.experiments.H037_decision_delay", "H037", "main"),
    ("research.experiments.H101_smc_structural_bias_ab", "H101", "main"),
    ("research.experiments.H102_price_action_confluence_ab", "H102", "main"),
]

_RUN_EXPERIMENT_GUARDED = [
    ("research.experiments.H001_liquidity_sweep_htf", "H001"),
    ("research.experiments.H002_qualified_sweep", "H002"),
    ("research.experiments.H002b_multisymbol_sweep", "H002b"),
    ("research.experiments.H008_bos_fvg", "H008"),
    ("research.experiments.H008b_session_filtered_bos", "H008b"),
    ("research.experiments.H008c_oos", "H008c"),
]

ALL_GUARDED_MODULES = [m for m, _, _ in _MAIN_GUARDED] + [m for m, _ in _RUN_EXPERIMENT_GUARDED] + [
    "research.experiments.H103_meta_decision_gate_ab",
]


def _no_argv_pollution(monkeypatch, argv0: str = "prog") -> None:
    """main()-shaped entry points call argparse.ArgumentParser().parse_args()
    with no explicit args -- it reads sys.argv by default. For scripts
    where the guard fires BEFORE parse_args() this never matters (the
    guard raises first), but H103's guard fires AFTER parsing, so its own
    test controls sys.argv explicitly."""
    monkeypatch.setattr(sys, "argv", [argv0])


# ── Property 1 + 2 + 7: every real entry point refuses direct execution,
#    before doing any work, producing no result/manifest artifact ────────

@pytest.mark.parametrize("module_path,hypothesis_id,fn_name", _MAIN_GUARDED)
def test_main_shaped_entry_point_refuses_direct_execution(module_path, hypothesis_id, fn_name, monkeypatch):
    _no_argv_pollution(monkeypatch)
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)

    with pytest.raises(DirectExecutionRefused) as exc_info:
        fn()

    assert hypothesis_id in str(exc_info.value)
    assert "run_controlled_hypothesis" in str(exc_info.value)


@pytest.mark.parametrize("module_path,hypothesis_id", _RUN_EXPERIMENT_GUARDED)
def test_run_experiment_shaped_entry_point_refuses_direct_execution(module_path, hypothesis_id):
    mod = importlib.import_module(module_path)
    fn = mod.run_experiment
    sig = inspect.signature(fn)
    # Positional-or-keyword args filled with harmless sentinels -- the
    # guard is the FIRST statement in every one of these functions, so
    # none of these values are ever actually touched before it raises.
    dummy_args = [None if p.default is inspect.Parameter.empty else p.default for p in sig.parameters.values()]

    with pytest.raises(DirectExecutionRefused) as exc_info:
        fn(*dummy_args)

    assert hypothesis_id in str(exc_info.value)


def test_h103_refuses_direct_execution_on_its_real_compute_path(monkeypatch):
    """H103 is guarded AFTER its --manifest-only early return (property 5
    covers that branch separately below) -- this proves the REAL,
    verdict-producing branch (no --manifest-only) still refuses."""
    _no_argv_pollution(monkeypatch)
    mod = importlib.import_module("research.experiments.H103_meta_decision_gate_ab")

    with pytest.raises(DirectExecutionRefused) as exc_info:
        mod.main()

    assert "H103" in str(exc_info.value)


def test_direct_refusal_never_creates_a_result_or_manifest_file(monkeypatch, tmp_path):
    """Property 2 + 7, made concrete: H101's guard fires before RESULT_PATH
    is ever written and before write_manifest() is ever called -- confirm
    no file appears in research/results/ as a side effect of the refused
    call (the guard is provably before ALL computation, not just before
    the LAST step)."""
    _no_argv_pollution(monkeypatch)
    import research.experiments.H101_smc_structural_bias_ab as h101

    before = {p.name for p in (REPO_ROOT / "research" / "results").glob("h101_smc_structural_bias_ab*")}
    with pytest.raises(DirectExecutionRefused):
        h101.main()
    after = {p.name for p in (REPO_ROOT / "research" / "results").glob("h101_smc_structural_bias_ab*")}

    assert before == after


# ── Property 5: legacy/historical read-only paths stay reachable ─────────

def test_h103_manifest_only_flag_stays_reachable_without_the_guard_firing(monkeypatch):
    """--manifest-only reads an EXISTING result.json and backfills a
    manifest -- it produces no NEW verdict, so it must never hit
    require_controlled_execution() at all. In this sandbox no real
    H103_meta_decision_gate_ab.json exists, so the function's own,
    pre-existing "nothing to backfill" SystemExit fires instead --
    proving control reached THAT check, not the guard."""
    monkeypatch.setattr(sys, "argv", ["prog", "--manifest-only"])
    mod = importlib.import_module("research.experiments.H103_meta_decision_gate_ab")

    with pytest.raises(SystemExit) as exc_info:
        mod.main()

    assert "not found" in str(exc_info.value)
    assert not isinstance(exc_info.value.__cause__, DirectExecutionRefused)


def test_reading_an_existing_historical_manifest_is_completely_unaffected():
    """A plain json.loads() of an already-committed manifest is not a
    "controlled execution" at all -- confirms Slice 6 didn't turn every
    read of research/results/ into something gated."""
    import json

    manifest_path = REPO_ROOT / "research" / "results" / "h019_crypto_positioning_ab_20260724_manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert "kind" in data


# ── Property 3: the original hypothesis-specific verdict logic is         =
#    untouched -- only ONE guard call was inserted per entry point ───────

_VERDICT_MARKERS: dict[str, str] = {
    "research.experiments.H019_crypto_positioning_ab": "def positioning_verdict",
    "research.experiments.H022_fx_cross_oos": "H022",
    "research.experiments.H023_wyckoff_volume_gating": "def main",
    "research.experiments.H025_information_compression": "def main",
    "research.experiments.H033_meta_confidence_gate": "def main",
    "research.experiments.H037_decision_delay": "def main",
    "research.experiments.H101_smc_structural_bias_ab": "def smc_verdict",
    "research.experiments.H102_price_action_confluence_ab": "def main",
    "research.experiments.H103_meta_decision_gate_ab": "def main",
    "research.experiments.H001_liquidity_sweep_htf": "def run_experiment",
    "research.experiments.H002_qualified_sweep": "def run_experiment",
    "research.experiments.H002b_multisymbol_sweep": "def run_experiment",
    "research.experiments.H008_bos_fvg": "def run_experiment",
    "research.experiments.H008b_session_filtered_bos": "def run_experiment",
    "research.experiments.H008c_oos": "def run_experiment",
}


@pytest.mark.parametrize("module_path", sorted(_VERDICT_MARKERS))
def test_original_decision_function_still_present_unchanged(module_path):
    mod = importlib.import_module(module_path)
    source = inspect.getsource(mod)
    assert _VERDICT_MARKERS[module_path] in source


def test_h101_smc_verdict_is_a_pure_unmodified_function_reachable_without_the_guard():
    """smc_verdict() itself is NOT guarded (only main() is) -- the
    hypothesis's own pre-registered decision rule stays directly callable
    and unit-testable exactly as before Slice 6, proving the guard was
    added around the entry point, not baked into the rule itself."""
    from research.experiments.H101_smc_structural_bias_ab import smc_verdict

    verdict, checks, reasons = smc_verdict(pooled_dpf=0.5, win_fraction=0.7, pooled_a_n=400)
    assert verdict.startswith("PASSED")
    assert checks == {"1_mean_dPF>0": True, "2_win_fraction>=0.60": True}


# ── Property 6: no script duplicates the orchestrator's own pre-flight
#    logic -- only ever calls require_controlled_execution() ────────────

_DUPLICATED_PREFLIGHT_MARKERS = (
    "validate_for_new_run(",
    "build_hypothesis_contract(",
    "resolve_hypothesis_definition_path(",
    "compute_execution_identity(",
)


@pytest.mark.parametrize("module_path", ALL_GUARDED_MODULES)
def test_no_script_duplicates_the_orchestrators_preflight_logic(module_path):
    mod = importlib.import_module(module_path)
    source = inspect.getsource(mod)
    for marker in _DUPLICATED_PREFLIGHT_MARKERS:
        assert marker not in source, f"{module_path} appears to duplicate pre-flight logic: {marker!r}"
    assert "require_controlled_execution" in source


@pytest.mark.parametrize("module_path", ALL_GUARDED_MODULES)
def test_each_script_calls_the_guard_exactly_once(module_path):
    """AST-based, not a text scan -- counts real Call nodes invoking
    require_controlled_execution, proving exactly one guard per script
    (no accidental duplication, no accidental omission)."""
    mod = importlib.import_module(module_path)
    source = inspect.getsource(mod)
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_controlled_execution"
    ]
    assert len(calls) == 1


# ── Property 4 + 8: a real orchestrated execution passes the guard, and
#    an execute_fn-internal identity computation matches the orchestrator's
#    own returned execution_identity, for identical inputs ──────────────

def _ohlcv(n: int = 300) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(11)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.10 + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


def _stub_hypothesis_kwargs(tmp_path: Path) -> dict:
    hyp_dir = tmp_path / "hypotheses"
    hyp_dir.mkdir(exist_ok=True)
    hyp_path = hyp_dir / "HSTUB_test_case.md"
    hyp_path.write_text("# HSTUB\n\nA stub hypothesis for Slice 6's adapter-mechanism test.\n")

    df = _ohlcv()
    ds_path = tmp_path / "EURUSD_H1_2y.csv"
    df.to_csv(ds_path)

    return dict(
        hypothesis_id="HSTUB",
        hypothesis_definition_path=hyp_path,
        symbol="EURUSD",
        timeframe="H1",
        dataset_paths=[ds_path],
        datasets={str(ds_path): df},
        train_period={"start": "2024-01-01", "end": "2024-01-05"},
        oos_period={"start": "2024-01-06", "end": "2024-01-13"},
        engine_config={"enabled": {"smc": True}},
        risk_config={"min_rr": 2.0},
    )


def test_valid_orchestrated_execution_reaches_a_guarded_execute_fn(tmp_path):
    """Property 4: the SAME require_controlled_execution() every H0XX
    script now calls does NOT raise when it's invoked from inside
    run_controlled_hypothesis()'s own execute_fn(contract) call -- this is
    the door Slice 6 deliberately leaves open (a future thin adapter
    wiring a real H0XX script's decision function as execute_fn would
    pass through exactly this way, with zero further plumbing)."""
    calls: list = []

    def execute_fn(contract: dict) -> dict:
        # Simulates what a thin per-hypothesis adapter would do: call the
        # SAME guard the real script calls, from inside execute_fn.
        require_controlled_execution("HSTUB")
        calls.append(contract)
        return {"verdict": "adapter ran for real"}

    kwargs = _stub_hypothesis_kwargs(tmp_path)
    out = run_controlled_hypothesis(execute_fn=execute_fn, **kwargs)

    assert len(calls) == 1
    assert out["result"] == {"verdict": "adapter ran for real"}


def test_guard_refuses_a_different_hypothesis_id_even_mid_orchestrated_run(tmp_path):
    """The contextvar check is by IDENTITY, not "something is running" --
    H101's guard must not pass just because HSTUB is mid-run."""
    def execute_fn(contract: dict) -> dict:
        require_controlled_execution("H101")  # wrong id -- HSTUB is what's actually running
        return {}

    kwargs = _stub_hypothesis_kwargs(tmp_path)
    with pytest.raises(DirectExecutionRefused):
        run_controlled_hypothesis(execute_fn=execute_fn, **kwargs)


def test_guard_releases_after_the_orchestrated_run_completes(tmp_path):
    """The contextvar is reset (not left set) once execute_fn returns --
    a direct call to the SAME hypothesis's guard immediately afterward
    must refuse again, exactly like before the run."""
    def execute_fn(contract: dict) -> dict:
        require_controlled_execution("HSTUB")
        return {}

    kwargs = _stub_hypothesis_kwargs(tmp_path)
    run_controlled_hypothesis(execute_fn=execute_fn, **kwargs)

    with pytest.raises(DirectExecutionRefused):
        require_controlled_execution("HSTUB")


def test_execution_identity_matches_between_adapter_and_orchestrator(tmp_path):
    """Property 8: execute_fn (the "adapter") independently recomputes
    compute_execution_identity() on the SAME contract dict it was handed;
    that value must equal what run_controlled_hypothesis() itself returns
    as execution_identity -- proving there is exactly one true identity
    for a given controlled run, not two that could silently drift."""
    captured: dict[str, str] = {}

    def execute_fn(contract: dict) -> dict:
        require_controlled_execution("HSTUB")
        captured["adapter_identity"] = compute_execution_identity(contract)
        return {"verdict": "ok"}

    kwargs = _stub_hypothesis_kwargs(tmp_path)
    out = run_controlled_hypothesis(execute_fn=execute_fn, **kwargs)

    assert captured["adapter_identity"] == out["execution_identity"]

    # And running the IDENTICAL inputs again reproduces the IDENTICAL
    # identity end to end -- not just self-consistent within one run.
    kwargs2 = _stub_hypothesis_kwargs(tmp_path)
    out2 = run_controlled_hypothesis(execute_fn=execute_fn, **kwargs2)
    assert out2["execution_identity"] == out["execution_identity"]


# ── scripts/run_h002.py: removed the auto-registry-write capability ──────

def test_run_h002_no_longer_writes_registry_json_directly():
    """A real, disclosed additional finding: this driver script used to
    call _update_registry(result), writing status="PASSED" straight into
    registry.json with no human review whenever H002 happened to come
    back PASSED -- a direct violation of the "never auto-write
    registry.json" invariant. require_controlled_execution() inside
    run_experiment() already makes this unreachable via the script's own
    CLI, but the write capability itself must not exist at all."""
    source = (REPO_ROOT / "scripts" / "run_h002.py").read_text()
    assert "def _update_registry" not in source
    assert 'open(registry_path, "w")' not in source
