"""
tests/test_provider_scorecard.py
------------------------------------
Provider Benchmark & Data Quality Lab Phase 5 — pure-function tests for
backtest/provider_scorecard.py. No D1, no fetch calls — every function
here is a pure aggregation/ranking over already-fetched row dicts.
"""
from __future__ import annotations

import pytest

from backtest.provider_scorecard import (
    DOMAINS,
    best_provider,
    domain_provider_summary,
    item_fields_for_domain,
    rank_providers,
)


def _row(provider: str, composite: float | None, fetch_ok: bool = True, error: str | None = None) -> dict:
    return {"provider": provider, "composite_score": composite, "fetch_ok": fetch_ok, "error": error}


# ── item_fields_for_domain ──────────────────────────────────────────

def test_item_fields_for_every_real_domain():
    assert item_fields_for_domain("price") == ("symbol", "timeframe")
    assert item_fields_for_domain("news") == ("symbol",)
    assert item_fields_for_domain("macro") == ("series",)
    assert item_fields_for_domain("analytics") == ("symbol",)


def test_item_fields_for_unknown_domain_raises():
    with pytest.raises(ValueError, match="Unknown domain"):
        item_fields_for_domain("bogus")


def test_domains_constant_matches_item_fields_keys():
    # Regression guard: DOMAINS and the private item-field map must never drift apart.
    for d in DOMAINS:
        item_fields_for_domain(d)  # must not raise for any declared domain


# ── rank_providers ───────────────────────────────────────────────────

def test_rank_providers_sorts_available_ones_by_score_desc():
    rows = [_row("a", 60.0), _row("b", 90.0), _row("c", 75.0)]
    ranked = rank_providers(rows)
    assert [r.provider for r in ranked] == ["b", "c", "a"]
    assert [r.rank for r in ranked] == [1, 2, 3]
    assert all(r.available for r in ranked)


def test_rank_providers_puts_unavailable_after_available():
    rows = [_row("failed", None, fetch_ok=False, error="boom"), _row("ok", 50.0)]
    ranked = rank_providers(rows)
    assert ranked[0].provider == "ok"
    assert ranked[0].available is True
    assert ranked[1].provider == "failed"
    assert ranked[1].available is False
    assert ranked[1].error == "boom"


def test_rank_providers_treats_fetch_ok_with_null_score_as_unavailable():
    # A provider can fetch_ok=True but have every scoring dimension
    # unmeasurable (composite_score=None via weight renormalization on an
    # all-None dims dict) — still not "available" for ranking purposes.
    rows = [_row("null-score", None, fetch_ok=True), _row("real", 40.0)]
    ranked = rank_providers(rows)
    assert ranked[0].provider == "real"
    assert ranked[1].provider == "null-score"
    assert ranked[1].available is False
    assert ranked[1].fetch_ok is True


def test_rank_providers_empty_list_returns_empty():
    assert rank_providers([]) == []


def test_rank_providers_never_drops_a_row():
    rows = [_row(f"p{i}", float(i) if i % 2 == 0 else None, fetch_ok=(i % 2 == 0)) for i in range(6)]
    assert len(rank_providers(rows)) == len(rows)


# ── best_provider ─────────────────────────────────────────────────────

def test_best_provider_returns_the_top_available_one():
    rows = [_row("low", 10.0), _row("high", 99.0), _row("failed", None, fetch_ok=False, error="x")]
    best = best_provider(rows)
    assert best is not None
    assert best.provider == "high"
    assert best.composite_score == 99.0


def test_best_provider_none_when_all_unavailable():
    rows = [_row("a", None, fetch_ok=False, error="e1"), _row("b", None, fetch_ok=True)]
    assert best_provider(rows) is None


def test_best_provider_none_on_empty_rows():
    assert best_provider([]) is None


# ── domain_provider_summary ──────────────────────────────────────────

def test_domain_provider_summary_aggregates_mean_score_and_fetch_ratio():
    rows = [
        _row("a", 80.0), _row("a", 60.0),  # a: 2 items, mean 70, 2/2 ok
        _row("b", 100.0), _row("b", None, fetch_ok=False, error="down"),  # b: mean 100 (only the real one), 1/2 ok
    ]
    summary = domain_provider_summary(rows)
    by_provider = {s["provider"]: s for s in summary}
    assert by_provider["a"]["mean_composite_score"] == 70.0
    assert by_provider["a"]["fetch_ok_ratio"] == 1.0
    assert by_provider["a"]["n_items"] == 2
    assert by_provider["b"]["mean_composite_score"] == 100.0
    assert by_provider["b"]["fetch_ok_ratio"] == 0.5
    assert by_provider["b"]["n_items"] == 2


def test_domain_provider_summary_all_failed_provider_has_null_mean_not_zero():
    rows = [_row("dead", None, fetch_ok=False, error="e1"), _row("dead", None, fetch_ok=False, error="e2")]
    summary = domain_provider_summary(rows)
    assert summary[0]["mean_composite_score"] is None  # never fabricated as 0.0
    assert summary[0]["fetch_ok_ratio"] == 0.0


def test_domain_provider_summary_sorts_by_mean_score_desc_nulls_last():
    rows = [_row("null", None, fetch_ok=False), _row("mid", 50.0), _row("top", 90.0)]
    summary = domain_provider_summary(rows)
    assert [s["provider"] for s in summary] == ["top", "mid", "null"]


def test_domain_provider_summary_empty_rows_returns_empty():
    assert domain_provider_summary([]) == []
