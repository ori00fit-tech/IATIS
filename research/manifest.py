"""
research/manifest.py
-----------------------
Reproducibility manifest for research runs (audit item H2).

The production audit rejected the walk-forward PF claims as
NOT ENOUGH EVIDENCE because the registry stored only summary numbers:
no git commit, no config snapshot, no dataset fingerprints — nothing a
reviewer could use to re-run the experiment and get the same answer.

A manifest binds one research run to:
  - the exact git commit (and whether the working tree was dirty),
  - a SHA256 of config.yaml plus an embedded copy of the blocks that
    drive the backtest (confluence / engines / risk / market_quality),
  - SHA256 + bar-count + date-range per input dataset,
  - the run parameters and full results.

Manifests are written to research/results/ as *_manifest.json, which is
NOT gitignored (unlike *_result.json) — commit them with the run.
A manifest whose `git.dirty` is true or whose commit is unknown is
labeled reproducible=false: numbers from an untracked working tree
cannot be independently verified.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent / "results"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The config.yaml blocks that actually change backtest behavior — embedded
# verbatim so a manifest survives later config edits.
_BEHAVIOR_BLOCKS = ("confluence", "engines", "risk", "market_quality", "regime", "data")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_files(paths: list[Path]) -> str:
    """Combined SHA256 across multiple files, in a stable (sorted-by-name)
    order, with each file's name mixed in so boundary content can't
    collide between files. Used because config.yaml's `engines` / `risk`
    / `ai` / symbol universe blocks moved into config/*.yaml (2026-07-12)
    — a single-file hash of config.yaml alone would silently stop
    detecting drift in those blocks."""
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: str(x)):
        h.update(p.name.encode("utf-8"))
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def _governance_config_files() -> list[Path]:
    """config.yaml plus every split-out config/*.yaml file that exists."""
    files = [PROJECT_ROOT / "config.yaml"]
    split_dir = PROJECT_ROOT / "config"
    if split_dir.is_dir():
        files.extend(sorted(split_dir.glob("*.yaml")))
    return [p for p in files if p.exists()]


def _git_state() -> dict[str, Any]:
    """Current commit + dirty flag; never raises (git may be absent)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "unknown"
        # --untracked-files=no: only MODIFICATIONS TO TRACKED FILES make a
        # run non-reproducible — untracked runtime artifacts (decision
        # logs, caches, backups) don't change what code produced the
        # numbers. Without this, every VPS run was permanently labeled
        # NOT REPRODUCIBLE because the server always carries untracked
        # operational files (observed: 8 of 13 manifests falsely red).
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        ).stdout.strip())
        return {"commit": commit, "dirty": dirty}
    except Exception:
        return {"commit": "unknown", "dirty": True}


def dataset_fingerprint(csv_path: Path, df=None) -> dict[str, Any]:
    """SHA256 + shape of one input dataset. Pass the loaded DataFrame to
    also record bar count and date range."""
    fp: dict[str, Any] = {
        "file": str(csv_path),
        "sha256": _sha256_file(csv_path),
        "size_bytes": csv_path.stat().st_size,
    }
    if df is not None and len(df):
        fp["bars"] = int(len(df))
        fp["first"] = str(df.index[0])
        fp["last"] = str(df.index[-1])
    return fp


def build_manifest(
    *,
    kind: str,
    config: dict[str, Any],
    params: dict[str, Any],
    datasets: list[dict[str, Any]],
    results: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a reproducibility manifest for one research run.

    Args:
        kind: run type, e.g. "walk_forward".
        config: the loaded config.yaml dict the run actually used.
        params: run parameters (step, windows, CLI args...).
        datasets: list of dataset_fingerprint() dicts.
        results: the run's full results payload.
    """
    git = _git_state()
    config_files = _governance_config_files()
    return {
        "kind": kind,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": git,
        # An unverifiable run must say so on its face.
        "reproducible": git["commit"] != "unknown" and not git["dirty"],
        "config": {
            "sha256": _sha256_files(config_files) if config_files else None,
            # config.yaml plus config/*.yaml (symbols/engines/risk/ai
            # split, 2026-07-12) — every file that fed the sha256 above.
            "files": [str(p.relative_to(PROJECT_ROOT)) for p in sorted(config_files, key=lambda x: str(x))],
            "behavior_blocks": {k: config.get(k) for k in _BEHAVIOR_BLOCKS if k in config},
        },
        "params": params,
        "datasets": datasets,
        "results": results,
    }


def write_manifest(manifest: dict[str, Any], name: str) -> Path:
    """Write to research/results/<name>_manifest.json (tracked by git)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{name}_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, default=str))
    return out
