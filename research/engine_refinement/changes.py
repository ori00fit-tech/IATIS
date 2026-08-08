"""
research/engine_refinement/changes.py
-----------------------------------------
Engine Refinement V1's own change log — deliberately separate from
research/results/registry.json, whose schema is reserved for
pre-registered trading hypotheses with falsification criteria (CLAUDE.md
rule 1). A BUG_FIX/CAUSALITY_FIX/SEMANTIC_FIX/OBSERVABILITY entry does not
fit that schema and would pollute it — this module is the operator's own
explicit correction to an earlier draft of this plan that proposed
writing refinement changelog entries into registry.json.

Every entry answers, honestly, two questions a reviewer needs to sanity-
check without reading the diff: did this change strategy semantics
(strategy_semantics_changed), and was it motivated by backtest
performance (performance_driven)? Per the refinement plan's own rule,
performance_driven must always be False in this branch — any change
where it would be True must STOP and become a separate, pre-registered
hypothesis instead, never be silently folded in here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

_MANIFEST_PATH = Path(__file__).resolve().parent / "changes.json"

ChangeType = Literal["BUG_FIX", "CAUSALITY_FIX", "SEMANTIC_FIX", "OBSERVABILITY"]


def _load() -> dict:
    if _MANIFEST_PATH.exists():
        return json.loads(_MANIFEST_PATH.read_text())
    return {"_comment": (
        "Engine Refinement V1 change log. NOT research/results/registry.json "
        "(that schema is reserved for pre-registered trading hypotheses). "
        "Every entry here is additive-only, append via "
        "research.engine_refinement.changes.append_change()."
    ), "changes": []}


def append_change(
    change_id: str,
    change_type: ChangeType,
    engine: str,
    description: str,
    *,
    strategy_semantics_changed: bool,
    performance_driven: bool = False,
    commit: str | None = None,
) -> dict:
    """Append one entry to research/engine_refinement/changes.json.
    performance_driven must be False for anything landing on this branch
    — a True value here is a signal this change belongs in a separate,
    pre-registered hypothesis instead, per the refinement plan's own
    forbidden-list (§19)."""
    if performance_driven:
        raise ValueError(
            f"{change_id}: performance_driven=True is not allowed in Engine "
            f"Refinement V1 — register a pre-registered hypothesis instead "
            f"(see research/hypotheses/TEMPLATE.md)."
        )
    manifest = _load()
    entry = {
        "change_id": change_id,
        "type": change_type,
        "engine": engine,
        "description": description,
        "strategy_semantics_changed": strategy_semantics_changed,
        "performance_driven": performance_driven,
        "commit": commit,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["changes"].append(entry)
    _MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    return entry


def load_changes() -> list[dict]:
    return _load()["changes"]
