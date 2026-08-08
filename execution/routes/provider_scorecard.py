"""
execution/routes/provider_scorecard.py
--------------------------------------------
Provider Benchmark & Data Quality Lab — Phase 5. Two read-only endpoints
synthesizing the four already-shipped benchmark domains (Price/News/
Macro/Analytics) — no new fetch/scoring engine, no new job kind. Every
computation is backtest.provider_scorecard's pure functions over rows
already read from each domain's own storage module
(storage.provider_benchmark/news_benchmark/macro_benchmark/
analytics_benchmark) — this module owns the D1 reads and orchestration
only.

MEASUREMENT / ADVISORY LAYER ONLY — same guarantee as every prior phase:
neither endpoint here ever writes config.yaml, config/symbols.yaml,
config/engines.yaml, or research/results/registry.json. GET /research/
best-provider is a recommendation an operator reviews and acts on
manually — it never reorders a provider_chains entry itself.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException

from backtest.provider_scorecard import DOMAINS, best_provider, domain_provider_summary, item_fields_for_domain
from execution.api_core import _check_auth

router = APIRouter()

# domain -> (storage module import path, run-id-filter kwarg name matching
# that module's own run_results(...) signature).
_DOMAIN_STORAGE_MODULE: dict[str, str] = {
    "price": "storage.provider_benchmark",
    "news": "storage.news_benchmark",
    "macro": "storage.macro_benchmark",
    "analytics": "storage.analytics_benchmark",
}


def _load_domain_storage(domain: str):
    import importlib
    return importlib.import_module(_DOMAIN_STORAGE_MODULE[domain])


def _latest_finished_run(storage_mod, limit_scan: int = 20) -> dict[str, Any] | None:
    """list_recent_runs() returns every status ordered by created_at
    DESC (none of the four storage modules filter by status themselves —
    only storage.provider_benchmark.score_history() does, and that's a
    different, multi-run aggregation). Scans the most recent `limit_scan`
    runs for the first one with status == 'finished' — a queued/running/
    failed/cancelled run has no stable, complete result set to summarize."""
    for run in storage_mod.list_recent_runs(limit=limit_scan):
        if run.get("status") == "finished":
            return run
    return None


@router.get("/research/provider-scorecard")
async def provider_scorecard(
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """One summary per domain: the latest FINISHED run's per-provider
    mean composite score / fetch-success ratio (backtest.
    provider_scorecard.domain_provider_summary). A domain with zero
    finished runs yet is present with `available: false` — never
    silently omitted, never fabricated as an empty-but-scored domain."""
    _check_auth(x_api_key, iatis_session)

    domains: dict[str, Any] = {}
    for domain in DOMAINS:
        storage_mod = _load_domain_storage(domain)
        run = _latest_finished_run(storage_mod)
        if run is None:
            domains[domain] = {"available": False, "run_id": None, "profile": None, "finished_at": None, "providers": []}
            continue
        rows = storage_mod.run_results(run["id"])
        domains[domain] = {
            "available": True,
            "run_id": run["id"],
            "profile": run["profile"],
            "finished_at": run.get("finished_at"),
            "providers": domain_provider_summary(rows),
        }
    return {"domains": domains}


@router.get("/research/best-provider")
async def best_provider_lookup(
    domain: str,
    symbol: str | None = None,
    series: str | None = None,
    timeframe: str | None = None,
    x_api_key: str | None = Header(default=None),
    iatis_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """The per-(symbol[+timeframe]|series, domain) 'best provider' query
    surface named in this lab's own roadmap. Looks at the latest FINISHED
    run for `domain` only — never mixes providers scored in different
    runs, since a stale run's numbers could be measuring a since-changed
    provider. Degrades to a real 200 with `available: false` (never a
    404) when there's no finished run yet or the item was never
    benchmarked — both are ordinary, expected steady states for a benchmark
    lab, not client errors."""
    _check_auth(x_api_key, iatis_session)

    if domain not in DOMAINS:
        raise HTTPException(status_code=400, detail=f"domain must be one of {list(DOMAINS)}.")
    fields = item_fields_for_domain(domain)

    item: dict[str, str] = {}
    if "symbol" in fields:
        if not symbol:
            raise HTTPException(status_code=400, detail=f"symbol is required for domain={domain!r}.")
        item["symbol"] = symbol.strip().upper()
    if "series" in fields:
        if not series:
            raise HTTPException(status_code=400, detail=f"series is required for domain={domain!r}.")
        item["series"] = series.strip().upper()
    if "timeframe" in fields:
        if not timeframe:
            raise HTTPException(status_code=400, detail=f"timeframe is required for domain={domain!r}.")
        item["timeframe"] = timeframe.strip().upper()
    elif timeframe:
        raise HTTPException(status_code=400, detail=f"timeframe is not applicable for domain={domain!r}.")

    storage_mod = _load_domain_storage(domain)
    run = _latest_finished_run(storage_mod)
    if run is None:
        return {
            "domain": domain, "item": item, "run_id": None, "profile": None, "finished_at": None,
            "available": False, "best": None, "ranking": [],
            "note": f"No finished {domain} benchmark run yet — run one from the Provider Eval tab.",
        }

    filter_kwargs: dict[str, str] = {}
    if "symbol" in item:
        filter_kwargs["symbol"] = item["symbol"]
    if "series" in item:
        filter_kwargs["series"] = item["series"]
    rows = storage_mod.run_results(run["id"], **filter_kwargs)
    if "timeframe" in item:
        rows = [r for r in rows if r.get("timeframe") == item["timeframe"]]

    if not rows:
        return {
            "domain": domain, "item": item, "run_id": run["id"], "profile": run["profile"],
            "finished_at": run.get("finished_at"), "available": False, "best": None, "ranking": [],
            "note": f"This item was not part of the latest finished {domain} run ({run['id']}).",
        }

    from backtest.provider_scorecard import best_provider as _best, rank_providers as _rank
    ranking = [r.to_dict() for r in _rank(rows)]
    top = _best(rows)
    return {
        "domain": domain, "item": item, "run_id": run["id"], "profile": run["profile"],
        "finished_at": run.get("finished_at"), "available": top is not None,
        "best": top.to_dict() if top else None, "ranking": ranking,
    }
