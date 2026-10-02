"""tests/test_hypothesis_version.py -- tests for backtest/hypothesis_
version.py (Hypothesis Discovery Engine, Phase 12 — explicit
hypothesis_version for registry.json entries)."""
from __future__ import annotations

import inspect

import pytest

from backtest import hypothesis_version as hv


# --- validate_hypothesis_version ---------------------------------------


def test_validate_accepts_absent_version():
    hv.validate_hypothesis_version({"title": "x"})  # no exception


@pytest.mark.parametrize("version", ["v1", "v2", "v10", "v0"])
def test_validate_accepts_well_formed_versions(version):
    hv.validate_hypothesis_version({"hypothesis_version": version})  # no exception


@pytest.mark.parametrize("version", ["1", "version1", "v", "v1.0", "V1", "v-1", ""])
def test_validate_rejects_malformed_versions(version):
    with pytest.raises(hv.HypothesisVersionError, match="invalid hypothesis_version"):
        hv.validate_hypothesis_version({"hypothesis_version": version})


def test_validate_treats_none_value_as_absent_never_malformed():
    """{"hypothesis_version": None} is indistinguishable from the key
    being absent entirely -- both mean "no version recorded yet"."""
    hv.validate_hypothesis_version({"hypothesis_version": None})  # no exception


def test_validate_rejects_non_string_version():
    with pytest.raises(hv.HypothesisVersionError):
        hv.validate_hypothesis_version({"hypothesis_version": 1})


# --- next_hypothesis_version ---------------------------------------------


def test_next_version_is_v1_when_absent():
    assert hv.next_hypothesis_version({"title": "x"}) == "v1"


def test_next_version_increments():
    assert hv.next_hypothesis_version({"hypothesis_version": "v1"}) == "v2"
    assert hv.next_hypothesis_version({"hypothesis_version": "v9"}) == "v10"


def test_next_version_raises_on_malformed_existing_value_never_guesses():
    with pytest.raises(hv.HypothesisVersionError):
        hv.next_hypothesis_version({"hypothesis_version": "not-a-version"})


# --- with_bumped_version ---------------------------------------------------


def test_with_bumped_version_returns_a_new_dict():
    entry = {"title": "x", "hypothesis_version": "v1"}
    before = dict(entry)
    updated = hv.with_bumped_version(entry)

    assert updated["hypothesis_version"] == "v2"
    assert updated is not entry
    assert entry == before  # input never mutated


def test_with_bumped_version_preserves_other_fields():
    entry = {"title": "x", "status": "RESEARCH", "hypothesis_version": "v3"}
    updated = hv.with_bumped_version(entry)
    assert updated["title"] == "x"
    assert updated["status"] == "RESEARCH"
    assert updated["hypothesis_version"] == "v4"


def test_with_bumped_version_from_absent_starts_at_v1():
    updated = hv.with_bumped_version({"title": "x"})
    assert updated["hypothesis_version"] == "v1"


# --- no lineage / no linkage between different HXXX ids ---------------------


def test_module_has_no_parent_child_or_lineage_concept():
    public_names = [n for n in dir(hv) if not n.startswith("_")]
    forbidden_substrings = ("parent", "child", "lineage", "link_hypothesis")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"hypothesis_version unexpectedly exposes {name!r}"


# --- structural: no I/O, no registry.json writes ----------------------------


def _source_without_module_docstring() -> str:
    source = inspect.getsource(hv)
    return source.split('"""', 2)[-1]


def test_module_performs_no_file_io():
    body = _source_without_module_docstring()
    forbidden = ("open(", "with open", "registry.json", "json.dump", "Path(")
    for pattern in forbidden:
        assert pattern not in body, f"hypothesis_version unexpectedly references {pattern!r}"


def test_nothing_outside_tests_imports_hypothesis_version_yet():
    from pathlib import Path

    candidates = [Path("scheduler.py"), Path("main.py")]
    execution_dir = Path("execution")
    if execution_dir.exists():
        candidates.extend(execution_dir.rglob("*.py"))
    for path in candidates:
        if not path.exists():
            continue
        text = path.read_text()
        assert "hypothesis_version" not in text, f"{path} unexpectedly references hypothesis_version"
