"""
research/leakage_guard.py
---------------------------
Reusable point-in-time / causal-join assertions for research scripts.

This project has already been bitten twice by the same bug shape: the
trade-management "+100%" result (docs/STRATEGY_EVIDENCE_2026-07.md) and the
first BOS+FVG measurement (H008, corrected by H008c) were both look-ahead
artifacts — a value computed with information not yet available at the
decision timestamp. Both fixes were bespoke, written once inside the
offending script. This module extracts the pattern into something every
future hypothesis (starting with H019's funding-rate alignment) can import
instead of re-deriving.

Scope, stated honestly: this is a set of RUNTIME ASSERTIONS over point-in-time
joins, not a static analyzer. It cannot detect every way a script can leak
information (e.g. fitting a parameter on the full dataset before slicing).
It catches the specific, recurring shape: "a value was pulled from a
time-indexed source that hadn't published yet as of the decision timestamp."
Use it at every join between a decision timestamp and an external time series
(funding rates, OI, news, macro releases, resampled bars).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Sequence

import pandas as pd


class LookaheadError(Exception):
    """Raised when a causal-join assertion finds information from the future."""


def _to_utc_index(index: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    return idx


def _to_utc_ts(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def assert_no_future_rows(
    df: pd.DataFrame,
    as_of: "pd.Timestamp | str",
    *,
    ts_col: str | None = None,
    inclusive: bool = False,
    label: str = "dataframe",
) -> None:
    """Raise LookaheadError if any row's timestamp is after (or, with
    inclusive=True, at-or-after) `as_of`.

    Pass ts_col=None to check the DataFrame's index; pass a column name to
    check a timestamp column instead (e.g. a raw funding-rate table indexed
    by an unrelated id column).
    """
    as_of_ts = _to_utc_ts(as_of)
    ts_values = _to_utc_index(df[ts_col]) if ts_col else _to_utc_index(df.index)
    violating = ts_values >= as_of_ts if inclusive else ts_values > as_of_ts
    if bool(violating.any()):
        n = int(violating.sum())
        worst = ts_values[violating].max()
        raise LookaheadError(
            f"{label}: {n} row(s) timestamped at/after the decision point "
            f"{as_of_ts.isoformat()} (latest offending: {worst.isoformat()}). "
            f"This data would not have existed yet at decision time."
        )


def causal_slice(
    df: pd.DataFrame,
    as_of: "pd.Timestamp | str",
    *,
    ts_col: str | None = None,
    inclusive: bool = False,
) -> pd.DataFrame:
    """Return only the rows of `df` known as of `as_of`. The safe way to
    build a point-in-time view — use this instead of `df[df.index <= x]`
    scattered ad hoc through research scripts, so the semantics (UTC
    normalization, inclusive/exclusive) are enforced in one place."""
    as_of_ts = _to_utc_ts(as_of)
    ts_values = _to_utc_index(df[ts_col]) if ts_col else _to_utc_index(df.index)
    mask = (ts_values <= as_of_ts) if inclusive else (ts_values < as_of_ts)
    return df.loc[mask]


def align_last_known(
    df: pd.DataFrame,
    as_of: "pd.Timestamp | str",
    *,
    ts_col: str | None = None,
    value_cols: Sequence[str] | None = None,
    publish_lag: timedelta = timedelta(0),
) -> pd.Series | None:
    """The most recent row of `df` published strictly before `as_of`,
    honoring an optional publish_lag (e.g. a metric that is timestamped at
    period-start but not actually published/knowable until period-end).

    This is the exact primitive H019 needs for funding-rate alignment:
    Binance settles funding on a fixed schedule, so
    `align_last_known(funding_df, decision_ts)` returns the rate that was
    ACTUALLY known at decision time — never a rate whose settlement is at
    or after the decision, and never a forward-filled value from a future
    aggregate. Returns None if nothing qualifies (e.g. decision predates
    the series).
    """
    as_of_ts = _to_utc_ts(as_of)
    ts_values = _to_utc_index(df[ts_col]) if ts_col else _to_utc_index(df.index)
    knowable_at = ts_values + publish_lag
    eligible = knowable_at < as_of_ts
    if not bool(eligible.any()):
        return None
    candidates = df.loc[eligible].copy()
    candidates = candidates.set_axis(ts_values[eligible], axis=0)
    row = candidates.sort_index().iloc[-1]
    return row[list(value_cols)] if value_cols else row


def assert_monotonic_timestamps(df: pd.DataFrame, *, ts_col: str | None = None,
                                 label: str = "dataframe") -> None:
    """Raise LookaheadError if timestamps are not strictly increasing — a
    common precondition for causal_slice/align_last_known to behave
    correctly, and a cheap early check for a shuffled or mis-joined series."""
    ts_values = _to_utc_index(df[ts_col]) if ts_col else _to_utc_index(df.index)
    if not ts_values.is_monotonic_increasing:
        raise LookaheadError(
            f"{label}: timestamps are not strictly increasing — causal_slice "
            f"and align_last_known assume sorted input."
        )
