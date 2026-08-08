"""
backtest/analytics_benchmark.py
------------------------------------
Provider Benchmark & Data Quality Lab — Phase 4 (Analytics Benchmark).

MEASUREMENT / ADVISORY LAYER ONLY, same guarantee as backtest/
price_benchmark.py (Phase 1), backtest/news_benchmark.py (Phase 2), and
backtest/macro_benchmark.py (Phase 3): this module never writes
config.yaml, config/symbols.yaml, config/engines.yaml, or
research/results/registry.json.

Deliberately narrow scope, chosen with the operator via AskUserQuestion
after a research pass found the roadmap's original "Analytics/predictive-
reproducibility benchmark" framing collides with two real constraints:
  1. Provider scarcity — TAAPI (fundamentals/taapi_client.py, the only
     technical-indicator provider in this codebase) has a confirmed,
     documented free-tier rate limit so tight that a SECOND call made
     seconds after the first is already rejected — unusable for any real
     reproducibility check (which needs two back-to-back calls) or a real
     benchmark run at all. MarketAux is the only usable analytics-shaped
     provider left.
  2. "Does this sentiment predict subsequent price movement" is a
     PREDICTIVE-VALIDITY claim, not a data-quality one — it needs
     CLAUDE.md-grade statistical rigor (a real chronological OOS split,
     Bonferroni-corrected significance testing, per rule 1's "pre-register
     before you build") to say anything honest, not a completeness/
     freshness-style score. Building that here would either be dishonestly
     shallow or would duplicate Mission Center's own hypothesis-evidence
     pipeline outside the one place this codebase's evidence discipline
     actually lives.

The operator chose (of three options presented): **reproducibility-only,
single provider** — score MarketAux's sentiment API on DETERMINISM (does
the same query, repeated seconds later, return the same sentiment value
for the same underlying article — never fabricated as stable when it
isn't), freshness, coverage, and latency. No "subsequent-outcome
tracking" dimension exists anywhere in this module — that's a genuine
trading hypothesis, not a provider benchmark, and belongs in Mission
Center's own pre-registered-hypothesis pipeline if it's ever pursued.

Single provider (`PROVIDERS = ("marketaux",)`) — no cross-provider
dimension of any kind, honestly reflecting that there's exactly one real
candidate today. Reuses backtest.news_benchmark.freshness_score and
backtest.price_benchmark.latency_score directly rather than duplicating
either.
"""
from __future__ import annotations

import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PROVIDERS: tuple[str, ...] = ("marketaux",)

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {"hours_back": 48, "limit": 20},
    "standard": {"hours_back": 72, "limit": 50},
    "deep": {"hours_back": 168, "limit": 100},
}

_WEIGHTS: dict[str, float] = {
    "determinism": 0.40,   # the star metric this phase was scoped for
    "freshness": 0.25,
    "coverage": 0.20,
    "latency": 0.15,
}


def _default_symbols() -> list[str]:
    from fundamentals.marketaux_client import MARKETAUX_SYMBOL_MAP
    return sorted(MARKETAUX_SYMBOL_MAP)


def _fetch_provider_articles(provider: str, symbol: str, hours_back: int, limit: int) -> list[dict] | None:
    if provider == "marketaux":
        from fundamentals.marketaux_client import get_news_articles
        return get_news_articles(symbol, limit=limit, hours_back=hours_back)
    raise ValueError(f"Unknown analytics provider {provider!r}")


def _article_key(article: dict) -> tuple[str, str, str]:
    """Identity for matching the SAME underlying article across two
    fetches — published_at + source + headline, all exact (not the
    normalized-headline fuzzy match news_benchmark.py's duplicate-rate
    dimension uses; here identity match, not similarity match, is
    correct — we're checking whether repeated calls describe the exact
    same real-world article consistently)."""
    return (article.get("published_at", ""), article.get("source", ""), article.get("headline", ""))


def coverage_score(articles: list[dict]) -> float:
    return 100.0 if len(articles) > 0 else 0.0


def determinism_score(first: list[dict], second: list[dict]) -> tuple[float | None, dict]:
    """Fraction of articles present in BOTH fetches whose sentiment value
    is byte-identical between the two calls. None (never fabricated) when
    there is zero overlap — nothing to compare, not "perfectly
    reproducible." A real article appearing in only one fetch (published
    in the gap between calls, or aged out of the hours_back window) is
    NOT counted as a mismatch — it simply isn't part of the comparison
    set, matching this benchmark's own "don't penalize what isn't
    actually comparable" discipline (see price_benchmark.build_consensus's
    identical reasoning for excluding non-overlapping timestamps)."""
    first_by_key = {_article_key(a): a for a in first}
    second_by_key = {_article_key(a): a for a in second}
    overlap_keys = set(first_by_key) & set(second_by_key)
    if not overlap_keys:
        return None, {"overlap_count": 0, "matched": 0, "mismatched": 0}
    matched = 0
    mismatched_examples: list[dict] = []
    for key in overlap_keys:
        a, b = first_by_key[key], second_by_key[key]
        if a.get("sentiment") == b.get("sentiment"):
            matched += 1
        elif len(mismatched_examples) < 5:
            mismatched_examples.append({
                "headline": key[2], "first_sentiment": a.get("sentiment"), "second_sentiment": b.get("sentiment"),
            })
    total = len(overlap_keys)
    score = round(100.0 * matched / total, 2)
    return score, {"overlap_count": total, "matched": matched, "mismatched": total - matched, "mismatched_examples": mismatched_examples}


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
class AnalyticsBenchmarkResult:
    provider: str
    symbol: str
    fetch_ok: bool
    error: str | None
    latency_ms: int | None
    article_count: int
    coverage_score: float | None
    determinism_score: float | None
    determinism_detail: dict = field(default_factory=dict)
    freshness_score: float | None = None
    latency_score: float | None = None
    composite_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_symbol(symbol: str, providers: list[str], hours_back: int, limit: int) -> list[AnalyticsBenchmarkResult]:
    """Fetches EVERY requested provider TWICE, back-to-back (the
    determinism check's own requirement), scoring every provider
    INCLUDING failed/unsupported fetches (fetch_ok=False, every score
    None, real error text — never silently dropped, matching Phase 1-3's
    report-every-point convention). `latency_ms` is the mean of the two
    fetch latencies, honestly documented as such (this phase inherently
    fetches twice, unlike Phase 1-3's single fetch)."""
    from backtest.news_benchmark import freshness_score as _freshness_score
    from backtest.price_benchmark import latency_score as _latency_score

    results: list[AnalyticsBenchmarkResult] = []
    for p in providers:
        start1 = time.monotonic()
        try:
            first = _fetch_provider_articles(p, symbol, hours_back, limit)
        except Exception as exc:  # one bad provider must never crash the whole benchmark
            results.append(AnalyticsBenchmarkResult(
                provider=p, symbol=symbol, fetch_ok=False,
                error=f"{type(exc).__name__}: {exc}", latency_ms=int((time.monotonic() - start1) * 1000),
                article_count=0, coverage_score=None, determinism_score=None,
            ))
            continue
        lat1 = int((time.monotonic() - start1) * 1000)
        if first is None:
            results.append(AnalyticsBenchmarkResult(
                provider=p, symbol=symbol, fetch_ok=False,
                error="Provider has no API key configured or does not support this symbol.",
                latency_ms=lat1, article_count=0, coverage_score=None, determinism_score=None,
            ))
            continue

        start2 = time.monotonic()
        try:
            second = _fetch_provider_articles(p, symbol, hours_back, limit)
        except Exception as exc:
            second = None
            second_error = f"{type(exc).__name__}: {exc}"
        else:
            second_error = None
        lat2 = int((time.monotonic() - start2) * 1000)
        mean_latency = round((lat1 + lat2) / 2)

        if second is None:
            # First fetch succeeded but the second (determinism check)
            # failed — still a real, reportable partial result, not a
            # silent drop. determinism stays None (nothing to compare).
            det_score, det_detail = None, {"overlap_count": 0, "matched": 0, "mismatched": 0, "second_fetch_error": second_error}
        else:
            det_score, det_detail = determinism_score(first, second)

        fresh = _freshness_score(first)
        lat_score = _latency_score(mean_latency)
        dims = {"determinism": det_score, "freshness": fresh, "coverage": coverage_score(first), "latency": lat_score}
        results.append(AnalyticsBenchmarkResult(
            provider=p, symbol=symbol, fetch_ok=True, error=None, latency_ms=mean_latency,
            article_count=len(first),
            coverage_score=dims["coverage"], determinism_score=det_score, determinism_detail=det_detail,
            freshness_score=fresh, latency_score=lat_score,
            composite_score=composite_score(dims),
        ))
    return results


def run_benchmark(
    run_id: str,
    profile: str,
    symbols: list[str] | None,
    providers_override: list[str] | None,
    hours_back: int | None,
    limit: int | None,
    on_result: Callable[[AnalyticsBenchmarkResult], None] | None = None,
) -> None:
    prof = PROFILES[profile]
    resolved_symbols = symbols or _default_symbols()
    resolved_providers = providers_override or list(PROVIDERS)
    resolved_hours_back = hours_back or prof["hours_back"]
    resolved_limit = limit or prof["limit"]

    for symbol in resolved_symbols:
        for result in score_symbol(symbol, resolved_providers, resolved_hours_back, resolved_limit):
            if on_result:
                on_result(result)


def main() -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--profile", required=True, choices=list(PROFILES))
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--providers", nargs="+", default=None, choices=list(PROVIDERS))
    ap.add_argument("--hours-back", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    from storage import analytics_benchmark

    prof = PROFILES[args.profile]
    symbols = args.symbols or _default_symbols()
    providers = args.providers or list(PROVIDERS)
    hours_back = args.hours_back or prof["hours_back"]
    limit = args.limit or prof["limit"]

    analytics_benchmark.upsert_run(args.run_id, args.profile, symbols, providers, hours_back, limit)
    analytics_benchmark.set_run_status(args.run_id, "running", started=True)
    try:
        def _on_result(result: AnalyticsBenchmarkResult) -> None:
            analytics_benchmark.record_result(args.run_id, result)
            print(f"{result.provider} {result.symbol}: composite={result.composite_score} fetch_ok={result.fetch_ok}")

        run_benchmark(args.run_id, args.profile, args.symbols, args.providers, args.hours_back, args.limit, on_result=_on_result)
        analytics_benchmark.set_run_status(args.run_id, "finished", finished=True)
    except Exception as exc:
        analytics_benchmark.set_run_status(args.run_id, "failed", error=str(exc), finished=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
