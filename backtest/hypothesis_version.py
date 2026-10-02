"""
backtest/hypothesis_version.py
---------------------------------------
Hypothesis Discovery Engine, Phase 12 — explicit hypothesis_version for
research/results/registry.json entries.

LOCKED SEMANTICS (operator's own final decision): `hypothesis_version`
tracks the evolution of the SAME HXXX entry over time (e.g. H017 v1 ->
v2 -> v3 as its own recorded formulation is corrected/refined) -- it is
NOT a lineage/parent-child link between DIFFERENT hypothesis numbers.
A genuinely new hypothesis gets a brand-new HXXX id under the existing
registry convention (CLAUDE.md: "V1 -> failed, V2 -> new hypothesis");
there is no "H017 v3 -> H018 v1" linkage mechanism here, and none is
needed by Phase 12.

This is distinct from, and must never be confused with, backtest.
research_matrix.compute_cell_fingerprint() -- the fingerprint is a
DATASET/IMPLEMENTATION identity (changes automatically the instant any
engine/timeframe/preset/provider/commit input changes); hypothesis_
version is a LIFECYCLE/SCHEMA identity for the registry ENTRY itself,
bumped deliberately, never automatically derived from a fingerprint.

NON-NEGOTIABLE: every function here is pure and performs NO file I/O.
Nothing in this module reads or writes research/results/registry.json
itself -- callers pass in (and receive back) plain dicts; applying a
change to the real file is left entirely to existing registry tooling,
never a new write path invented here.
"""
from __future__ import annotations

import re
from typing import Any

_VERSION_PATTERN = re.compile(r"^v(\d+)$")


class HypothesisVersionError(Exception):
    """Raised only for a malformed `hypothesis_version` value -- an
    absent field is always valid (treated as "no version recorded yet")."""


def validate_hypothesis_version(entry: dict[str, Any]) -> None:
    """A present `hypothesis_version` must match the literal pattern
    'vN' (N a non-negative integer, e.g. 'v1', 'v2', ... 'v10'). Absent
    is valid and raises nothing."""
    version = entry.get("hypothesis_version")
    if version is None:
        return
    if not isinstance(version, str) or not _VERSION_PATTERN.match(version):
        raise HypothesisVersionError(
            f"invalid hypothesis_version {version!r} -- must match 'vN' (e.g. 'v1', 'v2')."
        )


def next_hypothesis_version(entry: dict[str, Any]) -> str:
    """'v1' if `entry` has no hypothesis_version yet; otherwise the
    integer suffix incremented by one ('v2' -> 'v3', ...). Raises
    HypothesisVersionError first if the existing value is malformed --
    never silently "repairs" a bad value into a guessed next version."""
    validate_hypothesis_version(entry)
    version = entry.get("hypothesis_version")
    if version is None:
        return "v1"
    n = int(_VERSION_PATTERN.match(version).group(1))
    return f"v{n + 1}"


def with_bumped_version(entry: dict[str, Any]) -> dict[str, Any]:
    """Returns a NEW dict -- a shallow copy of `entry` with
    `hypothesis_version` set to next_hypothesis_version(entry). Never
    mutates the input dict, never touches any file."""
    updated = dict(entry)
    updated["hypothesis_version"] = next_hypothesis_version(entry)
    return updated
