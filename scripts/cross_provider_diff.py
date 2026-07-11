#!/usr/bin/env python3
"""
scripts/cross_provider_diff.py
---------------------------------
Cross-provider OHLC agreement check (audit follow-up, 2026-07-11).

Why this exists: docs/STRATEGY_EVIDENCE_2026-07.md already recorded one
concrete case of this problem by hand — "NZDUSD broker H4 disagrees with
the Twelve-Data backtest (0.985) that had it disabled — a data-source
discrepancy to investigate, not a promotion." There was no reusable tool to
find the NEXT one; this is that tool.

What it does: fetches the same symbol/timeframe from two (or more)
providers in core.data_providers, aligns bars on shared timestamps, and
reports how far they disagree — count of common/missing bars and the
distribution of close-price percentage differences. A provider pair that
disagrees materially means at least one of them is unfit as ground truth
for that symbol, which every backtest and live decision built on it
inherits silently.

READ-ONLY: fetches market data only, stores nothing, places no orders,
touches no production config. Needs live provider access (Twelve Data key,
or a broker session for cTrader) — network-blocked from the audit sandbox
for cTrader/ccxt, so run this on the VPS for a real answer.

Usage:
    python3 -m scripts.cross_provider_diff --symbol EURUSD --interval H4
    python3 -m scripts.cross_provider_diff --symbol BTCUSD --interval H4 \\
        --providers ccxt twelve_data
    python3 -m scripts.cross_provider_diff --symbol XAUUSD --interval H4 \\
        --tolerance-pct 0.1 --out research/results/xauusd_provider_diff
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from core.data_providers import DataFetchError, fetch_with_failover, provider_chain_for
from research import manifest as research_manifest
from utils.helpers import load_config


def _fetch_symbol_for(internal: str, config: dict) -> str:
    """internal ("EURUSD") -> fetch format ("EUR/USD") from config.yaml."""
    for s in config.get("data", {}).get("twelve_data_symbols", []):
        if s.get("internal") == internal:
            return s.get("symbol", internal)
    return internal


def align_bars(df_a: pd.DataFrame, df_b: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Inner-join two OHLC DataFrames on their (UTC) timestamp index.
    Returns the two frames re-indexed to the shared timestamps only, in
    original row order."""
    idx_a = df_a.index.tz_convert("UTC") if df_a.index.tz else df_a.index.tz_localize("UTC")
    idx_b = df_b.index.tz_convert("UTC") if df_b.index.tz else df_b.index.tz_localize("UTC")
    common = idx_a.intersection(idx_b)
    a = df_a.set_axis(idx_a, axis=0).loc[common].sort_index()
    b = df_b.set_axis(idx_b, axis=0).loc[common].sort_index()
    return a, b


def diff_bars(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    *,
    provider_a: str,
    provider_b: str,
    tolerance_pct: float = 0.05,
) -> dict[str, Any]:
    """Compare two OHLC frames for the same symbol/timeframe (already
    fetched independently) and report agreement stats. Pure function —
    unit-testable without any network access."""
    n_a, n_b = len(df_a), len(df_b)
    a, b = align_bars(df_a, df_b)
    n_common = len(a)

    if n_common == 0:
        return {
            "provider_a": provider_a, "provider_b": provider_b,
            "bars_a": n_a, "bars_b": n_b, "bars_common": 0,
            "verdict": "NO_OVERLAP — cannot compare (disjoint time ranges/timestamps)",
        }

    close_diff_pct = ((a["close"] - b["close"]).abs() / b["close"].abs()) * 100
    exceeding = close_diff_pct > tolerance_pct
    worst_idx = close_diff_pct.idxmax()

    result = {
        "provider_a": provider_a,
        "provider_b": provider_b,
        "bars_a": n_a,
        "bars_b": n_b,
        "bars_common": n_common,
        "bars_only_in_a": n_a - n_common,
        "bars_only_in_b": n_b - n_common,
        "close_diff_pct": {
            "mean": round(float(close_diff_pct.mean()), 5),
            "median": round(float(close_diff_pct.median()), 5),
            "max": round(float(close_diff_pct.max()), 5),
            "worst_timestamp": worst_idx.isoformat(),
        },
        "tolerance_pct": tolerance_pct,
        "bars_exceeding_tolerance": int(exceeding.sum()),
        "pct_bars_exceeding_tolerance": round(100 * float(exceeding.mean()), 2),
    }
    result["verdict"] = (
        "AGREE" if result["pct_bars_exceeding_tolerance"] < 1.0 else
        "MINOR_DISAGREEMENT" if result["pct_bars_exceeding_tolerance"] < 5.0 else
        "MATERIAL_DISAGREEMENT — do not treat either provider as ground truth without investigating"
    )
    return result


def run(symbol: str, interval: str, providers: list[str], outputsize: int,
        tolerance_pct: float, config: dict) -> dict[str, Any]:
    fetch_symbol = _fetch_symbol_for(symbol, config)
    fetched: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for p in providers:
        try:
            df, actual_provider = fetch_with_failover(
                fetch_symbol, interval, outputsize=outputsize, providers=[p],
            )
            fetched[actual_provider] = df
        except DataFetchError as exc:
            errors[p] = str(exc)

    comparisons = []
    names = list(fetched.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            comparisons.append(diff_bars(
                fetched[names[i]], fetched[names[j]],
                provider_a=names[i], provider_b=names[j],
                tolerance_pct=tolerance_pct,
            ))

    return {
        "symbol": symbol,
        "fetch_symbol": fetch_symbol,
        "interval": interval,
        "providers_requested": providers,
        "providers_fetched": names,
        "fetch_errors": errors,
        "comparisons": comparisons,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True, help="Internal symbol, e.g. EURUSD, BTCUSD")
    ap.add_argument("--interval", default="H4", help="Timeframe (default H4)")
    ap.add_argument("--outputsize", type=int, default=500)
    ap.add_argument("--providers", nargs="+", default=None,
                     help="Providers to compare (default: this symbol's config.yaml chain)")
    ap.add_argument("--tolerance-pct", type=float, default=0.05,
                     help="Close-price %% difference above which a bar counts as disagreeing")
    ap.add_argument("--out", default=None, help="Manifest name (writes research/results/<name>_manifest.json)")
    args = ap.parse_args()

    config = load_config()
    providers = args.providers or provider_chain_for(
        args.symbol, config.get("data", {}).get("provider_chains"))
    if len(providers) < 2:
        print(f"Need >=2 providers to compare, got: {providers}", file=sys.stderr)
        return 2

    results = run(args.symbol, args.interval, providers, args.outputsize,
                   args.tolerance_pct, config)

    for c in results["comparisons"]:
        print(f"{c['provider_a']} vs {c['provider_b']}: "
              f"{c.get('bars_common', 0)} common bars, verdict={c.get('verdict')}")
        if "close_diff_pct" in c:
            print(f"    close diff %%: mean={c['close_diff_pct']['mean']} "
                  f"max={c['close_diff_pct']['max']} "
                  f"({c['bars_exceeding_tolerance']}/{c['bars_common']} bars > {args.tolerance_pct}%%)")
    if results["fetch_errors"]:
        print(f"Fetch errors: {results['fetch_errors']}", file=sys.stderr)

    if args.out:
        m = research_manifest.build_manifest(
            kind="cross_provider_diff",
            config=config,
            params={"symbol": args.symbol, "interval": args.interval,
                    "providers": providers, "tolerance_pct": args.tolerance_pct},
            datasets=[],
            results=results,
        )
        path = research_manifest.write_manifest(m, args.out)
        print(f"Manifest written: {path}")

    material = any(c.get("verdict", "").startswith("MATERIAL") for c in results["comparisons"])
    return 1 if material else 0


if __name__ == "__main__":
    sys.exit(main())
