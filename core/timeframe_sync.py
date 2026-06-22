"""
core/timeframe_sync.py
------------------------
Resample a base timeframe up into higher timeframes (HTF) so engines that
need multi-timeframe context (e.g. SMC's HTF bias, ICT premium/discount)
all read from one consistent source instead of each engine resampling
independently and risking subtle mismatches.
"""

from __future__ import annotations

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Maps our internal timeframe labels to pandas resample rule strings
_RESAMPLE_RULE = {
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}

_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


def resample(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    """Resample an OHLCV DataFrame to a higher timeframe."""
    if target_timeframe not in _RESAMPLE_RULE:
        raise ValueError(f"Unsupported timeframe: {target_timeframe}")

    rule = _RESAMPLE_RULE[target_timeframe]
    out = df.resample(rule).agg(_AGG).dropna()
    logger.debug(f"Resampled {len(df)} bars -> {len(out)} bars @ {target_timeframe}")
    return out


def build_multi_timeframe_view(df_base: pd.DataFrame, timeframes: list[str]) -> dict[str, pd.DataFrame]:
    """Build a dict of {timeframe_label: DataFrame} from a single base series.

    Assumes df_base is the lowest (finest) timeframe in `timeframes`.
    """
    views: dict[str, pd.DataFrame] = {}
    base_label = timeframes[0]
    views[base_label] = df_base

    for tf in timeframes[1:]:
        views[tf] = resample(df_base, tf)

    return views
