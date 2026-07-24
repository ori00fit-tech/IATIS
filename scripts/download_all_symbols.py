#!/usr/bin/env python3
"""
scripts/download_all_symbols.py
---------------------------------
Download H1 historical data for ALL 20 IATIS symbols via Yahoo Finance.

Includes: FOREX (12) + Metals (2) + Energy (1) + Indices (3) + Crypto (2)

Usage:
    python3 scripts/download_all_symbols.py              # 2yr H1
    python3 scripts/download_all_symbols.py --years 5    # 5yr (max for H1)
    python3 scripts/download_all_symbols.py --force      # re-download existing
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Complete symbol map: internal_name → Yahoo Finance ticker
ALL_SYMBOLS = {
    # FOREX Majors
    "EURUSD":  "EURUSD=X",
    "GBPUSD":  "GBPUSD=X",
    "USDJPY":  "USDJPY=X",
    "USDCHF":  "USDCHF=X",
    "AUDUSD":  "AUDUSD=X",
    "USDCAD":  "USDCAD=X",
    "NZDUSD":  "NZDUSD=X",
    "EURJPY":  "EURJPY=X",
    "GBPJPY":  "GBPJPY=X",
    "AUDJPY":  "AUDJPY=X",
    "EURGBP":  "EURGBP=X",
    "EURCHF":  "EURCHF=X",
    # Metals
    "XAUUSD":  "GC=F",    # Gold Futures
    "XAGUSD":  "SI=F",    # Silver Futures
    # Energy
    "USOIL":   "CL=F",    # Crude Oil (WTI) Futures
    # Indices
    "US30":    "^DJI",    # Dow Jones
    "NAS100":  "^IXIC",   # Nasdaq Composite
    "SPX500":  "^GSPC",   # S&P 500
    # Crypto
    "BTCUSD":  "BTC-USD",
    "ETHUSD":  "ETH-USD",
}

# pip_size for each symbol (for backtest position sizing)
PIP_SIZE = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "USDCAD": 0.0001, "NZDUSD": 0.0001, "EURGBP": 0.0001,
    "EURCHF": 0.0001,
    "USDJPY": 0.01,   "EURJPY": 0.01,   "GBPJPY": 0.01,
    "AUDJPY": 0.01,   "USDCHF": 0.0001,
    "XAUUSD": 0.01,   "XAGUSD": 0.001,
    "USOIL":  0.01,
    "US30":   1.0,    "NAS100": 1.0,    "SPX500": 0.1,
    "BTCUSD": 1.0,    "ETHUSD": 0.01,
    # Equities/ETFs (2026-07-24, starter universe): not load-bearing for
    # calc_pnl (the "equity" branch below uses dollar_per_point only, not
    # PIP_SIZE) — kept here at the smallest quoted increment (1 cent) for
    # documentation consistency with every other symbol in this dict.
    "AAPL": 0.01, "NVDA": 0.01, "SPY": 0.01, "QQQ": 0.01,
}

# Asset class for P&L calculation
ASSET_CLASS = {
    **{s: "forex"  for s in ["EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD",
                              "USDCAD","NZDUSD","EURJPY","GBPJPY","AUDJPY",
                              "EURGBP","EURCHF"]},
    "XAUUSD": "metal",  "XAGUSD": "metal",
    "USOIL":  "metal",
    "US30":   "index",  "NAS100": "index", "SPX500": "index",
    "BTCUSD": "crypto", "ETHUSD": "crypto",
    # 2026-07-24, starter universe. Falls into calc_pnl's generic
    # (non-forex, non-crypto) branch: size = risk_usd/(sl_dist*dpp),
    # pnl = (exit-entry)*direction*size*dpp — with dpp=1.0 this is the
    # standard $1-per-point-per-share equity P&L model.
    "AAPL": "equity", "NVDA": "equity", "SPY": "equity", "QQQ": "equity",
}

# dollar_per_point for non-forex (per 0.01 lot or contract)
DOLLAR_PER_POINT = {
    "XAUUSD": 100.0,   # $1 per point per 0.01 lot = $100/lot
    "XAGUSD": 50.0,
    "USOIL":  100.0,
    "US30":   1.0,
    "NAS100": 1.0,
    "SPX500": 1.0,
    "BTCUSD": 1.0,
    "ETHUSD": 1.0,
    "AAPL": 1.0, "NVDA": 1.0, "SPY": 1.0, "QQQ": 1.0,  # $1/point/share
}


def download_symbol(symbol: str, yf_ticker: str, years: int,
                    data_dir: Path, force: bool) -> tuple[bool, str, int]:
    """Download one symbol. Returns (success, path, bars)."""
    import yfinance as yf
    import pandas as pd

    filename = f"{symbol}_H1_{years}y.csv"
    filepath = data_dir / filename

    if filepath.exists() and not force:
        df = pd.read_csv(filepath)
        return True, str(filepath), len(df)

    period = f"{years}y" if years <= 2 else f"{years}y"
    try:
        ticker = yf.Ticker(yf_ticker)
        df_raw = ticker.history(period=period, interval="1h", auto_adjust=True)

        if df_raw.empty:
            return False, "", 0

        df = df_raw.rename(columns={
            "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume"
        })[["open", "high", "low", "close", "volume"]].copy()

        # Ensure UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df.index.name = "datetime"
        df.to_csv(filepath)
        return True, str(filepath), len(df)

    except Exception as e:
        return False, str(e), 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=2,
                        help="Years of history (max 2 for H1 on Yahoo, default: 2)")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Specific symbols (default: all 20)")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if file exists")
    args = parser.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance not installed. Run: pip install yfinance")

    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    symbols = {k: v for k, v in ALL_SYMBOLS.items()
               if args.symbols is None or k in args.symbols}

    print(f"\n{'='*60}")
    print(f"IATIS Historical Data Download")
    print(f"{'='*60}")
    print(f"Symbols: {len(symbols)} | Interval: H1 | Period: {args.years}yr")
    print(f"Source: Yahoo Finance (free, no API key needed)")
    print()

    results = {"success": [], "failed": [], "skipped": []}

    for symbol, yf_ticker in symbols.items():
        print(f"  ⬇  {symbol:10} ({yf_ticker}) ... ", end="", flush=True)
        success, path_or_err, bars = download_symbol(
            symbol, yf_ticker, args.years, data_dir, args.force
        )
        if success and bars > 0:
            print(f"✅ {bars:,} bars → data/{symbol}_H1_{args.years}y.csv")
            results["success"].append((symbol, bars))
        elif success and bars == 0 and "skipped" in path_or_err.lower():
            print(f"⏭  skipped (already exists)")
            results["skipped"].append(symbol)
        elif success:
            print(f"⏭  already exists ({bars} bars)")
            results["skipped"].append(symbol)
        else:
            print(f"❌ FAILED: {path_or_err[:80]}")
            results["failed"].append((symbol, path_or_err))
        time.sleep(0.5)  # be polite to Yahoo Finance

    print(f"\n{'='*60}")
    print(f"Done: {len(results['success'])} downloaded, "
          f"{len(results['skipped'])} skipped, "
          f"{len(results['failed'])} failed")

    if results["failed"]:
        print("\nFailed symbols:")
        for sym, err in results["failed"]:
            print(f"  {sym}: {err[:80]}")

    print(f"\nFiles in data/:")
    for f in sorted(data_dir.glob("*_H1_*.csv")):
        size_kb = f.stat().st_size // 1024
        print(f"  {f.name} ({size_kb}KB)")


if __name__ == "__main__":
    main()
