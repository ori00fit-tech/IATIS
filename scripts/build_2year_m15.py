#!/usr/bin/env python3
"""
scripts/build_2year_m15.py
---------------------------
Build 2 years of M15 data using 3 strategies combined:

Strategy A: Yahoo batches (Forex/Metals)
  - yf.download() with start/end dates
  - 60 days per batch × 12 batches = ~720 days
  - Free, no API key

Strategy B: Binance pagination (Crypto)
  - ccxt fetch_ohlcv() with since parameter
  - Full history since 2017, unlimited
  - Free, no API key

Strategy C: TwelveData batches (all, highest quality)
  - time_series with start_date parameter
  - ~5 credits per batch × 12 batches = ~60 credits per symbol
  - Uses our remaining credits

Merges all sources → deduplicates → saves unified CSV

Usage:
    python3 scripts/build_2year_m15.py
    python3 scripts/build_2year_m15.py --symbols XAUUSD BTCUSD
    python3 scripts/build_2year_m15.py --days 730
"""
from __future__ import annotations
import argparse, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load env
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
    "EURUSD": {"yf": "EURUSD=X",  "td": "EUR/USD", "ccxt": None},
    "GBPUSD": {"yf": "GBPUSD=X",  "td": "GBP/USD", "ccxt": None},
    "AUDUSD": {"yf": "AUDUSD=X",  "td": "AUD/USD", "ccxt": None},
    "USDCAD": {"yf": "USDCAD=X",  "td": "USD/CAD", "ccxt": None},
    "NZDUSD": {"yf": "NZDUSD=X",  "td": "NZD/USD", "ccxt": None},
    "XAUUSD": {"yf": "GC=F",      "td": "XAU/USD", "ccxt": None},
    "XAGUSD": {"yf": "SI=F",      "td": "XAG/USD", "ccxt": None},
    "BTCUSD": {"yf": "BTC-USD",   "td": "BTC/USD", "ccxt": "BTC/USDT"},
    "ETHUSD": {"yf": "ETH-USD",   "td": "ETH/USD", "ccxt": "ETH/USDT"},
}


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize columns and index."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    rename = {"open":"open","high":"high","low":"low","close":"close","volume":"volume",
              "1. open":"open","2. high":"high","3. low":"low","4. close":"close"}
    df = df.rename(columns=rename)
    for col in ["open","high","low","close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "datetime"
    return df[["open","high","low","close","volume"]].dropna(subset=["open","close"])


def merge_dfs(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge multiple DataFrames, deduplicate, sort."""
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


# ── Strategy A: Yahoo batches ──────────────────────────────────────────────
def fetch_yahoo_batches(yf_sym: str, days: int = 730) -> pd.DataFrame | None:
    """Fetch M15 from Yahoo in 55-day batches (max 60d per request)."""
    try:
        import yfinance as yf
        frames = []
        end = datetime.now(timezone.utc)
        start_total = end - timedelta(days=days)
        batch_days = 55  # stay under 60d limit
        current_end = end
        batches = 0

        print(f"      Yahoo: {days}d ÷ {batch_days}d = {days//batch_days+1} batches")

        while current_end > start_total:
            current_start = max(current_end - timedelta(days=batch_days), start_total)
            try:
                df = yf.download(
                    yf_sym,
                    start=current_start.strftime("%Y-%m-%d"),
                    end=current_end.strftime("%Y-%m-%d"),
                    interval="15m",
                    auto_adjust=True,
                    progress=False,
                    multi_level_index=False,
                )
                if df is not None and len(df) > 5:
                    df = normalize_df(df)
                    frames.append(df)
                    batches += 1
                    print(f"        batch {batches}: {len(df)} bars ({current_start.date()} → {current_end.date()})")
            except Exception as e:
                print(f"        batch error: {str(e)[:40]}")
            current_end = current_start - timedelta(minutes=15)
            time.sleep(0.5)

        if not frames:
            return None
        result = merge_dfs(frames)
        print(f"      Yahoo total: {len(result)} bars")
        return result

    except Exception as e:
        print(f"      Yahoo failed: {e}")
        return None


# ── Strategy B: Binance pagination ────────────────────────────────────────
def fetch_binance_full(ccxt_sym: str, days: int = 730) -> pd.DataFrame | None:
    """Fetch full M15 history from Binance via ccxt pagination."""
    try:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        since_ms = int(since_dt.timestamp() * 1000)
        all_bars = []
        current = since_ms
        batch = 0

        print(f"      Binance: fetching {ccxt_sym} M15 since {since_dt.date()}")

        while True:
            bars = exchange.fetch_ohlcv(ccxt_sym, "15m", since=current, limit=1000)
            if not bars:
                break
            all_bars.extend(bars)
            batch += 1
            if batch % 5 == 0:
                print(f"        batch {batch}: {len(all_bars)} bars total")
            if len(bars) < 1000:
                break
            current = bars[-1][0] + 1
            time.sleep(exchange.rateLimit / 1000)

        if not all_bars:
            return None

        df = pd.DataFrame(all_bars, columns=["ts","open","high","low","close","volume"])
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df.index.name = "datetime"
        df = df[["open","high","low","close","volume"]].sort_index()
        print(f"      Binance total: {len(df)} bars")
        return df

    except Exception as e:
        print(f"      Binance failed: {str(e)[:60]}")
        return None


# ── Strategy C: TwelveData batches ────────────────────────────────────────
def fetch_td_batches(td_sym: str, days: int = 730) -> pd.DataFrame | None:
    """Fetch M15 from TwelveData in batches using start_date."""
    try:
        from core.twelve_data_client import TwelveDataClient
        key = os.environ.get("TWELVE_DATA_API_KEY", "")
        if not key:
            return None

        client = TwelveDataClient(api_key=key)
        frames = []
        end = datetime.now(timezone.utc)
        start_total = end - timedelta(days=days)
        # 5000 M15 bars = ~52 days
        batch_days = 50
        current_start = start_total
        batch = 0

        total_batches = days // batch_days + 1
        print(f"      TwelveData: {total_batches} batches needed (~{total_batches*1} credits)")

        while current_start < end:
            batch += 1
            try:
                df = client.time_series(
                    td_sym, "15min",
                    outputsize=5000,
                    start_date=current_start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_date=min(current_start + timedelta(days=batch_days), end).strftime("%Y-%m-%d %H:%M:%S"),
                )
                if df is not None and len(df) > 5:
                    frames.append(df)
                    print(f"        batch {batch}: {len(df)} bars")
                else:
                    print(f"        batch {batch}: empty")
            except Exception as e:
                print(f"        batch {batch} error: {str(e)[:40]}")
            current_start += timedelta(days=batch_days)
            time.sleep(8)  # rate limit

        if not frames:
            return None
        result = merge_dfs(frames)
        print(f"      TwelveData total: {len(result)} bars")
        return result

    except Exception as e:
        print(f"      TwelveData failed: {str(e)[:60]}")
        return None


# ── Main ───────────────────────────────────────────────────────────────────
def build_symbol(sym: str, info: dict, days: int) -> int:
    """Build 2-year M15 for one symbol. Returns bar count."""
    out = DATA_DIR / f"{sym}_15m_2y.csv"
    frames = []

    # Load existing
    if out.exists() and out.stat().st_size > 50_000:
        try:
            existing = pd.read_csv(out, index_col=0, parse_dates=True)
            existing.index = pd.to_datetime(existing.index, utc=True)
            existing_days = (existing.index[-1] - existing.index[0]).days
            if existing_days >= days * 0.9:
                print(f"  ⏭  {sym}: already {len(existing)} bars ({existing_days}d)")
                return len(existing)
            frames.append(existing)
            print(f"  Existing: {len(existing)} bars ({existing_days}d) — extending...")
        except Exception:
            pass

    print(f"\n  [{sym}]")

    # Strategy B: Binance (crypto)
    if info.get("ccxt"):
        print(f"    Strategy B: Binance pagination")
        df = fetch_binance_full(info["ccxt"], days)
        if df is not None:
            frames.append(df)

    # Strategy A: Yahoo batches (all)
    if info.get("yf"):
        print(f"    Strategy A: Yahoo batches")
        df = fetch_yahoo_batches(info["yf"], days)
        if df is not None:
            frames.append(df)

    # Strategy C: TwelveData (if Yahoo insufficient)
    current_bars = len(merge_dfs(frames)) if frames else 0
    target_bars = days * 24 * 4  # M15 bars per day = 96
    if current_bars < target_bars * 0.5 and info.get("td"):
        print(f"    Strategy C: TwelveData batches (need more data)")
        df = fetch_td_batches(info["td"], days)
        if df is not None:
            frames.append(df)

    if not frames:
        print(f"  ❌ {sym}: all strategies failed")
        return 0

    # Merge & save
    result = merge_dfs(frames)
    result.to_csv(out)
    coverage = (result.index[-1] - result.index[0]).days
    print(f"  ✅ {sym}: {len(result)} bars | {str(result.index[0])[:10]} → {str(result.index[-1])[:10]} ({coverage}d)")
    return len(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    symbols = {k: v for k, v in SYMBOLS.items()
               if not args.symbols or k in args.symbols}

    print(f"\n{'='*60}")
    print(f"IATIS M15 2-Year Data Builder")
    print(f"{'='*60}")
    print(f"Symbols: {list(symbols.keys())}")
    print(f"Target:  {args.days} days = ~{args.days*96:,} M15 bars per symbol")
    print(f"Sources: Binance(crypto) + Yahoo(batches) + TwelveData")
    print(f"{'='*60}")

    t0 = time.monotonic()
    results = {}

    for sym, info in symbols.items():
        if args.force:
            out = DATA_DIR / f"{sym}_15m_2y.csv"
            out.unlink(missing_ok=True)
        bars = build_symbol(sym, info, args.days)
        results[sym] = bars

    duration = time.monotonic() - t0
    print(f"\n{'='*60}")
    print(f"DONE in {duration/60:.0f} min")
    print(f"{'='*60}")
    for sym, bars in results.items():
        target = args.days * 96
        pct = bars / target * 100 if target > 0 else 0
        status = "✅" if pct >= 80 else "⚠️" if pct >= 40 else "❌"
        print(f"  {status} {sym}: {bars:>7,} bars ({pct:.0f}% of {target:,} target)")

    print(f"\nNext: python3 scripts/m15_smart_backtest.py --all")


if __name__ == "__main__":
    main()
