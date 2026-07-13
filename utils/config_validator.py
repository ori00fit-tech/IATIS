"""
utils/config_validator.py
--------------------------
Boot-time consistency checks for config.yaml + config/*.yaml.

Warn-only, by design: this module NEVER mutates config or auto-corrects
a mismatch. CLAUDE.md's promotion/freeze rules mean a weight or
threshold only ever changes via a pre-registered hypothesis and a clean
manifest — a validator silently "fixing" engines.enabled vs.
confluence.weights would be exactly the kind of mid-sample behavior
change that's forbidden. It only makes an existing inconsistency
visible in the boot log, the same way philosophy_audit.py surfaces a
PASSED-without-qualifying-evidence hypothesis without touching it.
"""

from __future__ import annotations

from typing import Any


def validate_config(config: dict[str, Any]) -> list[str]:
    """Return a list of human-readable warnings for a loaded config.

    Empty list = no inconsistencies found. Callers should log each
    warning at startup; none of them are fatal.
    """
    warnings: list[str] = []
    warnings.extend(_check_engine_weight_consistency(config))
    return warnings


def _check_engine_weight_consistency(config: dict[str, Any]) -> list[str]:
    enabled = (config.get("engines", {}) or {}).get("enabled", {}) or {}
    weights = (config.get("confluence", {}) or {}).get("weights", {}) or {}

    warnings: list[str] = []
    for name, is_enabled in enabled.items():
        weight = weights.get(name)
        if weight is None:
            continue
        if is_enabled and weight == 0:
            warnings.append(
                f"engines.enabled.{name}=true but confluence.weights.{name}=0 "
                "— this engine can never move the score."
            )
        elif not is_enabled and weight != 0:
            warnings.append(
                f"engines.enabled.{name}=false but confluence.weights.{name}={weight} "
                "— a disabled engine still carries nonzero weight metadata "
                "(harmless if scoring only sums enabled engines, but check)."
            )
    return warnings
