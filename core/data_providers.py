"""
core/data_providers.py
-----------------------
Multi-provider data fetching with automatic failover.

Provider priority (Free plan reliability):
  1. Twelve Data    — 800 req/day, M15+H1 native, best quality
  2. Yahoo Finance  — unlimited*, H1+ only (M15 via resample)
  3. Alpha Vantage  — 25 req/day, H1 intraday, FX + some metals

*Yahoo Finance has no official rate limit but throttles heavy usage.

Failover triggers:
  - HTTP 4xx/5xx errors (except 401 = wrong key)
  - Daily limit exceeded (RateLimitExceeded)
  - Timeout (10s per attempt)
  - Empty response / validation failure

Design:
  - Each provider is attempted in order
  - On failure: log warning, try next
  - On total failure: raise DataFetchError with all failure reasons
  - Cache-aware: cached Twelve Data response skips failover entirely
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)


class DataFetchError(Exception):
    """All providers failed to return data."""
    pass


@dataclass
class ProviderResult:
    symbol: str
    provider: str
    success: bool
    df: pd.DataFrame | None = None
    error: str = ""
    latency_ms: int = 0


@dataclass
class FetchAttempt:
    provider: str
    error: str
    latency_ms: int


# ---------------------------------------------------------------------------
# Individual provider fetch functions
# ---------------------------------------------------------------------------

def _fetch_twelve_data(
    symbol: str,
    interval: str,
    outputsize: int,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Primary: Twelve Data REST API."""
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "")
    if not api_key:
        raise DataFetchError("TWELVE_DATA_API_KEY not set")

    from core.twelve_data_client import TwelveDataClient
    client = TwelveDataClient(api_key=api_key)
    return client.time_series(symbol, interval, outputsize=outputsize, use_cache=use_cache)


def _to_yfinance_symbol(symbol: str) -> str:
    """Convert IATIS symbol format to Yahoo Finance ticker.

    EUR/USD → EURUSD=X
    XAU/USD → GC=F
    BTC/USD → BTC-USD
    """
    SPECIAL = {
        "XAU/USD": "GC=F",
        "XAG/USD": "SI=F",
        "WTI/USD": "CL=F",
        "BTC/USD": "BTC-USD",
        "ETH/USD": "ETH-USD",
        "DJI":     "^DJI",
        "NDX":     "^IXIC",
        "SPX":     "^GSPC",
    }
    if symbol in SPECIAL:
        return SPECIAL[symbol]
    # FX pairs: EUR/USD → EURUSD=X
    return symbol.replace("/", "") + "=X"


def _td_interval_to_yf(interval: str) -> tuple[str, str]:
    """Convert IATIS interval to yfinance (interval, period)."""
    # yfinance intraday: 1m, 5m, 15m, 30m, 60m, 90m, 1h
    # yfinance daily: 1d, 5d, 1wk, 1mo, 3mo
    MAP = {
        "M1":  ("1m",  "7d"),
        "M5":  ("5m",  "60d"),
        "M15": ("15m", "60d"),
        "H1":  ("1h",  "730d"),
        "H4":  ("1h",  "730d"),   # H4 via resample from H1
        "D1":  ("1d",  "730d"),
        "15min": ("15m", "60d"),
        "1h":    ("1h",  "730d"),
    }
    return MAP.get(interval, ("1h", "730d"))


def _fetch_yahoo_finance(
    symbol: str,
    interval: str,
    outputsize: int,
) -> pd.DataFrame:
    """Secondary: Yahoo Finance (free, no rate limit, H1+ only)."""
    try:
        import yfinance as yf
    except ImportError:
        raise DataFetchError("yfinance not installed: pip install yfinance")

    yf_symbol = _to_yfinance_symbol(symbol)
    yf_interval, period = _td_interval_to_yf(interval)

    logger.info(f"Yahoo Finance: fetching {symbol} ({yf_symbol}) @ {yf_interval}")
    ticker = yf.Ticker(yf_symbol)
    df_raw = ticker.history(period=period, interval=yf_interval, auto_adjust=True)

    if df_raw.empty:
        raise DataFetchError(f"Yahoo Finance: empty response for {yf_symbol}")

    # Normalize to IATIS standard format
    df = df_raw.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume"
    })[["open", "high", "low", "close", "volume"]].copy()

    # Ensure UTC index
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df.tail(outputsize)
    logger.info(f"Yahoo Finance: {len(df)} bars for {symbol}")
    return df


def _to_av_symbol(symbol: str) -> tuple[str, str]:
    """Convert to Alpha Vantage (from_currency, to_currency) or FX pair."""
    if "/" in symbol:
        parts = symbol.split("/")
        return parts[0], parts[1]
    return symbol[:3], symbol[3:]


def _fetch_alpha_vantage(
    symbol: str,
    interval: str,
    outputsize: int,
) -> pd.DataFrame:
    """Tertiary: Alpha Vantage (25 req/day free, FX only on free tier).

    Note: FX_INTRADAY requires Premium on Alpha Vantage free tier.
    Falls back gracefully if 'Information' key returned (premium required).
    """
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        raise DataFetchError("ALPHA_VANTAGE_API_KEY not set — skipping Alpha Vantage")

    try:
        import requests
    except ImportError:
        raise DataFetchError("requests not installed")

    AV_INTERVAL_MAP = {
        "M15": "15min", "15min": "15min",
        "H1": "60min", "1h": "60min",
        "M5": "5min", "M1": "1min",
    }
    av_interval = AV_INTERVAL_MAP.get(interval, "60min")
    from_sym, to_sym = _to_av_symbol(symbol)

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "FX_INTRADAY",
        "from_symbol": from_sym,
        "to_symbol": to_sym,
        "interval": av_interval,
        "outputsize": "full" if outputsize > 100 else "compact",
        "apikey": api_key,
    }

    logger.info(f"Alpha Vantage: fetching {symbol} @ {av_interval}")
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise DataFetchError(f"Alpha Vantage request failed: {exc}")

    # Handle API-level errors
    if "Information" in data:
        raise DataFetchError(
            f"Alpha Vantage: premium required for intraday FX. "
            f"Info: {data['Information'][:100]}"
        )
    if "Note" in data:
        raise DataFetchError(f"Alpha Vantage rate limited: {data['Note'][:100]}")
    if "Error Message" in data:
        raise DataFetchError(f"Alpha Vantage error: {data['Error Message']}")

    time_series_key = f"Time Series FX ({av_interval})"
    if time_series_key not in data:
        raise DataFetchError(f"Alpha Vantage: unexpected response keys: {list(data.keys())}")

    ts = data[time_series_key]
    records = []
    for ts_str, values in ts.items():
        records.append({
            "datetime": pd.Timestamp(ts_str, tz="UTC"),
            "open":   float(values["1. open"]),
            "high":   float(values["2. high"]),
            "low":    float(values["3. low"]),
            "close":  float(values["4. close"]),
            "volume": 0.0,
        })

    df = pd.DataFrame(records).set_index("datetime").sort_index()
    df = df.tail(outputsize)
    logger.info(f"Alpha Vantage: {len(df)} bars for {symbol}")
    return df


def _fetch_finnhub(
    symbol: str,
    interval: str,
    outputsize: int,
) -> pd.DataFrame:
    """Quaternary: Finnhub (free tier: 60 req/min, FX + crypto + stocks).

    Requires FINNHUB_API_KEY in .env
    Free tier supports: OANDA FX pairs, crypto, US stocks
    """
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        raise DataFetchError("FINNHUB_API_KEY not set — skipping Finnhub")

    try:
        import requests
        import time as _time
    except ImportError:
        raise DataFetchError("requests not installed")

    # Convert to Finnhub format
    FINNHUB_SYMBOL_MAP = {
        "EUR/USD": "OANDA:EUR_USD",
        "GBP/USD": "OANDA:GBP_USD",
        "USD/JPY": "OANDA:USD_JPY",
        "USD/CHF": "OANDA:USD_CHF",
        "AUD/USD": "OANDA:AUD_USD",
        "USD/CAD": "OANDA:USD_CAD",
        "NZD/USD": "OANDA:NZD_USD",
        "EUR/JPY": "OANDA:EUR_JPY",
        "GBP/JPY": "OANDA:GBP_JPY",
        "XAU/USD": "OANDA:XAU_USD",
        "BTC/USD": "BINANCE:BTCUSDT",
        "ETH/USD": "BINANCE:ETHUSDT",
    }
    fh_symbol = FINNHUB_SYMBOL_MAP.get(symbol)
    if not fh_symbol:
        raise DataFetchError(f"Finnhub: no mapping for {symbol}")

    RESOLUTION_MAP = {
        "M1": "1", "M5": "5", "M15": "15",
        "H1": "60", "1h": "60",
        "H4": "240", "D1": "D",
    }
    resolution = RESOLUTION_MAP.get(interval, "60")

    # Calculate time range
    end_ts = int(_time.time())
    hours_back = outputsize  # approximate: outputsize bars ≈ outputsize hours
    start_ts = end_ts - hours_back * 3600 * 2  # 2× buffer

    url = "https://finnhub.io/api/v1/forex/candle"
    params = {
        "symbol": fh_symbol,
        "resolution": resolution,
        "from": start_ts,
        "to": end_ts,
        "token": api_key,
    }

    logger.info(f"Finnhub: fetching {symbol} ({fh_symbol}) @ {resolution}min")
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise DataFetchError(f"Finnhub request failed: {exc}")

    if data.get("s") == "no_data":
        raise DataFetchError(f"Finnhub: no data for {fh_symbol}")
    if data.get("s") != "ok":
        raise DataFetchError(f"Finnhub: status={data.get('s')}, error={data.get('error', '?')}")

    records = []
    for i, ts in enumerate(data.get("t", [])):
        records.append({
            "datetime": pd.Timestamp(ts, unit="s", tz="UTC"),
            "open":   float(data["o"][i]),
            "high":   float(data["h"][i]),
            "low":    float(data["l"][i]),
            "close":  float(data["c"][i]),
            "volume": float(data.get("v", [0] * len(data["t"]))[i]),
        })

    if not records:
        raise DataFetchError(f"Finnhub: empty candles for {fh_symbol}")

    df = pd.DataFrame(records).set_index("datetime").sort_index()
    df = df.tail(outputsize)
    logger.info(f"Finnhub: {len(df)} bars for {symbol}")
    return df


# ---------------------------------------------------------------------------
# Main failover fetch function
# ---------------------------------------------------------------------------

def fetch_with_failover(
    symbol: str,
    interval: str,
    outputsize: int = 500,
    use_cache: bool = True,
    providers: list[str] | None = None,
) -> tuple[pd.DataFrame, str]:
    """Fetch OHLCV data with automatic failover across providers.

    Args:
        symbol:     e.g. "EUR/USD", "XAU/USD", "BTC/USD"
        interval:   "M15", "H1", "H4", "D1"
        outputsize: number of bars requested
        use_cache:  use Twelve Data cache if available
        providers:  override provider order (default: twelve_data, yahoo, alpha_vantage)

    Returns:
        (DataFrame, provider_name) — which provider actually delivered the data

    Raises:
        DataFetchError: all providers failed
    """
    if providers is None:
        providers = ["twelve_data", "yahoo_finance", "alpha_vantage", "finnhub"]

    attempts: list[FetchAttempt] = []

    for provider in providers:
        t0 = time.monotonic()
        try:
            if provider == "twelve_data":
                df = _fetch_twelve_data(symbol, interval, outputsize, use_cache)
            elif provider == "yahoo_finance":
                df = _fetch_yahoo_finance(symbol, interval, outputsize)
            elif provider == "alpha_vantage":
                df = _fetch_alpha_vantage(symbol, interval, outputsize)
            elif provider == "finnhub":
                df = _fetch_finnhub(symbol, interval, outputsize)
            else:
                raise DataFetchError(f"Unknown provider: {provider}")

            latency = int((time.monotonic() - t0) * 1000)

            if df is None or df.empty:
                raise DataFetchError(f"{provider}: returned empty DataFrame")

            logger.info(f"Data fetched via {provider}: {len(df)} bars for {symbol} ({latency}ms)")
            return df, provider

        except Exception as exc:
            latency = int((time.monotonic() - t0) * 1000)
            err_msg = str(exc)[:200]
            logger.warning(
                f"Provider '{provider}' failed for {symbol} @ {interval}: "
                f"{type(exc).__name__}: {err_msg} ({latency}ms) — trying next"
            )
            attempts.append(FetchAttempt(
                provider=provider,
                error=f"{type(exc).__name__}: {err_msg}",
                latency_ms=latency,
            ))

    # All providers failed
    failure_summary = " | ".join(
        f"{a.provider}: {a.error[:60]}" for a in attempts
    )
    raise DataFetchError(
        f"All providers failed for {symbol} @ {interval}. "
        f"Attempts: {failure_summary}"
    )


def fetch_multi_timeframe_with_failover(
    symbol: str,
    timeframes: list[str],
    outputsize: int = 500,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fetch multiple timeframes with failover, resampling higher TFs.

    Returns dict {timeframe: DataFrame}
    """
    from core.timeframe_sync import resample

    FREE_PLAN_NATIVE = {"M1", "M5", "M15", "H1"}

    views: dict[str, pd.DataFrame] = {}
    best_base_df: pd.DataFrame | None = None
    best_base_label: str | None = None
    _TF_ORDER = {"D1": 0, "H4": 1, "H1": 2, "M15": 3, "M5": 4, "M1": 5}

    for tf in timeframes:
        if tf in FREE_PLAN_NATIVE:
            df, provider = fetch_with_failover(
                symbol, tf, outputsize=outputsize, use_cache=use_cache
            )
            views[tf] = df
            current_order = _TF_ORDER.get(tf, 99)
            best_order = _TF_ORDER.get(best_base_label, 99) if best_base_label else 99
            if best_base_df is None or current_order < best_order:
                best_base_df = df
                best_base_label = tf

    # Resample higher timeframes
    if best_base_df is not None:
        for tf in timeframes:
            if tf not in views:
                try:
                    views[tf] = resample(best_base_df, tf)
                    logger.info(f"Resampled {symbol} @ {tf} from {best_base_label}")
                except Exception as exc:
                    logger.warning(f"Could not resample {tf}: {exc}")

    if not views:
        raise DataFetchError(
            f"No timeframes could be fetched for {symbol} from any provider."
        )

    return views
