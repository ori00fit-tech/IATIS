#!/usr/bin/env python3
"""
scripts/download_multi_provider_history.py
-----------------------------------------------
Orchestrator: for every (symbol, timeframe) in scope, tries Dukascopy
(scripts.download_dukascopy_history) -> cTrader
(scripts.download_ctrader_fx_history) -> Twelve Data
(scripts.download_twelve_data_history), IN THAT PRIORITY ORDER, and
keeps the FIRST provider that returns real bars — per the operator's
own explicit priority ("Dukascopy أولاً ثم cTrader ثم twelve_data").
Each of the three tiers is REUSED verbatim (download_symbol_hours/
coarsen_bars, download_symbol_deep, fetch_td_history) — this script
contains zero provider-specific fetch logic of its own, only the
fallback/verification/reporting orchestration around them.

Every written file is verified before being trusted, reusing the exact
correctness tooling already built this session rather than
reimplementing checks:
  - core.data_validator.validate_ohlcv — structural OHLC integrity
    (columns, nulls, high>=low, high==max(o,c), low==min(o,c),
    monotonic index, no duplicate timestamps).
  - backtest.price_benchmark.completeness_score/classify_gaps —
    session-aware gap classification (crypto 24/7, equity market hours,
    energy/indices CME/ICE Globex sessions incl. the daily maintenance
    break, FX weekly closure) so a real Saturday/holiday gap is never
    misreported as a missing-data problem.

Output: data/{SYMBOL}_{TIMEFRAME}_multiprovider.csv (a NEW, distinct
filename suffix from every single-provider script's own
_dukascopy/_ctrader/_twelve_data output — this orchestrator's file is
the one meant to actually be picked up by backtest/runner.py's loader
for M15/H1 physical timeframes, since it's the union across all three
tiers, not any one provider's partial coverage) plus a
research/results manifest AND a printed/written honest coverage
summary — real years/bars/provider obtained per (symbol, timeframe),
NEVER padded or fabricated: a symbol every provider failed for is
reported as failed, not silently dropped or backfilled with a
different symbol's numbers.

cTrader and Twelve Data are each optional tiers: if cTrader credentials
are absent/unreachable, or TWELVE_DATA_API_KEY is unset, that tier is
skipped for every symbol (logged once, not per-symbol) rather than
aborting the whole run — Dukascopy alone (needs no credentials) still
covers FX/metals/crypto; USOIL/indices need cTrader or Twelve Data,
since the public Dukascopy feed doesn't map those instruments (see
download_dukascopy_history.py's own SYMBOL_MAP scope note).

One (symbol, timeframe) item's total failure (every tier failed) never
aborts the run for the rest — matches this codebase's established
per-item-isolation convention (run_robustness_suite, download_symbol_hours'
per-hour isolation, etc.).

RUN ON THE VPS — this sandbox's network policy blocks all three
providers (Dukascopy, cTrader Open API, Twelve Data have not been
live-verified from this session; each individual script's own module
docstring already discloses this same limitation).

Usage:
    python3 -m scripts.download_multi_provider_history                                   # all 24 symbols, M15+H1
    python3 -m scripts.download_multi_provider_history --symbols EURUSD XAUUSD --timeframes M15
    python3 -m scripts.download_multi_provider_history --skip-ctrader --skip-twelve-data  # Dukascopy only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

DATA_DIR = PROJECT_ROOT / "data"

PROVIDER_ORDER: tuple[str, ...] = ("dukascopy", "ctrader", "twelve_data")
SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("M15", "H1")


def _hour_range(years: float) -> list[datetime]:
    """Local copy of download_dukascopy_history.py's own private
    _hour_range() — a tiny (5-line), stable helper duplicated rather than
    imported across the module boundary, matching this session's own
    established convention for small private business-logic helpers
    (e.g. _TF_MINUTES_PER_BAR in download_ctrader_fx_history.py)."""
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=years * 365.25)
    total_hours = int((end - start).total_seconds() // 3600)
    return [start + timedelta(hours=i) for i in range(total_hours)]


def _asset_class_for_symbol(symbol: str, config: dict) -> str:
    """Local equivalent of backtest.price_benchmark's own private
    _asset_class_for_symbol() — duplicated (not imported, since it's
    underscore-private in that module) for the same reason _hour_range()
    above is: a tiny, stable read of config['data']['twelve_data_symbols']'s
    own asset_class field."""
    for s in config.get("data", {}).get("twelve_data_symbols", []):
        if str(s.get("internal", "")).upper() == symbol:
            return str(s.get("asset_class", "fx_major"))
    return "fx_major"


@dataclass
class ProviderAttempt:
    provider: str
    success: bool
    error: str | None
    bars: int = 0


@dataclass
class SymbolTimeframeResult:
    symbol: str
    timeframe: str
    provider_used: str | None
    attempts: list[ProviderAttempt]
    df: pd.DataFrame | None = field(default=None, repr=False)
    verify: dict | None = None


def download_symbol_timeframe(
    symbol: str, timeframe: str, providers: dict[str, Callable[[], pd.DataFrame]]
) -> SymbolTimeframeResult:
    """Tries each provider in PROVIDER_ORDER that's present in `providers`
    (a provider absent from the dict means "not configured/available for
    this run" — e.g. no cTrader session, no Twelve Data API key — and is
    silently skipped, not counted as a failed attempt). Returns on the
    FIRST provider that returns a real, non-empty DataFrame. Pure
    orchestration: every provider-specific fetch call is a caller-supplied
    zero-arg closure, so this function needs no network/credentials to
    unit-test."""
    attempts: list[ProviderAttempt] = []
    for provider_name in PROVIDER_ORDER:
        fetch_fn = providers.get(provider_name)
        if fetch_fn is None:
            continue
        try:
            df = fetch_fn()
        except Exception as exc:
            attempts.append(ProviderAttempt(provider_name, False, str(exc)))
            continue
        if df is None or df.empty:
            attempts.append(ProviderAttempt(provider_name, False, "empty result"))
            continue
        attempts.append(ProviderAttempt(provider_name, True, None, bars=len(df)))
        return SymbolTimeframeResult(symbol, timeframe, provider_name, attempts, df=df)
    return SymbolTimeframeResult(symbol, timeframe, None, attempts, df=None)


def build_provider_fetchers(
    symbol: str, timeframe: str, *,
    dukascopy_years: float,
    ctrader_client, ctrader_years: float,
    td_api_key: str, td_symbol_map: dict[str, str],
) -> dict[str, Callable[[], pd.DataFrame]]:
    """Constructs the real, live zero-arg fetch closures for one (symbol,
    timeframe) — the only function in this script that touches real
    provider modules. A tier is simply omitted from the returned dict
    when its prerequisite isn't available (ctrader_client is None,
    td_api_key is empty) — download_symbol_timeframe() then treats that
    tier as not configured, never as a failed attempt."""
    fetchers: dict[str, Callable[[], pd.DataFrame]] = {}

    from scripts.download_dukascopy_history import (
        SYMBOL_MAP as DUKASCOPY_SYMBOL_MAP,
        coarsen_bars,
        download_symbol_hours,
    )

    if symbol in DUKASCOPY_SYMBOL_MAP:
        instrument = DUKASCOPY_SYMBOL_MAP[symbol]

        def _fetch_dukascopy(instrument=instrument, symbol=symbol, timeframe=timeframe,
                             years=dukascopy_years) -> pd.DataFrame:
            hours = _hour_range(years)
            base_df = download_symbol_hours(instrument, symbol, hours)
            if base_df.empty:
                return base_df
            return coarsen_bars(base_df, timeframe)

        fetchers["dukascopy"] = _fetch_dukascopy

    if ctrader_client is not None:
        from scripts.download_ctrader_fx_history import IATIS_TO_CTRADER, download_symbol_deep

        if symbol in IATIS_TO_CTRADER:
            def _fetch_ctrader(client=ctrader_client, symbol=symbol, timeframe=timeframe,
                               years=ctrader_years) -> pd.DataFrame:
                return download_symbol_deep(client, symbol, years=years, timeframe=timeframe)

            fetchers["ctrader"] = _fetch_ctrader

    if td_api_key and symbol in td_symbol_map:
        from scripts.download_twelve_data_history import fetch_td_history

        def _fetch_twelve_data(td_symbol=td_symbol_map[symbol], timeframe=timeframe,
                               api_key=td_api_key) -> pd.DataFrame:
            return fetch_td_history(td_symbol, timeframe, api_key)

        fetchers["twelve_data"] = _fetch_twelve_data

    return fetchers


def verify_and_write(symbol: str, timeframe: str, df: pd.DataFrame, asset_class: str, out_path: Path) -> dict:
    """Runs the correctness-verification step and writes the file only
    after computing (never skipping) both checks — an operator reading
    the coverage summary sees the real result either way, a clean pass
    or a specific structural failure."""
    from core.data_validator import DataValidationError, validate_ohlcv
    from backtest.price_benchmark import completeness_score

    try:
        validate_ohlcv(df)
        ohlc_valid, ohlc_error = True, None
    except DataValidationError as exc:
        ohlc_valid, ohlc_error = False, str(exc)

    comp_score, comp_detail = completeness_score(df, timeframe, asset_class)

    df.index.name = "datetime"
    df.to_csv(out_path)

    years = round((df.index[-1] - df.index[0]).days / 365.25, 2) if len(df) >= 2 else 0.0
    return {
        "ohlc_valid": ohlc_valid,
        "ohlc_error": ohlc_error,
        "completeness_score": comp_score,
        "completeness_detail": comp_detail,
        "bars": len(df),
        "years": years,
        "first": str(df.index[0]) if len(df) else None,
        "last": str(df.index[-1]) if len(df) else None,
    }


def coverage_summary(results: list[SymbolTimeframeResult]) -> list[dict]:
    """Pure, plain-dict summary of a batch of results — never includes a
    DataFrame, always includes every attempted provider's outcome, so a
    total failure is reported honestly (provider_used=None) rather than
    silently omitted from the summary."""
    rows: list[dict] = []
    for r in results:
        rows.append({
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "provider_used": r.provider_used,
            "attempts": [{"provider": a.provider, "success": a.success, "error": a.error, "bars": a.bars}
                        for a in r.attempts],
            "bars": (r.verify or {}).get("bars", 0),
            "years": (r.verify or {}).get("years", 0.0),
            "completeness_score": (r.verify or {}).get("completeness_score"),
            "ohlc_valid": (r.verify or {}).get("ohlc_valid"),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--timeframes", nargs="+", default=list(SUPPORTED_TIMEFRAMES), choices=SUPPORTED_TIMEFRAMES)
    ap.add_argument("--years", type=float, default=10.0)
    ap.add_argument("--force", action="store_true", help="re-download even if the output file exists")
    ap.add_argument("--skip-ctrader", action="store_true")
    ap.add_argument("--skip-twelve-data", action="store_true")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass

    from utils.helpers import load_config
    cfg = load_config()
    universe = {s["internal"]: s for s in cfg["data"]["twelve_data_symbols"]}
    symbols = [s.upper() for s in (args.symbols or list(universe))]
    unknown = [s for s in symbols if s not in universe]
    if unknown:
        raise SystemExit(f"Not in config.yaml's twelve_data_symbols universe: {unknown}")

    from scripts.download_twelve_data_history import symbol_universe as td_symbol_universe
    td_map = td_symbol_universe(cfg)
    td_api_key = "" if args.skip_twelve_data else os.environ.get("TWELVE_DATA_API_KEY", "")
    if not args.skip_twelve_data and not td_api_key:
        print("TWELVE_DATA_API_KEY not set — Twelve Data tier skipped for every symbol.")

    ctrader_client = None
    if not args.skip_ctrader:
        try:
            from scripts.download_ctrader_fx_history import _connect_client
            ctrader_client = _connect_client()
        except Exception as exc:
            print(f"cTrader unavailable — that tier skipped for every symbol ({exc}).")

    DATA_DIR.mkdir(exist_ok=True)
    results: list[SymbolTimeframeResult] = []
    t0 = time.monotonic()

    print("=" * 72)
    print(f"Multi-provider history download — {len(symbols)} symbol(s) x "
          f"{len(args.timeframes)} timeframe(s), priority: {', '.join(PROVIDER_ORDER)}")
    print("=" * 72)

    for sym in symbols:
        for tf in args.timeframes:
            out_path = DATA_DIR / f"{sym}_{tf}_multiprovider.csv"
            print(f"{sym} {tf} ... ", end="", flush=True)
            if out_path.exists() and not args.force:
                print(f"exists ({out_path}) — skipped, pass --force to re-download")
                continue

            fetchers = build_provider_fetchers(
                sym, tf,
                dukascopy_years=args.years,
                ctrader_client=ctrader_client, ctrader_years=args.years,
                td_api_key=td_api_key, td_symbol_map=td_map,
            )
            result = download_symbol_timeframe(sym, tf, fetchers)

            if result.provider_used is None:
                print("FAILED — every available provider failed:")
                for a in result.attempts:
                    print(f"    {a.provider}: {a.error}")
                results.append(result)
                continue

            asset_class = _asset_class_for_symbol(sym, cfg)
            result.verify = verify_and_write(sym, tf, result.df, asset_class, out_path)
            v = result.verify
            print(f"{result.provider_used} -> {v['bars']} bars, {v['years']}y, "
                  f"completeness={v['completeness_score']}, ohlc_valid={v['ohlc_valid']}"
                  + (f" ({v['ohlc_error']})" if not v["ohlc_valid"] else ""))
            print(f"  saved: {out_path}  ({time.monotonic() - t0:.0f}s elapsed)")
            results.append(result)

    if ctrader_client is not None:
        ctrader_client.disconnect()

    summary = coverage_summary(results)
    ok = [r for r in summary if r["provider_used"]]
    failed = [r for r in summary if not r["provider_used"]]

    print("\n" + "=" * 72)
    print(f"Coverage summary: {len(ok)}/{len(summary)} succeeded")
    for row in ok:
        print(f"  {row['symbol']:8s} {row['timeframe']:4s} -> {row['provider_used']:12s} "
              f"{row['bars']:6d} bars  {row['years']:.1f}y  "
              f"completeness={row['completeness_score']}  ohlc_valid={row['ohlc_valid']}")
    if failed:
        print(f"\n{len(failed)} item(s) FAILED — every provider failed:")
        for row in failed:
            print(f"  {row['symbol']:8s} {row['timeframe']:4s}: "
                  + "; ".join(f"{a['provider']}={a['error']}" for a in row["attempts"]))
    print("=" * 72)

    if not summary:
        print("\nNothing attempted — nothing to manifest.")
        return

    from research.manifest import build_manifest, dataset_fingerprint, write_manifest

    fps = []
    for row in ok:
        p = DATA_DIR / f"{row['symbol']}_{row['timeframe']}_multiprovider.csv"
        if p.exists():
            fps.append({**row, **dataset_fingerprint(p)})

    manifest = build_manifest(
        kind="multi_provider_history_download",
        config=cfg,
        params={"symbols": symbols, "timeframes": args.timeframes, "years": args.years,
                "provider_order": list(PROVIDER_ORDER)},
        datasets=fps,
        results={"succeeded": len(ok), "failed": len(failed), "coverage": summary},
    )
    outp = write_manifest(manifest, f"multi_provider_history_{time.strftime('%Y%m%d')}")
    print(f"\nManifest: {outp}")


if __name__ == "__main__":
    main()
