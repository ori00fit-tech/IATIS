"""
utils/helpers.py
-----------------
Small shared utilities: config loading, dict access helpers, ID generation.
Keep this file free of business logic — it should only ever contain
generic, reusable plumbing.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Governance-layer split (2026-07-12): these used to be inline blocks in
# config.yaml. Each file's top-level content is merged back into the
# effective config at the given dotted key so every existing caller of
# load_config() sees an identical structure to before the split — see
# each file's header comment for what changed (metadata added) vs. what
# didn't (the fields code actually reads).
_SPLIT_FILES: dict[str, tuple[str, ...]] = {
    "symbols.yaml": ("data", "twelve_data_symbols"),
    "engines.yaml": ("engines",),
    "risk.yaml": ("risk",),
    "ai.yaml": ("ai",),
}


def _set_nested(d: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def load_config(path: str | Path = PROJECT_ROOT / "config.yaml") -> dict[str, Any]:
    """Load the master YAML config into a plain dict.

    Merges the split-out governance files in config/ (symbols, engines,
    risk, ai) into the same effective structure the monolithic
    config.yaml used to produce, so every caller is unaffected.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        config: dict[str, Any] = yaml.safe_load(f) or {}

    config_dir = path.parent / "config"
    for filename, keys in _SPLIT_FILES.items():
        split_path = config_dir / filename
        if not split_path.exists():
            continue
        with open(split_path, "r", encoding="utf-8") as f:
            _set_nested(config, keys, yaml.safe_load(f))

    return config


def new_id(prefix: str = "id") -> str:
    """Generate a short unique id, useful for tagging trades/runs in logs."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def safe_get(d: dict, *keys, default=None):
    """Nested dict.get without raising on missing intermediate keys.

    Example: safe_get(config, "risk", "max_exposure", default=0.05)
    """
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur
