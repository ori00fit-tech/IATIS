"""
tests/test_mission_runner.py
--------------------------------
AI Research Lab / Mission Center Phase 1 (2026-07-27) — the safety-
critical test suite for backtest/mission_runner.py. Two properties here
are non-negotiable (per the user's explicit condition for building this
feature at all): registry.json is NEVER written by any code path in
this module/optimizer.py/multiple_testing.py, and a killed-and-resumed
mission continues rather than restarting from zero.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import mission_runner, multiple_testing, optimizer
from backtest.mission_runner import MissionConfig, run_mission
from backtest.optimizer import MissionSearchSpace
from storage import research_missions

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
    _ohlcv(n).to_csv(tmp_path / f"{symbol}_H1_2y.csv")


def _small_config(tmp_path: Path, mission_id: str, n_trials: int = 2, symbols=("EURUSD",)) -> MissionConfig:
    space = MissionSearchSpace(
        timeframes_choices=(("H1",),),
        engine_set_choices=(("nnfx", "price_action", "smc", "wyckoff"),),
        indicator_set_choices=((),),
        risk_param_ranges={"sl_atr_multiplier": (1.5, 2.5)},
    )
    return MissionConfig(
        mission_id=mission_id, name="test-mission", symbols=tuple(symbols),
        data_dir=tmp_path, start=None, end=None, sampler="random",
        n_trials_per_symbol=n_trials, objective_metric="profit_factor",
        min_trades=1, seed=42, search_space=space, oos_holdout_fraction=None,
        max_wall_clock_seconds=None, output_dir=tmp_path / "reports",
    )


# ── Hard-block safety tests ──────────────────────────────────────────────

def test_no_module_contains_a_write_call_near_registry_json():
    """Source-level guard: none of the three modules powering a mission
    should contain a file-write call anywhere near a "registry" mention —
    docstrings/comments legitimately explain the safety guarantee by
    NAME (so a bare substring-absence check would false-positive on this
    module's own documentation), but no write-shaped call
    (write_text/json.dump/yaml.*dump/open(...,"w")) may ever appear on a
    line mentioning "registry" in any of these three files."""
    write_markers = ("write_text", "json.dump", "yaml.dump", "yaml.safe_dump", '"w")', "'w')")
    for module in (mission_runner, optimizer, multiple_testing):
        source = inspect.getsource(module)
        for line in source.splitlines():
            if "registry" in line.lower():
                for marker in write_markers:
                    assert marker not in line, (
                        f"{module.__name__}: possible write near a registry "
                        f"reference: {line!r}"
                    )


def test_registry_json_byte_identical_before_and_after_mission_run(tmp_path):
    _write_dataset(tmp_path)
    before = REGISTRY_PATH.read_bytes()
    before_mtime = REGISTRY_PATH.stat().st_mtime

    mc = _small_config(tmp_path, "mission-registry-check")
    run_mission(mc)

    after = REGISTRY_PATH.read_bytes()
    after_mtime = REGISTRY_PATH.stat().st_mtime
    assert after == before
    assert after_mtime == before_mtime


def test_never_touches_config_yaml_or_engines_yaml(tmp_path):
    _write_dataset(tmp_path)
    before_config = CONFIG_YAML_PATH.read_bytes()
    before_engines = ENGINES_YAML_PATH.read_bytes()

    mc = _small_config(tmp_path, "mission-config-check")
    run_mission(mc)

    assert CONFIG_YAML_PATH.read_bytes() == before_config
    assert ENGINES_YAML_PATH.read_bytes() == before_engines


# ── Resume ────────────────────────────────────────────────────────────────

def test_resume_skips_already_completed_trials(tmp_path, monkeypatch):
    _write_dataset(tmp_path)
    mission_id = "mission-resume-check"

    real_evaluate_point = mission_runner.evaluate_point
    call_count = {"n": 0}

    def _kill_after_two(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] > 2:
            raise KeyboardInterrupt("simulated process kill")
        return real_evaluate_point(*args, **kwargs)

    monkeypatch.setattr(mission_runner, "evaluate_point", _kill_after_two)

    mc = _small_config(tmp_path, mission_id, n_trials=5)
    with pytest.raises(KeyboardInterrupt):
        run_mission(mc)

    recorded = research_missions.existing_trials(mission_id, "EURUSD")
    assert len(recorded) == 2
    assert [r["trial_number"] for r in recorded] == [0, 1]

    # Restore the real function and resume with the identical mission_id —
    # must continue from trial 2, not restart from 0.
    monkeypatch.setattr(mission_runner, "evaluate_point", real_evaluate_point)
    call_count["n"] = 0
    mc_resumed = _small_config(tmp_path, mission_id, n_trials=5)
    run_mission(mc_resumed)

    final = research_missions.existing_trials(mission_id, "EURUSD")
    assert len(final) == 5
    assert [r["trial_number"] for r in final] == [0, 1, 2, 3, 4]

    mission = research_missions.get_mission(mission_id)
    assert mission["status"] == "finished"


# ── Graceful stop / isolation ─────────────────────────────────────────────

def test_max_wall_clock_seconds_stops_gracefully_and_marks_finished(tmp_path):
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-budget-check", n_trials=1000)
    mc = MissionConfig(**{**mc.__dict__, "max_wall_clock_seconds": 0.0})
    run_mission(mc)

    mission = research_missions.get_mission("mission-budget-check")
    assert mission["status"] == "finished"
    recorded = research_missions.existing_trials("mission-budget-check", "EURUSD")
    assert len(recorded) < 1000


def test_one_symbol_failure_does_not_abort_other_symbols(tmp_path):
    # Only EURUSD gets a dataset written — GBPUSD is missing.
    _write_dataset(tmp_path, symbol="EURUSD")
    mc = _small_config(tmp_path, "mission-isolation-check", symbols=("EURUSD", "GBPUSD"))
    run_mission(mc)

    eurusd_trials = research_missions.existing_trials("mission-isolation-check", "EURUSD")
    gbpusd_trials = research_missions.existing_trials("mission-isolation-check", "GBPUSD")
    assert len(eurusd_trials) == 2
    assert len(gbpusd_trials) == 0

    mission = research_missions.get_mission("mission-isolation-check")
    assert mission["status"] == "finished"


def test_transient_cancellation_check_failure_does_not_mark_a_successful_mission_failed(tmp_path, monkeypatch):
    """Regression (found live, 2026-07-30): a mission whose every trial
    completed successfully was shown as status='failed' in the dashboard.
    Root cause: the cancellation-check read (get_mission, a real D1
    round-trip) was unguarded — a transient read error there propagated
    all the way up and overwrote the final status, even though nothing
    about the actual trials had gone wrong. _is_cancelled() must fail
    OPEN (treat a read error as "not cancelled"), never poison the run."""
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-transient-check-fail")

    from backtest import mission_runner as m
    real_get_mission = research_missions.get_mission

    def flaky_get_mission(mission_id):
        raise RuntimeError("D1 proxy timeout (transient)")

    monkeypatch.setattr(m.research_missions, "get_mission", flaky_get_mission)
    try:
        run_mission(mc)
    finally:
        monkeypatch.setattr(m.research_missions, "get_mission", real_get_mission)

    trials = research_missions.existing_trials("mission-transient-check-fail", "EURUSD")
    assert len(trials) == 2
    assert all(t["state"] == "COMPLETE" for t in trials)

    mission = research_missions.get_mission("mission-transient-check-fail")
    assert mission["status"] == "finished"
    assert mission["error"] is None


# ── Report generation ──────────────────────────────────────────────────────

def test_mission_report_written_with_exploratory_banner(tmp_path):
    _write_dataset(tmp_path)
    mc = _small_config(tmp_path, "mission-report-check")
    run_mission(mc)

    reports = list((tmp_path / "reports").glob("mission_mission-report-check_*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text())
    assert "EXPLORATORY" in payload["note"]
    assert "EXPLORATORY" in payload["mission_wide_significance"]["banner"]
    assert "EURUSD" in payload["per_symbol"]
    assert len(payload["per_symbol"]["EURUSD"]["trials"]) == 2


# ── CLI wiring ──────────────────────────────────────────────────────────────

def test_cli_wires_all_json_flags_into_mission_search_space(monkeypatch, tmp_path):
    import sys

    captured: dict = {}

    def fake_run_mission(mc: MissionConfig) -> None:
        captured["mc"] = mc

    monkeypatch.setattr(mission_runner, "run_mission", fake_run_mission)
    monkeypatch.setattr(sys, "argv", [
        "mission_runner.py",
        "--mission-id", "cli-test-mission",
        "--symbols", "eurusd", "gbpusd",
        "--data-dir", str(tmp_path),
        "--sampler", "random",
        "--n-trials-per-symbol", "3",
        "--objective-metric", "sharpe_ratio",
        "--min-trades", "5",
        "--seed", "99",
        "--timeframes-choices", '[["H1"],["H4","D1","H1"]]',
        "--engine-set-choices", '[["nnfx","price_action"]]',
        "--indicator-set-choices", "[[]]",
        "--context-filter-set-choices",
        '[[],[{"name":"direction","mode":"entry_filter","params":{"allowed":["BULLISH"]},"weight":0}]]',
        "--risk-param-ranges", '{"sl_atr_multiplier":[1.0,3.0]}',
        "--output-dir", str(tmp_path / "reports"),
    ])
    mission_runner.main()

    mc = captured["mc"]
    assert mc.mission_id == "cli-test-mission"
    assert mc.symbols == ("EURUSD", "GBPUSD")
    assert mc.sampler == "random"
    assert mc.n_trials_per_symbol == 3
    assert mc.objective_metric == "sharpe_ratio"
    assert mc.min_trades == 5
    assert mc.seed == 99
    assert mc.search_space.timeframes_choices == (("H1",), ("H4", "D1", "H1"))
    assert mc.search_space.engine_set_choices == (("nnfx", "price_action"),)
    assert mc.search_space.risk_param_ranges == {"sl_atr_multiplier": (1.0, 3.0)}
    assert mc.search_space.context_filter_set_choices == (
        (),
        ({"name": "direction", "mode": "entry_filter", "params": {"allowed": ["BULLISH"]}, "weight": 0},),
    )
