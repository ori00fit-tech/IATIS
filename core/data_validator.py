"""
core/data_validator.py
-----------------------
Sanity checks on OHLCV data before it's allowed into the engines.
This is intentionally strict: per the system's "no-trade intelligence"
principle, bad data should produce a validation failure, not a guess.
"""

from __future__ import annotations

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataValidationError(Exception):
    """Raised when OHLCV data fails sanity checks and must not be used."""


def validate_ohlcv(df: pd.DataFrame) -> bool:
    """Run a battery of structural checks. Raises DataValidationError on failure.

    Checks performed:
      - required columns present
      - no nulls
      - high >= low, high >= open/close, low <= open/close
      - monotonically increasing datetime index
      - no duplicate timestamps
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")

    if df[REQUIRED_COLUMNS].isnull().any().any():
        raise DataValidationError("Null values found in OHLCV data")

    if not (df["high"] >= df["low"]).all():
        raise DataValidationError("Found bars where high < low")

    if not ((df["high"] >= df["open"]) & (df["high"] >= df["close"])).all():
        raise DataValidationError("Found bars where high is not the max of open/close")

    if not ((df["low"] <= df["open"]) & (df["low"] <= df["close"])).all():
        raise DataValidationError("Found bars where low is not the min of open/close")

    if not df.index.is_monotonic_increasing:
        raise DataValidationError("Datetime index is not sorted ascending")

    if df.index.duplicated().any():
        raise DataValidationError("Duplicate timestamps found in data")

    logger.info(f"Validation passed: {len(df)} bars OK")
    return True


def find_gaps(df: pd.DataFrame, expected_freq: str) -> pd.DataFrame:
    """Return the timestamps where an expected bar is missing.

    Useful diagnostic — does not raise, just reports. Gap handling policy
    (forward-fill vs reject) is a Phase 2+ decision once we're on real data.
    """
    full_range = pd.date_range(start=df.index.min(), end=df.index.max(), freq=expected_freq)
    missing = full_range.difference(df.index)
    if len(missing) > 0:
        logger.warning(f"Found {len(missing)} gaps in data at expected freq={expected_freq}")
    return pd.DataFrame(index=missing)
