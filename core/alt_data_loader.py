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


# ---------------------------------------------------------------------------
# Official macro sources (data-layer redesign follow-up):
#   VIX   → CBOE's own daily-history CSV (free, no key — the source of truth)
#   DXY   → FRED DTWEXBGS (Nominal Broad U.S. Dollar Index — the free
#           official dollar-strength series; ICE's DXY itself is licensed).
#           Trend direction is what the Macro engine consumes, and the two
#           track closely for that purpose. Yahoo's DX-Y.NYB stays as the
#           fallback (it IS the ICE DXY, just an unofficial feed).
#   Yields→ FRED DGS10 / DGS2
# FRED needs FRED_API_KEY in .env; without it the keyless fredgraph.csv
# endpoint is tried before falling back to Yahoo.
# ---------------------------------------------------------------------------

CBOE_VIX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

_FRED_SERIES = {
    "DXY":   "DTWEXBGS",   # broad dollar index (proxy — see note above)
    "VIX":   "VIXCLS",
    "US10Y": "DGS10",
    "US02Y": "DGS2",
    "SPY":   "SP500",
    # LBMA Gold Fixing 10:30 AM (London), USD — the free official gold price
    # series, a trusted replacement for the Yahoo GLD ETF proxy (2026-07-17).
    "GLD":   "GOLDAMGBD228NLBM",
}


def _close_only_frame(dates, closes) -> pd.DataFrame:
    """FRED/CBOE-style close series → OHLCV contract (o=h=l=c). The Macro
    engine computes EMAs on closes, so synthetic OHLC is honest here."""
    idx = pd.to_datetime(dates, utc=True)
    s = pd.to_numeric(pd.Series(list(closes), index=idx), errors="coerce")
    s = s.dropna().sort_index()
    df = pd.DataFrame({"open": s, "high": s, "low": s, "close": s, "volume": 0.0})
    df.index.name = "datetime"
    return df[~df.index.duplicated(keep="first")]


def load_vix_from_cboe(months: int = 6) -> pd.DataFrame:
    """VIX daily history straight from CBOE (full OHLC, no key)."""
    import requests as req
    resp = req.get(CBOE_VIX_URL, timeout=20)
    resp.raise_for_status()
    df = pd.read_csv(pd.io.common.StringIO(resp.text))
    df.columns = [c.strip().lower() for c in df.columns]
    df["datetime"] = pd.to_datetime(df["date"], utc=True)
    df = df.set_index("datetime")[["open", "high", "low", "close"]].astype(float)
    df["volume"] = 0.0
    df = df.sort_index()
    cutoff = df.index.max() - pd.Timedelta(days=months * 31)
    df = df[df.index >= cutoff]
    logger.info(f"CBOE: {len(df)} VIX bars ({df.index[0].date()} to {df.index[-1].date()})")
    return df


def load_from_fred(series_id: str, months: int = 6) -> pd.DataFrame:
    """Daily FRED series → OHLCV frame. Uses the API when FRED_API_KEY is
    set, else the keyless fredgraph.csv export."""
    import os
    from datetime import date, timedelta

    import requests as req

    start = (date.today() - timedelta(days=months * 31)).isoformat()
    api_key = os.environ.get("FRED_API_KEY", "")
    if api_key:
        resp = req.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": api_key,
                    "file_type": "json", "observation_start": start},
            timeout=20,
        )
        resp.raise_for_status()
        obs = resp.json().get("observations", [])
        df = _close_only_frame([o["date"] for o in obs],
                               [o["value"] for o in obs])
    else:
        resp = req.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": series_id, "cosd": start},
            timeout=20,
        )
        resp.raise_for_status()
        raw = pd.read_csv(pd.io.common.StringIO(resp.text))
        df = _close_only_frame(raw.iloc[:, 0], raw.iloc[:, 1])
    if df.empty:
        raise ValueError(f"FRED returned no usable observations for {series_id}")
    logger.info(f"FRED: {len(df)} bars for {series_id}")
    return df


# Snapshot cache — the engine may be invoked once per symbol per run;
# macro series are daily, so refetching 4-6 tickers x 15 symbols per cycle
# was pure waste. 1h TTL keeps a scheduler run on one fetch set.
_SNAPSHOT_CACHE: dict[str, Any] = {"at": 0.0, "key": None, "data": None}
_SNAPSHOT_TTL_S = 3600


def load_macro_snapshot(symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Load a snapshot of key macro indicators (daily, last 6 months).

    Used by the Macro Engine to compute DXY trend, risk-on/off state,
    and yield curve signals.

    Source order per series — trusted, official sources only (Yahoo removed
    2026-07-17 as an untrusted price feed; every macro series now has a free
    official source, so no Yahoo fallback is needed):
        VIX   : CBOE CSV → FRED VIXCLS
        DXY   : FRED DTWEXBGS (broad-dollar proxy)
        US10Y : FRED DGS10          US02Y: FRED DGS2
        SPY   : FRED SP500          GLD  : FRED GOLDAMGBD228NLBM (LBMA gold)
    """
    import time as _time

    if symbols is None:
        symbols = ["DXY", "US10Y", "US02Y", "VIX", "GLD", "SPY"]

    cache_key = tuple(sorted(symbols))
    if (_SNAPSHOT_CACHE["data"] is not None
            and _SNAPSHOT_CACHE["key"] == cache_key
            and _time.time() - _SNAPSHOT_CACHE["at"] < _SNAPSHOT_TTL_S):
        return _SNAPSHOT_CACHE["data"]

    def _fred(sym: str) -> pd.DataFrame:
        return load_from_fred(_FRED_SERIES[sym])

    _SOURCES: dict[str, list] = {
        "VIX":   [("cboe", lambda s: load_vix_from_cboe()), ("fred", _fred)],
        "DXY":   [("fred", _fred)],
        "US10Y": [("fred", _fred)],
        "US02Y": [("fred", _fred)],
        "SPY":   [("fred", _fred)],
        "GLD":   [("fred", _fred)],
    }

    snapshot = {}
    for sym in symbols:
        for source_name, fetch in _SOURCES.get(sym, []):
            try:
                df = fetch(sym)
                if df is None or df.empty:
                    raise ValueError("empty frame")
                df.attrs["provider"] = source_name
                snapshot[sym] = df
                logger.info(f"Macro snapshot: {sym} via {source_name} ({len(df)} bars)")
                break
            except Exception as exc:
                logger.warning(f"Macro snapshot: {sym} via {source_name} failed: {exc}")
        else:
            logger.warning(f"Macro snapshot: ALL sources failed for {sym}")

    _SNAPSHOT_CACHE.update(at=_time.time(), key=cache_key, data=snapshot)
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
