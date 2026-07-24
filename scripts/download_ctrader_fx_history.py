#!/usr/bin/env python3
"""
scripts/download_ctrader_fx_history.py
------------------------------------------
Deep H1 FX history with REAL cTrader tick-volume, for the 7 enabled FX
symbols. Built specifically because every other historical FX source this
project has (Yahoo Finance, Twelve Data Free) reports zero volume for
forex — confirmed empirically 2026-07-23 on the VPS:
`load_from_csv('data/EURUSD_H1_2y.csv')['volume'].describe()` -> max 0.0.
That made the H023 Wyckoff-volume-gating A/B (arm A vs arm B, differing
only in whether FX volume is zeroed) a no-op: both arms already had zero
FX volume, so dPF=0.0 on every symbol was an artifact of the data source,
not a real null finding.

cTrader's own trendbars (execution/ctrader_client.py::get_trendbars) DO
carry real tick-volume for FX — that's the live feed's own volume field,
already trusted elsewhere in this codebase (position tracking,
reconciliation). This script pages backward through that same API using
the new `to_timestamp_ms` parameter (additive, 2026-07-24 — every other
get_trendbars() caller is unaffected) to build a multi-year H1 dataset,
so H023 can be re-run against data that actually contains the condition
it's testing.

Output: data/{SYMBOL}_H1_ctrader.csv (distinct filename from the existing
Yahoo-sourced data/{SYMBOL}_H1_{2y,5y}.csv — nothing is overwritten) plus
a research/results manifest with SHA256 fingerprints.

RUN ON THE VPS (cTrader Open API is network-blocked from the sandbox; a
live cTrader session is required, same credentials as the live trader —
this reads market history only, never touches positions or orders).

Usage:
    python3 -m scripts.download_ctrader_fx_history --probe EURUSD   # sanity: prints bar count + volume stats
    python3 -m scripts.download_ctrader_fx_history --years 3        # all 7 FX symbols, ~3y each
    python3 -m scripts.download_ctrader_fx_history --symbols EURUSD GBPUSD --years 2
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

try:
    from dotenv import load_dotenv
    # Explicit path, not bare load_dotenv(): the no-arg form locates .env
    # by walking up from the CALLING file's own directory (stack-frame
    # inspection, not os.getcwd()) — that made scripts/close_orphaned_trades.py
    # fail the same way on this VPS (D1_WORKER_URL is not set, 2026-07-23).
    # Anchoring to the repo root removes that ambiguity, but NOT a genuine
    # OS permission error (.env is 600, owned by the iatis service user) —
    # that's a real "wrong user" condition, not a discovery bug, so it's
    # caught explicitly below with an actionable message instead of a
    # raw traceback.
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
except PermissionError:
    _env_path = PROJECT_ROOT / ".env"
    raise SystemExit(
        f"Permission denied reading {_env_path} as the current user. "
        f".env is owned by the iatis service user (600) — run this as "
        f"that user instead:\n"
        f"  sudo -u iatis {sys.executable} -m scripts.download_ctrader_fx_history ..."
    )

DATA_DIR = PROJECT_ROOT / "data"

FX_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "EURJPY", "GBPJPY", "AUDJPY"]

# Conservative per-request size and pacing — this is a research download,
# not latency-sensitive; erring toward fewer/larger requests with pauses
# between them is safer than probing cTrader's undocumented per-request
# window limit aggressively. Adjust down if the VPS run hits errors.
BARS_PER_REQUEST = 1000
REQUEST_SLEEP_SEC = 2.0
MAX_REQUESTS_PER_SYMBOL = 60  # hard stop — ~60k H1 bars (~7 years) even in the worst case


def _connect_client():
    """One shared cTrader session for the whole download, reusing the
    exact same credential/connect path as live trading
    (execution/ctrader_client.py) — nothing broker-specific is
    reimplemented here. read_only=True: this script only ever calls
    get_trendbars()/get_account_info() (never places an order), so it
    can safely run alongside the live scheduler's own session — the
    single-session process lock (P0-3) exists specifically to prevent
    duplicate REAL ORDER submission, which a read_only client is
    hard-blocked from doing (see CTraderClient.place_market_order)."""
    import os
    from execution.ctrader_client import CTraderClient

    client = CTraderClient(
        client_id=os.environ["CTRADER_CLIENT_ID"],
        client_secret=os.environ["CTRADER_CLIENT_SECRET"],
        account_id=int(os.environ["CTRADER_ACCOUNT_ID"]),
        access_token=os.environ["CTRADER_ACCESS_TOKEN"],
        environment=os.environ.get("CTRADER_ENVIRONMENT", "demo"),
        read_only=True,
    )
    if not client.connect(timeout=30):
        raise SystemExit("Could not connect to cTrader — check .env credentials and network.")
    return client


def download_symbol_deep(client, symbol: str, years: float,
                         bars_per_request: int = BARS_PER_REQUEST) -> pd.DataFrame:
    """Page backward from now via get_trendbars(to_timestamp_ms=...) until
    `years` of H1 history is collected or the server stops returning new
    (older) bars — whichever comes first. Dedupes and sorts ascending."""
    target_bars = int(years * 365.25 * 24)  # H1 bars per year, calendar (over-estimates trading hours — fine, it's just a stop condition)
    all_bars: list[dict] = []
    seen_timestamps: set[int] = set()
    to_ts_ms: int | None = None
    requests_made = 0

    while len(all_bars) < target_bars and requests_made < MAX_REQUESTS_PER_SYMBOL:
        batch = client.get_trendbars(symbol, period="H1", count=bars_per_request,
                                     to_timestamp_ms=to_ts_ms)
        requests_made += 1
        if not batch:
            print(f"    (empty batch after {requests_made} requests — history floor reached)")
            break

        new_bars = [b for b in batch if b["timestamp"] * 1000 not in seen_timestamps]
        if not new_bars:
            print(f"    (batch #{requests_made} had no new bars — stopping)")
            break

        for b in new_bars:
            seen_timestamps.add(b["timestamp"] * 1000)
        all_bars.extend(new_bars)

        oldest_ts_sec = min(b["timestamp"] for b in batch)
        print(f"    batch #{requests_made}: +{len(new_bars)} bars "
              f"(total {len(all_bars)}), oldest so far "
              f"{pd.Timestamp(oldest_ts_sec, unit='s', tz='UTC').date()}")

        # Page strictly before the oldest bar just received.
        to_ts_ms = oldest_ts_sec * 1000 - 1
        time.sleep(REQUEST_SLEEP_SEC)

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("datetime")[["open", "high", "low", "close", "volume"]].sort_index()
    return df[~df.index.duplicated(keep="first")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", help="single symbol, print bar count + volume stats, no file written")
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--force", action="store_true", help="re-download even if the output file exists")
    args = parser.parse_args()

    client = _connect_client()

    if args.probe:
        df = download_symbol_deep(client, args.probe, years=min(args.years, 0.5))
        if df.empty:
            print(f"{args.probe}: no bars returned — check symbol name / connection.")
        else:
            print(f"{args.probe}: {len(df)} bars, "
                  f"{df.index[0]} -> {df.index[-1]}")
            print(df["volume"].describe())
        client.disconnect()
        return

    symbols = args.symbols or FX_SYMBOLS
    DATA_DIR.mkdir(exist_ok=True)
    csvs: list[str] = []
    t0 = time.monotonic()

    print("=" * 72)
    print(f"cTrader deep FX history download — {len(symbols)} symbol(s), "
          f"target {args.years}y each")
    print("=" * 72)

    for idx, sym in enumerate(symbols, 1):
        out_path = DATA_DIR / f"{sym}_H1_ctrader.csv"
        print(f"[{idx}/{len(symbols)}] {sym} ... ", end="", flush=True)
        if out_path.exists() and not args.force:
            print(f"exists ({out_path}) — skipped, pass --force to re-download")
            csvs.append(str(out_path))
            continue
        print()

        df = download_symbol_deep(client, sym, years=args.years)
        if df.empty:
            print(f"  {sym}: FAILED — no bars downloaded")
            continue

        vol_nonzero_frac = (df["volume"] > 0).mean()
        print(f"  {sym}: {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()}, "
              f"nonzero-volume fraction={vol_nonzero_frac:.2%}")
        if vol_nonzero_frac < 0.5:
            print(f"  WARNING: {sym} volume is mostly zero even from cTrader — "
                  f"this symbol may not carry real tick-volume either. Inspect "
                  f"before trusting an H023 re-run on it.")

        df.index.name = "datetime"
        df.to_csv(out_path)
        csvs.append(str(out_path))
        print(f"  saved: {out_path}  ({time.monotonic() - t0:.0f}s elapsed)")

    client.disconnect()

    if not csvs:
        print("\nNo files downloaded — nothing to manifest.")
        return

    from research.manifest import build_manifest, dataset_fingerprint, write_manifest
    from utils.helpers import load_config

    manifest = build_manifest(
        kind="ctrader_fx_history_download",
        config=load_config(),
        params={"symbols": symbols, "years": args.years,
                "bars_per_request": BARS_PER_REQUEST},
        datasets=[dataset_fingerprint(Path(c)) for c in csvs],
        results={"files_written": csvs},
    )
    outp = write_manifest(manifest, f"ctrader_fx_history_{time.strftime('%Y%m%d')}")
    print(f"\nManifest: {outp}")
    print("\nNext: re-run H023 pointed at these files instead of the Yahoo-sourced "
          "*_H1_2y.csv/*_H1_5y.csv ones (research/experiments/H023_wyckoff_volume_gating.py "
          "needs a small update to discover *_H1_ctrader.csv first).")


if __name__ == "__main__":
    main()
