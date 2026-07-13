"""
tests/test_research_manifest.py
----------------------------------
research/manifest.py (audit item H2): a research run's numbers are only
evidence if they are bound to the exact code, config, and data that
produced them.
"""
from __future__ import annotations

import json

import pandas as pd

from research import manifest as m


def _tiny_csv(tmp_path, content: str = "datetime,open,high,low,close\n2024-01-01,1,2,0.5,1.5\n"):
    p = tmp_path / "EURUSD_H1_2y.csv"
    p.write_text(content)
    return p


def test_dataset_fingerprint_hashes_content(tmp_path):
    p = _tiny_csv(tmp_path)
    fp1 = m.dataset_fingerprint(p)
    assert fp1["sha256"] and fp1["size_bytes"] == p.stat().st_size

    p.write_text(p.read_text() + "2024-01-02,1.5,2.5,1.0,2.0\n")
    fp2 = m.dataset_fingerprint(p)
    assert fp2["sha256"] != fp1["sha256"]


def test_dataset_fingerprint_records_bars_and_range(tmp_path):
    p = _tiny_csv(tmp_path)
    df = pd.DataFrame(
        {"close": [1.0, 2.0]},
        index=pd.to_datetime(["2024-01-01", "2024-06-30"]),
    )
    fp = m.dataset_fingerprint(p, df)
    assert fp["bars"] == 2
    assert "2024-01-01" in fp["first"] and "2024-06-30" in fp["last"]


def test_build_manifest_binds_git_config_and_data(tmp_path):
    p = _tiny_csv(tmp_path)
    manifest = m.build_manifest(
        kind="walk_forward",
        config={"confluence": {"min_score_to_trade": 58}, "engines": {"enabled": {"smc": True}}},
        params={"step_bars": 4},
        datasets=[m.dataset_fingerprint(p)],
        results={"EURUSD": {"consistency": "TEST"}},
    )
    assert manifest["kind"] == "walk_forward"
    assert manifest["git"]["commit"]  # never empty — "unknown" at worst
    assert manifest["config"]["behavior_blocks"]["confluence"]["min_score_to_trade"] == 58
    assert manifest["datasets"][0]["sha256"]
    # This repo checkout has uncommitted test scratch → the flag must be
    # coherent with the git state, not hardcoded true.
    assert manifest["reproducible"] == (
        manifest["git"]["commit"] != "unknown" and not manifest["git"]["dirty"]
    )


def test_build_manifest_config_hash_covers_split_governance_files(tmp_path, monkeypatch):
    """config.yaml's engines/risk/ai/symbols blocks moved into
    config/*.yaml (2026-07-12) — the manifest's config sha256 must still
    change when one of those split files changes, or a manifest would
    silently stop detecting drift in exactly the blocks it exists to
    protect (CLAUDE.md: negative/positive results committed with
    reproducible manifests)."""
    monkeypatch.setattr(m, "PROJECT_ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text("confluence:\n  min_score_to_trade: 58\n")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "risk.yaml").write_text("risk_per_trade_max: 0.01\n")

    p = _tiny_csv(tmp_path)
    first = m.build_manifest(
        kind="walk_forward", config={}, params={}, datasets=[m.dataset_fingerprint(p)], results={},
    )
    assert "config/risk.yaml" in first["config"]["files"]

    (config_dir / "risk.yaml").write_text("risk_per_trade_max: 0.02\n")
    second = m.build_manifest(
        kind="walk_forward", config={}, params={}, datasets=[m.dataset_fingerprint(p)], results={},
    )
    assert first["config"]["sha256"] != second["config"]["sha256"]


def test_write_manifest_lands_in_tracked_results_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "RESULTS_DIR", tmp_path / "results")
    out = m.write_manifest({"kind": "walk_forward", "x": 1}, "walk_forward_20260705")
    assert out.name == "walk_forward_20260705_manifest.json"
    assert json.loads(out.read_text())["x"] == 1


def test_manifest_filename_is_not_gitignored():
    """*_result.json is gitignored; *_manifest.json must NOT be, otherwise
    the whole point (committing the evidence) silently fails."""
    gitignore = (m.PROJECT_ROOT / ".gitignore").read_text()
    assert "research/results/*_result.json" in gitignore
    assert "_manifest" not in gitignore
