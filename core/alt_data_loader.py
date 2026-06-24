"""
core/alt_data_loader.py
-------------------------
Alternative data sources: Yahoo Finance and Alpha Vantage.

Yahoo Finance (yfinance):
    Best for: long historical data for research (1-10 years, daily/weekly)
    Reliable for: stocks, indices (SPY, QQQ, GLD), crypto (BTC-USD)
    Less reliable for: FX intraday (use Twelve Data for that)
    Cost: free, no API key needed
    Limit: no official rate limit, but don't hammer it

Alpha Vantage:
    Best for: FX intraday backup when Twelve Data credits run low
    Free tier: 25 req/day (very limited)
    Cost: free tier or paid plans
    Key: set ALPHA_VANTAGE_KEY in .env

Symbol mapping (Yahoo Finance format):
    EURUSD  → EURUSD=X
    XAUUSD  → GC=F  (Gold futures, best available)
    XAGUSD  → SI=F  (Silver futures)
    USOIL   → CL=F  (Crude oil futures)
    US30    → ^DJI
    NAS100  → NQ=F  (Nasdaq 100 futures)
    SPX500  → ^GSPC
    BTCUSD  → BTC-USD
    ETHUSD  → ETH-USD
    DXY     → DX-Y.NYB  (Dollar Index)
    US10Y   → ^TNX   (10Y Treasury Yield)
    SP500   → ^GSPC
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

# Yahoo Finance symbol mapping
_YF_SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "USDCHF": "USDCHF=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "AUDJPY": "AUDJPY=X",
    "EURGBP": "EURGBP=X",
    "EURCHF": "EURCHF=X",
    "XAUUSD": "GC=F",        # Gold futures (more reliable than XAUUSD=X)
    "XAGUSD": "SI=F",        # Silver futures
    "USOIL":  "CL=F",        # WTI Crude futures
    "US30":   "^DJI",
    "NAS100": "NQ=F",
    "SPX500": "^GSPC",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    # Macro tickers
    "DXY":    "DX-Y.NYB",
    "US10Y":  "^TNX",
    "US02Y":  "^IRX",
    "VIX":    "^VIX",
    "GLD":    "GLD",         # Gold ETF (liquid, reliable)
    "SPY":    "SPY",
}

_YF_INTERVAL_MAP = {
    "M5":  "5m",   # max 60 days
    "M15": "15m",  # max 60 days
    "H1":  "1h",   # max 730 days
    "D1":  "1d",   # unlimited
    "W1":  "1wk",
    "MN":  "1mo",
}

_YF_PERIOD_MAP = {
    "1mo": "1mo",
    "3mo": "3mo",
    "6mo": "6mo",
    "1y":  "1y",
    "2y":  "2y",
    "5y":  "5y",
    "10y": "10y",
    "max": "max",
}


def load_from_yfinance(
    symbol: str,
    interval: str = "D1",
    period: str = "2y",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Load OHLCV data from Yahoo Finance.

    Args:
        symbol: internal symbol (e.g. EURUSD) or Yahoo symbol (e.g. GC=F)
        interval: internal label (D1, H1, M15) or Yahoo label (1d, 1h, 15m)
        period: lookback period (1mo, 3mo, 1y, 2y, 5y, max)
        start: start date string YYYY-MM-DD (overrides period if set)
        end: end date string YYYY-MM-DD

    Returns:
        Standard OHLCV DataFrame (same contract as load_synthetic).

    Notes:
        - FX intraday (M15, H1) limited to ~60-730 days
        - Daily data goes back years for most symbols
        - Volume is unreliable for FX (tick volume proxy)
        - Gold/Silver futures have reliable volume
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance not installed. Run: pip install yfinance")

    # Resolve Yahoo symbol
    yf_symbol = _YF_SYMBOLS.get(symbol.upper(), symbol)
    yf_interval = _YF_INTERVAL_MAP.get(interval, interval)
    yf_period = _YF_PERIOD_MAP.get(period, period)

    logger.info(
        f"Yahoo Finance: fetching {symbol} ({yf_symbol}) @ {interval} "
        f"period={period if not start else f'{start} to {end or today()}'}"
    )

    try:
        ticker = yf.Ticker(yf_symbol)
        if start:
            raw = ticker.history(start=start, end=end, interval=yf_interval)
        else:
            raw = ticker.history(period=yf_period, interval=yf_interval)
    except Exception as exc:
        raise ValueError(f"Yahoo Finance fetch failed for {yf_symbol}: {exc}") from exc

    if raw.empty:
        raise ValueError(
            f"Yahoo Finance returned no data for {yf_symbol} @ {yf_interval}. "
            f"Check symbol name and interval limits."
        )

    # Normalize to OHLCV contract
    col_map = {
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    }
    df = raw.rename(columns=col_map)[["open", "high", "low", "close", "volume"]].copy()

    # Ensure UTC
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df.index.name = "datetime"
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = df.dropna(subset=["open", "high", "low", "close"])

    logger.info(
        f"Yahoo Finance: {len(df)} bars for {symbol} "
        f"({df.index[0].date()} to {df.index[-1].date()})"
    )
    return df


def today() -> str:
    from datetime import date
    return date.today().isoformat()


def load_macro_snapshot(symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Load a snapshot of key macro indicators (daily, last 6 months).

    Used by the Macro Engine to compute DXY trend, risk-on/off state,
    and yield curve signals.

    Default symbols: DXY, US10Y, US02Y, VIX, GLD (Gold ETF), SPY
    """
    if symbols is None:
        symbols = ["DXY", "US10Y", "US02Y", "VIX", "GLD", "SPY"]

    snapshot = {}
    for sym in symbols:
        try:
            df = load_from_yfinance(sym, interval="D1", period="6mo")
            snapshot[sym] = df
            logger.info(f"Macro snapshot: {sym} loaded ({len(df)} bars)")
        except Exception as exc:
            logger.warning(f"Macro snapshot: failed to load {sym}: {exc}")

    return snapshot


def load_historical_for_research(
    symbol: str,
    interval: str = "D1",
    years: int = 5,
) -> pd.DataFrame:
    """Load long historical data for hypothesis testing and backtesting.

    This is the primary function for research/ experiments that need
    more than the 3-month dataset we have from Twelve Data.

    Example:
        df = load_historical_for_research("XAUUSD", interval="D1", years=10)
        # 10 years of daily Gold data → 2500+ bars for proper backtesting
    """
    period = f"{min(years, 10)}y"
    return load_from_yfinance(symbol, interval=interval, period=period)


def load_from_alpha_vantage(
    symbol: str,
    interval: str = "H1",
    api_key: str = "",
) -> pd.DataFrame:
    """Load intraday FX data from Alpha Vantage (25 req/day on Free).

    Use as fallback when Twelve Data credits are exhausted.
    Alpha Vantage FX_INTRADAY endpoint supports: 1min, 5min, 15min, 30min, 60min

    Args:
        symbol: internal symbol e.g. EURUSD
        interval: H1 → 60min, M15 → 15min
        api_key: Alpha Vantage API key (ALPHA_VANTAGE_KEY in .env)
    """
    import os
    import requests as req

    api_key = api_key or os.environ.get("ALPHA_VANTAGE_KEY", "")
    if not api_key:
        raise ValueError(
            "Alpha Vantage API key not set. "
            "Add ALPHA_VANTAGE_KEY to .env or pass api_key parameter."
        )

    _AV_INTERVALS = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "60min"}
    av_interval = _AV_INTERVALS.get(interval, "60min")

    # FX symbols: EURUSD → from_symbol=EUR, to_symbol=USD
    if len(symbol) == 6 and symbol.isalpha():
        from_sym = symbol[:3]
        to_sym = symbol[3:]
    else:
        raise ValueError(f"Cannot parse FX symbol: {symbol}. Expected 6-char like EURUSD.")

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": from_sym,
        "to_symbol": to_sym,
        "interval": av_interval,
        "outputsize": "full",
        "apikey": api_key,
    }

    logger.info(f"Alpha Vantage: fetching {symbol} @ {interval}")
    try:
        resp = req.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise ValueError(f"Alpha Vantage request failed: {exc}") from exc

    if "Note" in data:
        raise ValueError(f"Alpha Vantage rate limit: {data['Note']}")
    if "Error Message" in data:
        raise ValueError(f"Alpha Vantage error: {data['Error Message']}")

    key = f"Time Series FX ({av_interval})"
    if key not in data:
        raise ValueError(f"Unexpected Alpha Vantage response structure: {list(data.keys())}")

    rows = []
    for ts_str, vals in data[key].items():
        rows.append({
            "datetime": pd.Timestamp(ts_str, tz="UTC"),
            "open":   float(vals["1. open"]),
            "high":   float(vals["2. high"]),
            "low":    float(vals["3. low"]),
            "close":  float(vals["4. close"]),
            "volume": 0.0,  # AV FX doesn't provide volume
        })

    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    df.index.name = "datetime"
    df = df[~df.index.duplicated(keep="first")]

    logger.info(f"Alpha Vantage: {len(df)} bars for {symbol}")
    return df
