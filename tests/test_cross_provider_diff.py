"""
tests/test_cross_provider_diff.py
------------------------------------
scripts/cross_provider_diff.py's pure comparison logic (align_bars,
diff_bars) — no network. The concrete case this tool targets is already
on record: docs/STRATEGY_EVIDENCE_2026-07.md's NZDUSD broker-vs-TwelveData
H4 discrepancy, found by hand with no reusable tool at the time.
"""
from __future__ import annotations

import pandas as pd

from scripts.cross_provider_diff import align_bars, diff_bars


def _bars(timestamps: list[str], closes: list[float]) -> pd.DataFrame:
    idx = pd.to_datetime(timestamps, utc=True)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes},
        index=idx,
    )


def test_align_bars_keeps_only_shared_timestamps():
    a = _bars(["2026-01-01", "2026-01-02", "2026-01-03"], [1.0, 2.0, 3.0])
    b = _bars(["2026-01-02", "2026-01-03", "2026-01-04"], [2.0, 3.0, 4.0])
    aligned_a, aligned_b = align_bars(a, b)
    assert len(aligned_a) == len(aligned_b) == 2
    assert list(aligned_a.index) == list(aligned_b.index)


def test_align_bars_handles_naive_and_tz_aware_mix():
    a = _bars(["2026-01-01"], [1.0])
    b = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
                      index=pd.to_datetime(["2026-01-01"]))  # tz-naive
    aligned_a, aligned_b = align_bars(a, b)
    assert len(aligned_a) == 1


def test_diff_bars_identical_series_is_agree():
    a = _bars(["2026-01-01", "2026-01-02"], [1.1000, 1.1050])
    b = _bars(["2026-01-01", "2026-01-02"], [1.1000, 1.1050])
    result = diff_bars(a, b, provider_a="p1", provider_b="p2")
    assert result["verdict"] == "AGREE"
    assert result["close_diff_pct"]["mean"] == 0.0
    assert result["bars_exceeding_tolerance"] == 0


def test_diff_bars_material_disagreement_flagged():
    # provider b is off by >5% on every bar
    a = _bars(["2026-01-01", "2026-01-02"], [100.0, 100.0])
    b = _bars(["2026-01-01", "2026-01-02"], [110.0, 110.0])
    result = diff_bars(a, b, provider_a="p1", provider_b="p2", tolerance_pct=0.05)
    assert result["verdict"].startswith("MATERIAL_DISAGREEMENT")
    assert result["bars_exceeding_tolerance"] == 2
    assert result["pct_bars_exceeding_tolerance"] == 100.0


def test_diff_bars_minor_disagreement_below_material_threshold():
    # 2 of 100 bars exceed tolerance -> 2% -> MINOR, not MATERIAL
    closes_a = [100.0] * 100
    closes_b = [100.0] * 98 + [100.2, 100.2]  # 0.2% off on 2 bars
    ts = pd.date_range("2026-01-01", periods=100, freq="4h", tz="UTC")
    a = pd.DataFrame({"open": closes_a, "high": closes_a, "low": closes_a, "close": closes_a}, index=ts)
    b = pd.DataFrame({"open": closes_b, "high": closes_b, "low": closes_b, "close": closes_b}, index=ts)
    result = diff_bars(a, b, provider_a="p1", provider_b="p2", tolerance_pct=0.05)
    assert result["verdict"] == "MINOR_DISAGREEMENT"
    assert result["bars_exceeding_tolerance"] == 2


def test_diff_bars_no_overlap_reports_gracefully():
    a = _bars(["2020-01-01"], [1.0])
    b = _bars(["2025-01-01"], [1.0])
    result = diff_bars(a, b, provider_a="p1", provider_b="p2")
    assert result["bars_common"] == 0
    assert "NO_OVERLAP" in result["verdict"]


def test_diff_bars_reports_missing_bars_asymmetrically():
    a = _bars(["2026-01-01", "2026-01-02", "2026-01-03"], [1.0, 2.0, 3.0])
    b = _bars(["2026-01-02"], [2.0])
    result = diff_bars(a, b, provider_a="p1", provider_b="p2")
    assert result["bars_only_in_a"] == 2
    assert result["bars_only_in_b"] == 0
    assert result["bars_common"] == 1
