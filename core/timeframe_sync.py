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


def resample(df: pd.DataFrame, target_timeframe: str, base_minutes: int | None = None) -> pd.DataFrame:
    """Resample an OHLCV DataFrame to a higher timeframe.

    base_minutes: bar duration (minutes) of `df` itself, if known. When
    given, the resampled series' LAST row is dropped if `df`'s own last
    bar doesn't reach that row's period end — i.e. the coarser candle is
    still forming. This is the same still-forming-bar protection BUG-010
    added for live fetches (core/data_providers.py::_drop_still_forming_bar),
    generalized to the resample-from-a-finer-series construction path used
    by backtests/injected/replay data. Without this, a D1 view built from
    a truncated intraday window (e.g. a backtest's df.iloc[:i+1]) silently
    reads a PARTIAL "today" candle instead of the last fully-closed day —
    directly undermining the D1-confirmation score every backtest trade
    decision relies on (BUG-011).
    """
    if target_timeframe not in _RESAMPLE_RULE:
        raise ValueError(f"Unsupported timeframe: {target_timeframe}")

    rule = _RESAMPLE_RULE[target_timeframe]
    out = df.resample(rule).agg(_AGG).dropna()
    if base_minutes is not None and len(out) > 0 and len(df) > 0:
        period_end = out.index[-1] + pd.tseries.frequencies.to_offset(rule)
        last_bar_covers_to = df.index[-1] + pd.Timedelta(minutes=base_minutes)
        if last_bar_covers_to < period_end:
            out = out.iloc[:-1]
    logger.debug(f"Resampled {len(df)} bars -> {len(out)} bars @ {target_timeframe}")
    return out


# Bar duration in minutes — used to refuse fabricating finer bars from a
# coarser base (you cannot invent H1 candles out of D1 data).
_TF_MINUTES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}

# Backtesting Lab Pro Phase B (2026-07-27) — the only timeframe labels this
# module can correctly resample/label (matches _RESAMPLE_RULE/_TF_MINUTES
# exactly). Any other label (M1, M5, M30, W1) would silently fall through
# _TF_MINUTES.get(tf, 60)'s 60-minute default — a real correctness bug, not
# just an unsupported choice — so a per-run timeframe override must be
# restricted to exactly this set.
SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(_TF_MINUTES.keys())


def build_multi_timeframe_view(df_base: pd.DataFrame, timeframes: list[str]) -> dict[str, pd.DataFrame]:
    """Build a dict of {timeframe_label: DataFrame} from a single base series.

    df_base is timeframes[0]. Coarser timeframes are downsampled from it;
    timeframes FINER than the base are skipped (upsampling would fabricate
    bars that never existed). In live mode this function isn't the source
    of finer frames anyway — each timeframe is fetched natively; this
    matters for the synthetic/injected/backtest paths, e.g. a [D1, H4, H1]
    config over daily bars yields a D1-only view.

    Every label in `timeframes` must be in SUPPORTED_TIMEFRAMES. This used
    to fall through _TF_MINUTES.get(tf, 60)'s 60-minute default for an
    unsupported label (e.g. "M1"/"M30"/"W1") — a real correctness bug
    (2026-08-13 forensic pass): a M30 base could be silently misjudged
    "not finer than H1" and resampled as if it were H1-based, producing
    wrong-cadence candles with no error anywhere. Fail loud instead.
    """
    unsupported = [tf for tf in timeframes if tf not in SUPPORTED_TIMEFRAMES]
    if unsupported:
        raise ValueError(
            f"Unsupported timeframe(s) {unsupported} — build_multi_timeframe_view "
            f"only understands {SUPPORTED_TIMEFRAMES}."
        )

    views: dict[str, pd.DataFrame] = {}
    base_label = timeframes[0]
    views[base_label] = df_base
    base_minutes = _TF_MINUTES[base_label]

    for tf in timeframes[1:]:
        if _TF_MINUTES[tf] < base_minutes:
            logger.debug(f"Skipping {tf}: finer than base {base_label}, cannot upsample")
            continue
        views[tf] = resample(df_base, tf, base_minutes=base_minutes)

    return views
