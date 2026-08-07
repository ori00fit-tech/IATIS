"""
backtest/news_benchmark.py
------------------------------
Provider Benchmark & Data Quality Lab — Phase 2 (News Benchmark).

MEASUREMENT / ADVISORY LAYER ONLY, same guarantee as backtest/
price_benchmark.py (Phase 1): this module never writes config.yaml,
config/symbols.yaml, config/engines.yaml, or research/results/registry.json
— every score is evidence an operator reviews, never an automatic input
to a live trading decision or to H021's own pre-registered process.

Real, structural difference from Phase 1 (price), stated up front: there
is no numeric "ground truth" for a news headline the way a median close
price is a ground truth for OHLC. Phase 1's correctness_vs_consensus/
pairwise_agreement_score dimensions (agree/disagree on a NUMBER) have NO
honest analog here. What Phase 2 measures instead, per (provider, symbol):
coverage (did the provider return real articles for this symbol/window),
source diversity, duplicate-headline rate, freshness of the newest
article, latency, sentiment availability (MarketAux only — Finnhub's free
/news has no sentiment field, always None, never fabricated), and
cross-provider COVERAGE agreement (do all providers agree there WAS real
news activity for this symbol/window — a presence/absence consensus, not
a numeric one).

Two real providers, both genuinely different in shape: MarketAux
(fundamentals/marketaux_client.py — per-entity-tagged, real per-article
sentiment, symbol-scoped) and Finnhub (fundamentals/finnhub_news_client.py
— category-wide feed, best-effort keyword-matched to a symbol, no
sentiment). Both are normalized into the same
{"headline","published_at","source","sentiment"} article shape so the
shared dimensions (coverage/diversity/duplicates/freshness/agreement) are
computed identically regardless of provider; sentiment_availability_score
is the one dimension that structurally differs and is documented, not
hidden, via composite_score's existing None-renormalization.
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

PROVIDERS: tuple[str, ...] = ("marketaux", "finnhub")

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {"hours_back": 48, "limit": 20},
    "standard": {"hours_back": 72, "limit": 50},
    "deep": {"hours_back": 168, "limit": 100},
}

_WEIGHTS: dict[str, float] = {
    "coverage": 0.25,
    "source_diversity": 0.15,
    "duplicate_rate": 0.15,
    "freshness": 0.15,
    "latency": 0.10,
    "sentiment_availability": 0.10,  # MarketAux-only — always None for Finnhub, excluded via renormalization
    "cross_provider_coverage_agreement": 0.10,
}

_SOURCE_DIVERSITY_TARGET = 3  # distinct sources counted as "full marks" — a small n shouldn't need more than this to score well


def _default_symbols() -> list[str]:
    """Union of both providers' own mapped symbols — a symbol neither
    provider supports is pointless to benchmark. Individual providers
    still record a real "not supported" result (never a silent 0) for a
    symbol they personally don't map, via score_symbol()'s fetch_ok=False
    path, matching price_benchmark.py's own per-provider-failure
    convention."""
    from fundamentals.finnhub_news_client import FINNHUB_NEWS_SYMBOL_MAP
    from fundamentals.marketaux_client import MARKETAUX_SYMBOL_MAP

    return sorted(set(MARKETAUX_SYMBOL_MAP) | set(FINNHUB_NEWS_SYMBOL_MAP))


def _fetch_provider_articles(provider: str, symbol: str, hours_back: int, limit: int) -> list[dict] | None:
    if provider == "marketaux":
        from fundamentals.marketaux_client import get_news_articles
        return get_news_articles(symbol, limit=limit, hours_back=hours_back)
    if provider == "finnhub":
        from fundamentals.finnhub_news_client import get_symbol_news
        return get_symbol_news(symbol, hours_back=hours_back, limit=limit)
    raise ValueError(f"Unknown news provider {provider!r}")


def _normalize_headline(headline: str) -> str:
    cleaned = "".join(ch.lower() for ch in headline if ch.isalnum() or ch.isspace())
    return " ".join(cleaned.split())


def coverage_score(articles: list[dict]) -> float:
    return 100.0 if len(articles) > 0 else 0.0


def source_diversity_score(articles: list[dict]) -> float | None:
    if not articles:
        return None
    distinct = len({a.get("source", "") for a in articles if a.get("source")})
    return round(100.0 * min(1.0, distinct / _SOURCE_DIVERSITY_TARGET), 2)


def duplicate_rate_score(articles: list[dict]) -> tuple[float | None, dict]:
    """100 - (near-duplicate headline pairs / all pairs * 100), by exact
    match on a normalized (lowercased, punctuation-stripped, whitespace-
    collapsed) headline — deliberately NOT fuzzy matching, to avoid a new
    dependency and to keep the metric simple enough to hand-verify."""
    n = len(articles)
    if n == 0:
        return None, {"n": 0, "distinct_headlines": 0}
    normalized = [_normalize_headline(a.get("headline", "")) for a in articles]
    counts: dict[str, int] = {}
    for h in normalized:
        counts[h] = counts.get(h, 0) + 1
    total_pairs = n * (n - 1) / 2
    if total_pairs == 0:
        return 100.0, {"n": n, "distinct_headlines": len(counts)}
    dup_pairs = sum(c * (c - 1) / 2 for c in counts.values())
    score = round(100.0 * (1.0 - dup_pairs / total_pairs), 2)
    return score, {"n": n, "distinct_headlines": len(counts)}


def freshness_score(articles: list[dict]) -> float | None:
    """Age of the single most recent article. 100 within 1h old, 0 at/past
    24h old — a much tighter ceiling than price_benchmark.py's bar-duration
    -relative freshness (news staleness is measured in real hours, not
    "bar durations"), linear in between."""
    if not articles:
        return None
    import pandas as pd

    newest = None
    for a in articles:
        try:
            ts = pd.Timestamp(a["published_at"])
        except (ValueError, TypeError, KeyError):
            continue
        if newest is None or ts > newest:
            newest = ts
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.tz_localize("UTC")
    age_hours = (pd.Timestamp.now(tz="UTC") - newest).total_seconds() / 3600.0
    if age_hours <= 1.0:
        return 100.0
    if age_hours >= 24.0:
        return 0.0
    return round(100.0 * (24.0 - age_hours) / 23.0, 2)


def sentiment_availability_score(articles: list[dict]) -> float | None:
    if not articles:
        return None
    return 100.0 if any(a.get("sentiment") is not None for a in articles) else None


def mean_sentiment(articles: list[dict]) -> float | None:
    """Informational only — never part of composite_score (there is no
    absolute "good"/"bad" sentiment value to score against)."""
    scores = [a["sentiment"] for a in articles if a.get("sentiment") is not None]
    return round(sum(scores) / len(scores), 4) if scores else None


def cross_provider_coverage_agreement(fetched: dict[str, list[dict] | None]) -> float | None:
    """Do ALL providers that fetched successfully this run agree on
    WHETHER there was real news activity (article_count > 0) for this
    symbol/window? The honest news-equivalent of Phase 1's numeric
    cross-provider agreement — there's no shared number to agree on, only
    presence/absence. None below 2 successful providers (nothing to
    compare), same convention as price_benchmark.pairwise_agreement_score."""
    ok = [v for v in fetched.values() if v is not None]
    if len(ok) < 2:
        return None
    has_news = {len(v) > 0 for v in ok}
    return 100.0 if len(has_news) == 1 else 0.0


def composite_score(dims: dict[str, float | None]) -> float | None:
    present = {k: v for k, v in dims.items() if v is not None and k in _WEIGHTS}
    if not present:
        return None
    total_weight = sum(_WEIGHTS[k] for k in present)
    if total_weight <= 0:
        return None
    score = sum(_WEIGHTS[k] * v for k, v in present.items()) / total_weight
    return round(score, 2)


@dataclass(frozen=True)
class NewsBenchmarkResult:
    provider: str
    symbol: str
    fetch_ok: bool
    error: str | None
    latency_ms: int | None
    article_count: int
    coverage_score: float | None
    source_diversity_score: float | None
    duplicate_rate_score: float | None
    freshness_score: float | None
    latency_score: float | None
    sentiment_availability_score: float | None
    cross_provider_coverage_agreement_score: float | None
    composite_score: float | None
    mean_sentiment: float | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_symbol(symbol: str, providers: list[str], hours_back: int, limit: int) -> list[NewsBenchmarkResult]:
    """Fetches EVERY requested provider independently, scores every one
    INCLUDING failed/unsupported fetches (fetch_ok=False, every score
    None, real error text — never silently dropped, matching
    price_benchmark.score_symbol_timeframe's report-every-point
    convention)."""
    from backtest.price_benchmark import latency_score as _latency_score

    fetched: dict[str, list[dict] | None] = {}
    latencies: dict[str, int] = {}
    errors: dict[str, str] = {}
    for p in providers:
        start = time.monotonic()
        try:
            articles = _fetch_provider_articles(p, symbol, hours_back, limit)
        except Exception as exc:  # one bad provider must never crash the whole benchmark
            latencies[p] = int((time.monotonic() - start) * 1000)
            errors[p] = f"{type(exc).__name__}: {exc}"
            fetched[p] = None
            continue
        latencies[p] = int((time.monotonic() - start) * 1000)
        if articles is None:
            errors[p] = "Provider has no API key configured or does not support this symbol."
        fetched[p] = articles

    agreement = cross_provider_coverage_agreement(fetched)

    results: list[NewsBenchmarkResult] = []
    for p in providers:
        articles = fetched.get(p)
        if articles is None:
            results.append(NewsBenchmarkResult(
                provider=p, symbol=symbol, fetch_ok=False,
                error=errors.get(p, "unknown error"), latency_ms=latencies.get(p),
                article_count=0, coverage_score=None, source_diversity_score=None,
                duplicate_rate_score=None, freshness_score=None, latency_score=None,
                sentiment_availability_score=None, cross_provider_coverage_agreement_score=None,
                composite_score=None,
            ))
            continue
        dup_score, dup_detail = duplicate_rate_score(articles)
        dims = {
            "coverage": coverage_score(articles),
            "source_diversity": source_diversity_score(articles),
            "duplicate_rate": dup_score,
            "freshness": freshness_score(articles),
            "latency": _latency_score(latencies.get(p)),
            "sentiment_availability": sentiment_availability_score(articles),
            "cross_provider_coverage_agreement": agreement,
        }
        results.append(NewsBenchmarkResult(
            provider=p, symbol=symbol, fetch_ok=True, error=None, latency_ms=latencies.get(p),
            article_count=len(articles),
            coverage_score=dims["coverage"], source_diversity_score=dims["source_diversity"],
            duplicate_rate_score=dims["duplicate_rate"], freshness_score=dims["freshness"],
            latency_score=dims["latency"], sentiment_availability_score=dims["sentiment_availability"],
            cross_provider_coverage_agreement_score=agreement,
            composite_score=composite_score(dims),
            mean_sentiment=mean_sentiment(articles),
            detail=dup_detail,
        ))
    return results


def run_benchmark(
    run_id: str,
    profile: str,
    symbols: list[str] | None,
    providers_override: list[str] | None,
    hours_back: int | None,
    limit: int | None,
    on_result: Callable[[NewsBenchmarkResult], None] | None = None,
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

    from storage import news_benchmark

    prof = PROFILES[args.profile]
    symbols = args.symbols or _default_symbols()
    providers = args.providers or list(PROVIDERS)
    hours_back = args.hours_back or prof["hours_back"]
    limit = args.limit or prof["limit"]

    news_benchmark.upsert_run(args.run_id, args.profile, symbols, providers, hours_back, limit)
    news_benchmark.set_run_status(args.run_id, "running", started=True)
    try:
        def _on_result(result: NewsBenchmarkResult) -> None:
            news_benchmark.record_result(args.run_id, result)
            print(f"{result.provider} {result.symbol}: composite={result.composite_score} fetch_ok={result.fetch_ok}")

        run_benchmark(args.run_id, args.profile, args.symbols, args.providers, args.hours_back, args.limit, on_result=_on_result)
        news_benchmark.set_run_status(args.run_id, "finished", finished=True)
    except Exception as exc:
        news_benchmark.set_run_status(args.run_id, "failed", error=str(exc), finished=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
