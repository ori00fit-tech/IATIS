#!/usr/bin/env python3
"""
scripts/build_database.py
--------------------------
Build complete 2-year OHLCV database for all symbols.

Fetches BASE timeframes only, derives the rest:
  Fetch:  1m(crypto), 5m, 1h, 1d
  Derive: 15m=3×5m, 30m=6×5m, 4h=4×1h, 1w=7×1d

Sources per timeframe:
  1m:  Binance only (crypto, free, 500d max)
  5m:  Binance(crypto) + TwelveData batches(forex/metals)
  1h:  Yahoo Finance (free, 730d) + TwelveData fill
  1d:  Stooq (free, 10+ years) + Yahoo fallback

Total TwelveData credits: ~301 (5m forex/metals only)

Usage:
    python3 scripts/build_database.py           # all symbols
    python3 scripts/build_database.py --sym XAUUSD BTCUSD
    python3 scripts/build_database.py --base-only  # skip derived TFs
"""
from __future__ import annotations
import argparse, os, sys, time, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Load env ───────────────────────────────────────────────────────────────
for p in [Path(".env"), Path("/root/IATIS/.env")]:
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

import pandas as pd

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

SYMBOLS = {
    "EURUSD": {"yf":"EURUSD=X",  "td":"EUR/USD", "stooq":"eurusd", "ccxt":None,          "class":"forex"},
    "GBPUSD": {"yf":"GBPUSD=X",  "td":"GBP/USD", "stooq":"gbpusd", "ccxt":None,          "class":"forex"},
    "AUDUSD": {"yf":"AUDUSD=X",  "td":"AUD/USD", "stooq":"audusd", "ccxt":None,          "class":"forex"},
    "USDCAD": {"yf":"USDCAD=X",  "td":"USD/CAD", "stooq":"usdcad", "ccxt":None,          "class":"forex"},
    "NZDUSD": {"yf":"NZDUSD=X",  "td":"NZD/USD", "stooq":"nzdusd", "ccxt":None,          "class":"forex"},
    "XAUUSD": {"yf":"GC=F",      "td":"XAU/USD", "stooq":None,     "ccxt":None,          "class":"metal"},
    "XAGUSD": {"yf":"SI=F",      "td":"XAG/USD", "stooq":None,     "ccxt":None,          "class":"metal"},
    "BTCUSD": {"yf":"BTC-USD",   "td":"BTC/USD", "stooq":None,     "ccxt":"BTC/USDT",   "class":"crypto"},
    "ETHUSD": {"yf":"ETH-USD",   "td":"ETH/USD", "stooq":None,     "ccxt":"ETH/USDT",   "class":"crypto"},
}

DAYS = 730  # 2 years


# ── Helpers ────────────────────────────────────────────────────────────────
def norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.lower().split()[-1] if ' ' in c else c.lower() for c in df.columns]
    df = df.rename(columns={"adj close":"close","adj_close":"close"})
    for c in ["open","high","low","close"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "datetime"
    return df[["open","high","low","close","volume"]].dropna(subset=["open","close"])


def merge(*frames) -> pd.DataFrame | None:
    valid = [f for f in frames if f is not None and len(f) > 0]
    if not valid: return None
    out = pd.concat(valid)
    return out[~out.index.duplicated(keep="last")].sort_index()


def load_existing(path: Path) -> pd.DataFrame | None:
    try:
        if path.exists() and path.stat().st_size > 5000:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df.index = pd.to_datetime(df.index, utc=True)
            return df.sort_index()
    except Exception:
        pass
    return None


def save(df: pd.DataFrame, path: Path) -> int:
    df.to_csv(path)
    return len(df)


def coverage(df: pd.DataFrame | None) -> str:
    if df is None or len(df) == 0: return "0 bars"
    days = (df.index[-1] - df.index[0]).days
    return f"{len(df):,} bars ({days}d: {str(df.index[0])[:10]}→{str(df.index[-1])[:10]})"


# ── Fetch functions ────────────────────────────────────────────────────────
def from_yahoo(yf_sym: str, interval: str, period: str = "730d") -> pd.DataFrame | None:
    try:
        import yfinance as yf
        df = yf.download(yf_sym, period=period, interval=interval,
                        auto_adjust=True, progress=False, multi_level_index=False)
        return norm(df) if df is not None and len(df) > 5 else None
    except Exception as e:
        print(f"        Yahoo error: {str(e)[:50]}")
        return None


def from_stooq(stooq_sym: str, interval: str = "1d") -> pd.DataFrame | None:
    """Stooq provides free daily data for forex pairs."""
    if interval not in ("1d",): return None
    try:
        import pandas_datareader as pdr
        end = datetime.now()
        start = end - timedelta(days=DAYS)
        df = pdr.get_data_stooq(stooq_sym.upper(), start=start, end=end)
        if df is None or len(df) < 5: return None
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "datetime"
        df["volume"] = df.get("volume", 0)
        return df[["open","high","low","close","volume"]].sort_index()
    except Exception as e:
        print(f"        Stooq error: {str(e)[:50]}")
        return None


def from_binance(ccxt_sym: str, tf: str, days: int = DAYS) -> pd.DataFrame | None:
    """Fetch from Binance via ccxt with full pagination."""
    try:
        import ccxt
        ex = ccxt.binance({"enableRateLimit": True})
        tf_map = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
        all_bars, current = [], since
        batches = 0
        while True:
            bars = ex.fetch_ohlcv(ccxt_sym, tf_map[tf], since=current, limit=1000)
            if not bars: break
            all_bars.extend(bars)
            batches += 1
            if batches % 10 == 0:
                print(f"        Binance: {len(all_bars):,} bars...", end="\r")
            if len(bars) < 1000: break
            current = bars[-1][0] + 1
            time.sleep(ex.rateLimit / 1000)
        if not all_bars: return None
        df = pd.DataFrame(all_bars, columns=["ts","open","high","low","close","volume"])
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.index.name = "datetime"
        return df[["open","high","low","close","volume"]].sort_index()
    except Exception as e:
        print(f"        Binance error: {str(e)[:60]}")
        return None


def from_twelvedata_batches(td_sym: str, tf: str, days: int = DAYS) -> pd.DataFrame | None:
    """Fetch historical data from TwelveData using date-range batches."""
    import requests
    key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not key: return None

    tf_bars_per_day = {"1m":1440,"5m":288,"15m":96,"30m":48,"1h":24,"4h":6,"1d":1}
    bars_per_day = tf_bars_per_day.get(tf, 24)
    batch_days = min(50, max(5, 4900 // bars_per_day))
    n_batches = (days // batch_days) + 1

    print(f"        TwelveData: {n_batches} batches × {batch_days}d (~{n_batches} credits)")

    frames = []
    current_end = datetime.now(timezone.utc)
    target_start = current_end - timedelta(days=days)

    for i in range(n_batches):
        current_start = max(current_end - timedelta(days=batch_days), target_start)
        try:
            r = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
                "symbol": td_sym, "interval": tf, "outputsize": 5000,
                "start_date": current_start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": current_end.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "UTC", "apikey": key,
            })
            data = r.json()
            if data.get("status") == "error":
                msg = data.get("message","")
                print(f"        batch {i+1}: {msg[:50]}")
                if "limit" in msg.lower(): break
                continue
            values = data.get("values", [])
            if not values:
                current_end = current_start - timedelta(minutes=1)
                if current_end <= target_start: break
                continue
            rows = [{"datetime": v["datetime"],
                     "open":float(v["open"]), "high":float(v["high"]),
                     "low":float(v["low"]), "close":float(v["close"]),
                     "volume":float(v.get("volume",0))} for v in values]
            df = pd.DataFrame(rows)
            df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
            df = df.set_index("datetime").sort_index()
            frames.append(df)
            print(f"        batch {i+1}/{n_batches}: {len(df)} bars "
                  f"({str(df.index[0])[:10]}→{str(df.index[-1])[:10]})")
        except Exception as e:
            print(f"        batch {i+1} error: {str(e)[:40]}")
        current_end = current_start - timedelta(minutes=1)
        if current_end <= target_start: break
        time.sleep(8)

    return merge(*frames) if frames else None


# ── Build one symbol ───────────────────────────────────────────────────────
def build_base_tf(sym: str, info: dict, tf: str) -> pd.DataFrame | None:
    """Fetch one base timeframe using best available sources."""
    out = DATA_DIR / f"{sym}_{tf}_2y.csv"
    existing = load_existing(out)

    # Check if complete
    if existing is not None:
        cov = (existing.index[-1] - existing.index[0]).days
        if cov >= DAYS * 0.85:
            print(f"    {tf}: ⏭  {coverage(existing)}")
            return existing

    print(f"    {tf}: fetching...")
    frames = [existing] if existing is not None else []

    if tf == "1d":
        # Stooq (best for daily, free, unlimited)
        if info.get("stooq"):
            df = from_stooq(info["stooq"], "1d")
            if df is not None:
                print(f"      ✅ Stooq: {coverage(df)}")
                frames.append(df)
        # Yahoo fallback
        df = from_yahoo(info["yf"], "1d", "730d")
        if df is not None:
            print(f"      ✅ Yahoo: {coverage(df)}")
            frames.append(df)

    elif tf == "1h":
        # Yahoo (730d free)
        df = from_yahoo(info["yf"], "1h", "730d")
        if df is not None:
            print(f"      ✅ Yahoo: {coverage(df)}")
            frames.append(df)
        # TwelveData if needed
        if not frames or (merge(*frames) is not None and
                          (merge(*frames).index[-1] - merge(*frames).index[0]).days < DAYS * 0.8):
            df = from_twelvedata_batches(info["td"], "1h", DAYS)
            if df is not None:
                print(f"      ✅ TwelveData: {coverage(df)}")
                frames.append(df)

    elif tf == "5m":
        # Binance for crypto (free, best quality, 2 years)
        if info.get("ccxt"):
            df = from_binance(info["ccxt"], "5m", DAYS)
            if df is not None:
                print(f"      ✅ Binance: {coverage(df)}")
                frames.append(df)
        
        # Yahoo recent 60d (free, always available)
        df = from_yahoo(info["yf"], "5m", "60d")
        if df is not None:
            print(f"      ✅ Yahoo (60d): {coverage(df)}")
            frames.append(df)

        # Check if we need more data
        current = merge(*frames)
        current_days = (current.index[-1] - current.index[0]).days if current is not None else 0
        
        if current_days < DAYS * 0.8:
            # TwelveData batches (only if credits available)
            df = from_twelvedata_batches(info["td"], "5min", DAYS)
            if df is not None:
                print(f"      ✅ TwelveData: {coverage(df)}")
                frames.append(df)

    elif tf == "1m":
        # Only crypto from Binance (forex 1m = too many credits)
        if info.get("ccxt"):
            df = from_binance(info["ccxt"], "1m", min(DAYS, 30))  # limit 30 days for 1m
            if df is not None:
                print(f"      ✅ Binance 1m: {coverage(df)}")
                frames.append(df)
        else:
            print(f"      ⚠️  1m skipped for non-crypto (too expensive)")
            return None

    result = merge(*frames)
    if result is not None:
        n = save(result, out)
        print(f"    {tf}: ✅ saved {coverage(result)}")
    return result


def build_derived(sym: str, source_df: pd.DataFrame, source_tf: str,
                  target_tf: str, rule: str) -> pd.DataFrame | None:
    """Resample source_tf → target_tf."""
    out = DATA_DIR / f"{sym}_{target_tf}_2y.csv"
    if out.exists() and out.stat().st_size > 5000:
        existing = load_existing(out)
        if existing is not None and (existing.index[-1] - existing.index[0]).days >= DAYS * 0.8:
            print(f"    {target_tf}: ⏭  {coverage(existing)}")
            return existing

    try:
        df = source_df.resample(rule).agg(
            {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}
        ).dropna(subset=["open","close"])
        n = save(df, out)
        print(f"    {target_tf}: ✅ {coverage(df)} (from {source_tf})")
        return df
    except Exception as e:
        print(f"    {target_tf}: ❌ {e}")
        return None


def build_symbol(sym: str, info: dict) -> dict:
    print(f"\n{'─'*50}")
    print(f"[{sym}] ({info['class']})")
    print(f"{'─'*50}")
    results = {}

    # Base timeframes
    df_1d = build_base_tf(sym, info, "1d")
    df_1h = build_base_tf(sym, info, "1h")
    df_5m = build_base_tf(sym, info, "5m")
    df_1m = build_base_tf(sym, info, "1m") if info.get("ccxt") else None

    results["1d"] = len(df_1d) if df_1d is not None else 0
    results["1h"] = len(df_1h) if df_1h is not None else 0
    results["5m"] = len(df_5m) if df_5m is not None else 0
    results["1m"] = len(df_1m) if df_1m is not None else 0

    # Derived timeframes
    print(f"\n  Deriving timeframes:")
    if df_5m is not None:
        df_15m = build_derived(sym, df_5m, "5m", "15m", "15min")
        df_30m = build_derived(sym, df_5m, "5m", "30m", "30min")
        results["15m"] = len(df_15m) if df_15m is not None else 0
        results["30m"] = len(df_30m) if df_30m is not None else 0

    if df_1h is not None:
        df_4h = build_derived(sym, df_1h, "1h", "4h", "4h")
        results["4h"] = len(df_4h) if df_4h is not None else 0

    if df_1d is not None:
        df_1w = build_derived(sym, df_1d, "1d", "1w", "1W-MON")
        results["1w"] = len(df_1w) if df_1w is not None else 0

    return results


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sym", nargs="+", default=None)
    parser.add_argument("--base-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    symbols = {k: v for k, v in SYMBOLS.items() if not args.sym or k in args.sym}

    print(f"\n{'='*60}")
    print(f"IATIS Database Builder — 2 Year OHLCV")
    print(f"{'='*60}")
    print(f"Symbols:  {list(symbols.keys())}")
    print(f"Target:   {DAYS} days")
    print(f"Base TFs: 1m(crypto), 5m, 1h, 1d")
    print(f"Derived:  15m=3×5m, 30m=6×5m, 4h=4×1h, 1w=7×1d")
    print(f"Sources:  Binance→Yahoo→Stooq→TwelveData")
    print(f"{'='*60}")

    if args.force:
        import shutil
        for f in DATA_DIR.glob("*.csv"):
            f.unlink()
        print("Cleared existing data\n")

    t0 = time.monotonic()
    all_results = {}

    for sym, info in symbols.items():
        all_results[sym] = build_symbol(sym, info)

    duration = time.monotonic() - t0

    # Summary
    print(f"\n\n{'='*60}")
    print(f"DATABASE BUILD COMPLETE — {duration/60:.0f} min")
    print(f"{'='*60}")
    print(f"{'Symbol':<10}", end="")
    tfs = ["1m","5m","15m","30m","1h","4h","1d","1w"]
    for tf in tfs:
        print(f"{tf:>8}", end="")
    print()
    print("-"*74)
    for sym, res in all_results.items():
        print(f"{sym:<10}", end="")
        for tf in tfs:
            bars = res.get(tf, 0)
            cell = f"{bars//1000}k" if bars >= 1000 else str(bars) if bars > 0 else "—"
            print(f"{cell:>8}", end="")
        print()

    print(f"\nFiles: {DATA_DIR}/SYMBOL_TF_2y.csv")
    print(f"Next:  python3 scripts/m15_smart_backtest.py --all")

    # Save manifest
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "days": DAYS,
        "symbols": list(symbols.keys()),
        "results": all_results,
    }
    Path("storage/db_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()


# ── Standalone gap-filler (no TwelveData) ─────────────────────────────────
def fill_gaps_free(sym: str, info: dict) -> None:
    """Fill 5m gaps using only free sources: Yahoo batches via pandas."""
    out = DATA_DIR / f"{sym}_5m_2y.csv"
    existing = load_existing(out)
    
    if existing is not None:
        days_covered = (existing.index[-1] - existing.index[0]).days
        print(f"  {sym} 5m: {days_covered}d covered, trying to extend...")
    
    frames = [existing] if existing is not None else []
    
    # Yahoo 60d — always free
    df = from_yahoo(info["yf"], "5m", "60d")
    if df is not None:
        frames.append(df)
        print(f"  {sym}: Yahoo 5m 60d ✅ {len(df)} bars")
    
    # Try multiple Yahoo periods by manipulating dates
    # yfinance supports specific date ranges
    try:
        import yfinance as yf
        from datetime import datetime, timedelta, timezone
        
        end = datetime.now(timezone.utc)
        # Try going back in chunks of 50 days
        for days_back in range(60, 400, 50):
            chunk_end = end - timedelta(days=days_back - 50)
            chunk_start = end - timedelta(days=days_back)
            try:
                df_chunk = yf.download(
                    info["yf"],
                    start=chunk_start.strftime("%Y-%m-%d"),
                    end=chunk_end.strftime("%Y-%m-%d"),
                    interval="5m",
                    auto_adjust=True, progress=False, multi_level_index=False,
                )
                if df_chunk is not None and len(df_chunk) > 10:
                    df_chunk = norm(df_chunk)
                    frames.append(df_chunk)
                    print(f"  {sym}: Yahoo chunk {chunk_start.date()}→{chunk_end.date()} ✅ {len(df_chunk)} bars")
            except Exception:
                pass  # Yahoo rejects dates older than 60d silently
    except Exception as e:
        print(f"  {sym}: Yahoo extended failed: {str(e)[:40]}")
    
    if frames:
        result = merge(*frames)
        if result is not None:
            n = save(result, out)
            days = (result.index[-1] - result.index[0]).days
            print(f"  {sym} 5m: saved {n:,} bars ({days}d)")
    
    # Also derive 15m/30m from whatever we have
    df_5m = load_existing(out)
    if df_5m is not None:
        build_derived(sym, df_5m, "5m", "15m", "15min")
        build_derived(sym, df_5m, "5m", "30m", "30min")


if __name__ == "__main__":
    # Also support --fill-gaps mode
    import sys
    if "--fill-gaps" in sys.argv:
        symbols_arg = []
        for i, arg in enumerate(sys.argv):
            if arg == "--sym" and i + 1 < len(sys.argv):
                symbols_arg = sys.argv[i+1:]
                break
        
        syms = {k: v for k, v in SYMBOLS.items() 
                if not symbols_arg or k in symbols_arg}
        print(f"\nFilling gaps for: {list(syms.keys())}")
        for sym, info in syms.items():
            fill_gaps_free(sym, info)
    else:
        main()
