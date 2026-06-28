#!/usr/bin/env python3
"""
scripts/download_smart.py
--------------------------
Smart incremental data downloader — builds 2 years of history
by fetching in batches and appending, using all available providers.

Strategy per timeframe:
  5m:  Yahoo (60d) + TD batches → ~60 days max
  15m: Yahoo (60d) + TD batches → ~52 days per request
  30m: Yahoo (60d) + TD batches → ~104 days per request
  1h:  TD (5000 bars=208d) × 4 batches OR Yahoo 730d → 2 years ✅
  4h:  TD (5000 bars=833d) × 1 batch → 2 years ✅

For short TF (5m/15m/30m): collects max available (~60d from Yahoo)
For long TF (1h/4h): collects full 2 years

Usage:
  python3 scripts/download_smart.py           # all symbols, all TFs
  python3 scripts/download_smart.py --tf 1h 4h
  python3 scripts/download_smart.py --sym XAUUSD BTCUSD
  python3 scripts/download_smart.py --force   # re-download all
"""
from __future__ import annotations
import argparse, os, sys, time, json
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Load .env ──────────────────────────────────────────────────────────────
def _load_env():
    for p in [Path(".env"), Path("/root/IATIS/.env")]:
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
            return
_load_env()

# ── Symbol map ─────────────────────────────────────────────────────────────
SYMBOLS = {
    "EURUSD": {"td":"EUR/USD",  "yf":"EURUSD=X",  "fh":"OANDA:EUR_USD"},
    "GBPUSD": {"td":"GBP/USD",  "yf":"GBPUSD=X",  "fh":"OANDA:GBP_USD"},
    "AUDUSD": {"td":"AUD/USD",  "yf":"AUDUSD=X",  "fh":"OANDA:AUD_USD"},
    "USDCAD": {"td":"USD/CAD",  "yf":"USDCAD=X",  "fh":"OANDA:USD_CAD"},
    "NZDUSD": {"td":"NZD/USD",  "yf":"NZDUSD=X",  "fh":"OANDA:NZD_USD"},
    "XAUUSD": {"td":"XAU/USD",  "yf":"GC=F",      "fh":"OANDA:XAU_USD"},
    "XAGUSD": {"td":"XAG/USD",  "yf":"SI=F",      "fh":"OANDA:XAG_USD"},
    "BTCUSD": {"td":"BTC/USD",  "yf":"BTC-USD",   "fh":"BINANCE:BTCUSDT"},
    "ETHUSD": {"td":"ETH/USD",  "yf":"ETH-USD",   "fh":"BINANCE:ETHUSDT"},
}

# ── Timeframe config ───────────────────────────────────────────────────────
TF_CONFIG = {
    "5m":  {"td":"5min",  "yf":"5m",  "fh":"5",   "minutes":5,   "max_days":60,  "td_bars":500},
    "15m": {"td":"15min", "yf":"15m", "fh":"15",  "minutes":15,  "max_days":180, "td_bars":5000},
    "30m": {"td":"30min", "yf":"30m", "fh":"30",  "minutes":30,  "max_days":365, "td_bars":5000},
    "1h":  {"td":"1h",    "yf":"1h",  "fh":"60",  "minutes":60,  "max_days":730, "td_bars":5000},
    "4h":  {"td":"4h",    "yf":"4h",  "fh":"D",   "minutes":240, "max_days":730, "td_bars":5000},
}

DATA_DIR = Path("data")
PROGRESS_FILE = Path("storage/download_progress.json")


# ── Progress tracking ──────────────────────────────────────────────────────
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_progress(progress: dict):
    PROGRESS_FILE.parent.mkdir(exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2, default=str))


# ── Fetch functions ────────────────────────────────────────────────────────
def fetch_td(sym: str, tf: str, end_dt: datetime | None = None) -> "pd.DataFrame | None":
    """Fetch from Twelve Data."""
    try:
        import pandas as pd
        from core.twelve_data_client import TwelveDataClient
        key = os.environ.get("TWELVE_DATA_API_KEY", "")
        if not key:
            return None
        cfg = TF_CONFIG[tf]
        client = TwelveDataClient(api_key=key)
        df = client.time_series(
            SYMBOLS[sym]["td"],
            cfg["td"],
            outputsize=cfg["td_bars"],
        )
        return df if (df is not None and len(df) > 10) else None
    except Exception as e:
        if "404" not in str(e) and "400" not in str(e):
            print(f"        TD error: {str(e)[:60]}")
        return None


def fetch_yf(sym: str, tf: str) -> "pd.DataFrame | None":
    """Fetch from Yahoo Finance."""
    try:
        import yfinance as yf
        import pandas as pd
        cfg = TF_CONFIG[tf]
        yf_sym = SYMBOLS[sym]["yf"]
        max_days = cfg["max_days"]
        yf_interval = cfg["yf"]

        # Yahoo limits
        if yf_interval in ("5m", "15m", "30m"):
            period = "60d"
        elif yf_interval == "1h":
            period = "730d"
        else:
            period = "730d"

        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period=period, interval=yf_interval, auto_adjust=True)
        if df is None or len(df) < 10:
            return None

        df = df.rename(columns={"Open":"open","High":"high","Low":"low",
                                 "Close":"close","Volume":"volume"})
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "datetime"
        return df[["open","high","low","close","volume"]].dropna()
    except Exception as e:
        print(f"        YF error: {str(e)[:60]}")
        return None


def fetch_finnhub(sym: str, tf: str,
                  start: datetime, end: datetime) -> "pd.DataFrame | None":
    """Fetch from Finnhub for a specific time range."""
    try:
        import requests, pandas as pd
        key = os.environ.get("FINNHUB_API_KEY", "")
        if not key:
            return None
        cfg = TF_CONFIG[tf]
        fh_sym = SYMBOLS[sym]["fh"]
        resolution = cfg["fh"]
        url = (f"https://finnhub.io/api/v1/forex/candle"
               f"?symbol={fh_sym}&resolution={resolution}"
               f"&from={int(start.timestamp())}&to={int(end.timestamp())}"
               f"&token={key}")
        # crypto uses different endpoint
        if sym in ("BTCUSD", "ETHUSD"):
            url = (f"https://finnhub.io/api/v1/crypto/candle"
                   f"?symbol={fh_sym}&resolution={resolution}"
                   f"&from={int(start.timestamp())}&to={int(end.timestamp())}"
                   f"&token={key}")
        r = requests.get(url, timeout=20)
        data = r.json()
        if data.get("s") != "ok" or not data.get("t"):
            return None
        df = pd.DataFrame({
            "open":data["o"],"high":data["h"],"low":data["l"],
            "close":data["c"],"volume":data["v"],
        }, index=pd.to_datetime(data["t"], unit="s", utc=True))
        df.index.name = "datetime"
        return df.sort_index() if len(df) > 5 else None
    except Exception as e:
        print(f"        FH error: {str(e)[:60]}")
        return None


def fetch_av(sym: str, tf: str) -> "pd.DataFrame | None":
    """Fetch from Alpha Vantage."""
    try:
        import requests, pandas as pd
        key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        if not key or key in ("demo", ""):
            return None
        cfg = TF_CONFIG[tf]
        av_interval = {"5m":"5min","15m":"15min","30m":"30min","1h":"60min","4h":"60min"}[tf]
        sym_info = SYMBOLS[sym]
        if sym in ("BTCUSD","ETHUSD"):
            crypto = {"BTCUSD":"BTC","ETHUSD":"ETH"}[sym]
            url = (f"https://www.alphavantage.co/query?function=CRYPTO_INTRADAY"
                   f"&symbol={crypto}&market=USD&interval={av_interval}"
                   f"&outputsize=full&apikey={key}")
            key_ts = f"Time Series Crypto ({av_interval})"
            o,h,l,c = "1a. open (USD)","2a. high (USD)","3a. low (USD)","4a. close (USD)"
        else:
            from_sym = sym[:3]
            url = (f"https://www.alphavantage.co/query?function=FX_INTRADAY"
                   f"&from_symbol={from_sym}&to_symbol=USD&interval={av_interval}"
                   f"&outputsize=full&apikey={key}")
            key_ts = f"Time Series FX ({av_interval})"
            o,h,l,c = "1. open","2. high","3. low","4. close"
        r = requests.get(url, timeout=30)
        data = r.json()
        if key_ts not in data:
            return None
        rows = []
        for dt, v in data[key_ts].items():
            try:
                rows.append({"datetime":dt,"open":float(v[o]),"high":float(v[h]),
                             "low":float(v[l]),"close":float(v[c]),"volume":0})
            except Exception:
                pass
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()
        return df if len(df) > 10 else None
    except Exception as e:
        print(f"        AV error: {str(e)[:60]}")
        return None


# ── Core download logic ────────────────────────────────────────────────────
def get_existing(out: Path) -> "pd.DataFrame | None":
    """Load existing CSV if valid."""
    try:
        if out.exists() and out.stat().st_size > 5000:
            import pandas as pd
            df = pd.read_csv(out, index_col=0, parse_dates=True)
            df.index = pd.to_datetime(df.index, utc=True)
            return df.sort_index()
    except Exception:
        pass
    return None


def merge_and_save(existing, new_df, out: Path) -> int:
    """Merge new data with existing, deduplicate, save."""
    import pandas as pd
    if existing is not None and new_df is not None:
        combined = pd.concat([existing, new_df])
    elif new_df is not None:
        combined = new_df
    else:
        return 0
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    combined.to_csv(out)
    return len(combined)


def download_symbol_tf(sym: str, tf: str, force: bool = False) -> dict:
    """Download one symbol + timeframe using smart incremental approach."""
    import pandas as pd
    out = DATA_DIR / f"{sym}_{tf}_2y.csv"
    DATA_DIR.mkdir(exist_ok=True)
    cfg = TF_CONFIG[tf]

    existing = get_existing(out)
    target_days = cfg["max_days"]
    now = datetime.now(timezone.utc)
    target_start = now - timedelta(days=target_days)

    # Check if already complete
    if not force and existing is not None:
        coverage_days = (existing.index[-1] - existing.index[0]).days
        bars = len(existing)
        if coverage_days >= target_days * 0.85:
            return {"status": "cached", "bars": bars, "days": coverage_days}

    results = []
    is_crypto = sym in ("BTCUSD", "ETHUSD")

    # Strategy 0: CCXT/Binance for crypto (FREE, unlimited history since 2017)
    if is_crypto:
        print(f"      → Binance/ccxt...", end=" ", flush=True)
        try:
            from core.ccxt_provider import fetch_ccxt
            tf_map = {"5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h"}
            df_ccxt = fetch_ccxt(sym, tf_map.get(tf, "1h"), days=730)
            if df_ccxt is not None and len(df_ccxt) > 100:
                print(f"✅ {len(df_ccxt)} bars ({str(df_ccxt.index[0])[:10]} → {str(df_ccxt.index[-1])[:10]})")
                results.append(df_ccxt)
            else:
                print("❌")
        except Exception as e:
            print(f"❌ {str(e)[:40]}")
        time.sleep(0.5)

    # Strategy 1: Yahoo Finance (free, best coverage for 1h/4h)
    print(f"      → Yahoo...", end=" ", flush=True)
    df_yf = fetch_yf(sym, tf)
    if df_yf is not None and len(df_yf) > 10:
        print(f"✅ {len(df_yf)} bars ({str(df_yf.index[0])[:10]} → {str(df_yf.index[-1])[:10]})")
        results.append(df_yf)
        time.sleep(0.5)
    else:
        print("❌")

    # Strategy 2: Twelve Data (paid but we have credits, fills gaps)
    print(f"      → TwelveData...", end=" ", flush=True)
    df_td = fetch_td(sym, tf)
    if df_td is not None and len(df_td) > 10:
        print(f"✅ {len(df_td)} bars ({str(df_td.index[0])[:10]} → {str(df_td.index[-1])[:10]})")
        results.append(df_td)
        time.sleep(8)
    else:
        print("❌")
        time.sleep(1)

    # Note: AlphaVantage FX_INTRADAY = premium ($$$)
    # Note: Finnhub forex candles = not available on free plan
    # → Providers: Binance/ccxt (crypto) + Yahoo (forex/metals) + TwelveData

    if not results:
        return {"status": "failed", "bars": 0, "days": 0}

    # Merge all sources + existing
    import pandas as pd
    combined = existing
    for df in results:
        if combined is None:
            combined = df
        else:
            combined = pd.concat([combined, df])

    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    combined.to_csv(out)

    bars = len(combined)
    days = (combined.index[-1] - combined.index[0]).days
    start_date = str(combined.index[0])[:10]
    end_date   = str(combined.index[-1])[:10]

    return {
        "status": "ok", "bars": bars, "days": days,
        "start": start_date, "end": end_date,
    }


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sym", nargs="+", default=None)
    parser.add_argument("--tf", nargs="+", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    symbols = {k: v for k, v in SYMBOLS.items()
               if not args.sym or k in args.sym}
    timeframes = {k: v for k, v in TF_CONFIG.items()
                  if not args.tf or k in args.tf}

    total = len(symbols) * len(timeframes)
    progress = load_progress()

    print(f"\n{'='*60}")
    print(f"IATIS Smart Data Downloader")
    print(f"{'='*60}")
    print(f"Symbols:    {list(symbols.keys())}")
    print(f"Timeframes: {list(timeframes.keys())}")
    print(f"Target:     {total} files | up to 2 years each")
    print(f"Providers:  Yahoo Finance (free) → Twelve Data (credits)")
    print(f"Note:       AV FX_INTRADAY=premium, Finnhub forex=not free")
    print(f"Coverage:")
    print(f"  5m/15m/30m: Yahoo 60d + TwelveData 5000bars")
    print(f"  1h/4h:      Yahoo 730d + TwelveData 5000bars → ~2 years ✅")
    print(f"{'='*60}\n")

    t_start = time.monotonic()
    ok = cached = failed = 0
    summary = []

    for sym in symbols:
        print(f"\n[{sym}]")
        for tf in timeframes:
            key = f"{sym}_{tf}"
            print(f"  {tf}:", end="")

            r = download_symbol_tf(sym, tf, args.force)
            progress[key] = r
            save_progress(progress)

            status = r["status"]
            bars   = r.get("bars", 0)
            days   = r.get("days", 0)
            start  = r.get("start", "?")
            end    = r.get("end", "?")

            if status == "cached":
                print(f" ⏭  {bars} bars ({days}d) — already complete")
                cached += 1
            elif status == "ok":
                print(f" ✅  {bars} bars | {start} → {end} ({days}d)")
                ok += 1
            else:
                print(f" ❌  all providers failed")
                failed += 1

            summary.append({
                "symbol": sym, "tf": tf,
                "status": status, "bars": bars, "days": days,
            })

    duration = time.monotonic() - t_start
    print(f"\n{'='*60}")
    print(f"DONE in {duration/60:.0f} min")
    print(f"  ✅ Downloaded: {ok}")
    print(f"  ⏭  Cached:    {cached}")
    print(f"  ❌ Failed:    {failed}")
    print(f"{'='*60}")
    print(f"\nFiles: data/SYMBOL_TF_2y.csv")
    print(f"Next:  python3 scripts/m15_smart_backtest.py --all")

    # Save summary
    out = Path("storage/download_summary.json")
    out.write_text(json.dumps({
        "generated": datetime.now().isoformat(),
        "summary": summary,
    }, indent=2))
    print(f"Log:   {out}")


if __name__ == "__main__":
    main()
