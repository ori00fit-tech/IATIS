"""
data_trust/quality.py
---------------------------------------
Phase 11 — the three-state trust assessment: VALID / VALID_WITH_WARNINGS /
INVALID, built ENTIRELY on top of the existing, unmodified core.
data_validator.validate_ohlcv()/find_gaps() -- never a reimplementation
of OHLC or gap detection logic.

Three independent checks, combined by MAX SEVERITY (never losing a
signal because another check also fired):

  1. validate_ohlcv(df)           -- reused verbatim (Phase 11 never
                                      touches core/data_validator.py).
                                      Any failure -> INVALID.
  2. assess_timezone(df)          -- NEW. tz-naive or non-UTC -> INVALID
                                      (operator's own locked decision:
                                      HARD, never a warning -- time
                                      identity underlies every gap/
                                      critical-window judgment this
                                      module makes).
  3. assess_missing_bars(...)     -- NEW, only run when the caller
                                      supplies `expected_freq` (never
                                      guessed). Classification per the
                                      operator's own locked table below.

NON-NEGOTIABLE: `critical_window` and `provenance` are ALWAYS caller-
supplied, NEVER inferred here from an engine name, a hypothesis_id,
config["engines"], a strategy name, or the timeframe alone -- there is
no hidden strategy knowledge anywhere in this package. A caller that
doesn't pass `critical_window` gets the maximally conservative default:
every gap is treated as critical.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from core.data_validator import DataValidationError, find_gaps, validate_ohlcv
from data_trust.manifest import VALID_PROVENANCE, DataTrustError, build_manifest

VALID = "VALID"
VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
INVALID = "INVALID"

_SEVERITY = {VALID: 0, VALID_WITH_WARNINGS: 1, INVALID: 2}


def assess_timezone(df: pd.DataFrame) -> dict[str, Any]:
    """Locked contract: tz-naive -> INVALID, non-UTC tz-aware -> INVALID,
    UTC tz-aware -> VALID. No provider gets an exemption for assuming
    UTC internally -- this is an independently testable trust point, not
    an inherited assumption."""
    tz = getattr(df.index, "tz", None)
    if tz is None:
        return {"timezone": None, "ok": False,
                "reason": "tz-naive index -- a decision-grade dataset must be tz-aware"}
    tz_name = str(tz)
    if tz_name.upper() != "UTC":
        return {"timezone": tz_name, "ok": False,
                "reason": f"index is tz-aware but not UTC (got {tz_name!r})"}
    return {"timezone": "UTC", "ok": True, "reason": None}


def assess_missing_bars(
    df: pd.DataFrame, expected_freq: str, critical_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> dict[str, Any]:
    """Reuses core.data_validator.find_gaps() for raw gap detection --
    this function only CLASSIFIES what it finds, never rediscovers gaps
    itself.

    critical_window: an explicit (start, end) timestamp range (inclusive
    on both ends) the CALLER owns -- e.g. converted from "the last 210
    decision-timeframe bars" by the caller itself. Locked table:

        no gaps                                -> VALID
        gaps only OUTSIDE critical_window       -> VALID_WITH_WARNINGS
        any gap INSIDE critical_window          -> INVALID
        critical_window=None + any gap          -> INVALID
        critical_window=None + no gaps          -> VALID
    """
    gaps = find_gaps(df, expected_freq)
    gaps_total = len(gaps)

    if gaps_total == 0:
        return {"gaps_total": 0, "gaps_in_critical_window": 0, "classification": VALID}

    if critical_window is None:
        return {"gaps_total": gaps_total, "gaps_in_critical_window": gaps_total, "classification": INVALID}

    window_start, window_end = critical_window
    gaps_in_critical = int(((gaps.index >= window_start) & (gaps.index <= window_end)).sum())
    classification = INVALID if gaps_in_critical > 0 else VALID_WITH_WARNINGS
    return {"gaps_total": gaps_total, "gaps_in_critical_window": gaps_in_critical, "classification": classification}


def assess_data_trust(
    df: pd.DataFrame, *, symbol: str, timeframe: str, provider: str, provenance: str,
    expected_freq: str | None = None, critical_window: tuple[pd.Timestamp, pd.Timestamp] | None = None,
) -> dict[str, Any]:
    """The SOLE orchestrating entry point. Runs the three checks above,
    combines them by max severity, then hands the result to data_trust.
    manifest.build_manifest() as an already-computed snapshot -- this
    function computes the quality result exactly once; build_manifest()
    never recomputes any part of it."""
    if provenance not in VALID_PROVENANCE:
        raise DataTrustError(f"assess_data_trust: provenance must be one of {VALID_PROVENANCE}, got {provenance!r}.")

    status = VALID
    reasons: list[str] = []

    try:
        validate_ohlcv(df)
    except DataValidationError as exc:
        status = INVALID
        reasons.append(f"OHLC validation failed: {exc}")

    tz_result = assess_timezone(df)
    if not tz_result["ok"]:
        status = INVALID
        reasons.append(f"timezone check failed: {tz_result['reason']}")

    missing_bars_result = None
    if expected_freq is not None:
        missing_bars_result = assess_missing_bars(df, expected_freq, critical_window)
        classification = missing_bars_result["classification"]
        if _SEVERITY[classification] > _SEVERITY[status]:
            status = classification
        if classification != VALID:
            reasons.append(
                f"missing bars: {missing_bars_result['gaps_total']} total, "
                f"{missing_bars_result['gaps_in_critical_window']} in critical window"
            )

    quality_result = {
        "validation_status": status, "reasons": reasons,
        "timezone": tz_result["timezone"], "missing_bars": missing_bars_result,
    }
    manifest = build_manifest(
        df, symbol=symbol, timeframe=timeframe, provider=provider, provenance=provenance,
        quality_result=quality_result,
    )
    return {"manifest": manifest, "validation_status": status, "reasons": reasons}
