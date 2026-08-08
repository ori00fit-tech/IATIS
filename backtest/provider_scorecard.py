"""
backtest/provider_scorecard.py
------------------------------------
Provider Benchmark & Data Quality Lab — Phase 5 (multi-domain scorecard +
best_provider query surface).

Pure functions only — no D1 access, no fetch calls, no subprocess job.
Unlike Phases 1-4, Phase 5 introduces no new benchmark ENGINE: there is
nothing new to measure, only a synthesis over the four domains' own
already-recorded results (backtest/price_benchmark.py, news_benchmark.py,
macro_benchmark.py, analytics_benchmark.py all already write real,
scored rows to their own D1 tables). This module mirrors backtest/
meta_analysis.py's own "pure functions over already-fetched rows" shape:
the D1 reads happen in execution/routes/provider_scorecard.py, which
calls each domain's storage.*.list_recent_runs()/run_results() and hands
the rows here for aggregation/ranking.

MEASUREMENT / ADVISORY LAYER ONLY, same guarantee as every prior phase
in this lab: nothing here ever writes config.yaml, config/symbols.yaml,
config/engines.yaml, or research/results/registry.json. best_provider()
is a read-only recommendation for an operator to review and act on
manually via config.yaml's provider_chains — it never reorders a chain
itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DOMAINS: tuple[str, ...] = ("price", "news", "macro", "analytics")

# Each domain's result rows key their "item" (the thing being benchmarked)
# differently: price rows are per (symbol, timeframe); news/analytics rows
# are per symbol; macro rows are per named series. Every domain's rows
# share provider/fetch_ok/composite_score regardless.
_ITEM_FIELDS: dict[str, tuple[str, ...]] = {
    "price": ("symbol", "timeframe"),
    "news": ("symbol",),
    "macro": ("series",),
    "analytics": ("symbol",),
}


def item_fields_for_domain(domain: str) -> tuple[str, ...]:
    if domain not in _ITEM_FIELDS:
        raise ValueError(f"Unknown domain {domain!r} — choose from {DOMAINS}.")
    return _ITEM_FIELDS[domain]


@dataclass(frozen=True)
class RankedProvider:
    provider: str
    rank: int
    available: bool
    composite_score: float | None
    fetch_ok: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "rank": self.rank, "available": self.available,
            "composite_score": self.composite_score, "fetch_ok": self.fetch_ok, "error": self.error,
        }


def rank_providers(rows: list[dict[str, Any]]) -> list[RankedProvider]:
    """Ranks every provider that has a row for one item (all `rows` here
    are assumed to already be filtered to the same symbol/series[+timeframe]
    — the caller's job, this function doesn't filter). Providers with a
    successful fetch and a real composite_score are ranked first, highest
    score first; a fetch_ok row with composite_score=None (every
    dimension was unmeasurable) and any fetch_ok=False row are ranked
    after, in the order given — never silently dropped, matching every
    prior phase's 'report every point' convention. `rank` is 1-based and
    reflects DISPLAY ORDER, not necessarily 'quality tier' for the
    unavailable tail."""
    scored = [r for r in rows if r.get("fetch_ok") and r.get("composite_score") is not None]
    unscored = [r for r in rows if not (r.get("fetch_ok") and r.get("composite_score") is not None)]
    scored.sort(key=lambda r: -float(r["composite_score"]))

    out: list[RankedProvider] = []
    for i, r in enumerate(scored + unscored):
        fetch_ok = bool(r.get("fetch_ok"))
        composite = r.get("composite_score")
        out.append(RankedProvider(
            provider=r["provider"], rank=i + 1,
            available=fetch_ok and composite is not None,
            composite_score=composite, fetch_ok=fetch_ok, error=r.get("error"),
        ))
    return out


def best_provider(rows: list[dict[str, Any]]) -> RankedProvider | None:
    """The single top-ranked AVAILABLE provider, or None when nothing in
    `rows` is both fetch_ok and has a real composite_score — never
    fabricates a 'best' pick out of an all-failed item."""
    ranked = rank_providers(rows)
    for r in ranked:
        if r.available:
            return r
    return None


def domain_provider_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregates every result row from ONE domain's ONE (finished) run
    into one row per provider: mean composite_score across every item
    that provider was benchmarked against this run, and a real
    fetch-success ratio. Mirrors storage/provider_benchmark.py's own
    score_history() per-run aggregation, generalized to work identically
    across all four domains' row shapes (they all share provider/
    fetch_ok/composite_score) since this is pure Python over already-
    fetched rows, not a second copy of that SQL."""
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_provider.setdefault(r["provider"], []).append(r)

    out: list[dict[str, Any]] = []
    for provider, provider_rows in by_provider.items():
        scores = [r["composite_score"] for r in provider_rows if r.get("composite_score") is not None]
        ok_count = sum(1 for r in provider_rows if r.get("fetch_ok"))
        total = len(provider_rows)
        out.append({
            "provider": provider,
            "mean_composite_score": round(sum(scores) / len(scores), 2) if scores else None,
            "fetch_ok_ratio": round(ok_count / total, 3) if total else 0.0,
            "n_items": total,
        })
    out.sort(key=lambda r: (r["mean_composite_score"] is None, -(r["mean_composite_score"] or 0), r["provider"]))
    return out
