"""
tests/test_config_validator.py
---------------------------------
utils/config_validator.py — boot-time, warn-only consistency checks.
Must never mutate config or change engine/weight behavior; it only
produces human-readable warning strings for the boot log.
"""
from __future__ import annotations

from utils.config_validator import validate_config


def test_validate_config_no_warnings_when_consistent():
    config = {
        "engines": {"enabled": {"a": True, "b": False}},
        "confluence": {"weights": {"a": 0.5, "b": 0.0}},
    }
    assert validate_config(config) == []


def test_validate_config_warns_enabled_engine_with_zero_weight():
    config = {
        "engines": {"enabled": {"a": True}},
        "confluence": {"weights": {"a": 0.0}},
    }
    warnings = validate_config(config)
    assert len(warnings) == 1
    assert "engines.enabled.a=true" in warnings[0]


def test_validate_config_warns_disabled_engine_with_nonzero_weight():
    config = {
        "engines": {"enabled": {"a": False}},
        "confluence": {"weights": {"a": 0.3}},
    }
    warnings = validate_config(config)
    assert len(warnings) == 1
    assert "engines.enabled.a=false" in warnings[0]


def test_validate_config_ignores_engine_missing_from_weights():
    config = {
        "engines": {"enabled": {"a": True}},
        "confluence": {"weights": {}},
    }
    assert validate_config(config) == []


def test_validate_config_handles_missing_sections_gracefully():
    assert validate_config({}) == []
    assert validate_config({"engines": {}}) == []
    assert validate_config({"confluence": {}}) == []


def test_validate_config_never_mutates_input():
    config = {
        "engines": {"enabled": {"a": True}},
        "confluence": {"weights": {"a": 0.0}},
    }
    import copy
    before = copy.deepcopy(config)
    validate_config(config)
    assert config == before
