#!/usr/bin/env python3
"""
scripts/download_mt5_history.py
----------------------------------
Deep historical H1 (default) download from the Wine-hosted MT5 bridge
(scripts/mt5_bridge/mt5_bridge_server.py), for symbols the operator has
verified the bridge serves correctly. Mirrors
scripts/download_ctrader_fx_history.py's backward-pagination approach —
same windowing pattern, different transport (HTTP to a local bridge
instead of a direct cTrader session).

RUN ON THE VPS, with MT5_BRIDGE_URL pointed at the real Wine-hosted
bridge (see docs/MT5_BRIDGE_SETUP.md) — this cannot run from a sandbox
with no Wine/MT5 terminal.

Output: data/{SYMBOL}_{TIMEFRAME}_mt5.csv (distinct filename — nothing
existing is overwritten) plus a research/results manifest with SHA256
fingerprints, matching this project's other download-script convention.

Usage:
    python3 -m scripts.download_mt5_history --probe EURUSD                # sanity check, no file written
    python3 -m scripts.download_mt5_history --symbols EURUSD XAUUSD --years 2
    python3 -m scripts.download_mt5_history --symbols EURUSD --timeframe H4 --years 3
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import requests

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD"]

BARS_PER_REQUEST = 1000
REQUEST_SLEEP_SEC = 1.0
MAX_REQUESTS_PER_SYMBOL = 60  # hard stop, same conservative cap as the cTrader script

_TF_MINUTES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}


def _bridge_url() -> str:
    url = os.environ.get("MT5_BRIDGE_URL")
    if not url:
        raise SystemExit(
            "MT5_BRIDGE_URL is not set. Point it at the Wine-hosted bridge "
            "(default http://127.0.0.1:18812) — see docs/MT5_BRIDGE_SETUP.md."
        )
    return url.rstrip("/")


def _bridge_headers() -> dict:
    return {"X-Bridge-Token": os.environ.get("MT5_BRIDGE_TOKEN", "")}


def _check_health() -> None:
    resp = requests.get(f"{_bridge_url()}/health", headers=_bridge_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("connected"):
        raise SystemExit(
            f"MT5 bridge reports not connected: {data}. Confirm the Wine-hosted "
            f"terminal is running and logged in before downloading history."
        )
    print(f"Bridge health OK: {data.get('account')}")


def _fetch_batch(symbol: str, timeframe: str, count: int, before: int | None) -> list[dict]:
    params: dict = {"symbol": symbol, "timeframe": timeframe, "count": count}
    if before is not None:
        params["before"] = before
    resp = requests.get(f"{_bridge_url()}/rates", params=params, headers=_bridge_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()["rates"]


def download_symbol_deep(symbol: str, timeframe: str, years: float,
                         bars_per_request: int = BARS_PER_REQUEST) -> pd.DataFrame:
    """Page backward via /rates?before=... until `years` of history is
    collected or the bridge stops returning new (older) bars."""
    tf_minutes = _TF_MINUTES.get(timeframe, 60)
    target_bars = int(years * 365.25 * 24 * 60 / tf_minutes)
    all_bars: list[dict] = []
    seen_timestamps: set[int] = set()
    before_ts: int | None = None
    requests_made = 0

    while len(all_bars) < target_bars and requests_made < MAX_REQUESTS_PER_SYMBOL:
        batch = _fetch_batch(symbol, timeframe, bars_per_request, before_ts)
        requests_made += 1
        if not batch:
            print(f"    (empty batch after {requests_made} requests — history floor reached)")
            break

        new_bars = [b for b in batch if b["time"] not in seen_timestamps]
        if not new_bars:
            print(f"    (batch #{requests_made} had no new bars — stopping)")
            break

        for b in new_bars:
            seen_timestamps.add(b["time"])
        all_bars.extend(new_bars)

        oldest_ts = min(b["time"] for b in batch)
        print(f"    batch #{requests_made}: +{len(new_bars)} bars "
              f"(total {len(all_bars)}), oldest so far "
              f"{pd.Timestamp(oldest_ts, unit='s', tz='UTC').date()}")

        before_ts = oldest_ts - 1
        time.sleep(REQUEST_SLEEP_SEC)

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars)
    df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("datetime")[["open", "high", "low", "close", "volume"]].sort_index()
    return df[~df.index.duplicated(keep="first")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", help="single symbol, print bar count only, no file written")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--timeframe", default="H1", choices=sorted(_TF_MINUTES))
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--force", action="store_true", help="re-download even if the output file exists")
    args = parser.parse_args()

    _check_health()

    if args.probe:
        df = download_symbol_deep(args.probe, args.timeframe, years=min(args.years, 0.5))
        if df.empty:
            print(f"{args.probe}: no bars returned — check symbol name / MT5_SYMBOL_MAP on the bridge.")
        else:
            print(f"{args.probe}: {len(df)} bars, {df.index[0]} -> {df.index[-1]}")
        return

    symbols = args.symbols or DEFAULT_SYMBOLS
    DATA_DIR.mkdir(exist_ok=True)
    csvs: list[str] = []
    t0 = time.monotonic()

    print("=" * 72)
    print(f"MT5 bridge deep history download — {len(symbols)} symbol(s) @ "
          f"{args.timeframe}, target {args.years}y each")
    print("=" * 72)

    for idx, sym in enumerate(symbols, 1):
        out_path = DATA_DIR / f"{sym}_{args.timeframe}_mt5.csv"
        print(f"[{idx}/{len(symbols)}] {sym} ... ", end="", flush=True)
        if out_path.exists() and not args.force:
            print(f"exists ({out_path}) — skipped, pass --force to re-download")
            csvs.append(str(out_path))
            continue
        print()

        df = download_symbol_deep(sym, args.timeframe, years=args.years)
        if df.empty:
            print(f"  {sym}: FAILED — no bars downloaded")
            continue

        print(f"  {sym}: {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()}")
        df.index.name = "datetime"
        df.to_csv(out_path)
        csvs.append(str(out_path))
        print(f"  saved: {out_path}  ({time.monotonic() - t0:.0f}s elapsed)")

    if not csvs:
        print("\nNo files downloaded — nothing to manifest.")
        return

    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    manifest = build_manifest(
        kind="mt5_history_download",
        config=load_config(),
        params={"symbols": symbols, "timeframe": args.timeframe, "years": args.years,
                "bars_per_request": BARS_PER_REQUEST},
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"files_written": csvs},
    )
    outp = write_manifest(manifest, f"mt5_history_{time.strftime('%Y%m%d')}")
    print(f"\nManifest: {outp}")


if __name__ == "__main__":
    main()
