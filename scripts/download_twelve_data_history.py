#!/usr/bin/env python3
"""
scripts/download_twelve_data_history.py
------------------------------------------
M15/H1 history for the full 24-symbol config.yaml `data.twelve_data_symbols`
universe (both `enabled` and paused/experimental entries — this is a bulk
download utility, not a live-trading symbol filter), via plain Twelve Data
pagination only.

Deliberately Twelve Data ONLY — no Yahoo Finance, no ccxt fallback (unlike
scripts/download_deep_history.py, which already covers H4/D1 for the same
universe WITH those fallbacks for symbols Twelve Data's free plan
plan-gates). Yahoo Finance was explicitly REMOVED from every price/macro
chain in this codebase (see CLAUDE.md's "Data layer" section) and this
script's own purpose is the THIRD tier of a three-provider, M15/H1-
capable download priority (Dukascopy first, then cTrader, then Twelve
Data — per the operator's own explicit request) — undiluted by any
fourth, untrusted source.

Generalizes scripts/download_deep_history.py's fetch_td_deep() backward-
pagination approach (page via `end_date` until the free plan's own
history floor for that (symbol, interval) pair — measured empirically by
"Twelve Data returned fewer than outputsize=5000 bars", never assumed)
from its existing H4/D1-only scope to M15/H1, reusing
core.twelve_data_client.INTERVAL_MAP for the interval-code translation
instead of a second hand-maintained mapping.

Output: data/{SYMBOL}_{M15,H1}_twelve_data.csv, plus a research/results
manifest with SHA256 fingerprints per file. Distinct filename suffix
(`_twelve_data`) from download_deep_history.py's own `_deep` H4/D1
output and download_dukascopy_history.py's/download_ctrader_fx_history.py's
`_dukascopy`/`_ctrader` output — nothing is overwritten by any of them.

Rate limit: Twelve Data's free plan allows 8 requests/minute —
TD_RATE_SLEEP paces every request to stay under it (matches
download_deep_history.py's own pacing constant). A finer M15/H1 pull
across the full 24-symbol universe is a genuinely long-running download;
run it as a background job, and prefer --symbols for a partial/targeted
run over the full universe when iterating.

Usage:
    python3 -m scripts.download_twelve_data_history --probe EURUSD                  # single symbol, one small request, no file written
    python3 -m scripts.download_twelve_data_history --timeframe M15                 # all 24 symbols, M15
    python3 -m scripts.download_twelve_data_history --symbols EURUSD XAUUSD --timeframe H1
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
    # Explicit path, not bare load_dotenv() — see
    # scripts/download_ctrader_fx_history.py for why bare load_dotenv()
    # is ambiguous, and why a PermissionError (.env is 600, owned by the
    # iatis service user) needs an actionable message instead of a raw
    # traceback (confirmed live on the VPS 2026-08-11).
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
except PermissionError:
    _env_path = PROJECT_ROOT / ".env"
    raise SystemExit(
        f"Permission denied reading {_env_path} as the current user. "
        f".env is owned by the iatis service user (600) — run this as "
        f"that user instead:\n"
        f"  sudo -u iatis {sys.executable} -m scripts.download_twelve_data_history ..."
    )

from core.twelve_data_client import INTERVAL_MAP

DATA_DIR = PROJECT_ROOT / "data"

SUPPORTED_TIMEFRAMES = ("M15", "H1")
TD_RATE_SLEEP = 8.5   # free plan: 8 req/min
TD_OUTPUTSIZE = 5000  # max bars per request on the free plan
_OHLCV = ["open", "high", "low", "close", "volume"]


def _td_get(params: dict, api_key: str, tries: int = 4) -> dict:
    params = {**params, "apikey": api_key}
    for attempt in range(tries):
        try:
            r = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
            j = r.json()
        except (requests.RequestException, ValueError):
            time.sleep(5)
            continue
        if j.get("code") == 429:          # minute-rate exceeded
            time.sleep(25)
            continue
        return j
    return {"code": "network", "message": "request failed after retries"}


def _to_frame(values: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(values)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    df["volume"] = (
        pd.to_numeric(df["volume"], errors="coerce").fillna(0) if "volume" in df.columns else 0.0
    )
    return df[_OHLCV].sort_index()


def fetch_td_history(td_symbol: str, timeframe: str, api_key: str,
                     outputsize: int = TD_OUTPUTSIZE) -> pd.DataFrame:
    """Paginate backward via `end_date` until the plan's own history
    floor for this (symbol, timeframe) pair is reached — the same
    mechanism as scripts/download_deep_history.py::fetch_td_deep,
    generalized to any interval INTERVAL_MAP knows about (this script
    only ever passes M15/H1, but the function itself doesn't hardcode
    that)."""
    interval = INTERVAL_MAP[timeframe]
    chunks: list[pd.DataFrame] = []
    end_date: str | None = None
    while True:
        params = {"symbol": td_symbol, "interval": interval, "outputsize": outputsize, "order": "asc"}
        if end_date:
            params["end_date"] = end_date
        j = _td_get(params, api_key)
        if "values" not in j:
            if not chunks:
                raise RuntimeError(f"{td_symbol} {interval}: {j.get('code')} {j.get('message')}")
            break  # paginated past the history floor — done
        chunk = _to_frame(j["values"])
        chunks.append(chunk)
        print(f"    chunk: {len(chunk):5d} bars  {chunk.index[0]} -> {chunk.index[-1]}")
        if len(chunk) < outputsize:
            break  # start of available history reached
        end_date = str(chunk.index[0])  # continue backwards from oldest bar
        time.sleep(TD_RATE_SLEEP)
    df = pd.concat(chunks).sort_index()
    return df[~df.index.duplicated(keep="first")]


def _integrity(df: pd.DataFrame) -> str:
    dups = int(df.index.duplicated().sum())
    bad = int((df["high"] < df["low"]).sum())
    return f"{len(df)} bars, dups={dups}, high<low={bad}"


def symbol_universe(cfg: dict) -> dict[str, str]:
    """{internal -> Twelve Data symbol} for the FULL config.yaml
    `data.twelve_data_symbols` list — enabled and paused/experimental
    entries alike. This is a bulk download utility, not a live-trading
    symbol filter, so (unlike scripts/download_deep_history.py, which
    filters to `enabled` since it feeds the live-decision data layer)
    every configured symbol is in scope."""
    return {s["internal"]: s["symbol"] for s in cfg["data"]["twelve_data_symbols"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", help="single symbol, one small request, print result, no file written")
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--timeframe", default="M15", choices=SUPPORTED_TIMEFRAMES)
    ap.add_argument("--force", action="store_true", help="re-download even if the output file exists")
    args = ap.parse_args()

    api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not api_key:
        sys.exit("TWELVE_DATA_API_KEY not set.")

    from utils.helpers import load_config
    cfg = load_config()
    by_internal = symbol_universe(cfg)

    if args.probe:
        symbol = args.probe.upper()
        if symbol not in by_internal:
            raise SystemExit(f"{symbol}: not in config.yaml's twelve_data_symbols universe.")
        interval = INTERVAL_MAP[args.timeframe]
        j = _td_get({"symbol": by_internal[symbol], "interval": interval, "outputsize": 20, "order": "asc"}, api_key)
        if "values" not in j:
            raise SystemExit(f"{symbol}: {j.get('code')} {j.get('message')}")
        df = _to_frame(j["values"])
        print(f"{symbol}: {len(df)} bars, {df.index[0]} -> {df.index[-1]}")
        print(df.tail(3))
        return

    symbols = [s.upper() for s in (args.symbols or list(by_internal))]
    unknown = [s for s in symbols if s not in by_internal]
    if unknown:
        raise SystemExit(f"Not in config.yaml's twelve_data_symbols universe: {unknown}")

    DATA_DIR.mkdir(exist_ok=True)
    collected: list[dict] = []
    t0 = time.monotonic()

    print("=" * 72)
    print(f"Twelve Data history download — {len(symbols)} symbol(s) @ {args.timeframe}")
    print("=" * 72)

    for idx, sym in enumerate(symbols, 1):
        out_path = DATA_DIR / f"{sym}_{args.timeframe}_twelve_data.csv"
        print(f"[{idx}/{len(symbols)}] {sym} ... ", end="", flush=True)
        if out_path.exists() and not args.force:
            print(f"exists ({out_path}) — skipped, pass --force to re-download")
            collected.append({"symbol": sym, "timeframe": args.timeframe, "file": str(out_path)})
            continue
        print()

        try:
            df = fetch_td_history(by_internal[sym], args.timeframe, api_key)
        except Exception as exc:
            print(f"  {sym}: FAILED — {exc}")
            continue
        if df.empty:
            print(f"  {sym}: FAILED — no bars downloaded")
            continue

        df.to_csv(out_path)
        note = _integrity(df)
        years = (df.index[-1] - df.index[0]).days / 365.25
        print(f"  {sym}: {note}, span={years:.1f}y")
        collected.append({"symbol": sym, "timeframe": args.timeframe, "file": str(out_path),
                          "bars": len(df), "span_years": round(years, 1),
                          "first": str(df.index[0]), "last": str(df.index[-1])})
        print(f"  saved: {out_path}  ({time.monotonic() - t0:.0f}s elapsed)")
        time.sleep(TD_RATE_SLEEP)

    if not collected:
        print("\nNo files downloaded — nothing to manifest.")
        return

    from research.manifest import build_manifest, dataset_fingerprint, write_manifest

    fps = [{**c, **dataset_fingerprint(Path(c["file"]))} for c in collected if Path(c["file"]).exists()]
    manifest = build_manifest(
        kind="twelve_data_history_download",
        config=cfg,
        params={"symbols": symbols, "timeframe": args.timeframe, "outputsize": TD_OUTPUTSIZE},
        datasets=fps,
        results={"files_written": len(fps)},
    )
    outp = write_manifest(manifest, f"twelve_data_history_{time.strftime('%Y%m%d')}")
    print(f"\nManifest: {outp}")
    print(f"\nDone — {len(collected)} file(s) in {DATA_DIR}/")


if __name__ == "__main__":
    main()
