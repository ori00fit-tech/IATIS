"""
tests/test_macro_benchmark.py
---------------------------------
Provider Benchmark & Data Quality Lab Phase 3 — pure-function tests for
backtest/macro_benchmark.py's scoring math, plus hard-block safety tests
proving this module never writes to config.yaml, config/symbols.yaml,
config/engines.yaml, or registry.json, and never calls
core.alt_data_loader.load_macro_snapshot() (the Macro engine's live
source).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd
import pytest

from backtest.macro_benchmark import (
    PROFILES,
    SERIES_CADENCE,
    SERIES_PROVIDERS,
    completeness_score,
    composite_score,
    cross_provider_agreement_score,
    freshness_score,
    run_benchmark,
    score_series,
    timestamp_integrity_score,
)


def _close_frame(dates: list[str], values: list[float]) -> pd.DataFrame:
    idx = pd.to_datetime(dates, utc=True, format="mixed")
    s = pd.Series(values, index=idx).sort_index()
    return pd.DataFrame({"open": s, "high": s, "low": s, "close": s, "volume": 0.0})


# ── catalog sanity ──────────────────────────────────────────────────

def test_only_three_series_have_two_providers():
    dual = {k for k, v in SERIES_PROVIDERS.items() if len(v) >= 2}
    assert dual == {"VIX", "US10Y", "US02Y"}


def test_every_cadence_series_is_in_provider_catalog():
    assert set(SERIES_CADENCE) == set(SERIES_PROVIDERS)


def test_profiles_bound_alpha_vantage_usage():
    # Deep is the largest profile — confirm it never explodes past a
    # small, safe number of Alpha Vantage calls (free tier: 25/day).
    deep_series = PROFILES["deep"]["series"]
    av_calls = sum(1 for s in deep_series if "alpha_vantage" in SERIES_PROVIDERS.get(s, ()))
    assert av_calls <= 10


# ── completeness_score ──────────────────────────────────────────────

def test_completeness_empty_frame_is_zero():
    score, detail = completeness_score(pd.DataFrame(), "daily", 6)
    assert score == 0.0
    assert detail["observations"] == 0


def test_completeness_full_daily_coverage_scores_high():
    # ~6 months of business-day observations (5/7 fraction).
    dates = [d.isoformat() for d in pd.bdate_range("2026-01-01", periods=130)]
    df = _close_frame(dates, [1.0] * len(dates))
    score, detail = completeness_score(df, "daily", 6)
    assert score >= 90.0


def test_completeness_sparse_monthly_scores_low():
    # Only 2 monthly observations when ~6 are expected over 6 months.
    df = _close_frame(["2026-01-01", "2026-06-01"], [1.0, 1.0])
    score, _ = completeness_score(df, "monthly", 6)
    assert score < 50.0


# ── freshness_score ──────────────────────────────────────────────────

def test_freshness_none_for_empty_frame():
    assert freshness_score(pd.DataFrame(), "daily") is None


def test_freshness_daily_fresh_within_bound():
    df = _close_frame([pd.Timestamp.now(tz="UTC").isoformat()], [1.0])
    assert freshness_score(df, "daily") == 100.0


def test_freshness_daily_very_stale():
    old = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)).isoformat()
    df = _close_frame([old], [1.0])
    assert freshness_score(df, "daily") == 0.0


def test_freshness_quarterly_tolerates_longer_staleness_than_daily():
    # 60 days old: stale for daily, fresh for quarterly.
    ts = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=60)).isoformat()
    df = _close_frame([ts], [1.0])
    assert freshness_score(df, "daily") == 0.0
    assert freshness_score(df, "quarterly") == 100.0


# ── timestamp_integrity_score ────────────────────────────────────────

def test_timestamp_integrity_empty_is_100():
    score, detail = timestamp_integrity_score(pd.DataFrame(), "daily")
    assert score == 100.0
    assert detail["checked"] == 0


def test_timestamp_integrity_clean_daily_series_is_100():
    dates = [d.isoformat() for d in pd.bdate_range("2026-01-01", periods=20)]
    df = _close_frame(dates, [1.0] * len(dates))
    score, detail = timestamp_integrity_score(df, "daily")
    assert score == 100.0
    assert detail["future_dated"] == 0


def test_timestamp_integrity_flags_future_dated_observation():
    future = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=5)).isoformat()
    df = _close_frame(["2026-01-01", future], [1.0, 1.0])
    score, detail = timestamp_integrity_score(df, "daily")
    assert detail["future_dated"] == 1
    assert score < 100.0


def test_timestamp_integrity_flags_irregular_monthly_gap():
    # Two "monthly" observations only 5 days apart — a real irregularity.
    df = _close_frame(["2026-01-01", "2026-01-06"], [1.0, 1.0])
    score, detail = timestamp_integrity_score(df, "monthly")
    assert detail["irregular_gaps"] == 1
    assert score < 100.0


def test_timestamp_integrity_daily_cadence_has_no_min_gap_check():
    # Daily series with a 1-day gap is completely normal — no _CADENCE_MIN_GAP_DAYS entry for "daily".
    df = _close_frame(["2026-01-01", "2026-01-02"], [1.0, 1.0])
    score, detail = timestamp_integrity_score(df, "daily")
    assert score == 100.0
    assert detail["irregular_gaps"] == 0


# ── cross_provider_agreement_score ───────────────────────────────────

def test_cross_provider_agreement_none_below_two_providers():
    score, detail = cross_provider_agreement_score({"fred": _close_frame(["2026-01-01"], [4.25])})
    assert score is None


def test_cross_provider_agreement_high_when_values_close():
    fetched = {
        "fred": _close_frame(["2026-01-01"], [4.25]),
        "alpha_vantage": _close_frame(["2026-01-02"], [4.2510]),
    }
    score, detail = cross_provider_agreement_score(fetched, tolerance_pct=1.0)
    assert score == 100.0
    assert detail["max_diff_pct"] < 1.0


def test_cross_provider_agreement_low_when_values_diverge():
    fetched = {
        "fred": _close_frame(["2026-01-01"], [4.25]),
        "alpha_vantage": _close_frame(["2026-01-02"], [8.50]),  # wildly different
    }
    score, detail = cross_provider_agreement_score(fetched, tolerance_pct=1.0)
    assert score is not None and score < 50.0


def test_cross_provider_agreement_ignores_failed_providers():
    fetched = {
        "fred": _close_frame(["2026-01-01"], [4.25]),
        "cboe": None,
    }
    score, detail = cross_provider_agreement_score(fetched)
    assert score is None


# ── composite_score ──────────────────────────────────────────────────

def test_composite_score_none_when_all_dims_none():
    assert composite_score({"completeness": None, "freshness": None}) is None


def test_composite_score_renormalizes_when_agreement_missing():
    # Single-provider series (13 of 16): cross_provider_agreement is None
    # — the remaining 4 weights (0.30+0.25+0.15+0.10=0.80) must renormalize
    # to sum to 1, not silently treat the missing weight as a 0-scoring dim.
    dims = {"completeness": 100.0, "freshness": 100.0, "timestamp_integrity": 100.0,
            "latency": 100.0, "cross_provider_agreement": None}
    assert composite_score(dims) == 100.0


def test_composite_score_weighted_average_with_all_dims_present():
    dims = {"completeness": 100.0, "freshness": 0.0, "timestamp_integrity": 100.0,
            "latency": 100.0, "cross_provider_agreement": 100.0}
    # freshness weight 0.25 dragging the average below 100
    score = composite_score(dims)
    assert 70.0 < score < 80.0


# ── score_series orchestration ───────────────────────────────────────

def test_score_series_reports_every_provider_including_unsupported(monkeypatch):
    """cboe genuinely can't supply DXY — must surface as a real,
    non-crashing fetch_ok=False row, not silently skipped."""
    from core.alt_data_loader import load_from_fred

    def fake_fred(series_id, months=6):
        return _close_frame(["2026-01-01"], [100.0])

    monkeypatch.setattr("core.alt_data_loader.load_from_fred", fake_fred)
    results = score_series("DXY", ["fred", "cboe"], None, tolerance_pct=1.0)
    by_provider = {r.provider: r for r in results}
    assert by_provider["fred"].fetch_ok is True
    assert by_provider["cboe"].fetch_ok is False
    assert "DXY" in by_provider["cboe"].error


def test_score_series_dual_source_populates_agreement(monkeypatch):
    def fake_fred(series_id, months=6):
        return _close_frame(["2026-01-01"], [4.25])

    def fake_av(series_key, months=None):
        return _close_frame(["2026-01-02"], [4.26])

    monkeypatch.setattr("core.alt_data_loader.load_from_fred", fake_fred)
    monkeypatch.setattr("core.alt_data_loader.load_from_alpha_vantage_economic", fake_av)
    results = score_series("US10Y", ["fred", "alpha_vantage"], None, tolerance_pct=1.0)
    assert all(r.fetch_ok for r in results)
    assert all(r.cross_provider_agreement_score is not None for r in results)
    assert all(r.composite_score is not None for r in results)


def test_score_series_single_provider_never_gets_agreement_score(monkeypatch):
    def fake_fred(series_id, months=6):
        return _close_frame(["2026-01-01"], [100.0])

    monkeypatch.setattr("core.alt_data_loader.load_from_fred", fake_fred)
    results = score_series("SPY", ["fred"], None, tolerance_pct=1.0)
    assert results[0].cross_provider_agreement_score is None
    assert results[0].composite_score is not None  # still scores via renormalization


def test_score_series_one_provider_exception_does_not_crash_the_run(monkeypatch):
    def fake_fred(series_id, months=6):
        raise ConnectionError("network down")

    monkeypatch.setattr("core.alt_data_loader.load_from_fred", fake_fred)
    results = score_series("DXY", ["fred"], None, tolerance_pct=1.0)
    assert len(results) == 1
    assert results[0].fetch_ok is False
    assert "network down" in results[0].error


# ── run_benchmark profile resolution ─────────────────────────────────

def test_run_benchmark_smoke_profile_covers_expected_series(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "backtest.macro_benchmark.score_series",
        lambda series_key, providers, months, tolerance_pct: (seen.append(series_key), [])[1],
    )
    run_benchmark("run-x", "smoke", None, None, None, 1.0)
    assert seen == PROFILES["smoke"]["series"]


def test_run_benchmark_explicit_series_overrides_profile(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "backtest.macro_benchmark.score_series",
        lambda series_key, providers, months, tolerance_pct: (seen.append(series_key), [])[1],
    )
    run_benchmark("run-y", "deep", ["VIX"], None, None, 1.0)
    assert seen == ["VIX"]


def test_run_benchmark_providers_override_applies_to_every_series(monkeypatch):
    seen_providers = []
    monkeypatch.setattr(
        "backtest.macro_benchmark.score_series",
        lambda series_key, providers, months, tolerance_pct: (seen_providers.append(providers), [])[1],
    )
    run_benchmark("run-z", "smoke", None, ["fred"], None, 1.0)
    assert all(p == ["fred"] for p in seen_providers)


# ── hard-block safety: never writes config/registry files, never
#    reaches the Macro engine's live decision-path source ────────────

def test_macro_benchmark_module_contains_no_write_call_near_config_files():
    import backtest.macro_benchmark as mb
    import storage.macro_benchmark as sb

    source = inspect.getsource(mb) + inspect.getsource(sb)
    forbidden = ["config.yaml", "engines.yaml", "symbols.yaml", "registry.json"]
    write_markers = ["write_text(", "safe_dump(", "yaml.dump(", 'open(', '"w")', "'w')"]
    for line in source.splitlines():
        if any(marker in line for marker in write_markers):
            assert not any(f in line for f in forbidden), f"Suspicious write near a config/registry file: {line}"


def test_macro_benchmark_never_calls_load_macro_snapshot():
    """Source-scan pin: backtest/macro_benchmark.py's executable code
    (module docstring excluded — it explicitly discusses
    load_macro_snapshot in prose, explaining why it's NOT called) must
    never actually invoke load_macro_snapshot (the Macro engine's
    live-decision-path source) — this module is benchmark-only, on a
    completely separate code path."""
    import backtest.macro_benchmark as mb
    source = inspect.getsource(mb)
    # Strip the module's own leading triple-quoted docstring before scanning.
    first, second = source.find('"""'), source.find('"""', source.find('"""') + 3)
    code_only = source[second + 3:] if first != -1 and second != -1 else source
    assert "load_macro_snapshot" not in code_only


def test_run_benchmark_never_touches_config_or_registry_files(monkeypatch):
    import backtest.macro_benchmark as mb

    config_path = Path("config.yaml")
    symbols_path = Path("config/symbols.yaml")
    engines_path = Path("config/engines.yaml")
    registry_path = Path("research/results/registry.json")
    before = {p: (p.read_bytes(), p.stat().st_mtime) for p in [config_path, symbols_path, engines_path, registry_path] if p.exists()}

    def fake_fred(series_id, months=6):
        return _close_frame(["2026-01-01"], [1.0])

    monkeypatch.setattr("core.alt_data_loader.load_from_fred", fake_fred)
    monkeypatch.setattr("core.alt_data_loader.load_vix_from_cboe", lambda months=6: _close_frame(["2026-01-01"], [17.0]))

    results = []
    mb.run_benchmark("test-run", "smoke", None, None, None, 1.0, on_result=results.append)
    assert len(results) > 0

    after = {p: (p.read_bytes(), p.stat().st_mtime) for p in before}
    assert after == before
