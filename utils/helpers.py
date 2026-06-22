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


def load_config(path: str | Path = PROJECT_ROOT / "config.yaml") -> dict[str, Any]:
    """Load the master YAML config into a plain dict."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
