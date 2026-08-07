"""
backtest/price_benchmark.py
------------------------------
Provider Benchmark & Data Quality Lab — Phase 1 (Price Benchmark).

MEASUREMENT / ADVISORY LAYER ONLY. This module never writes to
config.yaml (data.provider_chains), config/symbols.yaml, config/engines.yaml,
or research/results/registry.json — every score/rank it produces is
evidence an operator reviews and manually acts on, never an automatic
input to a live trading decision. Flow is strictly:
    Benchmark -> Evidence -> Data Confidence -> Provider recommendation
    -> Operator/config approval
never Benchmark -> change provider -> trade.

What it measures, per (provider, symbol, timeframe): completeness (with
expected-market-closure vs. real-gap classification), per-field OHLC
correctness against a MEDIAN CONSENSUS across every provider that
returned that bar (never blind trust of any single "ground truth"
provider — two providers can agree with each other while both deviate
from a wider consensus, which is exactly what correctness_vs_consensus
is built to catch and pairwise_agreement_score alone would not),
timestamp-boundary-alignment integrity (generalizing the exact
from_ms % bar_ms bug class fixed in core.data_providers._fetch_dukascopy_jforex
to every provider's every returned bar), OHLC structural integrity (via
core.data_validator.validate_ohlcv, reused not reimplemented),
cross-provider pairwise agreement (via scripts.cross_provider_diff.diff_bars,
reused not reimplemented), freshness, and latency.

Reuses rather than duplicates: core.data_providers.provider_chain_for/
fetch_with_failover, core.data_validator.validate_ohlcv/find_gaps,
scripts.cross_provider_diff.diff_bars.
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

# config/symbols.yaml's own asset_class field (merged verbatim into
# config['data']['twelve_data_symbols'] by utils.helpers.load_config) —
# the richer per-symbol taxonomy, used here for SYMBOL SELECTION. This is
# deliberately distinct from core.data_providers.symbol_class()'s coarser
# crypto/metals/energy/indices/fx/stocks/etf bucket, used for PROVIDER
# CHAIN resolution — both taxonomies are correct in their own place.
_IN_SCOPE_ASSET_CLASSES = {"fx_major", "fx_minor", "metals", "crypto", "indices"}

_SMOKE_SYMBOL_CAP = 10

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {"timeframes": ["H1", "H4", "D1"], "outputsize": 300},
    "standard": {"timeframes": ["M15", "H1", "H4", "D1"], "outputsize": 500},
    "deep": {"timeframes": ["M15", "H1", "H4", "D1"], "outputsize": 1500},
}

_TF_MS: dict[str, int] = {
    "M1": 60_000, "M5": 300_000, "M15": 900_000, "M30": 1_800_000,
    "H1": 3_600_000, "H4": 14_400_000, "D1": 86_400_000,
}
_TF_TO_PANDAS_FREQ: dict[str, str] = {
    "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
    "H1": "1h", "H4": "4h", "D1": "1D",
}
_TF_MINUTES: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440,
}

_WEIGHTS: dict[str, float] = {
    "completeness": 0.25,
    "correctness": 0.20,
    "timestamp_integrity": 0.15,
    "ohlc_integrity": 0.10,
    "spread_quality": 0.10,   # always None Phase 1 — no bid/ask field exists
                               # anywhere in this codebase (confirmed by grep);
                               # a documented, never-populated stub, excluded
                               # via composite_score's own renormalization,
                               # never silently treated as 0.
    "cross_provider_agreement": 0.10,
    "freshness": 0.05,
    "latency": 0.05,
}


def _in_scope_symbols(config: dict, *, include_retired: bool = False) -> list[str]:
    excluded = set() if include_retired else {"RETIRED"}
    out: list[str] = []
    for s in config.get("data", {}).get("twelve_data_symbols", []):
        if s.get("asset_class") not in _IN_SCOPE_ASSET_CLASSES:
            continue
        if s.get("status") in excluded:
            continue
        internal = str(s.get("internal", "")).upper()
        if internal:
            out.append(internal)
    return out


def _asset_class_for_symbol(symbol: str, config: dict) -> str:
    for s in config.get("data", {}).get("twelve_data_symbols", []):
        if str(s.get("internal", "")).upper() == symbol:
            return str(s.get("asset_class", "fx_major"))
    return "fx_major"


def _is_forex_week_closure(ts: pd.Timestamp) -> bool:
    """No existing module covers this: regimes/session_context.py only
    classifies intraday Asia/London/NY windows, core/market_quality.py's
    _day_penalty only scores thinness — neither returns an open/closed
    boolean for the FX weekly market closure."""
    ts = ts.tz_convert("UTC") if getattr(ts, "tzinfo", None) else ts.tz_localize("UTC")
    wd = ts.weekday()  # Mon=0 .. Sun=6
    if wd == 5:
        return True
    if wd == 4 and ts.hour >= 22:
        return True
    if wd == 6 and ts.hour < 22:
        return True
    return False


def classify_gaps(df: pd.DataFrame, interval: str, asset_class: str) -> dict[str, int]:
    freq = _TF_TO_PANDAS_FREQ.get(interval)
    if freq is None or df.empty or len(df) < 2:
        return {"expected_closure": 0, "real_gap": 0, "total_missing": 0}
    from core.data_validator import find_gaps

    missing = find_gaps(df, freq)
    if asset_class == "crypto":
        expected = 0
        real = len(missing.index)
    else:
        expected = sum(1 for ts in missing.index if _is_forex_week_closure(ts))
        real = len(missing.index) - expected
    return {"expected_closure": int(expected), "real_gap": int(real), "total_missing": int(len(missing.index))}


def completeness_score(df: pd.DataFrame, interval: str, asset_class: str) -> tuple[float, dict]:
    detail = classify_gaps(df, interval, asset_class)
    bars_present = len(df)
    total_expected = bars_present + detail["real_gap"]
    if total_expected <= 0:
        return 100.0, detail
    score = max(0.0, min(100.0, 100.0 * bars_present / total_expected))
    return round(score, 2), detail


def timestamp_integrity_score(df: pd.DataFrame, interval: str) -> tuple[float, dict]:
    """Generalizes the exact from_ms % bar_ms boundary-alignment bug
    class fixed this session in core.data_providers._fetch_dukascopy_jforex
    to every provider's every returned bar."""
    bar_ms = _TF_MS.get(interval)
    if bar_ms is None or df.empty:
        return 100.0, {"checked": 0, "misaligned": 0}
    idx = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
    epoch_ms = idx.astype("int64") // 1_000_000
    misaligned = int((epoch_ms % bar_ms != 0).sum())
    checked = len(df)
    score = round(100.0 * (checked - misaligned) / checked, 2) if checked else 100.0
    return score, {"checked": checked, "misaligned": misaligned}


def ohlc_integrity_score(df: pd.DataFrame) -> tuple[float, str | None]:
    """Direct reuse of core.data_validator.validate_ohlcv — pass/fail
    (100/0) with only the first failing check's reason, a known
    limitation documented here, not hidden: validate_ohlcv raises on the
    FIRST failure, so this can't report every distinct violation."""
    from core.data_validator import DataValidationError, validate_ohlcv

    if df.empty:
        return 0.0, "No bars to validate"
    try:
        validate_ohlcv(df)
        return 100.0, None
    except DataValidationError as exc:
        return 0.0, str(exc)


def build_consensus(fetched: dict[str, pd.DataFrame], *, min_providers: int = 2) -> pd.DataFrame:
    """Per-timestamp MEDIAN O/H/L/C across every provider that returned
    that bar. Timestamps with fewer than min_providers contributing are
    EXCLUDED — a "consensus" of one provider is just that provider's own
    possibly-wrong value. Volume is deliberately excluded: cross-venue
    tick volume (ccxt/Binance vs cTrader vs Twelve Data) is not a
    comparable quantity by nature."""
    frames = []
    for provider, df in fetched.items():
        if df is None or df.empty:
            continue
        idx = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
        sub = df[["open", "high", "low", "close"]].copy()
        sub.index = idx
        sub.columns = pd.MultiIndex.from_product([[provider], sub.columns])
        frames.append(sub)
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    combined = pd.concat(frames, axis=1)
    result = {}
    for field_name in ["open", "high", "low", "close"]:
        field_cols = combined.xs(field_name, axis=1, level=1)
        count = field_cols.count(axis=1)
        median = field_cols.median(axis=1)
        result[field_name] = median.where(count >= min_providers)
    consensus = pd.DataFrame(result).dropna(how="any")
    return consensus


def correctness_vs_consensus(df: pd.DataFrame, consensus: pd.DataFrame, *, tolerance_pct: float = 0.05) -> dict[str, Any]:
    if df.empty or consensus.empty:
        return {"score": None, "detail": {"bars_compared": 0}}
    idx = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
    a = df[["open", "high", "low", "close"]].copy()
    a.index = idx
    common = a.index.intersection(consensus.index)
    if len(common) == 0:
        return {"score": None, "detail": {"bars_compared": 0}}
    a = a.loc[common]
    b = consensus.loc[common]
    per_field: dict[str, Any] = {}
    total_checks = 0
    total_within = 0
    for field_name in ["open", "high", "low", "close"]:
        diff_pct = ((a[field_name] - b[field_name]).abs() / b[field_name].abs().replace(0, pd.NA)) * 100
        diff_pct = diff_pct.dropna()
        checked = len(diff_pct)
        exceeding = int((diff_pct > tolerance_pct).sum())
        per_field[field_name] = {
            "checked": checked, "exceeding": exceeding,
            "mean_diff_pct": round(float(diff_pct.mean()), 5) if checked else None,
        }
        total_checks += checked
        total_within += checked - exceeding
    score = round(100.0 * total_within / total_checks, 2) if total_checks else None
    return {"score": score, "detail": {"bars_compared": int(len(common)), "per_field": per_field}}


_MAX_EVIDENCE_POINTS = 100


def build_evidence_series(
    df: pd.DataFrame, consensus: pd.DataFrame, *, tolerance_pct: float = 0.05, max_points: int = _MAX_EVIDENCE_POINTS,
) -> list[dict[str, Any]]:
    """Phase 1b — the raw per-bar (close, consensus_close, diff_pct) tuples
    an Evidence drill-down chart overlays: this provider's close price vs.
    the same median consensus correctness_vs_consensus already computed,
    capped to the most recent `max_points` bars (a drill-down evidence
    view, not a full historical re-plot — Phase 1's tables already give
    full aggregate evidence via completeness/correctness/timestamp_
    integrity detail). Returns [] when there's nothing to align against
    (empty df/consensus), matching every other dimension's "None/empty
    when unmeasurable" convention."""
    if df.empty or consensus.empty:
        return []
    idx = df.index.tz_convert("UTC") if df.index.tz else df.index.tz_localize("UTC")
    a = df[["close"]].copy()
    a.index = idx
    common = a.index.intersection(consensus.index).sort_values()
    if len(common) == 0:
        return []
    common = common[-max_points:]
    out: list[dict[str, Any]] = []
    for ts in common:
        close = float(a.loc[ts, "close"])
        cons_close = float(consensus.loc[ts, "close"])
        diff_pct = abs(close - cons_close) / abs(cons_close) * 100 if cons_close != 0 else None
        out.append({
            "ts": ts.isoformat(),
            "close": round(close, 6),
            "consensus_close": round(cons_close, 6),
            "diff_pct": round(diff_pct, 5) if diff_pct is not None else None,
            "exceeds_tolerance": bool(diff_pct is not None and diff_pct > tolerance_pct),
        })
    return out


def pairwise_agreement_score(provider: str, fetched: dict[str, pd.DataFrame], *, tolerance_pct: float = 0.05) -> float | None:
    """Direct reuse of scripts.cross_provider_diff.diff_bars, averaged
    against every OTHER provider that fetched successfully this run.
    Deliberately kept DISTINCT from correctness_vs_consensus: two
    providers can pairwise-agree with each other while both deviate from
    a wider 3+-provider median — correctness_vs_consensus catches that,
    pairwise agreement alone would not (see tests/test_price_benchmark.py's
    dedicated regression test proving these two genuinely differ)."""
    from scripts.cross_provider_diff import diff_bars

    if provider not in fetched:
        return None
    peers = [p for p in fetched if p != provider and fetched[p] is not None and not fetched[p].empty]
    if not peers:
        return None
    scores = []
    for peer in peers:
        result = diff_bars(fetched[provider], fetched[peer], provider_a=provider, provider_b=peer, tolerance_pct=tolerance_pct)
        if "pct_bars_exceeding_tolerance" in result:
            scores.append(max(0.0, 100.0 - result["pct_bars_exceeding_tolerance"]))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


def freshness_score(df: pd.DataFrame, interval: str) -> float | None:
    if df.empty:
        return None
    minutes = _TF_MINUTES.get(interval)
    if minutes is None:
        return None
    last_ts = df.index[-1]
    if getattr(last_ts, "tzinfo", None) is None:
        last_ts = last_ts.tz_localize("UTC")
    age_minutes = (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds() / 60.0
    bar_durations_stale = max(0.0, age_minutes / minutes)
    if bar_durations_stale <= 1.0:
        return 100.0
    if bar_durations_stale >= 10.0:
        return 0.0
    return round(100.0 * (10.0 - bar_durations_stale) / 9.0, 2)


def latency_score(latency_ms: int | None) -> float | None:
    """10s ceiling grounded in _fetch_dukascopy_jforex's own documented
    worst-case cold-fetch observation this session."""
    if latency_ms is None:
        return None
    if latency_ms <= 500:
        return 100.0
    if latency_ms >= 10_000:
        return 0.0
    return round(100.0 * (10_000 - latency_ms) / 9_500, 2)


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
class BenchmarkResult:
    provider: str
    symbol: str
    timeframe: str
    fetch_ok: bool
    error: str | None
    latency_ms: int | None
    bars_fetched: int
    completeness_score: float | None
    completeness_detail: dict = field(default_factory=dict)
    correctness_score: float | None = None
    correctness_detail: dict = field(default_factory=dict)
    timestamp_integrity_score: float | None = None
    timestamp_integrity_detail: dict = field(default_factory=dict)
    ohlc_integrity_score: float | None = None
    ohlc_integrity_reason: str | None = None
    spread_quality_score: float | None = None
    cross_provider_agreement_score: float | None = None
    freshness_score: float | None = None
    latency_score: float | None = None
    composite_score: float | None = None
    evidence_series: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fetch_symbol_for(internal: str, config: dict) -> str:
    for s in config.get("data", {}).get("twelve_data_symbols", []):
        if str(s.get("internal", "")).upper() == internal:
            return s.get("symbol", internal)
    return internal


def score_symbol_timeframe(
    symbol: str, timeframe: str, providers: list[str], outputsize: int,
    config: dict, tolerance_pct: float = 0.05,
) -> list[BenchmarkResult]:
    """Fetches EVERY requested provider independently (fetch_with_failover
    forced to providers=[p], same pattern as scripts/cross_provider_diff.py),
    builds the median consensus across everyone that succeeded, and scores
    every provider INCLUDING ones whose fetch failed (fetch_ok=False, every
    score None, real error text recorded — never silently dropped, matching
    backtest/robustness.py's report-every-point convention)."""
    from core.data_providers import DataFetchError, fetch_with_failover

    fetch_symbol = _fetch_symbol_for(symbol, config)
    asset_class = _asset_class_for_symbol(symbol, config)

    fetched: dict[str, pd.DataFrame] = {}
    latencies: dict[str, int | None] = {}
    errors: dict[str, str] = {}
    for p in providers:
        start = time.monotonic()
        try:
            df, _actual = fetch_with_failover(fetch_symbol, timeframe, outputsize=outputsize, providers=[p])
            latencies[p] = int((time.monotonic() - start) * 1000)
            fetched[p] = df
        except DataFetchError as exc:
            latencies[p] = int((time.monotonic() - start) * 1000)
            errors[p] = str(exc)
        except Exception as exc:  # one bad provider must never crash the whole benchmark
            latencies[p] = int((time.monotonic() - start) * 1000)
            errors[p] = f"{type(exc).__name__}: {exc}"

    consensus = build_consensus(fetched)

    results: list[BenchmarkResult] = []
    for p in providers:
        if p not in fetched:
            results.append(BenchmarkResult(
                provider=p, symbol=symbol, timeframe=timeframe, fetch_ok=False,
                error=errors.get(p, "unknown error"), latency_ms=latencies.get(p), bars_fetched=0,
                completeness_score=None,
            ))
            continue
        df = fetched[p]
        comp_score, comp_detail = completeness_score(df, timeframe, asset_class)
        ts_score, ts_detail = timestamp_integrity_score(df, timeframe)
        ohlc_score, ohlc_reason = ohlc_integrity_score(df)
        corr = correctness_vs_consensus(df, consensus, tolerance_pct=tolerance_pct)
        agree = pairwise_agreement_score(p, fetched, tolerance_pct=tolerance_pct)
        fresh = freshness_score(df, timeframe)
        lat = latency_score(latencies.get(p))
        evidence_series = build_evidence_series(df, consensus, tolerance_pct=tolerance_pct)
        dims = {
            "completeness": comp_score, "correctness": corr["score"],
            "timestamp_integrity": ts_score, "ohlc_integrity": ohlc_score,
            "spread_quality": None, "cross_provider_agreement": agree,
            "freshness": fresh, "latency": lat,
        }
        results.append(BenchmarkResult(
            provider=p, symbol=symbol, timeframe=timeframe, fetch_ok=True, error=None,
            latency_ms=latencies.get(p), bars_fetched=len(df),
            completeness_score=comp_score, completeness_detail=comp_detail,
            correctness_score=corr["score"], correctness_detail=corr["detail"],
            timestamp_integrity_score=ts_score, timestamp_integrity_detail=ts_detail,
            ohlc_integrity_score=ohlc_score, ohlc_integrity_reason=ohlc_reason,
            spread_quality_score=None, cross_provider_agreement_score=agree,
            freshness_score=fresh, latency_score=lat,
            composite_score=composite_score(dims),
            evidence_series=evidence_series,
        ))
    return results


def run_benchmark(
    run_id: str,
    profile: str,
    symbols: list[str] | None,
    timeframes: list[str] | None,
    providers_override: list[str] | None,
    outputsize: int | None,
    tolerance_pct: float,
    config: dict,
    on_result: Callable[[BenchmarkResult], None] | None = None,
) -> None:
    from core.data_providers import provider_chain_for

    prof = PROFILES[profile]
    resolved_symbols = symbols
    if resolved_symbols is None:
        resolved_symbols = _in_scope_symbols(config, include_retired=(profile == "deep"))
        if profile == "smoke":
            resolved_symbols = resolved_symbols[:_SMOKE_SYMBOL_CAP]
    resolved_timeframes = timeframes or prof["timeframes"]
    resolved_outputsize = outputsize or prof["outputsize"]
    overrides = config.get("data", {}).get("provider_chains")

    for symbol in resolved_symbols:
        providers = providers_override or provider_chain_for(symbol, overrides)
        for tf in resolved_timeframes:
            for result in score_symbol_timeframe(symbol, tf, providers, resolved_outputsize, config, tolerance_pct):
                if on_result:
                    on_result(result)


def main() -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    from utils.helpers import load_config

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", required=True, choices=list(PROFILES))
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--timeframes", nargs="+", default=None)
    ap.add_argument("--providers", nargs="+", default=None)
    ap.add_argument("--outputsize", type=int, default=None)
    ap.add_argument("--tolerance-pct", type=float, default=0.05)
    args = ap.parse_args()

    config = load_config()
    from storage import provider_benchmark

    prof = PROFILES[args.profile]
    symbols = args.symbols
    if symbols is None:
        symbols = _in_scope_symbols(config, include_retired=(args.profile == "deep"))
        if args.profile == "smoke":
            symbols = symbols[:_SMOKE_SYMBOL_CAP]
    timeframes = args.timeframes or prof["timeframes"]
    outputsize = args.outputsize or prof["outputsize"]

    provider_benchmark.upsert_run(
        args.run_id, args.profile, symbols, timeframes, args.providers,
        outputsize, args.tolerance_pct,
    )
    provider_benchmark.set_run_status(args.run_id, "running", started=True)
    try:
        def _on_result(result: BenchmarkResult) -> None:
            provider_benchmark.record_result(args.run_id, result)
            print(f"{result.provider} {result.symbol} {result.timeframe}: "
                  f"composite={result.composite_score} fetch_ok={result.fetch_ok}")

        run_benchmark(
            args.run_id, args.profile, args.symbols, args.timeframes,
            args.providers, args.outputsize, args.tolerance_pct, config,
            on_result=_on_result,
        )
        provider_benchmark.set_run_status(args.run_id, "finished", finished=True)
    except Exception as exc:
        provider_benchmark.set_run_status(args.run_id, "failed", error=str(exc), finished=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
