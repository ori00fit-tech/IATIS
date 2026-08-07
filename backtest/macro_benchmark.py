"""
backtest/macro_benchmark.py
------------------------------
Provider Benchmark & Data Quality Lab — Phase 3 (Macro Benchmark).

MEASUREMENT / ADVISORY LAYER ONLY, same guarantee as backtest/
price_benchmark.py (Phase 1) and backtest/news_benchmark.py (Phase 2):
this module never writes config.yaml, config/symbols.yaml,
config/engines.yaml, or research/results/registry.json — every score is
evidence an operator reviews, never an automatic input to a live trading
decision. In particular, it never calls or modifies
core.alt_data_loader.load_macro_snapshot() — the Macro engine's own
live-decision-path source stays completely untouched by anything here.

Real, structural difference from Phase 1 (price) and Phase 2 (news),
stated up front: macro data has no symbol or timeframe dimension — it is
a small, fixed CATALOG of named series (VIX, DXY, US10Y, ...), each with
its own real publication cadence (daily/weekly/monthly/quarterly), and
almost every series has exactly ONE real provider (FRED). A genuine
cross-provider comparison — this codebase's own established anti-"two
providers agreeing while both wrong" discipline — is only possible for
the 3 series that genuinely have two independent sources today:
  VIX    : CBOE (primary) vs FRED (VIXCLS)
  US10Y  : FRED (DGS10)   vs Alpha Vantage (TREASURY_YIELD, maturity=10year)
  US02Y  : FRED (DGS2)    vs Alpha Vantage (TREASURY_YIELD, maturity=2year)
For every other series, cross_provider_agreement_score stays None
(excluded via composite_score's own renormalization, never fabricated).
Even for those 3, this is a single PAIRWISE dimension, not a 3+-provider
median consensus like price_benchmark.py's correctness_vs_consensus —
with only ever 2 real macro providers for any one series, a genuine
"two providers agree while both wrong" check is not distinguishable from
plain pairwise agreement, and this module says so rather than building
two dimensions that would compute the same number.

No revision-prone series (CPI/NFP/GDP-style point-in-time vintage
tracking) is attempted here — this phase adds real single-provider
coverage for CPI/REAL_GDP/UNEMPLOYMENT/NONFARM_PAYROLL (via Alpha
Vantage, net-new — FRED supplies none of these in this codebase), but a
genuine "no look-ahead from a later revision" capability needs real
point-in-time/vintage data tracking this module does not build — a
separate, larger future phase, named not silently skipped.

Two providers, normalized into one shared close-series shape (o=h=l=c,
via core.alt_data_loader._close_only_frame) so every dimension computes
identically regardless of source: FRED (core.alt_data_loader.load_from_fred,
CBOE (core.alt_data_loader.load_vix_from_cboe, VIX only), and Alpha
Vantage economic indicators (core.alt_data_loader.load_from_alpha_vantage_economic,
new this phase). Alpha Vantage's free tier is 25 requests/day TOTAL,
shared with this codebase's existing FX-intraday-fallback usage of the
same key — profiles below are deliberately sized to stay well under that
even across a few runs in one day.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Which provider(s) genuinely supply each series. Only VIX/US10Y/US02Y
# have two — everywhere else cross_provider_agreement_score stays None.
SERIES_PROVIDERS: dict[str, tuple[str, ...]] = {
    "VIX":               ("cboe", "fred"),
    "US10Y":             ("fred", "alpha_vantage"),
    "US02Y":             ("fred", "alpha_vantage"),
    "DXY":               ("fred",),
    "SPY":               ("fred",),
    "GLD":               ("fred",),
    "OIL_WTI":           ("fred",),
    "NATGAS":            ("fred",),
    "CREDIT_SPREAD":     ("fred",),
    "FED_BALANCE_SHEET": ("fred",),
    "COPPER":            ("fred",),
    "FED_FUNDS_RATE":    ("alpha_vantage",),
    "CPI":               ("alpha_vantage",),
    "REAL_GDP":          ("alpha_vantage",),
    "UNEMPLOYMENT":      ("alpha_vantage",),
    "NONFARM_PAYROLL":   ("alpha_vantage",),
}

# Real publication cadence per series — drives freshness/completeness/
# timestamp-integrity expectations (a monthly CPI print isn't "stale" at
# 20 days old the way a daily yield would be).
SERIES_CADENCE: dict[str, str] = {
    "VIX": "daily", "US10Y": "daily", "US02Y": "daily", "DXY": "daily",
    "SPY": "daily", "GLD": "daily", "OIL_WTI": "daily", "NATGAS": "daily",
    "CREDIT_SPREAD": "daily", "FED_FUNDS_RATE": "daily",
    "FED_BALANCE_SHEET": "weekly",
    "COPPER": "monthly", "CPI": "monthly", "UNEMPLOYMENT": "monthly", "NONFARM_PAYROLL": "monthly",
    "REAL_GDP": "quarterly",
}

_STANDARD_SERIES: list[str] = [
    "VIX", "DXY", "US10Y", "US02Y", "SPY", "GLD",
    "OIL_WTI", "NATGAS", "CREDIT_SPREAD", "FED_BALANCE_SHEET", "COPPER",
]
_AV_ONLY_SERIES: list[str] = ["FED_FUNDS_RATE", "CPI", "REAL_GDP", "UNEMPLOYMENT", "NONFARM_PAYROLL"]

PROFILES: dict[str, dict[str, Any]] = {
    # 1 Alpha Vantage call (US10Y) — well under the 25/day free-tier cap.
    "smoke": {"series": ["VIX", "DXY", "US10Y"]},
    # Every series with a real FRED/CBOE source, incl. both dual-source
    # series — 2 Alpha Vantage calls (US10Y, US02Y).
    "standard": {"series": list(_STANDARD_SERIES)},
    # standard + the 5 Alpha-Vantage-only series — 7 Alpha Vantage calls
    # total, still comfortably under the daily cap even for a couple of
    # deep runs in one day.
    "deep": {"series": list(_STANDARD_SERIES) + list(_AV_ONLY_SERIES)},
}

# Fresh/very-stale day boundaries per cadence — 100 at/under the fresh
# bound, 0 at/over the stale bound, linear in between. Daily's generous
# upper bound (10d) tolerates a 3-day weekend plus a holiday; no per-
# country holiday calendar is modeled (a documented limitation, same
# spirit as price_benchmark.py's own _is_forex_week_closure not knowing
# about bank holidays either).
_CADENCE_FRESH_STALE_DAYS: dict[str, tuple[float, float]] = {
    "daily": (3.0, 10.0),
    "weekly": (10.0, 30.0),
    "monthly": (45.0, 90.0),
    "quarterly": (100.0, 200.0),
}

# Expected fraction of calendar days with a real observation, per cadence
# — used for completeness. Daily series lose ~2/7 to weekends (no holiday
# calendar modeled, a documented limitation); monthly/quarterly are
# expected at ~1 observation per period.
_CADENCE_EXPECTED_FRACTION: dict[str, float] = {
    "daily": 5.0 / 7.0,
    "weekly": 1.0,
    "monthly": 1.0,
    "quarterly": 1.0,
}
_CADENCE_DAYS: dict[str, int] = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 91}

# Minimum real gap (days) between consecutive observations of a
# monthly/quarterly series — an unusually short gap suggests a
# duplicate/misdated publication rather than a genuine second print.
_CADENCE_MIN_GAP_DAYS: dict[str, int] = {"monthly": 20, "quarterly": 75}

_WEIGHTS: dict[str, float] = {
    "completeness": 0.30,
    "freshness": 0.25,
    "timestamp_integrity": 0.15,
    "latency": 0.10,
    "cross_provider_agreement": 0.20,  # None for 13 of 16 series — excluded via renormalization
}


def completeness_score(df: pd.DataFrame, cadence: str, lookback_months: int) -> tuple[float, dict]:
    if df.empty:
        return 0.0, {"observations": 0, "expected_approx": 0}
    cadence_days = _CADENCE_DAYS.get(cadence, 1)
    fraction = _CADENCE_EXPECTED_FRACTION.get(cadence, 1.0)
    span_days = max(1, lookback_months * 30)
    expected_approx = max(1, round((span_days / cadence_days) * fraction))
    observed = len(df)
    score = max(0.0, min(100.0, 100.0 * observed / expected_approx))
    return round(score, 2), {"observations": observed, "expected_approx": expected_approx}


def freshness_score(df: pd.DataFrame, cadence: str) -> float | None:
    if df.empty:
        return None
    bounds = _CADENCE_FRESH_STALE_DAYS.get(cadence)
    if bounds is None:
        return None
    fresh_days, stale_days = bounds
    last_ts = df.index[-1]
    if getattr(last_ts, "tzinfo", None) is None:
        last_ts = last_ts.tz_localize("UTC")
    age_days = (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds() / 86400.0
    if age_days <= fresh_days:
        return 100.0
    if age_days >= stale_days:
        return 0.0
    return round(100.0 * (stale_days - age_days) / (stale_days - fresh_days), 2)


def timestamp_integrity_score(df: pd.DataFrame, cadence: str) -> tuple[float, dict]:
    """No duplicate/out-of-order timestamps to check — _close_only_frame
    already dedupes and sorts every frame this module ever sees. What's
    genuinely checkable here: no future-dated observation (a real
    provider/clock anomaly), and — for monthly/quarterly series — no
    unusually short gap between consecutive prints (suggests a
    duplicate/misdated publication, not a genuine second observation)."""
    if df.empty:
        return 100.0, {"checked": 0, "future_dated": 0, "irregular_gaps": 0}
    now = pd.Timestamp.now(tz="UTC")
    idx = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
    future_dated = int((idx > now).sum())
    checked = len(idx)
    irregular = 0
    min_gap = _CADENCE_MIN_GAP_DAYS.get(cadence)
    if min_gap is not None and checked >= 2:
        gaps = idx.to_series().diff().dt.days.dropna()
        irregular = int((gaps < min_gap).sum())
    violations = future_dated + irregular
    score = round(100.0 * max(0, checked - violations) / checked, 2) if checked else 100.0
    return score, {"checked": checked, "future_dated": future_dated, "irregular_gaps": irregular}


def cross_provider_agreement_score(
    fetched: dict[str, pd.DataFrame], *, tolerance_pct: float = 1.0,
) -> tuple[float | None, dict]:
    """Compares each successful provider's MOST RECENT published value —
    not a per-timestamp join like price_benchmark.py's median consensus,
    since different macro sources publish "daily" data on different
    calendar days for the same underlying quantity (e.g. CBOE same-day vs
    FRED next-business-day for VIX) and a strict date-alignment join would
    mostly find nothing to compare. This is the practically meaningful
    "do these two sources currently roughly agree" check. None below 2
    successful providers — nothing to compare, matching every other
    benchmark module's convention in this lab."""
    ok = {p: df for p, df in fetched.items() if df is not None and not df.empty}
    if len(ok) < 2:
        return None, {"providers_compared": list(ok)}
    latest = {p: float(df["close"].iloc[-1]) for p, df in ok.items()}
    values = list(latest.values())
    import itertools
    diffs = [abs(a - b) / abs(a) * 100 for a, b in itertools.combinations(values, 2) if a != 0]
    if not diffs:
        return None, {"providers_compared": list(ok), "latest_values": latest}
    max_diff_pct = max(diffs)
    score = 100.0 if max_diff_pct <= tolerance_pct else max(0.0, 100.0 - (max_diff_pct - tolerance_pct) * 10)
    return round(score, 2), {
        "providers_compared": list(ok), "latest_values": latest,
        "max_diff_pct": round(max_diff_pct, 4),
    }


def composite_score(dims: dict[str, float | None]) -> float | None:
    """Missing dimensions (None) are excluded and the remaining weights
    RENORMALIZED to sum to 1 — never silently treated as 0."""
    present = {k: v for k, v in dims.items() if v is not None and k in _WEIGHTS}
    if not present:
        return None
    total_weight = sum(_WEIGHTS[k] for k in present)
    if total_weight <= 0:
        return None
    score = sum(_WEIGHTS[k] * v for k, v in present.items()) / total_weight
    return round(score, 2)


@dataclass(frozen=True)
class MacroBenchmarkResult:
    provider: str
    series: str
    fetch_ok: bool
    error: str | None
    latency_ms: int | None
    observation_count: int
    completeness_score: float | None
    completeness_detail: dict = field(default_factory=dict)
    freshness_score: float | None = None
    timestamp_integrity_score: float | None = None
    timestamp_integrity_detail: dict = field(default_factory=dict)
    latency_score: float | None = None
    cross_provider_agreement_score: float | None = None
    cross_provider_agreement_detail: dict = field(default_factory=dict)
    composite_score: float | None = None
    latest_value: float | None = None
    latest_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fetch_provider_series(provider: str, series_key: str, months: int | None) -> pd.DataFrame:
    from core.alt_data_loader import (
        _FRED_LOOKBACK_MONTHS,
        _FRED_SERIES,
        load_from_alpha_vantage_economic,
        load_from_fred,
        load_vix_from_cboe,
    )

    if provider == "cboe":
        if series_key != "VIX":
            raise ValueError(f"cboe only supplies VIX, not {series_key!r}")
        return load_vix_from_cboe(months=months if months is not None else 6)
    if provider == "fred":
        fred_id = _FRED_SERIES.get(series_key)
        if fred_id is None:
            raise ValueError(f"fred has no series id mapped for {series_key!r}")
        lookback = months if months is not None else _FRED_LOOKBACK_MONTHS.get(series_key, 6)
        return load_from_fred(fred_id, months=lookback)
    if provider == "alpha_vantage":
        return load_from_alpha_vantage_economic(series_key, months=months)
    raise ValueError(f"Unknown macro provider {provider!r}")


def score_series(
    series_key: str, providers: list[str], months: int | None, tolerance_pct: float = 1.0,
) -> list[MacroBenchmarkResult]:
    """Fetches EVERY requested provider independently, scores every one
    INCLUDING failed/unsupported fetches (fetch_ok=False, every score
    None, real error text — never silently dropped). A provider that
    doesn't genuinely supply this series (e.g. cboe for DXY) surfaces
    here the same way a real fetch failure would, via
    _fetch_provider_series's own ValueError — no separate "unsupported"
    branch needed."""
    cadence = SERIES_CADENCE.get(series_key, "daily")
    fetched: dict[str, pd.DataFrame] = {}
    latencies: dict[str, int] = {}
    errors: dict[str, str] = {}
    for p in providers:
        start = time.monotonic()
        try:
            fetched[p] = _fetch_provider_series(p, series_key, months)
        except Exception as exc:  # one bad provider must never crash the whole benchmark
            errors[p] = f"{type(exc).__name__}: {exc}"
        latencies[p] = int((time.monotonic() - start) * 1000)

    agree_score, agree_detail = cross_provider_agreement_score(fetched, tolerance_pct=tolerance_pct)
    from backtest.price_benchmark import latency_score as _latency_score

    results: list[MacroBenchmarkResult] = []
    for p in providers:
        if p not in fetched:
            results.append(MacroBenchmarkResult(
                provider=p, series=series_key, fetch_ok=False,
                error=errors.get(p, "unknown error"), latency_ms=latencies.get(p),
                observation_count=0, completeness_score=None,
            ))
            continue
        df = fetched[p]
        lookback_months = months if months is not None else 6
        comp_score, comp_detail = completeness_score(df, cadence, lookback_months)
        fresh = freshness_score(df, cadence)
        ts_score, ts_detail = timestamp_integrity_score(df, cadence)
        lat = _latency_score(latencies.get(p))
        dims = {
            "completeness": comp_score, "freshness": fresh,
            "timestamp_integrity": ts_score, "latency": lat,
            "cross_provider_agreement": agree_score,
        }
        results.append(MacroBenchmarkResult(
            provider=p, series=series_key, fetch_ok=True, error=None,
            latency_ms=latencies.get(p), observation_count=len(df),
            completeness_score=comp_score, completeness_detail=comp_detail,
            freshness_score=fresh,
            timestamp_integrity_score=ts_score, timestamp_integrity_detail=ts_detail,
            latency_score=lat,
            cross_provider_agreement_score=agree_score, cross_provider_agreement_detail=agree_detail,
            composite_score=composite_score(dims),
            latest_value=round(float(df["close"].iloc[-1]), 6),
            latest_date=df.index[-1].isoformat(),
        ))
    return results


def run_benchmark(
    run_id: str,
    profile: str,
    series: list[str] | None,
    providers_override: list[str] | None,
    months: int | None,
    tolerance_pct: float,
    on_result: Callable[[MacroBenchmarkResult], None] | None = None,
) -> None:
    prof = PROFILES[profile]
    resolved_series = series or list(prof["series"])

    for series_key in resolved_series:
        native_providers = list(SERIES_PROVIDERS.get(series_key, ()))
        providers = providers_override if providers_override else native_providers
        for result in score_series(series_key, providers, months, tolerance_pct):
            if on_result:
                on_result(result)


def main() -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", required=True, choices=list(PROFILES))
    ap.add_argument("--series", nargs="+", default=None, choices=list(SERIES_PROVIDERS))
    ap.add_argument("--providers", nargs="+", default=None, choices=["cboe", "fred", "alpha_vantage"])
    ap.add_argument("--months", type=int, default=None)
    ap.add_argument("--tolerance-pct", type=float, default=1.0)
    args = ap.parse_args()

    from storage import macro_benchmark

    prof = PROFILES[args.profile]
    series = args.series or list(prof["series"])

    macro_benchmark.upsert_run(args.run_id, args.profile, series, args.providers, args.months, args.tolerance_pct)
    macro_benchmark.set_run_status(args.run_id, "running", started=True)
    try:
        def _on_result(result: MacroBenchmarkResult) -> None:
            macro_benchmark.record_result(args.run_id, result)
            print(f"{result.provider} {result.series}: composite={result.composite_score} fetch_ok={result.fetch_ok}")

        run_benchmark(args.run_id, args.profile, args.series, args.providers, args.months, args.tolerance_pct, on_result=_on_result)
        macro_benchmark.set_run_status(args.run_id, "finished", finished=True)
    except Exception as exc:
        macro_benchmark.set_run_status(args.run_id, "failed", error=str(exc), finished=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
