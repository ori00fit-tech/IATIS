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

    # H4 is not a native yfinance interval — the map above fetches 1h.
    # Resample before returning so a direct H4 request never gets 1h bars
    # mislabeled as H4 (pre-audit latent bug; the multi-TF path now avoids
    # asking Yahoo for H4 at all via _NATIVE_TF).
    if interval == "H4":
        from core.timeframe_sync import resample
        df = resample(df, "H4")

    df = df.tail(outputsize)
    logger.info(f"Yahoo Finance: {len(df)} bars for {symbol}")
    return df


def _to_av_symbol(symbol: str) -> tuple[str, str]:
    """Convert to Alpha Vantage (from_currency, to_currency) or FX pair."""
    if "/" in symbol:
        parts = symbol.split("/")
        return parts[0], parts[1]
    return symbol[:3], symbol[3:]


def _is_equity_symbol(symbol: str) -> bool:
    """True only for symbols explicitly registered as stocks/ETFs
    (_STOCKS/_ETF below) — deliberately NOT a generic 'no slash present'
    heuristic. That heuristic was tried first and found WRONG the same
    day via a real regression test: index/metal fetch-names like 'SPX'
    (FETCH_TO_INTERNAL) and 'XAUUSD'/'SPX500' (internal form) are ALSO
    slash-free in some calling conventions, so a bare slash-absence
    check silently misrouted them to the equity endpoint instead of
    correctly refusing them (test_alpaca_provider.py's
    test_non_crypto_symbol_is_refused caught this — Alpaca's equity path
    made a real network attempt for 'XAUUSD'). Used to route the shared
    'alpha_vantage'/'finnhub'/'alpaca' provider-chain entries to the
    correct endpoint without inventing new provider names."""
    internal = _internal_symbol(symbol)
    return internal in _STOCKS or internal in _ETF


def _fetch_alpha_vantage_equity(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Stocks/ETFs via Alpha Vantage's core (non-premium) equity endpoints
    — TIME_SERIES_INTRADAY for intraday intervals, TIME_SERIES_DAILY for
    D1, TIME_SERIES_WEEKLY for W1. Unlike FX_INTRADAY (confirmed premium-
    gated on the free tier — see _fetch_alpha_vantage), these are Alpha
    Vantage's original, documented-free core product; NOT YET verified
    against the real API from this codebase — see
    scripts/probe_equity_data_providers.py."""
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        raise DataFetchError("ALPHA_VANTAGE_API_KEY not set — skipping Alpha Vantage")

    try:
        import requests
    except ImportError:
        raise DataFetchError("requests not installed")

    AV_INTRADAY_MAP = {
        "M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min",
        "H1": "60min", "1h": "60min",
    }
    params: dict[str, str] = {"symbol": symbol, "apikey": api_key,
                              "outputsize": "full" if outputsize > 100 else "compact"}
    if interval in AV_INTRADAY_MAP:
        params["function"] = "TIME_SERIES_INTRADAY"
        params["interval"] = AV_INTRADAY_MAP[interval]
        time_series_key = f"Time Series ({AV_INTRADAY_MAP[interval]})"
    elif interval == "W1":
        params["function"] = "TIME_SERIES_WEEKLY"
        time_series_key = "Weekly Time Series"
    else:  # D1 and anything else defaults to daily
        params["function"] = "TIME_SERIES_DAILY"
        time_series_key = "Time Series (Daily)"

    url = "https://www.alphavantage.co/query"
    logger.info(f"Alpha Vantage (equity): fetching {symbol} @ {params['function']}")
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise DataFetchError(f"Alpha Vantage equity request failed: {exc}")

    if "Information" in data:
        raise DataFetchError(f"Alpha Vantage equity: {data['Information'][:100]}")
    if "Note" in data:
        raise DataFetchError(f"Alpha Vantage equity rate limited: {data['Note'][:100]}")
    if "Error Message" in data:
        raise DataFetchError(f"Alpha Vantage equity error: {data['Error Message']}")
    if time_series_key not in data:
        raise DataFetchError(f"Alpha Vantage equity: unexpected response keys: {list(data.keys())}")

    ts = data[time_series_key]
    records = []
    for ts_str, values in ts.items():
        records.append({
            "datetime": pd.Timestamp(ts_str, tz="UTC"),
            "open":   float(values["1. open"]),
            "high":   float(values["2. high"]),
            "low":    float(values["3. low"]),
            "close":  float(values["4. close"]),
            "volume": float(values.get("5. volume", 0.0)),
        })
    if not records:
        raise DataFetchError(f"Alpha Vantage equity: empty series for {symbol}")

    df = pd.DataFrame(records).set_index("datetime").sort_index()
    df = df.tail(outputsize)
    logger.info(f"Alpha Vantage (equity): {len(df)} bars for {symbol}")
    return df


def _fetch_alpha_vantage(
    symbol: str,
    interval: str,
    outputsize: int,
) -> pd.DataFrame:
    """Tertiary: Alpha Vantage (25 req/day free, FX only on free tier).

    Note: FX_INTRADAY requires Premium on Alpha Vantage free tier.
    Falls back gracefully if 'Information' key returned (premium required).
    Equity/ETF symbols (no '/') route to _fetch_alpha_vantage_equity().
    """
    if _is_equity_symbol(symbol):
        return _fetch_alpha_vantage_equity(symbol, interval, outputsize)

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


def _fetch_finnhub_equity(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Stocks/ETFs via Finnhub's /stock/candle endpoint — a genuinely
    DIFFERENT endpoint and symbol format from the FX/crypto path below
    (plain ticker, e.g. 'AAPL', no OANDA:/BINANCE: prefix).

    CONFIRMED BROKEN on the free tier (2026-07-24, real VPS probe, all
    4 starter symbols): HTTP 403 Forbidden. This module's own
    'Free tier supports... US stocks' docstring claim predates this
    endpoint ever being called and was wrong for stock CANDLES
    specifically — Finnhub has, at various points, gated historical
    stock candles behind a paid plan while keeping forex/crypto candles
    free, and that gate is confirmed active now. Left in the chain (last
    position, config.yaml) as a harmless no-op fallback — it fails fast
    and cleanly to the next provider; not worth removing the code path
    since the free-tier status could change again and re-probing is
    cheap. Do not add a paid Finnhub plan on the strength of this
    module's stale claim alone."""
    api_key = os.environ.get("FINNHUB_API_KEY", "")
    if not api_key:
        raise DataFetchError("FINNHUB_API_KEY not set — skipping Finnhub")

    try:
        import requests
        import time as _time
    except ImportError:
        raise DataFetchError("requests not installed")

    RESOLUTION_MAP = {
        "M1": "1", "M5": "5", "M15": "15", "M30": "30",
        "H1": "60", "1h": "60", "D1": "D", "W1": "W",
    }
    resolution = RESOLUTION_MAP.get(interval, "D")

    end_ts = int(_time.time())
    # Daily/weekly bars need a much wider lookback window than hourly ones
    # to reach `outputsize` bars — approximate generously, Finnhub simply
    # returns what exists in range.
    seconds_per_bar = {"1": 60, "5": 300, "15": 900, "30": 1800,
                       "60": 3600, "D": 86400, "W": 604800}.get(resolution, 86400)
    start_ts = end_ts - outputsize * seconds_per_bar * 2  # 2x buffer

    url = "https://finnhub.io/api/v1/stock/candle"
    params = {"symbol": symbol, "resolution": resolution,
              "from": start_ts, "to": end_ts, "token": api_key}

    logger.info(f"Finnhub (equity): fetching {symbol} @ {resolution}")
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise DataFetchError(f"Finnhub equity request failed: {exc}")

    if data.get("s") == "no_data":
        raise DataFetchError(f"Finnhub equity: no data for {symbol}")
    if data.get("s") != "ok":
        raise DataFetchError(f"Finnhub equity: status={data.get('s')}, error={data.get('error', '?')}")

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
        raise DataFetchError(f"Finnhub equity: empty candles for {symbol}")

    df = pd.DataFrame(records).set_index("datetime").sort_index()
    df = df.tail(outputsize)
    logger.info(f"Finnhub (equity): {len(df)} bars for {symbol}")
    return df


def _fetch_finnhub(
    symbol: str,
    interval: str,
    outputsize: int,
) -> pd.DataFrame:
    """Quaternary: Finnhub (free tier: 60 req/min, FX + crypto + stocks).

    Requires FINNHUB_API_KEY in .env
    Free tier supports: OANDA FX pairs, crypto, US stocks
    Equity/ETF symbols (no '/') route to _fetch_finnhub_equity(), a
    genuinely different endpoint — see that function's docstring for
    why the stock-candle path needs its own free-tier verification.
    """
    if _is_equity_symbol(symbol):
        return _fetch_finnhub_equity(symbol, interval, outputsize)

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


_FCS_INDEX_SYMBOL_MAP = {
    # fetch-symbol (main._YF_ONLY values) -> FCS stock/history symbol code
    "DJI": "DJ:DJI",
    "NDX": "NASDAQ:NDX",
    "SPX": "SP:SPX",
}

_FCS_PERIOD_MAP = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d",
}


def _fetch_fcs_api(
    symbol: str,
    interval: str,
    outputsize: int,
) -> pd.DataFrame:
    """FX/metals via FCS API's forex/history, indices via its stock/history.

    Requires FCS_API_KEY in .env. Free tier caps at 300 candles/request
    (docs: "1 credit count for each 300 candles returned") — length is
    clamped rather than silently truncated server-side without our knowledge.
    """
    api_key = os.environ.get("FCS_API_KEY", "")
    if not api_key:
        raise DataFetchError("FCS_API_KEY not set — skipping FCS API")

    try:
        import requests
    except ImportError:
        raise DataFetchError("requests not installed")

    period = _FCS_PERIOD_MAP.get(interval)
    if period is None:
        raise DataFetchError(f"FCS API: unsupported interval {interval}")

    if symbol in _FCS_INDEX_SYMBOL_MAP:
        url = "https://api-v4.fcsapi.com/stock/history"
        fcs_symbol = _FCS_INDEX_SYMBOL_MAP[symbol]
    else:
        url = "https://api-v4.fcsapi.com/forex/history"
        fcs_symbol = symbol.replace("/", "")

    params = {
        "symbol": fcs_symbol,
        "period": period,
        "length": min(outputsize, 300),
        "access_key": api_key,
    }

    logger.info(f"FCS API: fetching {symbol} ({fcs_symbol}) @ {period}")
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise DataFetchError(f"FCS API request failed: {exc}")

    if not data.get("status"):
        raise DataFetchError(f"FCS API error: {str(data.get('msg', data))[:100]}")

    candles = data.get("response")
    if not candles:
        raise DataFetchError(f"FCS API: empty response for {fcs_symbol}")

    records = []
    for c in candles.values():
        records.append({
            "datetime": pd.Timestamp(int(c["t"]), unit="s", tz="UTC"),
            "open":   float(c["o"]),
            "high":   float(c["h"]),
            "low":    float(c["l"]),
            "close":  float(c["c"]),
            "volume": float(c.get("v", 0) or 0),
        })

    df = pd.DataFrame(records).set_index("datetime").sort_index()
    df = df.tail(outputsize)
    logger.info(f"FCS API: {len(df)} bars for {symbol}")
    return df


# ---------------------------------------------------------------------------
# Main failover fetch function
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Asset-class provider chains (philosophy-audit data-layer proposal)
#
# One-size-fits-all failover caused the July starvation incident: crypto has
# better free sources than Twelve Data (Binance via ccxt — native H4/D1,
# unlimited), and the broker's own bars (cTrader) are what live executions
# actually trade against (the NZDUSD broker-vs-TD discrepancy documented in
# STRATEGY_EVIDENCE was a data-source artifact). Chains are per asset class,
# overridable from config.yaml data.provider_chains.
# ---------------------------------------------------------------------------

# Timeframes each provider serves NATIVELY (everything else is resampled
# from the best fetched base). twelve_data reflects the Free plan (H4/D1
# return 403); yahoo H4 is intentionally absent — its "H4" is 1h data.
_NATIVE_TF: dict[str, set] = {
    "ctrader":       {"M1", "M5", "M15", "M30", "H1", "H4", "D1"},
    "ccxt":          {"M1", "M5", "M15", "M30", "H1", "H4", "D1"},
    "twelve_data":   {"M1", "M5", "M15", "H1"},
    "yahoo_finance": {"H1", "D1"},
    "alpha_vantage": {"M1", "M5", "M15", "M30", "H1"},
    "finnhub":       {"M1", "M5", "M15", "M30", "H1", "H4", "D1"},
    "fcs_api":       {"M1", "M5", "M15", "M30", "H1", "H4", "D1"},
    "alpaca":        {"M1", "M5", "M15", "M30", "H1", "H4", "D1"},
    # MT5's standard TIMEFRAME_* constants cover all of these natively,
    # same as cTrader (see the MT5 section above _fetch_mt5).
    "mt5":           {"M1", "M5", "M15", "M30", "H1", "H4", "D1"},
    # dukas-api's documented timeFrame enum has no 30-minute or 4-hour
    # candle — see _DUKASCOPY_JFOREX_TF above _fetch_dukascopy_jforex.
    "dukascopy_jforex": {"M1", "M5", "M15", "H1", "D1"},
}

# FCS API added 2026-07-14 (fx/metals/indices only — no crypto endpoint used
# here): placed right after twelve_data (fx/metals) or right after ctrader
# where there is no twelve_data entry (indices), per operator request.
#
# alpaca added 2026-07-16 (operator request): crypto ONLY — Alpaca serves
# US stocks + crypto, no FX/metals/index CFDs. Placed right after ccxt as
# the first fallback AND as the independent cross-check partner for
# core/data_confidence.py's top-2 comparison on the carrier pairs
# (ccxt/Binance vs Alpaca are genuinely different venues).
#
# yahoo_finance REMOVED from every price chain (2026-07-16, operator
# request; demoted to last on 2026-07-14 as a first step). Measured
# grounds, not opinion (scripts/verify_data_integrity.py run 2026-07-16):
# its index symbols are the WRONG instruments (^IXIC composite ≠ NDX,
# cash sessions = 28% bar coverage), its metals are futures with a basis
# to spot (GC=F ≠ XAU/USD), its "H4" is a 1h resample, and it throttles
# with no rate-limit contract. A missing bar is recoverable; a plausible
# but wrong bar silently poisons decisions. The fetcher stays in the
# codebase for deliberate offline use (cross_provider_diff, downloads) —
# it is simply never consulted for live decisions anymore.
# "mt5" deliberately absent from every entry below (2026-07-27): the
# Wine-hosted bridge (see the MT5 section above _fetch_mt5) is an
# unofficial, unsupported setup, not something to default every live
# FX/metals decision onto sight-unseen. Opt in per asset class via
# config.yaml's data.provider_chains override once docs/
# MT5_BRIDGE_SETUP.md's verification steps pass on the real VPS.
# "dukascopy_jforex" deliberately absent too (2026-08-08) — same reasoning,
# stronger caveat: the bridge (see the Dukascopy JForex section above
# _fetch_dukascopy_jforex) is unaudited, third-party code, not something to
# default any live decision onto. Opt in per asset class via config.yaml's
# data.provider_chains override once docs/DUKASCOPY_JFOREX_BRIDGE_SETUP.md's
# verification steps pass on the real VPS.
DEFAULT_CHAINS: dict[str, list[str]] = {
    "crypto":  ["ccxt", "alpaca", "twelve_data", "finnhub"],
    "metals":  ["ctrader", "twelve_data", "fcs_api", "finnhub"],
    "energy":  ["ctrader", "finnhub"],
    "indices": ["ctrader", "fcs_api", "finnhub"],
    "fx":      ["ctrader", "twelve_data", "fcs_api", "alpha_vantage", "finnhub"],
    # 2026-07-24, LIVE-VERIFIED — see config.yaml's stocks/etf note
    # (alpaca/twelve_data/alpha_vantage confirmed working, finnhub
    # confirmed broken on the free tier, left in as a harmless fallback).
    "stocks":  ["alpaca", "twelve_data", "alpha_vantage", "finnhub"],
    "etf":     ["alpaca", "twelve_data", "alpha_vantage", "finnhub"],
}

_CRYPTO = {"BTCUSD", "ETHUSD"}
_METALS = {"XAUUSD", "XAGUSD"}
_ENERGY = {"USOIL"}
_INDICES = {"US30", "NAS100", "SPX500"}
# Starter universe only (2026-07-24) — extend as real symbols are added to
# config/symbols.yaml. Every equity/ETF ticker MUST be listed in one of
# these two sets: symbol_class() has no generic equity-detection fallback
# (unlike _is_equity_symbol()'s '/' heuristic used at the provider-fetch
# layer) specifically so an unregistered new ticker fails loudly here
# (falls through to "fx", which then 404s/mismatches obviously) rather
# than silently misrouting to a provider chain that can't serve it.
_STOCKS = {"AAPL", "NVDA"}
_ETF = {"SPY", "QQQ"}

# Fetch-symbol → internal name for the non-trivial cases (main._YF_ONLY
# uses these fetch names for the Yahoo-only symbols).
_FETCH_TO_INTERNAL = {"WTI/USD": "USOIL", "DJI": "US30", "NDX": "NAS100", "SPX": "SPX500"}


def _internal_symbol(symbol: str) -> str:
    return _FETCH_TO_INTERNAL.get(symbol, symbol.replace("/", ""))


def symbol_class(symbol: str) -> str:
    internal = _internal_symbol(symbol)
    if internal in _CRYPTO:
        return "crypto"
    if internal in _METALS:
        return "metals"
    if internal in _ENERGY:
        return "energy"
    if internal in _INDICES:
        return "indices"
    if internal in _STOCKS:
        return "stocks"
    if internal in _ETF:
        return "etf"
    return "fx"


def provider_chain_for(symbol: str, overrides: dict | None = None) -> list[str]:
    """Provider order for this symbol's asset class. `overrides` is the
    config.yaml data.provider_chains mapping (class → list), if any."""
    cls = symbol_class(symbol)
    chain = (overrides or {}).get(cls) or DEFAULT_CHAINS[cls]
    return list(chain)


def _fetch_ccxt_provider(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Crypto via ccxt (Binance) — free, unlimited, NATIVE H4/D1."""
    from core.ccxt_provider import fetch_ccxt
    internal = _internal_symbol(symbol)
    tf_minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30,
                  "H1": 60, "H4": 240, "D1": 1440}.get(interval, 60)
    days = max(2, int(outputsize * tf_minutes / (24 * 60)) + 3)
    df = fetch_ccxt(internal, timeframe=interval, days=days)
    if df is None or df.empty:
        raise DataFetchError(f"ccxt: no data for {internal} @ {interval}")
    return df.tail(outputsize)


_ALPACA_TF_MAP = {
    "M1": "1Min", "M5": "5Min", "M15": "15Min", "M30": "30Min",
    "H1": "1Hour", "H4": "4Hour", "D1": "1Day",
}


_ALPACA_TF_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800,
    "H1": 3600, "H4": 14400, "D1": 86400,
}


def _fetch_alpaca_equity(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Stocks/ETFs via Alpaca's stock bars endpoint (/v2/stocks/{symbol}/bars)
    — a genuinely different endpoint from the crypto path in _fetch_alpaca
    below (plain ticker, e.g. 'AAPL', no '/' — and a different host path,
    /v2/stocks/ vs /v1beta3/crypto/us/). Same auth (ALPACA_API_KEY/
    ALPACA_API_SECRET), same response bar shape (o/h/l/c/v/t), same
    _ALPACA_TF_MAP. Added 2026-07-24 (operator request) — the
    'Alpaca serves US stocks and crypto only' comment on _fetch_alpaca
    predates this function; only the crypto half was ever wired in
    before.

    TWO real bugs found and fixed across two rounds of VPS probing
    (2026-07-24), neither guessable from docs alone:
    (1) Sends an EXPLICIT start/end window rather than relying on
        `limit`+`sort=desc` alone — omitting start/end returned only a
        single (today's) bar instead of real history.
    (2) `end` defaulting to "now" 403'd once start/end were added — the
        free plan's SIP feed forbids querying the last 15 minutes of
        data, confirmed via Alpaca's own documented FAQ. Fixed by
        requesting `feed=iex` explicitly (the feed the free plan
        actually has rights to) AND pulling `end` back 20 minutes (a
        5-minute margin past the documented 15) so the request never
        touches the restricted window regardless of feed selection.
    Re-verify with scripts/probe_equity_data_providers.py after any
    change here."""
    api_key = os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("ALPACA_API_SECRET", "")
    if not (api_key and api_secret):
        raise DataFetchError("ALPACA_API_KEY/ALPACA_API_SECRET not set — skipping Alpaca")

    tf = _ALPACA_TF_MAP.get(interval)
    if tf is None:
        raise DataFetchError(f"Alpaca: unsupported interval {interval}")

    try:
        import requests
    except ImportError:
        raise DataFetchError("requests not installed")

    from datetime import datetime, timedelta, timezone
    seconds_per_bar = _ALPACA_TF_SECONDS.get(interval, 86400)
    # Confirmed by a real VPS probe (2026-07-24, second round): the
    # start/end fix above still 403'd. Root cause per Alpaca's own docs
    # (not guessed — see the explicit search that led here): the free
    # plan's SIP feed forbids querying the last 15 minutes of data, and
    # `end` defaulting to "now" always fell inside that window. Two-part
    # fix: (1) `feed=iex` explicitly requests the feed the free plan
    # actually has rights to, rather than relying on default feed
    # selection; (2) `end` is pulled back 20 minutes (5-minute safety
    # margin past the documented 15) so the request never touches the
    # restricted window regardless of feed.
    end_dt = datetime.now(timezone.utc) - timedelta(minutes=20)
    start_dt = end_dt - timedelta(seconds=outputsize * seconds_per_bar * 2)  # 2x buffer

    base = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
    params = {
        "timeframe": tf,
        "limit": min(int(outputsize), 10000),
        "sort": "desc",
        "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "feed": "iex",
    }
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}

    logger.info(f"Alpaca (equity): fetching {symbol} @ {tf} ({params['start']} to {params['end']})")
    try:
        resp = requests.get(f"{base}/v2/stocks/{symbol}/bars",
                            params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise DataFetchError(f"Alpaca equity request failed: {exc}")

    bars = data.get("bars") or []
    if not bars:
        raise DataFetchError(f"Alpaca equity: empty response for {symbol}")

    records = [{
        "datetime": pd.Timestamp(b["t"]),
        "open": float(b["o"]), "high": float(b["h"]),
        "low": float(b["l"]), "close": float(b["c"]),
        "volume": float(b.get("v", 0.0)),
    } for b in bars]
    df = pd.DataFrame(records).set_index("datetime").sort_index()
    return df.tail(outputsize)


def _fetch_alpaca(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Crypto bars via Alpaca Market Data (v1beta3) — NATIVE H1/H4/D1.

    Role in this codebase (2026-07-16, operator request): a second,
    INDEPENDENT crypto source alongside ccxt/Binance — both a failover
    and the cross-check partner core/data_confidence.py needs for the
    carrier pairs. Equity/ETF symbols (no '/') route to
    _fetch_alpaca_equity(), a genuinely different endpoint added
    2026-07-24 — this function stays crypto-only.

    Auth: ALPACA_API_KEY / ALPACA_API_SECRET in .env. Data host is
    https://data.alpaca.markets (ALPACA_DATA_URL to override) — NOT the
    paper-api.alpaca.markets trading host, which serves orders, not bars.
    """
    if _is_equity_symbol(symbol):
        return _fetch_alpaca_equity(symbol, interval, outputsize)

    api_key = os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("ALPACA_API_SECRET", "")
    if not (api_key and api_secret):
        raise DataFetchError("ALPACA_API_KEY/ALPACA_API_SECRET not set — skipping Alpaca")

    internal = _internal_symbol(symbol)
    if internal not in _CRYPTO:
        raise DataFetchError(f"Alpaca serves crypto only in this codebase (got {internal})")

    tf = _ALPACA_TF_MAP.get(interval)
    if tf is None:
        raise DataFetchError(f"Alpaca: unsupported interval {interval}")

    try:
        import requests
    except ImportError:
        raise DataFetchError("requests not installed")

    base = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
    alpaca_symbol = f"{internal[:-3]}/{internal[-3:]}"  # BTCUSD → BTC/USD
    params = {
        "symbols": alpaca_symbol,
        "timeframe": tf,
        "limit": min(int(outputsize), 10000),
        "sort": "desc",  # newest first + limit = the latest N bars
    }
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}

    logger.info(f"Alpaca: fetching {alpaca_symbol} @ {tf}")
    try:
        resp = requests.get(f"{base}/v1beta3/crypto/us/bars",
                            params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise DataFetchError(f"Alpaca request failed: {exc}")

    bars = (data.get("bars") or {}).get(alpaca_symbol) or []
    if not bars:
        raise DataFetchError(f"Alpaca: empty response for {alpaca_symbol}")

    records = [{
        "datetime": pd.Timestamp(b["t"]),
        "open": float(b["o"]),
        "high": float(b["h"]),
        "low": float(b["l"]),
        "close": float(b["c"]),
        "volume": float(b.get("v", 0.0)),
    } for b in bars]
    df = pd.DataFrame(records).set_index("datetime").sort_index()
    return df.tail(outputsize)


_ctrader_feed_client = None
_ctrader_feed_lock = None


def get_shared_ctrader_client():
    """Return the one process-wide cTrader session (lazy-connect, shared).

    Both this module's data fetching AND execution.trade_executor's order
    placement MUST go through this single client. cTrader's Open API
    allows only ONE authenticated session per account+app — two
    independently-connecting CTraderClient instances in the same process
    fight over that slot forever, each kicking the other's session out
    with ALREADY_LOGGED_IN and endlessly reconnecting (diagnosed 2026-07-14
    from live scheduler logs: trade_executor.py used to open a second,
    separate client on every EXECUTE-verdict symbol).

    Raises DataFetchError when credentials are absent or the connect
    attempt fails, so callers fall through cleanly (tests, CI, local dev,
    and TradeExecutor's own dry_run/no-broker paths never reach this).
    """
    if not (os.getenv("CTRADER_CLIENT_ID") and os.getenv("CTRADER_ACCESS_TOKEN")):
        raise DataFetchError("cTrader feed not configured (no credentials in env)")

    global _ctrader_feed_client, _ctrader_feed_lock
    import threading
    if _ctrader_feed_lock is None:
        _ctrader_feed_lock = threading.Lock()
    with _ctrader_feed_lock:
        if _ctrader_feed_client is None:
            from execution.ctrader_client import CTraderClient
            client = CTraderClient()
            if not client.connect(timeout=30):
                raise DataFetchError("cTrader feed: connect failed")
            _ctrader_feed_client = client
    return _ctrader_feed_client


def _fetch_ctrader(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Broker bars via the shared cTrader client (see get_shared_ctrader_client)."""
    client = get_shared_ctrader_client()
    internal = _internal_symbol(symbol)
    bars = client.get_trendbars(internal, period=interval, count=outputsize)
    if not bars:
        raise DataFetchError(f"cTrader feed: no trendbars for {internal} @ {interval}")
    df = pd.DataFrame(bars)
    # BUG FIX (2026-07-27): execution/ctrader_client.py's _on_trendbars_res
    # stores "timestamp" as utcTimestampInMinutes*60 — i.e. SECONDS since
    # epoch (confirmed by every other consumer of get_trendbars(): scripts/
    # download_ctrader_fx_history.py:152 and scripts/backtest_ic_symbols.py
    # both treat it as seconds; tests/test_ctrader_message_handlers.py's own
    # assertions match that scale). Parsing it here with unit="ms" divided
    # every cTrader-sourced bar's real elapsed time by 1000, collapsing a
    # 2026 date to ~20 days after the Unix epoch (Jan 1970) and compressing
    # real 1-hour bar spacing to ~3.6 seconds — silently corrupting every
    # cTrader-fetched DataFrame's index (H4/D1 resampling, gap detection,
    # last_bar_time provenance) while leaving the OHLC values untouched.
    # Root-caused live via the Cross-Provider Data Confidence panel
    # (core/data_confidence.py): every ctrader-vs-twelve_data check on FX
    # symbols returned NO_OVERLAP (0 common bars) — impossible unless one
    # side's timestamps were off by orders of magnitude, which this was.
    df.index = pd.to_datetime(df.pop("timestamp"), unit="s", utc=True)
    return df[["open", "high", "low", "close", "volume"]].tail(outputsize)


# ---------------------------------------------------------------------------
# MT5 (2026-07-27, operator request) — Wine-hosted bridge, opt-in only.
#
# MetaTrader5's official Python package is Windows-only (a thin wrapper over
# a DLL that talks to a real, running MT5 terminal on the SAME machine) —
# unlike cTrader's real cross-platform network protocol, there is no
# official way to reach MT5 from this Linux process. The operator's chosen
# setup: a Wine-hosted Windows Python + MT5 terminal on this same VPS,
# fronted by a small localhost-only HTTP bridge (scripts/mt5_bridge/
# mt5_bridge_server.py) that is the only thing that ever imports
# MetaTrader5. This function talks to that bridge exactly like every other
# pull-based provider here talks to a remote REST API — MT5's own Python
# API has no push/streaming surface even on native Windows, so there is no
# persistent-session machinery to build (unlike execution/ctrader_client.py).
#
# Deliberately NOT added to DEFAULT_CHAINS below — see that dict's own
# comment. Opt in per asset class via config.yaml's data.provider_chains
# override once docs/MT5_BRIDGE_SETUP.md's verification steps pass on the
# real VPS.
# ---------------------------------------------------------------------------

def _mt5_bridge_url() -> str:
    url = os.getenv("MT5_BRIDGE_URL")
    if not url:
        raise DataFetchError("MT5 bridge not configured (MT5_BRIDGE_URL unset)")
    return url.rstrip("/")


def _fetch_mt5(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Bars via the Wine-hosted MT5 bridge (scripts/mt5_bridge/mt5_bridge_server.py).

    Unofficial/unsupported path — see this section's header comment. Any
    bridge failure (unreachable, timeout, terminal disconnected, HTTP
    error) raises DataFetchError so the provider chain falls through
    cleanly, exactly like a missing cTrader credential does today.
    """
    import requests
    internal = _internal_symbol(symbol)
    try:
        resp = requests.get(
            f"{_mt5_bridge_url()}/rates",
            params={"symbol": internal, "timeframe": interval, "count": outputsize},
            headers={"X-Bridge-Token": os.getenv("MT5_BRIDGE_TOKEN", "")},
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()["rates"]
    except DataFetchError:
        raise
    except Exception as exc:
        raise DataFetchError(f"MT5 bridge: {exc}") from exc
    if not rows:
        raise DataFetchError(f"MT5 bridge: no bars for {internal} @ {interval}")
    df = pd.DataFrame(rows)
    # Bridge returns "time" in SECONDS since epoch (mt5_bridge_server.py's
    # own contract) — deliberately not repeating the ms/s unit mistake
    # found and fixed in _fetch_ctrader above.
    df.index = pd.to_datetime(df.pop("time"), unit="s", utc=True)
    return df[["open", "high", "low", "close", "volume"]].tail(outputsize)


# ---------------------------------------------------------------------------
# Dukascopy JForex (2026-08-08, operator request) — live/near-live bridge,
# opt-in only, DATA ONLY (no order execution — see this section's tail
# comment).
#
# Dukascopy's official programmatic access is the JForex SDK: a Java library
# (com.dukascopy.api.IClient) that runs headless (no Wine hack needed, unlike
# MT5 — the SDK itself supports a plain JVM on Linux). Writing and
# maintaining a bespoke Java bridge here isn't necessary: a real, existing
# open-source project (ismailfer/dukascopy-api-websocket, aka "dukas-api",
# https://github.com/ismailfer/dukascopy-api-websocket, also published as a
# Docker image) already wraps the JForex SDK as a small local REST+WebSocket
# service — verified directly against its README (not assumed): GET
# /api/v1/history?instID=EURUSD&timeFrame=15MIN&from=... for historical
# candles, plus position open/modify/close endpoints this integration does
# NOT call (see below). This function talks to that bridge exactly like
# _fetch_mt5 above talks to the Wine-hosted MT5 bridge — same "one more pull-
# based HTTP provider" shape, no persistent-session machinery needed here.
#
# UNOFFICIAL, THIRD-PARTY BRIDGE — stronger caveat than MT5's Wine
# fragility: this is unaudited, unaffiliated-with-Dukascopy code the
# operator must review themselves before pointing any real (even demo)
# credentials at it. See docs/DUKASCOPY_JFOREX_BRIDGE_SETUP.md.
#
# Deliberately NOT added to DEFAULT_CHAINS below — see that dict's own
# comment, same opt-in-per-asset-class pattern as MT5.
#
# ORDER EXECUTION DELIBERATELY OUT OF SCOPE for this function/phase, even
# though the bridge's own POST/PUT/DELETE /api/v1/position endpoints could
# support it: wiring a brand-new, third-party, unofficial broker path into
# execution/trade_executor.py is a separate, materially higher-stakes
# decision (real/demo money placed through unaudited code) that needs its
# own dedicated safety design (dry_run gating, config.yaml provider
# selection, extensive testing) — not rushed into a data-provider addition,
# matching the MT5 bridge's own "the request was specifically 'data
# provider'" scope boundary.
# ---------------------------------------------------------------------------

# dukas-api's REAL timeFrame enum, verified 2026-08-07 against the real
# HistDataController.java source on the operator's VPS (the README's
# documented contract was wrong on two other points — see below — so this
# was re-confirmed from source, not re-trusted from docs):
# 1SEC | 10SEC | 1MIN | 5MIN | 10MIN | 15MIN | 1HOUR | DAILY. No native
# 4-hour candle — H4 falls through to resampling from H1 elsewhere in the
# pipeline, same as every other provider without a native H4 (see
# _NATIVE_TF below). M30/10MIN/10SEC/1SEC have no IATIS-internal timeframe
# counterpart and are simply never requested here.
_DUKASCOPY_JFOREX_TF: dict[str, str] = {
    "M1": "1MIN", "M5": "5MIN", "M15": "15MIN", "H1": "1HOUR", "D1": "DAILY",
}

# Bar duration in milliseconds per timeframe — used to compute a real
# `from` timestamp (see _fetch_dukascopy_jforex: the bridge has no
# "give me N bars" mode, only a from/to time range; `from` is a REQUIRED
# param on the real endpoint).
_DUKASCOPY_JFOREX_TF_MS: dict[str, int] = {
    "M1": 60_000, "M5": 5 * 60_000, "M15": 15 * 60_000,
    "H1": 60 * 60_000, "D1": 24 * 60 * 60_000,
}


def _dukascopy_jforex_bridge_url() -> str:
    url = os.getenv("DUKASCOPY_JFOREX_BRIDGE_URL")
    if not url:
        raise DataFetchError("Dukascopy JForex bridge not configured (DUKASCOPY_JFOREX_BRIDGE_URL unset)")
    return url.rstrip("/")


def _dukascopy_jforex_inst_id(internal_symbol: str) -> str:
    """The bridge's /api/v1/history calls Instrument.valueOf(instID)
    directly against the real JForex SDK enum, whose constant names are
    slash-separated ("EUR/USD", not IATIS's internal "EURUSD") — confirmed
    live 2026-08-07 both by the bridge's own subscription log
    ("instruments=[BTC/USD, EUR/USD, ...]") and by a real
    instID=EUR%2FUSD request returning real bars. Every symbol currently
    mapped through this provider is a clean 3+3 split (FX majors/minors,
    metals, and BTC/ETH crosses are all 6 chars), so inserting the slash
    at position 3 recovers the SDK's own format without a lookup table."""
    if len(internal_symbol) != 6:
        raise DataFetchError(
            f"Dukascopy JForex bridge: cannot derive instID for {internal_symbol!r} "
            f"(expected a 6-char XXXYYY symbol)"
        )
    return f"{internal_symbol[:3]}/{internal_symbol[3:]}"


def _fetch_dukascopy_jforex(symbol: str, interval: str, outputsize: int) -> pd.DataFrame:
    """Bars via the locally-run dukas-api bridge (see this section's header
    comment) fronting the official JForex SDK.

    Unofficial/unsupported path — see the header comment above. Any bridge
    failure (unreachable, timeout, unmapped timeframe, HTTP error) raises
    DataFetchError so the provider chain falls through cleanly, exactly
    like a missing MT5/cTrader credential does today.

    Verified live against a real, connected bridge on the operator's VPS
    (2026-08-07) — the dukas-api README's documented contract was wrong on
    three points, all fixed here from the real HistDataController.java
    source rather than re-guessed from docs: (1) there is no `count` param
    at all, only a required `from` (epoch-ms) + optional `to`; (2) instID
    must be slash-separated (see _dukascopy_jforex_inst_id); (3) each
    candle's timestamp field is named "timestamp" (not "time") and is in
    MILLISECONDS (not seconds).
    """
    import time as _time

    import requests

    internal = _internal_symbol(symbol)
    tf = _DUKASCOPY_JFOREX_TF.get(interval)
    if tf is None:
        raise DataFetchError(f"Dukascopy JForex bridge: no native timeframe mapping for {interval!r}")
    inst_id = _dukascopy_jforex_inst_id(internal)
    bar_ms = _DUKASCOPY_JFOREX_TF_MS[interval]
    now_ms = int(_time.time() * 1000)
    # Generously wide window (2.5x the naive span + a 7-day pad) since the
    # bridge has no bar-count mode — weekend/holiday gaps (especially on
    # D1) mean a naive outputsize*bar_ms span would come up short. Trimmed
    # back down to outputsize via .tail() below, same as every other
    # provider here.
    from_ms = now_ms - int(bar_ms * outputsize * 2.5) - 7 * 24 * 60 * 60_000
    try:
        resp = requests.get(
            f"{_dukascopy_jforex_bridge_url()}/api/v1/history",
            params={"instID": inst_id, "timeFrame": tf, "from": from_ms},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
    except DataFetchError:
        raise
    except Exception as exc:
        raise DataFetchError(f"Dukascopy JForex bridge: {exc}") from exc
    if not rows:
        raise DataFetchError(f"Dukascopy JForex bridge: no bars for {internal} @ {interval}")
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("timestamp"), unit="ms", utc=True)
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df[["open", "high", "low", "close", "volume"]].tail(outputsize)


# Forensic Audit follow-up (2026-08-04) — a provider's "latest N bars"
# response can include the CURRENT, still-forming candle for whatever
# timeframe bucket "now" falls into (a well-documented behavior of
# ccxt/Binance's fetchOHLCV in particular, and not verifiably ruled out
# for several other providers either — none of them are documented here
# as trimming it). Its high/low/close keep changing until the bar
# actually closes, which would make every engine's df.iloc[-1] read a
# moving target instead of the closed bar main.py's own decision-report
# comment ("last closed candle of the decision timeframe") assumes.
# Mirrors core/timeframe_sync.py's private _TF_MINUTES (copied, not
# imported — different module, same precedent as quant_engine.py's own
# local copy) extended with M1/M5/M30, since this trim applies to every
# timeframe this module fetches, not just the 4 core/timeframe_sync.py
# resamples between.
_TF_MINUTES_FOR_TRIM: dict[str, int] = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440,
}


def _drop_still_forming_bar(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Drops the last row only when its own bar-duration window hasn't
    fully elapsed yet (bar_open_time + duration > now) — a no-op for the
    common case of a provider that already only returns closed bars, or
    a live fetch that happens to land right after a bar just closed."""
    if df.empty:
        return df
    minutes = _TF_MINUTES_FOR_TRIM.get(interval)
    if minutes is None:
        return df
    last_open = df.index[-1]
    if getattr(last_open, "tzinfo", None) is None:
        last_open = last_open.tz_localize("UTC")
    bar_close = last_open + pd.Timedelta(minutes=minutes)
    if bar_close > pd.Timestamp.now(tz="UTC"):
        return df.iloc[:-1]
    return df


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
        providers:  override provider order (default: twelve_data,
                    alpha_vantage, finnhub — yahoo removed 2026-07-16, see
                    the DEFAULT_CHAINS note; pass it explicitly for
                    deliberate offline comparisons only)

    Returns:
        (DataFrame, provider_name) — which provider actually delivered the data

    Raises:
        DataFetchError: all providers failed
    """
    if providers is None:
        providers = ["twelve_data", "alpha_vantage", "finnhub"]

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
            elif provider == "fcs_api":
                df = _fetch_fcs_api(symbol, interval, outputsize)
            elif provider == "alpaca":
                df = _fetch_alpaca(symbol, interval, outputsize)
            elif provider == "ccxt":
                df = _fetch_ccxt_provider(symbol, interval, outputsize)
            elif provider == "ctrader":
                df = _fetch_ctrader(symbol, interval, outputsize)
            elif provider == "mt5":
                df = _fetch_mt5(symbol, interval, outputsize)
            elif provider == "dukascopy_jforex":
                df = _fetch_dukascopy_jforex(symbol, interval, outputsize)
            else:
                raise DataFetchError(f"Unknown provider: {provider}")

            latency = int((time.monotonic() - t0) * 1000)

            if df is None or df.empty:
                raise DataFetchError(f"{provider}: returned empty DataFrame")

            df = _drop_still_forming_bar(df, interval)
            if df.empty:
                raise DataFetchError(
                    f"{provider}: only bar returned was still forming — no closed bar yet"
                )

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


# Resampling H4 needs ≥ 210 H4 bars (NNFX EMA200 + buffer, main.py's
# starvation invariant) ⇒ ≥ ~840 H1 base bars. Below this the disk
# archive is consulted; above it the fetched window is already enough.
_MIN_RESAMPLE_BASE_BARS = 900


def _deepen_with_history(symbol: str, tf: str | None, df: pd.DataFrame) -> pd.DataFrame:
    """Left-extend a short fetched frame with the local history archive.

    The archive is data/{SYMBOL}_{TF}_deep.csv, written by
    scripts/download_deep_history.py (the tool this deepener was actually
    designed against — its own docstring already measures real per-provider
    depth, e.g. ~6.5y H4 / ~19y D1 on Twelve Data Free). Confirmed BUG
    (2026-08-04): this function previously looked for
    data/{SYMBOL}_{TF}_2y.csv instead — the cache path core/data_manager.py's
    separate, unrelated DataManager class writes, which nothing in the live
    or scheduled pipeline ever populates (only the manual, un-timered
    scripts/download_all_data.py does). The filename never matched what
    download_deep_history.py actually produces, so this deepener silently
    no-op'd on every real deployment — the exact NNFX/MTF "Insufficient
    data" starvation class it was built to fix never got fixed. Merge
    rules: identical lowercase OHLCV schema, indexes coerced to UTC, and
    the FETCHED bars win on duplicate timestamps — the archive only ever
    supplies older bars, so live decisions keep pricing off the live feed.
    Any failure returns the frame unchanged; this is a deepener, never a
    gate.
    """
    if tf is None or len(df) >= _MIN_RESAMPLE_BASE_BARS:
        return df
    try:
        from core.data_manager import DATA_DIR
        path = DATA_DIR / f"{symbol}_{tf}_deep.csv"
        if not path.exists():
            return df
        hist = pd.read_csv(path, index_col=0, parse_dates=True)
        hist.columns = [str(c).lower() for c in hist.columns]
        needed = ["open", "high", "low", "close", "volume"]
        if not set(needed) <= set(hist.columns):
            return df
        hist = hist[needed]
        hist.index = pd.to_datetime(hist.index, utc=True)
        fresh = df.copy()
        fresh.index = pd.to_datetime(fresh.index, utc=True)
        merged = pd.concat([hist, fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        if len(merged) <= len(df):
            return df
        merged.attrs = dict(df.attrs)
        logger.info(
            f"Deepened {symbol} @ {tf} resample base with disk history: "
            f"{len(df)} fetched + archive -> {len(merged)} bars"
        )
        return merged
    except Exception as exc:
        logger.warning(f"History deepening skipped for {symbol} @ {tf}: {exc}")
        return df


def fetch_multi_timeframe_with_failover(
    symbol: str,
    timeframes: list[str],
    outputsize: int = 500,
    use_cache: bool = True,
    providers: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch multiple timeframes with failover, resampling higher TFs.

    Native-timeframe aware (data-layer redesign): each timeframe is first
    requested from the chain providers that serve it NATIVELY (ccxt and
    cTrader serve H4/D1 directly — no resampling, no starvation); only
    when no native provider delivers does it fall back to resampling from
    the best fetched base. The chain defaults to the symbol's asset class
    (provider_chain_for) and can be overridden per class via config.yaml
    data.provider_chains or per call via `providers`.

    Returns dict {timeframe: DataFrame}
    """
    from core.timeframe_sync import resample

    chain = providers or provider_chain_for(symbol)

    views: dict[str, pd.DataFrame] = {}
    best_base_df: pd.DataFrame | None = None
    best_base_label: str | None = None
    _TF_ORDER = {"D1": 0, "H4": 1, "H1": 2, "M30": 3, "M15": 4, "M5": 5, "M1": 6}

    for tf in timeframes:
        native = [p for p in chain if tf in _NATIVE_TF.get(p, set())]
        if not native:
            continue
        try:
            df, provider = fetch_with_failover(
                symbol, tf, outputsize=outputsize, use_cache=use_cache,
                providers=native,
            )
        except DataFetchError as exc:
            logger.warning(f"No native provider delivered {symbol} @ {tf}: {exc}")
            continue
        df.attrs["provider"] = provider          # surfaced in decision reports
        views[tf] = df
        # Base for resampling missing TFs: prefer the coarsest fetched
        # intraday TF (H1 over M15 — coarser base = more accurate resample).
        current_order = _TF_ORDER.get(tf, 99)
        best_order = _TF_ORDER.get(best_base_label, 99) if best_base_label else 99
        if tf != "D1" and (best_base_df is None or current_order < best_order):
            best_base_df = df
            best_base_label = tf

    # Resample whatever is still missing from the best fetched base.
    if best_base_df is not None:
        for tf in timeframes:
            if tf not in views:
                try:
                    views[tf] = resample(best_base_df, tf)
                    views[tf].attrs["provider"] = (
                        f"resampled:{best_base_label}"
                        f"({best_base_df.attrs.get('provider', '?')})"
                    )
                    logger.info(f"Resampled {symbol} @ {tf} from {best_base_label}")
                except Exception as exc:
                    logger.warning(f"Could not resample {tf}: {exc}")

    # Deepen every produced view (native OR resampled) against its OWN
    # target-timeframe archive (data/{SYMBOL}_{TF}_deep.csv, written by
    # scripts/download_deep_history.py). A provider-capped window (FCS's
    # 300-bar cap) or a resample from a thin base (Twelve Data Free 403s
    # on native H4/D1, forcing a resample from a thin H1 fetch) can land
    # under NNFX's 210-H4-bar EMA200 floor or the MTF gate's 50-D1-bar
    # floor — exactly the DATA STARVATION class the audit flags (663
    # NNFX "Insufficient data" decisions). Deliberately applied per FINAL
    # timeframe here, not to the pre-resample base: download_deep_
    # history.py writes already-resampled H4/D1 archives directly (it
    # requests Twelve Data's own 4h/1day series, not H1), never a
    # base-timeframe one, so a base-level archive lookup would never
    # find a real file in practice. The archive only ever supplies older
    # bars; the freshly fetched/resampled bars win on any overlap.
    for tf in list(views.keys()):
        views[tf] = _deepen_with_history(symbol, tf, views[tf])

    if not views:
        raise DataFetchError(
            f"No timeframes could be fetched for {symbol} from any provider."
        )

    return views
