#!/usr/bin/env python3
"""
scripts/download_deep_history.py
----------------------------------
Deepest history the free data plans actually serve, for every enabled
symbol — measured, not assumed (probed 2026-07-05):

  Twelve Data Free (12 FX + XAUUSD):
      1day : up to ~19 years (outputsize 5000)
      4h   : back to ~2020-01-30 (~6.5 years) — the plan's hard floor
  Plan-gated on TD (XAGUSD, USOIL, US30, NAS100, SPX500) → Yahoo:
      1d   : 10+ years ("max")
      4h   : resampled from 1h, limited to Yahoo's 730-day window
  Crypto (BTCUSD, ETHUSD) → ccxt/Binance directly, NOT Twelve Data:
      1day / 4h : full exchange history, free, unrated (since 2017) —
      routed here instead of TD because ccxt's own floor is real
      exchange history, not an arbitrary free-plan gate (2026-07-13,
      added when a 10-year backtest data pull asked for the deepest
      history achievable per symbol).

So the ">=10 years" goal is met on D1 for ALL symbols, and on H4 for
crypto too; FX/metals/indices H4 gets the maximum each source allows —
a hard external plan limit, not something more code can fix. Output:
data/{SYMBOL}_{TF}_deep.csv plus a research/results manifest with
SHA256 fingerprints per file.

Run on the VPS (Yahoo is blocked from some sandboxes):
    python3 scripts/download_deep_history.py               # everything
    python3 scripts/download_deep_history.py --symbols EURUSD XAUUSD
    python3 scripts/download_deep_history.py --skip-existing
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests

from dotenv import load_dotenv
load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Symbols Twelve Data Free refuses (404 plan-gate) → fetched from Yahoo.
YAHOO_ONLY = {
    "XAGUSD": "SI=F",
    "USOIL": "CL=F",
    "US30": "^DJI",
    "NAS100": "^NDX",
    "SPX500": "^GSPC",
}

# Crypto: route to ccxt/Binance instead of Twelve Data. Binance's own
# history (free, since listing ~2017) is deeper than any TD free-plan
# floor and isn't subject to TD's rate/output-size gating.
CCXT_DEEP = {"BTCUSD", "ETHUSD"}
_INTERVAL_TO_CCXT_TF = {"4h": "4h", "1day": "1d"}

TD_RATE_SLEEP = 8.5   # free plan: 8 req/min
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


def fetch_td_deep(td_symbol: str, interval: str, api_key: str) -> pd.DataFrame:
    """Paginate backwards with end_date until the plan's history floor."""
    chunks: list[pd.DataFrame] = []
    end_date: str | None = None
    while True:
        params = {"symbol": td_symbol, "interval": interval, "outputsize": 5000, "order": "asc"}
        if end_date:
            params["end_date"] = end_date
        j = _td_get(params, api_key)
        if "values" not in j:
            if not chunks:
                raise RuntimeError(f"{td_symbol} {interval}: {j.get('code')} {j.get('message')}")
            break  # paginated past the history floor — done
        chunk = _to_frame(j["values"])
        chunks.append(chunk)
        print(f"    chunk: {len(chunk):5d} bars  {chunk.index[0].date()} -> {chunk.index[-1].date()}")
        if len(chunk) < 5000:
            break  # start of available history reached
        end_date = str(chunk.index[0])  # continue backwards from oldest bar
        time.sleep(TD_RATE_SLEEP)
    df = pd.concat(chunks).sort_index()
    return df[~df.index.duplicated(keep="first")]


def fetch_yahoo_deep(yf_symbol: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    t = yf.Ticker(yf_symbol)
    if interval == "1day":
        raw = t.history(period="max", interval="1d", auto_adjust=True)
    else:  # 4h — Yahoo has no native 4h; resample its 730-day 1h window
        raw = t.history(period="730d", interval="1h", auto_adjust=True)
    if raw is None or raw.empty:
        raise RuntimeError(f"Yahoo returned no data for {yf_symbol} {interval}")
    raw = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    raw.index = pd.to_datetime(raw.index, utc=True)
    if interval == "4h":
        raw = (raw.resample("4h")
                  .agg({"open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum"})
                  .dropna())
    return raw.sort_index()


def fetch_ccxt_deep(symbol: str, interval: str) -> pd.DataFrame:
    """Full ccxt/Binance history for a crypto symbol — paginates via
    core.ccxt_provider.fetch_ccxt until the exchange's own listing date,
    not an artificial plan floor."""
    from core.ccxt_provider import fetch_ccxt

    tf = _INTERVAL_TO_CCXT_TF.get(interval, interval)
    df = fetch_ccxt(symbol, timeframe=tf, days=3650)  # 10y ask; pagination stops at listing
    if df is None or df.empty:
        raise RuntimeError(f"ccxt returned no data for {symbol} {tf}")
    return df


def _integrity(df: pd.DataFrame) -> str:
    dups = int(df.index.duplicated().sum())
    bad = int((df["high"] < df["low"]).sum())
    return f"{len(df)} bars, dups={dups}, high<low={bad}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--timeframes", nargs="+", default=["4h", "1day"])
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not api_key:
        sys.exit("TWELVE_DATA_API_KEY not set (needed for the non-gated symbols)")

    from utils.helpers import load_config
    cfg = load_config()
    enabled = [s for s in cfg["data"]["twelve_data_symbols"] if s.get("enabled")]
    if args.symbols:
        enabled = [s for s in enabled if s["internal"] in set(args.symbols)]

    DATA_DIR.mkdir(exist_ok=True)
    collected = []
    label = {"4h": "H4", "1day": "D1"}

    for s in enabled:
        sym = s["internal"]
        for interval in args.timeframes:
            out = DATA_DIR / f"{sym}_{label.get(interval, interval)}_deep.csv"
            if args.skip_existing and out.exists():
                print(f"{sym} {interval}: exists, skipping")
                continue
            print(f"{sym} {interval}:")
            try:
                if sym in CCXT_DEEP:
                    df = fetch_ccxt_deep(sym, interval)
                elif sym in YAHOO_ONLY:
                    df = fetch_yahoo_deep(YAHOO_ONLY[sym], interval)
                else:
                    df = fetch_td_deep(s["symbol"], interval, api_key)
                    time.sleep(TD_RATE_SLEEP)
            except Exception as exc:
                print(f"    FAILED: {exc}")
                continue
            df.to_csv(out)
            note = _integrity(df)
            years = (df.index[-1] - df.index[0]).days / 365.25
            print(f"    saved {out.name}: {note}, span={years:.1f}y")
            collected.append({"symbol": sym, "interval": interval, "file": str(out),
                              "bars": len(df), "span_years": round(years, 1),
                              "first": str(df.index[0]), "last": str(df.index[-1])})

    # Bind the collection run to code+data fingerprints (audit item H2).
    try:
        from research.manifest import build_manifest, dataset_fingerprint, write_manifest
        fps = [{**c, **dataset_fingerprint(Path(c["file"]))} for c in collected]
        manifest = build_manifest(
            kind="deep_history_collection",
            config=cfg,
            params={"timeframes": args.timeframes,
                    "td_4h_floor": "2020-01-30 (plan limit, probed 2026-07-05)",
                    "yahoo_only": sorted(YAHOO_ONLY),
                    "ccxt_deep": sorted(CCXT_DEEP)},
            datasets=fps,
            results={"files": len(collected)},
        )
        out_path = write_manifest(manifest, f"deep_history_{time.strftime('%Y%m%d')}")
        print(f"\nManifest: {out_path}")
    except Exception as exc:
        print(f"Manifest write failed (data still saved): {exc}")

    print(f"\nDone — {len(collected)} files in {DATA_DIR}/")


if __name__ == "__main__":
    main()
