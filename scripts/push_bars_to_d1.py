#!/usr/bin/env python3
"""
scripts/push_bars_to_d1.py
------------------------------
Pushes the already-downloaded, trusted-provider CSVs (from
download_dukascopy_history.py / download_ctrader_fx_history.py /
download_twelve_data_history.py / download_multi_provider_history.py)
into the Cloudflare D1 `market_bars` warehouse (storage/market_bars.py),
and computes/stores a `dataset_manifest` row for every (symbol,
timeframe) pushed — the operator's own explicit design: D1 as the
canonical historical data source, one unified market_bars table, a
manifest recording real coverage/gap/integrity state so a caller can
check readiness before trusting a dataset for research.

M15 and H1 are the two NATIVELY fetched timeframes (this codebase's
download scripts never fetch H4/D1 directly — see download_dukascopy_
history.py's own module docstring: H4/D1 are always derived from H1 by
resampling). This script follows that exact convention: it pushes
whatever M15/H1 CSVs it finds on disk as-is, then DERIVES H4 and D1 by
resampling the pushed H1 series (never from M15 — H1 is this codebase's
canonical resample base everywhere else too) and pushes those as their
own market_bars rows, tagged with a "{source}_resampled" source label
so a manifest reader can tell native from derived at a glance.

For each symbol, source CSVs are looked up by suffix, in priority order
(mirrors the operator's own Dukascopy > cTrader > Twelve Data priority,
with the orchestrator's already-fallback-resolved output preferred when
present): _multiprovider.csv > _dukascopy.csv > _ctrader.csv >
_twelve_data.csv. A missing file for a given (symbol, timeframe) is
reported honestly (NO_SOURCE_FILE), never silently skipped without a
trace.

Scope, per the operator's own explicit sequencing instruction: this
script only pushes data and computes manifests — it does NOT touch
backtest/runner.py or Mission Center. Wiring a native D1 read path into
the backtest loader (load once per Mission, reuse the DataFrame across
every trial — never a per-trial D1 query) is a deliberate, separate
follow-up phase, taken up only once every dataset here reports READY.

RUN ON THE VPS — needs real D1_WORKER_URL/D1_PROXY_TOKEN credentials
(same requirement as every other storage/*.py D1 write) and the
already-downloaded CSVs from the provider scripts.

Usage:
    python3 -m scripts.push_bars_to_d1                      # all 24 configured symbols
    python3 -m scripts.push_bars_to_d1 --symbols EURUSD XAUUSD
    python3 -m scripts.push_bars_to_d1 --data-dir /path/to/data
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

DATA_DIR = PROJECT_ROOT / "data"

# Priority order: the orchestrator's own already-fallback-resolved output
# first, then each individual provider script's own suffix, matching the
# operator's stated Dukascopy -> cTrader -> Twelve Data priority.
_SOURCE_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("multiprovider", "_multiprovider.csv"),
    ("dukascopy", "_dukascopy.csv"),
    ("ctrader", "_ctrader.csv"),
    ("twelve_data", "_twelve_data.csv"),
)

_NATIVE_TIMEFRAMES: tuple[str, ...] = ("M15", "H1")
_DERIVED_TIMEFRAMES: tuple[str, ...] = ("H4", "D1")
_TF_TO_PANDAS_FREQ = {"H4": "4h", "D1": "1D"}


def _asset_class_for_symbol(symbol: str, config: dict) -> str:
    """Local equivalent of backtest.price_benchmark's own private
    _asset_class_for_symbol() — duplicated (underscore-private in that
    module), same convention already established in
    scripts/download_multi_provider_history.py."""
    for s in config.get("data", {}).get("twelve_data_symbols", []):
        if str(s.get("internal", "")).upper() == symbol:
            return str(s.get("asset_class", "fx_major"))
    return "fx_major"


def find_source_csv(symbol: str, timeframe: str, data_dir: Path) -> tuple[Path, str] | None:
    """First matching (path, source_label) in priority order, or None if
    nothing was ever downloaded for this (symbol, timeframe)."""
    for source, suffix in _SOURCE_SUFFIXES:
        path = data_dir / f"{symbol}_{timeframe}{suffix}"
        if path.exists():
            return path, source
    return None


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Standard OHLC/volume resample — associativity-safe (open=first,
    high=max, low=min, close=last, volume=sum), the same aggregation
    already proven this session (download_dukascopy_history.py's
    coarsen_bars)."""
    rule = _TF_TO_PANDAS_FREQ[timeframe]
    out = df.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["open", "high", "low", "close"])


def push_symbol(symbol: str, data_dir: Path, config: dict) -> dict[str, dict[str, Any]]:
    """Pushes both native timeframes (whichever CSVs exist) then derives
    H4/D1 from the native H1 series. Every one of the 4 timeframes gets a
    result entry, even a total miss (NO_SOURCE_FILE) — never silently
    dropped from the return value."""
    from storage import market_bars

    asset_class = _asset_class_for_symbol(symbol, config)
    results: dict[str, dict[str, Any]] = {}
    h1_df: pd.DataFrame | None = None
    h1_source: str | None = None

    for tf in _NATIVE_TIMEFRAMES:
        found = find_source_csv(symbol, tf, data_dir)
        if found is None:
            results[tf] = {"status": "NO_SOURCE_FILE", "rows": 0, "source": None}
            continue
        path, source = found
        df = load_csv(path)
        written = market_bars.upsert_bars(symbol, tf, df, source=source)
        manifest = market_bars.compute_manifest(symbol, tf, source=source, asset_class=asset_class)
        market_bars.upsert_manifest(manifest)
        results[tf] = {"status": manifest["status"], "rows": written, "source": source}
        if tf == "H1":
            h1_df, h1_source = df, source

    if h1_df is not None and not h1_df.empty:
        for tf in _DERIVED_TIMEFRAMES:
            derived_df = resample_ohlcv(h1_df, tf)
            if derived_df.empty:
                results[tf] = {"status": "NO_SOURCE_FILE", "rows": 0, "source": None}
                continue
            derived_source = f"{h1_source}_resampled"
            written = market_bars.upsert_bars(symbol, tf, derived_df, source=derived_source)
            manifest = market_bars.compute_manifest(symbol, tf, source=derived_source, asset_class=asset_class)
            market_bars.upsert_manifest(manifest)
            results[tf] = {"status": manifest["status"], "rows": written, "source": derived_source}
    else:
        for tf in _DERIVED_TIMEFRAMES:
            results[tf] = {"status": "NO_SOURCE_FILE", "rows": 0, "source": None}

    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--data-dir", default=str(DATA_DIR))
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

    data_dir = Path(args.data_dir)
    print("=" * 88)
    print(f"Push to D1 market_bars — {len(symbols)} symbol(s), timeframes: "
          f"{', '.join(_NATIVE_TIMEFRAMES + _DERIVED_TIMEFRAMES)}")
    print("=" * 88)

    all_results: dict[str, dict[str, dict[str, Any]]] = {}
    for sym in symbols:
        print(f"{sym} ... ", end="", flush=True)
        result = push_symbol(sym, data_dir, cfg)
        all_results[sym] = result
        print(", ".join(
            f"{tf}={result[tf]['status']}({result[tf]['rows']})"
            for tf in ("M15", "H1", "H4", "D1")
        ))

    print("\n" + "=" * 88)
    ready = sum(
        1 for sym in all_results for tf in ("M15", "H1", "H4", "D1")
        if all_results[sym][tf]["status"] == "READY"
    )
    total = len(all_results) * 4
    print(f"Manifest status: {ready}/{total} (symbol, timeframe) datasets READY")
    for sym, result in all_results.items():
        not_ready = [tf for tf in ("M15", "H1", "H4", "D1") if result[tf]["status"] != "READY"]
        if not_ready:
            print(f"  {sym}: not READY — " + ", ".join(f"{tf}={result[tf]['status']}" for tf in not_ready))
    print("=" * 88)


if __name__ == "__main__":
    main()
