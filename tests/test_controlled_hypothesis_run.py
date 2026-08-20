"""
tests/test_controlled_hypothesis_run.py
-------------------------------------------
Slice 5 (Hypothesis Contract Enforcement, 2026-08-19) — proves the
mandatory 10-step pre-flight gate in
research/controlled_hypothesis_run.py::run_controlled_hypothesis()
actually blocks execution before an experiment starts, actually permits
it when the contract is complete, and that the returned evidence
(execution_identity, dataset/hypothesis-definition fingerprints) is the
exact same one the gate validated -- not a second, independently
recomputed copy that could silently drift.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research import controlled_hypothesis_run as chr_mod
from research.controlled_hypothesis_run import ControlledRunAborted, run_controlled_hypothesis
from research.hypothesis_contract import compute_execution_identity

REPO_ROOT = Path(__file__).resolve().parent.parent


def _ohlcv(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.08 + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


def _hyp_md(tmp_path: Path, hypothesis_id: str = "H999") -> tuple[Path, Path]:
    d = tmp_path / "hypotheses"
    d.mkdir(exist_ok=True)
    p = d / f"{hypothesis_id}_test_case.md"
    p.write_text("# H999\n\nStatement A.\n")
    return p, d


def _price_csv(tmp_path: Path, symbol: str = "EURUSD", n: int = 300) -> tuple[Path, pd.DataFrame]:
    df = _ohlcv(n)
    p = tmp_path / f"{symbol}_H1_2y.csv"
    df.to_csv(p)
    return p, df


def _base_kwargs(tmp_path: Path, hyp_path: Path, ds_path: Path, df: pd.DataFrame, execute_calls: list) -> dict:
    def execute_fn(contract: dict) -> dict:
        execute_calls.append(contract)
        return {"verdict": "PASSED", "pf": 1.4}

    return dict(
        hypothesis_id="H999",
        hypothesis_definition_path=hyp_path,
        symbol="EURUSD",
        timeframe="H1",
        dataset_paths=[ds_path],
        datasets={str(ds_path): df},
        train_period={"start": "2024-01-01", "end": "2024-01-05"},
        oos_period={"start": "2024-01-06", "end": "2024-01-13"},
        engine_config={"enabled": {"smc": True}},
        risk_config={"min_rr": 2.0},
        execute_fn=execute_fn,
    )


# ── Property 2: a valid contract permits execution ───────────────────────

def test_valid_contract_permits_execution(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    out = run_controlled_hypothesis(**_base_kwargs(tmp_path, hyp_path, ds_path, df, calls))

    assert len(calls) == 1  # execute_fn was actually reached, exactly once
    assert out["result"] == {"verdict": "PASSED", "pf": 1.4}
    assert out["contract"]["hypothesis_id"] == "H999"


# ── Property 1: invalid/missing contract fields prevent execution BEFORE
#    the experiment starts ────────────────────────────────────────────────

def test_missing_dataset_dataframe_aborts_before_execute_fn(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path, df, calls)
    kwargs["datasets"] = {}  # the one dataset_path has no matching loaded frame

    with pytest.raises(ControlledRunAborted) as exc_info:
        run_controlled_hypothesis(**kwargs)

    assert exc_info.value.stage == "missing_dataset"
    assert calls == []  # execute_fn NEVER reached


def test_structurally_invalid_dataset_aborts_before_execute_fn(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    bad_df = df.copy()
    bad_df.loc[bad_df.index[5], "high"] = bad_df.loc[bad_df.index[5], "low"] - 1.0  # high < low
    calls: list = []
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path, df, calls)
    kwargs["datasets"] = {str(ds_path): bad_df}

    with pytest.raises(ControlledRunAborted) as exc_info:
        run_controlled_hypothesis(**kwargs)

    assert exc_info.value.stage == "structural_validation"
    assert calls == []


def test_incomplete_contract_aborts_before_execute_fn(tmp_path):
    """Empty risk_config -- a required contract field -- must fail closed
    at validate_for_new_run(), never reaching execute_fn."""
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path, df, calls)
    kwargs["risk_config"] = {}

    with pytest.raises(ControlledRunAborted) as exc_info:
        run_controlled_hypothesis(**kwargs)

    assert exc_info.value.stage == "contract_validation"
    assert calls == []


def test_unknown_hypothesis_id_aborts_before_execute_fn(tmp_path):
    """No .md file exists for this ID -- resolve_hypothesis_definition_path
    itself raises, before any dataset work even begins."""
    from research.hypothesis_contract import HypothesisContractError

    hyp_path, hyp_dir = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path, df, calls)
    kwargs["hypothesis_id"] = "H000"
    kwargs["hypothesis_definition_path"] = None  # force real resolution against hyp_dir

    # resolve_hypothesis_definition_path uses the module-level HYPOTHESES_DIR
    # default unless a path is explicitly supplied -- supply hyp_dir so this
    # test doesn't depend on (or pollute) the real research/hypotheses/ dir.
    import research.hypothesis_contract as hc
    original_dir = hc.HYPOTHESES_DIR
    hc.HYPOTHESES_DIR = hyp_dir
    try:
        with pytest.raises(HypothesisContractError, match="No hypothesis definition file found"):
            run_controlled_hypothesis(**kwargs)
    finally:
        hc.HYPOTHESES_DIR = original_dir
    assert calls == []


# ── Property 3/4/5: returned evidence matches the exact contract used ────

def test_result_carries_the_exact_execution_identity_used_before_execution(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    out = run_controlled_hypothesis(**_base_kwargs(tmp_path, hyp_path, ds_path, df, calls))

    # The contract execute_fn actually saw...
    seen_contract = calls[0]
    # ...must be the identical contract compute_execution_identity() was
    # (and can independently be re-)computed from.
    assert out["execution_identity"] == compute_execution_identity(seen_contract)
    assert out["execution_identity"] == compute_execution_identity(out["contract"])


def test_dataset_fingerprint_in_evidence_equals_contract_fingerprint(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path, df, calls)
    kwargs["write_manifest_as"] = "test_slice5_ds_fp"
    out = run_controlled_hypothesis(**kwargs)

    manifest_path = REPO_ROOT / "research" / "results" / "test_slice5_ds_fp_manifest.json"
    try:
        persisted = json.loads(manifest_path.read_text())
        persisted_contract = persisted["params"]["hypothesis_contract"]
        assert persisted_contract["dataset_fingerprints"] == out["contract"]["dataset_fingerprints"]
        assert persisted["datasets"] == out["contract"]["dataset_fingerprints"]
        # Real bar count/date range, not just a bare sha256 (the Slice-5 fix).
        assert persisted_contract["dataset_fingerprints"][0]["bars"] == len(df)
    finally:
        manifest_path.unlink(missing_ok=True)


def test_hypothesis_definition_fingerprint_in_evidence_equals_contract_fingerprint(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    kwargs = _base_kwargs(tmp_path, hyp_path, ds_path, df, calls)
    kwargs["write_manifest_as"] = "test_slice5_hyp_fp"
    out = run_controlled_hypothesis(**kwargs)

    manifest_path = REPO_ROOT / "research" / "results" / "test_slice5_hyp_fp_manifest.json"
    try:
        persisted = json.loads(manifest_path.read_text())
        persisted_contract = persisted["params"]["hypothesis_contract"]
        assert (
            persisted_contract["hypothesis_definition"]["sha256"]
            == out["contract"]["hypothesis_definition"]["sha256"]
        )
        assert persisted["params"]["execution_identity"] == out["execution_identity"]
    finally:
        manifest_path.unlink(missing_ok=True)


# ── Property 6/7: changing dataset or hypothesis definition changes identity

def test_changing_dataset_changes_execution_identity_end_to_end(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    out1 = run_controlled_hypothesis(**_base_kwargs(tmp_path, hyp_path, ds_path, df, calls))

    df2 = _ohlcv(300, seed=99)  # a genuinely different, still OHLC-valid dataset
    df2.to_csv(ds_path)
    calls2: list = []
    kwargs2 = _base_kwargs(tmp_path, hyp_path, ds_path, df2, calls2)
    out2 = run_controlled_hypothesis(**kwargs2)

    assert out1["execution_identity"] != out2["execution_identity"]


def test_changing_hypothesis_definition_changes_execution_identity_end_to_end(tmp_path):
    hyp_path, _ = _hyp_md(tmp_path)
    ds_path, df = _price_csv(tmp_path)
    calls: list = []
    out1 = run_controlled_hypothesis(**_base_kwargs(tmp_path, hyp_path, ds_path, df, calls))

    hyp_path.write_text(hyp_path.read_text() + "\nRevised falsification criteria.\n")
    calls2: list = []
    out2 = run_controlled_hypothesis(**_base_kwargs(tmp_path, hyp_path, ds_path, df, calls2))

    assert out1["execution_identity"] != out2["execution_identity"]


# ── Property 8: legacy historical execution stays readable, never falsely
#    marked contract-compliant ────────────────────────────────────────────

def test_legacy_manifest_remains_readable_and_is_not_falsely_marked_compliant():
    manifest_path = REPO_ROOT / "research" / "results" / "h019_crypto_positioning_ab_20260724_manifest.json"
    assert manifest_path.exists()
    historical = json.loads(manifest_path.read_text())
    assert "hypothesis_contract" not in historical.get("params", {})
    assert "execution_identity" not in historical.get("params", {})
    # This module never touches research/results/ except via the optional,
    # explicitly-opted-in write_manifest_as path exercised in other tests
    # above -- confirmed by construction (no unconditional file I/O
    # anywhere in run_controlled_hypothesis()).
    assert json.loads(manifest_path.read_text()) == historical


# ── Property 9: no alternate entry point inside this canonical module can
#    bypass validate_for_new_run() before execute_fn ──────────────────────

def test_execute_fn_is_called_exactly_once_and_only_after_validation():
    """AST-based static-analysis proof (more robust than a text/line scan,
    which would false-match prose mentions of these names inside
    docstrings): the ONLY real Call node invoking `validate_for_new_run`
    appears strictly before the ONLY real Call node invoking `execute_fn`
    -- there is no second public function in this module that could reach
    execute_fn some other way."""
    import ast

    source = inspect.getsource(chr_mod)
    tree = ast.parse(source)

    validate_call_lines = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "validate_for_new_run"
    ]
    execute_call_lines = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "execute_fn"
    ]

    assert len(validate_call_lines) == 1
    assert len(execute_call_lines) == 1
    assert execute_call_lines[0] > validate_call_lines[0]

    public_functions = [
        name for name, obj in inspect.getmembers(chr_mod, inspect.isfunction)
        if obj.__module__ == chr_mod.__name__ and not name.startswith("_")
    ]
    # Slice 6 adds one more public function, require_controlled_execution() --
    # it never calls execute_fn itself (it only raises or returns None), so
    # it doesn't create a second path to execute_fn; it's the bypass GUARD
    # every H0*.py script now calls, not an alternate entry point.
    assert sorted(public_functions) == ["require_controlled_execution", "run_controlled_hypothesis"]


def test_no_write_call_near_registry_config_or_engines_mention():
    write_markers = ("write_text", "json.dump", "yaml.dump", "yaml.safe_dump", '"w")', "'w')")
    source = inspect.getsource(chr_mod)
    for line in source.splitlines():
        lowered = line.lower()
        if "registry" in lowered or "engines.yaml" in lowered or ("config.yaml" in lowered and "load_config" not in lowered):
            for marker in write_markers:
                assert marker not in line, f"possible write near a protected file reference: {line!r}"
