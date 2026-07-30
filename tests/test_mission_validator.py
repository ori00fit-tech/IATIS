"""
tests/test_mission_validator.py
------------------------------------
AI Research Lab / Mission Center Phase 3 (2026-07-30) — the safety-
critical test suite for backtest/mission_validator.py, mirroring
tests/test_mission_runner.py's own two non-negotiable properties: no
code path here ever writes research/results/registry.json, config.yaml,
or config/engines.yaml, and a validation's per-symbol results are always
recorded (pass or fail) — nothing suppressed.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import mission_runner, mission_validator
from backtest.mission_validator import (
    MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD,
    NO_EDGE,
    STRONG_LEAD,
    WEAK_LEAD,
    ValidationConfig,
    run_validation,
)
from backtest.optimizer import MissionSearchSpace, _ENGINES_IDX_KEY, _INDICATORS_IDX_KEY, _TF_IDX_KEY
from storage import research_mission_validations, research_missions

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "research" / "results" / "registry.json"
CONFIG_YAML_PATH = REPO_ROOT / "config.yaml"
ENGINES_YAML_PATH = REPO_ROOT / "config" / "engines.yaml"


def _ohlcv(n: int, seed: int = 7, trend: float = 0.10) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


def _write_dataset(tmp_path: Path, symbol: str = "EURUSD", n: int = 2400) -> None:
    _ohlcv(n, seed=hash(symbol) % 1000).to_csv(tmp_path / f"{symbol}_H1_2y.csv")


def _space() -> MissionSearchSpace:
    return MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )


def _seed_mission_and_trial(mission_id: str, trial_symbol: str = "EURUSD", state: str = "COMPLETE") -> None:
    space = _space()
    raw_params = {_TF_IDX_KEY: 0, _ENGINES_IDX_KEY: 0, _INDICATORS_IDX_KEY: 0, "sl_atr_multiplier": 2.0}
    research_missions.upsert_mission(
        mission_id=mission_id, name="test-mission", sampler="random", objective_metric="profit_factor",
        symbols=[trial_symbol], n_trials_per_symbol=1, min_trades=1, seed=42,
        search_space=mission_runner._search_space_dict(space), config={}, status="finished",
    )
    research_missions.record_trial(
        mission_id=mission_id, trial_number=0, symbol=trial_symbol, state=state,
        objective_value=1.2 if state == "COMPLETE" else None,
        params=raw_params, metrics={"profit_factor": 1.2}, trades=50 if state == "COMPLETE" else 0,
        error=None, started_at="t", finished_at="t",
    )


def _small_vc(tmp_path: Path, validation_id: str, mission_id: str,
              validation_symbols=("EURUSD", "GBPUSD")) -> ValidationConfig:
    return ValidationConfig(
        validation_id=validation_id, mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=tuple(validation_symbols), data_dir=tmp_path, start=None, end=None,
        wf_windows=2, wf_min_trades_per_window=1, wf_warmup_bars=50,
        rb_multipliers=(0.8, 1.0, 1.2), rb_params=("sl_atr_multiplier",), rb_min_trades=1,
        mc_n_simulations=50, mc_seed=1, output_dir=tmp_path / "reports",
    )


# ── Hard-block safety tests ──────────────────────────────────────────────

def test_no_write_call_near_registry_mention_in_mission_validator():
    write_markers = ("write_text", "json.dump", "yaml.dump", "yaml.safe_dump", '"w")', "'w')")
    source = inspect.getsource(mission_validator)
    for line in source.splitlines():
        if "registry" in line.lower():
            for marker in write_markers:
                assert marker not in line, f"possible write near a registry reference: {line!r}"


def test_registry_json_byte_identical_before_and_after_validation_run(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-registry-check")

    before = REGISTRY_PATH.read_bytes()
    before_mtime = REGISTRY_PATH.stat().st_mtime

    vc = _small_vc(tmp_path, "v-registry-check", "val-registry-check")
    run_validation(vc)

    assert REGISTRY_PATH.read_bytes() == before
    assert REGISTRY_PATH.stat().st_mtime == before_mtime


def test_never_touches_config_yaml_or_engines_yaml(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-config-check")

    before_config = CONFIG_YAML_PATH.read_bytes()
    before_engines = ENGINES_YAML_PATH.read_bytes()

    vc = _small_vc(tmp_path, "v-config-check", "val-config-check")
    run_validation(vc)

    assert CONFIG_YAML_PATH.read_bytes() == before_config
    assert ENGINES_YAML_PATH.read_bytes() == before_engines


# ── Happy path / recording ────────────────────────────────────────────────

def test_run_validation_records_every_symbol_and_writes_report(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-happy-path")

    vc = _small_vc(tmp_path, "v-happy-path", "val-happy-path")
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-happy-path")
    assert validation is not None
    assert validation["status"] == "finished"
    assert validation["total_symbols"] == 2
    assert validation["overall_verdict"] in (NO_EDGE, WEAK_LEAD, STRONG_LEAD)

    results = research_mission_validations.validation_results("v-happy-path")
    assert {r["symbol"] for r in results} == {"EURUSD", "GBPUSD"}
    for r in results:
        breakdown = json.loads(r["criteria_breakdown_json"])
        # Every criterion is always present, pass or fail — nothing suppressed.
        assert set(breakdown) == {
            "profit_factor", "trades", "max_drawdown_pct", "expectancy", "sharpe_ratio",
            "walk_forward", "monte_carlo_risk_of_ruin", "monte_carlo_probability_profit",
            "robustness_all_stable",
        }
        for c in breakdown.values():
            assert set(c) == {"actual", "threshold", "passed"}

    reports = list((tmp_path / "reports").glob("mission_validation_v-happy-path_*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["overall_verdict"] == validation["overall_verdict"]
    assert len(report["results"]) == 2


def test_rejects_non_complete_trial(tmp_path):
    _write_dataset(tmp_path, "EURUSD")
    _write_dataset(tmp_path, "GBPUSD")
    _seed_mission_and_trial("val-pruned-check", state="PRUNED")

    vc = _small_vc(tmp_path, "v-pruned-check", "val-pruned-check")
    run_validation(vc)

    validation = research_mission_validations.get_validation("v-pruned-check")
    assert validation["status"] == "failed"
    assert "COMPLETE" in validation["error"]
    assert research_mission_validations.validation_results("v-pruned-check") == []


def test_unknown_mission_fails_cleanly(tmp_path):
    vc = _small_vc(tmp_path, "v-unknown-mission", "does-not-exist")
    run_validation(vc)
    validation = research_mission_validations.get_validation("v-unknown-mission")
    assert validation["status"] == "failed"
    assert "not found" in validation["error"]


# ── Verdict boundaries ─────────────────────────────────────────────────────

def _seed_validation_row(validation_id: str, mission_id: str, symbols: list[str]) -> None:
    research_mission_validations.upsert_validation(
        validation_id=validation_id, mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
        validation_symbols=symbols, objective_metric="profit_factor", criteria={}, status="running",
    )


def test_verdict_boundaries(monkeypatch):
    """Pins the verdict table directly, independent of real backtests:
    0/1 passing -> NO_EDGE; some-but-not-all (or all but below the
    STRONG_LEAD symbol floor) -> WEAK_LEAD; all passing AND >= the floor
    -> STRONG_LEAD. Two symbols both passing must still cap at WEAK_LEAD
    (pins MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD)."""
    from backtest import mission_validator as mv

    def _fake_eval(symbol, point, vc, *, passing_symbols):
        passed = symbol in passing_symbols
        return {
            "symbol": symbol, "passed": passed,
            "metrics": {}, "monte_carlo": {}, "walk_forward": {}, "robustness": {},
            "criteria_breakdown": {}, "feature_mining": None, "started_at": "t", "finished_at": "t",
        }

    cases = [
        ([], 3, NO_EDGE),                                             # 0 of 3 passing
        (["EURUSD"], 3, NO_EDGE),                                     # 1 of 3 -> still NO_EDGE (<=1)
        (["EURUSD", "GBPUSD"], 2, WEAK_LEAD),                         # 2 of 2 passing, but below the floor
        (["EURUSD", "GBPUSD", "XAUUSD"], 3, STRONG_LEAD),             # 3 of 3, meets the floor
    ]
    assert MIN_VALIDATION_SYMBOLS_FOR_STRONG_LEAD == 3

    for passing_symbols, n_total, expected in cases:
        symbols = (["EURUSD", "GBPUSD", "XAUUSD"])[:n_total]
        vid = f"v-verdict-{n_total}-{len(passing_symbols)}"
        mission_id = f"mission-verdict-{vid}"
        _seed_mission_and_trial(mission_id)
        _seed_validation_row(vid, mission_id, symbols)

        vc = ValidationConfig(
            validation_id=vid, mission_id=mission_id, trial_number=0, trial_symbol="EURUSD",
            validation_symbols=tuple(symbols), data_dir=Path("."), start=None, end=None,
            output_dir=Path("/tmp"),
        )
        monkeypatch.setattr(mv, "_evaluate_symbol",
                             lambda s, p, v, _ps=passing_symbols: _fake_eval(s, p, v, passing_symbols=_ps))
        run_validation(vc)

        validation = research_mission_validations.get_validation(vid)
        assert validation["overall_verdict"] == expected, (symbols, passing_symbols, validation)
