"""tests/test_replay.py — decision replay harness (research/replay.py, S2).

The golden property: a decision replayed from its stored artifact must
reproduce the ORIGINAL verdict, score, votes, prices and data
fingerprints exactly. A deliberate config change must surface as a diff
(or a changed verdict), never pass silently.
"""
from __future__ import annotations

import json

import pytest

from research import replay as replay_mod
from utils.helpers import load_config


def _pipeline_config() -> dict:
    """Real config.yaml, forced offline and DETERMINISTIC: a seeded
    synthetic frame is injected directly (the plain synthetic source
    draws a fresh random walk per run — flaky branches), news + telegram
    off. Seed 42 reproducibly yields NO_TRADE, confluence score 55.0,
    votes SMC=BEARISH(39) / NNFX=BULLISH(55) / PA,Wyckoff=NEUTRAL."""
    from core.data_loader import load_synthetic
    config = load_config()
    config["data"]["source"] = "injected"
    config["data"]["symbol"] = "EURUSD"
    config["data"]["_injected_df"] = load_synthetic(bars=400, timeframe="H1", seed=42)
    config.setdefault("fundamentals", {})["news_filter_enabled"] = False
    config.setdefault("telegram", {})["enabled"] = False
    # Persist a window for EVERY verdict so the test never depends on the
    # synthetic walk happening to produce an EXECUTE.
    config.setdefault("system", {})["persist_replay_windows"] = "all"
    return config


@pytest.fixture
def windows_dir(tmp_path, monkeypatch):
    d = tmp_path / "replay_windows"
    monkeypatch.setattr(replay_mod, "DEFAULT_DIR", d)
    return d


def _run_and_capture(config) -> dict:
    from main import run_pipeline
    return run_pipeline(config)


def test_golden_replay_reproduces_decision_exactly(windows_dir):
    report = _run_and_capture(_pipeline_config())
    artifacts = list(windows_dir.glob("*.json"))
    assert len(artifacts) == 1, "pipeline should have persisted exactly one window"

    result = replay_mod.replay(artifacts[0])
    assert result.diffs == []
    assert result.identical
    assert result.replayed_verdict == report["final_verdict"]


def test_replay_artifact_is_self_contained(windows_dir):
    _run_and_capture(_pipeline_config())
    artifact = json.loads(next(windows_dir.glob("*.json")).read_text())
    assert artifact["schema"] == 1
    assert artifact["frames"], "input frames must be embedded"
    assert artifact["config"]["confluence"], "effective config must be embedded"
    assert artifact["original"]["engine_votes"], "original votes must be embedded"
    # No injection leftovers may be persisted.
    assert not any(k.startswith("_") for k in artifact["config"]["data"])


def test_replay_mode_writes_nothing(windows_dir, fake_d1):
    """The replayed run must leave no trace: no decision rows, no outcome
    rows, no shadow signals, no new artifacts."""
    _run_and_capture(_pipeline_config())
    artifact = next(windows_dir.glob("*.json"))

    before_decisions = fake_d1.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
    before_artifacts = len(list(windows_dir.glob("*.json")))

    replay_mod.replay(artifact)

    after_decisions = fake_d1.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
    assert after_decisions == before_decisions, "replay wrote a decision row"
    assert len(list(windows_dir.glob("*.json"))) == before_artifacts, "replay persisted a window"


def test_config_change_is_detected(windows_dir):
    """The regression property: replaying under a modified config must not
    silently pass. With the seeded frame (SMC=BEARISH weight-0.202 vs
    NNFX=BULLISH weight-0.227 → NNFX wins, score 55), inverting the two
    weights flips the majority to SMC (score 39) — the diff MUST surface."""
    _run_and_capture(_pipeline_config())
    path = next(windows_dir.glob("*.json"))

    artifact = json.loads(path.read_text())
    artifact["config"]["confluence"]["weights"]["smc"] = 0.5
    artifact["config"]["confluence"]["weights"]["nnfx"] = 0.05
    tampered = path.parent / "tampered.json"
    tampered.write_text(json.dumps(artifact))

    result = replay_mod.replay(tampered)
    assert not result.identical, "weight change must produce a diff"
    assert any(d.startswith("confluence.score") for d in result.diffs), result.diffs
    # The data itself was untouched — fingerprints must still match.
    assert all("sha256" not in d for d in result.diffs)


def test_data_tampering_is_detected(windows_dir):
    """Changed input bars must show up as a data-fingerprint diff even if
    the verdict happens to survive."""
    _run_and_capture(_pipeline_config())
    path = next(windows_dir.glob("*.json"))

    artifact = json.loads(path.read_text())
    tf = next(iter(artifact["frames"]))
    artifact["frames"][tf]["data"][-1][3] *= 1.01  # nudge one close by 1%
    tampered = path.parent / "tampered_data.json"
    tampered.write_text(json.dumps(artifact))

    result = replay_mod.replay(tampered)
    assert not result.identical
    assert any("sha256" in d or "score" in d or "final_verdict" in d for d in result.diffs)


def test_replay_all_and_cli_exit_codes(windows_dir, monkeypatch, capsys):
    _run_and_capture(_pipeline_config())
    results = replay_mod.replay_all(windows_dir)
    assert len(results) == 1 and results[0].identical

    monkeypatch.setattr("sys.argv", ["replay", "--all", "--dir", str(windows_dir)])
    assert replay_mod.main() == 0
    out = capsys.readouterr().out
    assert "IDENTICAL" in out
