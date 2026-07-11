"""
tests/test_leakage_guard.py
------------------------------
research/leakage_guard.py: point-in-time assertions for research scripts
(audit follow-up, 2026-07-11) — catches the bug shape behind the
trade-management "+100%" mirage and the raw H008 BOS+FVG result.
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from research import leakage_guard as lg


def _series(timestamps: list[str], **cols) -> pd.DataFrame:
    idx = pd.to_datetime(timestamps, utc=True)
    return pd.DataFrame({k: v for k, v in cols.items()}, index=idx)


def test_assert_no_future_rows_passes_when_all_past():
    df = _series(["2026-01-01", "2026-01-02"], value=[1, 2])
    lg.assert_no_future_rows(df, "2026-01-03")  # no raise


def test_assert_no_future_rows_raises_on_future_row():
    df = _series(["2026-01-01", "2026-01-05"], value=[1, 2])
    with pytest.raises(lg.LookaheadError, match="1 row"):
        lg.assert_no_future_rows(df, "2026-01-03")


def test_assert_no_future_rows_inclusive_catches_exact_match():
    df = _series(["2026-01-01", "2026-01-03"], value=[1, 2])
    lg.assert_no_future_rows(df, "2026-01-03")  # exclusive: the exact-match row is fine
    with pytest.raises(lg.LookaheadError):
        lg.assert_no_future_rows(df, "2026-01-03", inclusive=True)


def test_assert_no_future_rows_naive_timestamp_is_treated_as_utc():
    df = _series(["2026-01-01", "2026-01-05"], value=[1, 2])
    with pytest.raises(lg.LookaheadError):
        lg.assert_no_future_rows(df, pd.Timestamp("2026-01-03"))  # tz-naive as_of


def test_causal_slice_excludes_future_rows_by_default():
    df = _series(["2026-01-01", "2026-01-02", "2026-01-03"], value=[1, 2, 3])
    out = lg.causal_slice(df, "2026-01-02")
    assert list(out["value"]) == [1]  # strictly before, 01-02 excluded


def test_causal_slice_inclusive_includes_exact_match():
    df = _series(["2026-01-01", "2026-01-02", "2026-01-03"], value=[1, 2, 3])
    out = lg.causal_slice(df, "2026-01-02", inclusive=True)
    assert list(out["value"]) == [1, 2]


def test_align_last_known_returns_most_recent_prior_row():
    funding = _series(
        ["2026-01-01T00:00:00", "2026-01-01T08:00:00", "2026-01-01T16:00:00"],
        rate=[0.01, 0.02, 0.03],
    )
    # Decision at 09:00 — only the 00:00 and 08:00 rates are known.
    row = lg.align_last_known(funding, "2026-01-01T09:00:00")
    assert row["rate"] == 0.02


def test_align_last_known_returns_none_before_series_start():
    funding = _series(["2026-01-01T08:00:00"], rate=[0.02])
    row = lg.align_last_known(funding, "2026-01-01T00:00:00")
    assert row is None


def test_align_last_known_respects_publish_lag():
    # A rate timestamped at period-start but not knowable until 8h later.
    funding = _series(["2026-01-01T00:00:00"], rate=[0.01])
    # At 04:00, the 8h-lagged 00:00 rate is NOT yet knowable.
    assert lg.align_last_known(
        funding, "2026-01-01T04:00:00", publish_lag=timedelta(hours=8)
    ) is None
    # At 08:01, it is.
    row = lg.align_last_known(
        funding, "2026-01-01T08:01:00", publish_lag=timedelta(hours=8)
    )
    assert row["rate"] == 0.01


def test_align_last_known_exact_settlement_boundary_excluded():
    # A decision exactly AT the funding settlement timestamp must not see
    # that settlement's rate — it settles at that instant, not before it.
    funding = _series(["2026-01-01T08:00:00"], rate=[0.02])
    row = lg.align_last_known(funding, "2026-01-01T08:00:00")
    assert row is None


def test_align_last_known_value_cols_subset():
    df = _series(["2026-01-01"], rate=[0.02], open_interest=[100.0])
    row = lg.align_last_known(df, "2026-01-02", value_cols=["rate"])
    assert list(row.index) == ["rate"]


def test_assert_monotonic_timestamps_passes_on_sorted():
    df = _series(["2026-01-01", "2026-01-02"], value=[1, 2])
    lg.assert_monotonic_timestamps(df)  # no raise


def test_assert_monotonic_timestamps_raises_on_shuffled():
    df = pd.DataFrame(
        {"value": [1, 2]},
        index=pd.to_datetime(["2026-01-05", "2026-01-01"], utc=True),
    )
    with pytest.raises(lg.LookaheadError, match="not strictly increasing"):
        lg.assert_monotonic_timestamps(df)


def test_ts_col_variant_used_instead_of_index():
    df = pd.DataFrame({
        "ts": pd.to_datetime(["2026-01-01", "2026-01-05"], utc=True),
        "value": [1, 2],
    })
    with pytest.raises(lg.LookaheadError):
        lg.assert_no_future_rows(df, "2026-01-03", ts_col="ts")
    out = lg.causal_slice(df, "2026-01-03", ts_col="ts")
    assert list(out["value"]) == [1]
