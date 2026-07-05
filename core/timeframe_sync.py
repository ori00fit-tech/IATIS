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


# Bar duration in minutes — used to refuse fabricating finer bars from a
# coarser base (you cannot invent H1 candles out of D1 data).
_TF_MINUTES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}


def build_multi_timeframe_view(df_base: pd.DataFrame, timeframes: list[str]) -> dict[str, pd.DataFrame]:
    """Build a dict of {timeframe_label: DataFrame} from a single base series.

    df_base is timeframes[0]. Coarser timeframes are downsampled from it;
    timeframes FINER than the base are skipped (upsampling would fabricate
    bars that never existed). In live mode this function isn't the source
    of finer frames anyway — each timeframe is fetched natively; this
    matters for the synthetic/injected/backtest paths, e.g. a [D1, H4, H1]
    config over daily bars yields a D1-only view.
    """
    views: dict[str, pd.DataFrame] = {}
    base_label = timeframes[0]
    views[base_label] = df_base
    base_minutes = _TF_MINUTES.get(base_label, 60)

    for tf in timeframes[1:]:
        if _TF_MINUTES.get(tf, 60) < base_minutes:
            logger.debug(f"Skipping {tf}: finer than base {base_label}, cannot upsample")
            continue
        views[tf] = resample(df_base, tf)

    return views
