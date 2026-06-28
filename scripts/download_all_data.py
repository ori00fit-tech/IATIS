#!/usr/bin/env python3
"""
scripts/download_all_data.py
------------------------------
Download 2 years of OHLCV data for all symbols across all timeframes.
Uses failover: Twelve Data → Yahoo Finance → Alpha Vantage → Finnhub

Symbols: 9 (Forex + Metals + Crypto)
Timeframes: 5m, 15m, 30m, 1h, 4h
Total files: 9 × 5 = 45 CSV files
Credits: ~40 Twelve Data requests (max 5000 bars/request)

Usage:
    python3 scripts/download_all_data.py
    python3 scripts/download_all_data.py --symbols XAUUSD BTCUSD
    python3 scripts/download_all_data.py --timeframes 15m 1h
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Config ────────────────────────────────────────────────────────────────
SYMBOLS = {
    "EURUSD": {"td": "EUR/USD", "yf": "EURUSD=X",  "av": "EUR",  "class": "forex"},
    "GBPUSD": {"td": "GBP/USD", "yf": "GBPUSD=X",  "av": "GBP",  "class": "forex"},
    "AUDUSD": {"td": "AUD/USD", "yf": "AUDUSD=X",  "av": "AUD",  "class": "forex"},
    "USDCAD": {"td": "USD/CAD", "yf": "USDCAD=X",  "av": "CAD",  "class": "forex"},
    "NZDUSD": {"td": "NZD/USD", "yf": "NZDUSD=X",  "av": "NZD",  "class": "forex"},
    "XAUUSD": {"td": "XAU/USD", "yf": "GC=F",      "av": "XAUUSD","class": "metal"},
    "BTCUSD": {"td": "BTC/USD", "yf": "BTC-USD",   "av": "BTC",  "class": "crypto"},
    "ETHUSD": {"td": "ETH/USD", "yf": "ETH-USD",   "av": "ETH",  "class": "crypto"},
    "XAGUSD": {"td": "XAG/USD", "yf": "SI=F",      "av": "XAGUSD","class": "metal"},
}

TIMEFRAMES = {
    "5m":  {"td": "5min",  "yf": "5m",  "av": "5min",  "bars": 5000},
    "15m": {"td": "15min", "yf": "15m", "av": "15min", "bars": 5000},
    "30m": {"td": "30min", "yf": "30m", "av": "30min", "bars": 5000},
    "1h":  {"td": "1h",    "yf": "1h",  "av": "60min", "bars": 5000},
    "4h":  {"td": "4h",    "yf": "4h",  "av": "240min","bars": 5000},
}

DATA_DIR = Path("data")


# ── Provider functions ─────────────────────────────────────────────────────

def fetch_twelve_data(sym_info: dict, tf_info: dict, internal: str) -> "pd.DataFrame | None":
    try:
        from core.twelve_data_client import TwelveDataClient
        import os
        key = os.environ.get("TWELVE_DATA_API_KEY", "")
        if not key:
            return None
        client = TwelveDataClient(api_key=key)
        df = client.time_series(sym_info["td"], tf_info["td"], outputsize=tf_info["bars"])
        if df is not None and len(df) > 50:
            return df
    except Exception as e:
        if "404" not in str(e):
            print(f"      TwelveData: {str(e)[:50]}")
    return None


def fetch_yahoo(sym_info: dict, tf_info: dict, internal: str) -> "pd.DataFrame | None":
    try:
        import yfinance as yf
        import pandas as pd

        yf_sym = sym_info["yf"]
        yf_interval = tf_info["yf"]

        # yfinance limits: 5m/15m/30m → 60 days, 1h → 730 days, 4h → 730 days
        if yf_interval in ("5m", "15m", "30m"):
            period = "60d"
        else:
            period = "730d"

        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period=period, interval=yf_interval, auto_adjust=True)

        if df is None or len(df) < 10:
            return None

        # Normalize columns
        df = df.rename(columns={"Open":"open","High":"high","Low":"low",
                                "Close":"close","Volume":"volume"})
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "datetime"
        df = df[["open","high","low","close","volume"]].dropna()
        return df if len(df) > 50 else None

    except Exception as e:
        print(f"      Yahoo: {str(e)[:50]}")
    return None


def fetch_alpha_vantage(sym_info: dict, tf_info: dict, internal: str) -> "pd.DataFrame | None":
    try:
        import os, requests
        import pandas as pd

        key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        if not key or key == "demo":
            return None

        av_interval = tf_info["av"]
        cls = sym_info["class"]
        sym = sym_info["av"]

        if cls == "forex":
            url = (f"https://www.alphavantage.co/query?function=FX_INTRADAY"
                   f"&from_symbol={sym}&to_symbol=USD&interval={av_interval}"
                   f"&outputsize=full&apikey={key}")
            r = requests.get(url, timeout=30)
            data = r.json()
            key_ts = f"Time Series FX ({av_interval})"
        elif cls in ("crypto",):
            url = (f"https://www.alphavantage.co/query?function=CRYPTO_INTRADAY"
                   f"&symbol={sym}&market=USD&interval={av_interval}"
                   f"&outputsize=full&apikey={key}")
            r = requests.get(url, timeout=30)
            data = r.json()
            key_ts = f"Time Series Crypto ({av_interval})"
        else:
            return None

        if key_ts not in data:
            return None

        ts = data[key_ts]
        rows = []
        for dt, vals in ts.items():
            try:
                rows.append({
                    "datetime": dt,
                    "open": float(vals.get("1. open", vals.get("1a. open (USD)", 0))),
                    "high": float(vals.get("2. high", vals.get("2a. high (USD)", 0))),
                    "low":  float(vals.get("3. low",  vals.get("3a. low (USD)", 0))),
                    "close":float(vals.get("4. close",vals.get("4a. close (USD)", 0))),
                    "volume": 0,
                })
            except Exception:
                pass

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()
        return df if len(df) > 50 else None

    except Exception as e:
        print(f"      AlphaVantage: {str(e)[:50]}")
    return None


def fetch_finnhub(sym_info: dict, tf_info: dict, internal: str) -> "pd.DataFrame | None":
    try:
        import os, requests, time
        import pandas as pd

        key = os.environ.get("FINNHUB_API_KEY", "")
        if not key:
            return None

        cls = sym_info["class"]

        # Map interval
        interval_map = {"5m":"5","15m":"15","30m":"30","1h":"60","4h":"D"}
        resolution = interval_map.get(list(TIMEFRAMES.keys())[
            list(v["td"] for v in TIMEFRAMES.values()).index(tf_info["td"])
        ], "60")

        now = int(time.time())
        start = int((datetime.now() - timedelta(days=730)).timestamp())

        if cls == "forex":
            fh_sym = f"OANDA:{sym_info['av']}_{sym_info['av']}"
            # simpler approach
            from_sym = sym_info["av"]
            fh_sym = f"OANDA:{from_sym}_USD"
        elif cls == "crypto":
            fh_sym = f"BINANCE:{sym_info['av']}USDT"
        else:
            return None

        url = (f"https://finnhub.io/api/v1/stock/candle"
               f"?symbol={fh_sym}&resolution={resolution}"
               f"&from={start}&to={now}&token={key}")
        r = requests.get(url, timeout=20)
        data = r.json()

        if data.get("s") != "ok" or not data.get("t"):
            return None

        df = pd.DataFrame({
            "datetime": pd.to_datetime(data["t"], unit="s", utc=True),
            "open":  data["o"], "high":  data["h"],
            "low":   data["l"], "close": data["c"], "volume": data["v"],
        }).set_index("datetime").sort_index()
        return df if len(df) > 50 else None

    except Exception as e:
        print(f"      Finnhub: {str(e)[:50]}")
    return None


# ── Main download logic ────────────────────────────────────────────────────

PROVIDERS = [
    ("TwelveData", fetch_twelve_data),
    ("Yahoo",      fetch_yahoo),
    ("AlphaVantage", fetch_alpha_vantage),
    ("Finnhub",    fetch_finnhub),
]


def download_one(internal: str, sym_info: dict, tf_label: str, tf_info: dict,
                 force: bool = False) -> bool:
    out = DATA_DIR / f"{internal}_{tf_label}_2y.csv"

    if not force and out.exists() and out.stat().st_size > 50_000:
        import pandas as pd
        try:
            df = pd.read_csv(out, index_col=0)
            print(f"    ⏭  {internal} {tf_label}: cached ({len(df)} bars)")
            return True
        except Exception:
            pass

    for provider_name, fetch_fn in PROVIDERS:
        print(f"    ⟳  {internal} {tf_label} [{provider_name}]...", end=" ", flush=True)
        try:
            df = fetch_fn(sym_info, tf_info, internal)
            if df is not None and len(df) > 50:
                DATA_DIR.mkdir(exist_ok=True)
                df.to_csv(out)
                bars = len(df)
                start = str(df.index[0])[:10]
                end   = str(df.index[-1])[:10]
                print(f"✅ {bars} bars ({start} → {end})")
                time.sleep(1)
                return True
            else:
                print(f"❌ empty")
        except Exception as e:
            print(f"❌ {str(e)[:40]}")

        time.sleep(2)

    print(f"    ✗  {internal} {tf_label}: ALL PROVIDERS FAILED")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--timeframes", nargs="+", default=None)
    parser.add_argument("--force", action="store_true", help="Re-download even if cached")
    args = parser.parse_args()

    # Load .env manually
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    # Filter symbols and timeframes
    symbols = {k: v for k, v in SYMBOLS.items()
               if not args.symbols or k in args.symbols}
    timeframes = {k: v for k, v in TIMEFRAMES.items()
                  if not args.timeframes or k in args.timeframes}

    total = len(symbols) * len(timeframes)
    print(f"\n{'='*60}")
    print(f"IATIS Multi-Source Data Downloader")
    print(f"{'='*60}")
    print(f"Symbols:    {len(symbols)} — {list(symbols.keys())}")
    print(f"Timeframes: {len(timeframes)} — {list(timeframes.keys())}")
    print(f"Total:      {total} files")
    print(f"Providers:  TwelveData → Yahoo → AlphaVantage → Finnhub")
    print(f"{'='*60}\n")

    t_start = time.monotonic()
    ok = fail = 0

    for internal, sym_info in symbols.items():
        print(f"\n[{internal}] ({sym_info['class']})")
        for tf_label, tf_info in timeframes.items():
            if download_one(internal, sym_info, tf_label, tf_info, args.force):
                ok += 1
            else:
                fail += 1

    duration = time.monotonic() - t_start
    print(f"\n{'='*60}")
    print(f"COMPLETE in {duration/60:.0f} min")
    print(f"  ✅ Success: {ok}/{total}")
    print(f"  ❌ Failed:  {fail}/{total}")
    print(f"\nFiles saved in: {DATA_DIR.resolve()}/")
    print(f"  Format: SYMBOL_TIMEFRAME_2y.csv")
    print(f"\nNext step:")
    print(f"  python3 scripts/m15_smart_backtest.py --all")


if __name__ == "__main__":
    main()
