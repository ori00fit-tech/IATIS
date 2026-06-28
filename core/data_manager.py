"""
core/data_manager.py
---------------------
IATIS Data Provider Manager — Institutional-grade data layer.

Architecture:
    IATIS Engines
         │
    DataManager (unified interface)
         │
    ┌────┴────────────────────────────────┐
    │  Provider Chain (failover)          │
    │  1. Binance/ccxt  (crypto, free)    │
    │  2. Yahoo Finance (all, free)       │
    │  3. Stooq         (forex/stocks)    │
    │  4. Twelve Data   (all, credits)    │
    │  5. Finnhub       (news/sentiment)  │
    └─────────────────────────────────────┘
         │
    Validation + Cleaning
         │
    Timeframe Builder (resample from base)
         │
    Local CSV Cache

Key design:
  - Never depends on single provider
  - Resample internally: 5m→15m,30m | 1h→4h | 1d→1w,1m
  - Symbol registry with metadata
  - Transparent failover logging
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# ── Symbol Registry ────────────────────────────────────────────────────────
SYMBOL_REGISTRY: dict[str, dict[str, Any]] = {
    # Forex
    "EURUSD": {"class":"forex", "pip":0.0001, "session":"24x5", "currency":"USD",
               "td":"EUR/USD",  "yf":"EURUSD=X",  "ccxt":None, "stooq":"eurusd"},
    "GBPUSD": {"class":"forex", "pip":0.0001, "session":"24x5", "currency":"USD",
               "td":"GBP/USD",  "yf":"GBPUSD=X",  "ccxt":None, "stooq":"gbpusd"},
    "AUDUSD": {"class":"forex", "pip":0.0001, "session":"24x5", "currency":"USD",
               "td":"AUD/USD",  "yf":"AUDUSD=X",  "ccxt":None, "stooq":"audusd"},
    "USDCAD": {"class":"forex", "pip":0.0001, "session":"24x5", "currency":"CAD",
               "td":"USD/CAD",  "yf":"USDCAD=X",  "ccxt":None, "stooq":"usdcad"},
    "NZDUSD": {"class":"forex", "pip":0.0001, "session":"24x5", "currency":"USD",
               "td":"NZD/USD",  "yf":"NZDUSD=X",  "ccxt":None, "stooq":"nzdusd"},
    "USDJPY": {"class":"forex", "pip":0.01,   "session":"24x5", "currency":"JPY",
               "td":"USD/JPY",  "yf":"USDJPY=X",  "ccxt":None, "stooq":"usdjpy"},
    "GBPJPY": {"class":"forex", "pip":0.01,   "session":"24x5", "currency":"JPY",
               "td":"GBP/JPY",  "yf":"GBPJPY=X",  "ccxt":None, "stooq":"gbpjpy"},
    "EURJPY": {"class":"forex", "pip":0.01,   "session":"24x5", "currency":"JPY",
               "td":"EUR/JPY",  "yf":"EURJPY=X",  "ccxt":None, "stooq":"eurjpy"},
    # Metals
    "XAUUSD": {"class":"metal", "pip":0.01,   "session":"24x5", "currency":"USD",
               "td":"XAU/USD",  "yf":"GC=F",      "ccxt":"PAXG/USDT", "stooq":None},
    "XAGUSD": {"class":"metal", "pip":0.001,  "session":"24x5", "currency":"USD",
               "td":"XAG/USD",  "yf":"SI=F",       "ccxt":None, "stooq":None},
    # Crypto
    "BTCUSD": {"class":"crypto","pip":1.0,    "session":"24x7", "currency":"USD",
               "td":"BTC/USD",  "yf":"BTC-USD",   "ccxt":"BTC/USDT", "stooq":None,
               "ccxt_exchange":"binance"},
    "ETHUSD": {"class":"crypto","pip":0.01,   "session":"24x7", "currency":"USD",
               "td":"ETH/USD",  "yf":"ETH-USD",   "ccxt":"ETH/USDT", "stooq":None,
               "ccxt_exchange":"binance"},
    # Indices
    "NAS100": {"class":"index", "pip":0.1,    "session":"us_market", "currency":"USD",
               "td":"IXIC",     "yf":"^IXIC",     "ccxt":None, "stooq":"^NDX"},
    "SPX500": {"class":"index", "pip":0.1,    "session":"us_market", "currency":"USD",
               "td":"SPX",      "yf":"^GSPC",     "ccxt":None, "stooq":"^SPX"},
    "US30":   {"class":"index", "pip":1.0,    "session":"us_market", "currency":"USD",
               "td":"DJIA",     "yf":"^DJI",      "ccxt":None, "stooq":"^DJI"},
}

# ── Base timeframes to fetch (others are resampled) ────────────────────────
BASE_TIMEFRAMES = {
    "5m":  {"td":"5min",  "yf":"5m",  "minutes":5},
    "1h":  {"td":"1h",    "yf":"1h",  "minutes":60},
    "1d":  {"td":"1day",  "yf":"1d",  "minutes":1440},
}

# ── Resample map: target → (source, periods) ──────────────────────────────
RESAMPLE_FROM = {
    "15m": ("5m",  3),
    "30m": ("5m",  6),
    "4h":  ("1h",  4),
    "1w":  ("1d",  7),
}


# ── Provider implementations ───────────────────────────────────────────────

class BinanceProvider:
    """Free unlimited crypto history from Binance via ccxt."""
    name = "Binance"

    def fetch(self, symbol: str, timeframe: str, days: int) -> pd.DataFrame | None:
        info = SYMBOL_REGISTRY.get(symbol, {})
        ccxt_sym = info.get("ccxt")
        if not ccxt_sym:
            return None
        try:
            import ccxt
            exchange_id = info.get("ccxt_exchange", "binance")
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            tf_map = {"5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"4h","1d":"1d"}
            tf = tf_map.get(timeframe, "1h")
            tf_min = {"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}.get(tf,60)
            since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
            all_bars = []
            current = since_ms
            while True:
                batch = exchange.fetch_ohlcv(ccxt_sym, tf, since=current, limit=1000)
                if not batch: break
                all_bars.extend(batch)
                if len(batch) < 1000: break
                current = batch[-1][0] + 1
                time.sleep(exchange.rateLimit / 1000)
            if not all_bars:
                return None
            df = pd.DataFrame(all_bars, columns=["ts","open","high","low","close","volume"])
            df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
            df.index.name = "datetime"
            return df[["open","high","low","close","volume"]].sort_index()
        except Exception as e:
            logger.debug(f"Binance {symbol} {timeframe}: {e}")
            return None


class YahooProvider:
    """Yahoo Finance — free, forex/metals/indices, 1h=730d."""
    name = "Yahoo"

    def fetch(self, symbol: str, timeframe: str, days: int) -> pd.DataFrame | None:
        info = SYMBOL_REGISTRY.get(symbol, {})
        yf_sym = info.get("yf")
        if not yf_sym:
            return None
        try:
            import yfinance as yf
            tf_map = {"5m":"5m","15m":"15m","30m":"30m","1h":"1h","4h":"1h","1d":"1d"}
            yf_tf = tf_map.get(timeframe, "1h")
            period = "60d" if yf_tf in ("5m","15m","30m") else "730d"
            df = yf.Ticker(yf_sym).history(period=period, interval=yf_tf, auto_adjust=True)
            if df is None or len(df) < 5:
                return None
            df = df.rename(columns={"Open":"open","High":"high","Low":"low",
                                     "Close":"close","Volume":"volume"})
            df.index = pd.to_datetime(df.index, utc=True)
            df.index.name = "datetime"
            return df[["open","high","low","close","volume"]].dropna().sort_index()
        except Exception as e:
            logger.debug(f"Yahoo {symbol} {timeframe}: {e}")
            return None


class StooqProvider:
    """Stooq — free historical data for forex pairs."""
    name = "Stooq"

    def fetch(self, symbol: str, timeframe: str, days: int) -> pd.DataFrame | None:
        info = SYMBOL_REGISTRY.get(symbol, {})
        stooq_sym = info.get("stooq")
        if not stooq_sym or timeframe not in ("1d", "1h"):
            return None
        try:
            import pandas_datareader as pdr
            end = datetime.now()
            start = end - timedelta(days=days)
            df = pdr.get_data_stooq(stooq_sym.upper(), start=start, end=end)
            if df is None or len(df) < 5:
                return None
            df.columns = [c.lower() for c in df.columns]
            df.index = pd.to_datetime(df.index, utc=True)
            df.index.name = "datetime"
            df["volume"] = df.get("volume", 0)
            return df[["open","high","low","close","volume"]].sort_index()
        except Exception as e:
            logger.debug(f"Stooq {symbol} {timeframe}: {e}")
            return None


class TwelveDataProvider:
    """Twelve Data — best quality, uses API credits."""
    name = "TwelveData"

    def fetch(self, symbol: str, timeframe: str, days: int) -> pd.DataFrame | None:
        info = SYMBOL_REGISTRY.get(symbol, {})
        td_sym = info.get("td")
        if not td_sym:
            return None
        try:
            from core.twelve_data_client import TwelveDataClient
            key = os.environ.get("TWELVE_DATA_API_KEY", "")
            if not key:
                return None
            tf_map = {"5m":"5min","15m":"15min","30m":"30min","1h":"1h","4h":"4h","1d":"1day"}
            client = TwelveDataClient(api_key=key)
            df = client.time_series(td_sym, tf_map.get(timeframe, "1h"), outputsize=5000)
            return df if (df is not None and len(df) > 5) else None
        except Exception as e:
            logger.debug(f"TwelveData {symbol} {timeframe}: {e}")
            return None


# ── Timeframe resampling ───────────────────────────────────────────────────

def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV to higher timeframe."""
    resampled = df.resample(rule).agg({
        "open":  "first",
        "high":  "max",
        "low":   "min",
        "close": "last",
        "volume":"sum",
    }).dropna(subset=["open","close"])
    return resampled


RESAMPLE_RULES = {
    "15m": "15min", "30m": "30min",
    "4h": "4h",     "1w": "1W-MON",
}


# ── DataManager ────────────────────────────────────────────────────────────

class DataManager:
    """
    Unified data access layer with automatic failover and caching.

    Usage:
        dm = DataManager()
        df = dm.get("XAUUSD", "15m", days=365)
        mtf = dm.get_mtf("BTCUSD", ["5m","15m","1h","4h","1d"], days=730)
    """

    def __init__(self):
        self.providers = [
            BinanceProvider(),
            YahooProvider(),
            StooqProvider(),
            TwelveDataProvider(),
        ]
        self._load_env()

    def _load_env(self):
        for p in [Path(".env"), Path("/root/IATIS/.env")]:
            if p.exists():
                for line in p.read_text().splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        return DATA_DIR / f"{symbol}_{timeframe}_2y.csv"

    def _load_cache(self, path: Path) -> pd.DataFrame | None:
        try:
            if path.exists() and path.stat().st_size > 5000:
                df = pd.read_csv(path, index_col=0, parse_dates=True)
                df.index = pd.to_datetime(df.index, utc=True)
                return df.sort_index()
        except Exception:
            pass
        return None

    def _save_cache(self, df: pd.DataFrame, path: Path):
        try:
            df.to_csv(path)
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    def _merge(self, a: pd.DataFrame | None, b: pd.DataFrame | None) -> pd.DataFrame | None:
        if a is None: return b
        if b is None: return a
        combined = pd.concat([a, b])
        return combined[~combined.index.duplicated(keep="last")].sort_index()

    def get(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 730,
        force: bool = False,
    ) -> pd.DataFrame | None:
        """
        Get OHLCV data for symbol+timeframe.
        Uses cache first, then fetches from providers with failover.
        If timeframe can be resampled from a base TF, does so automatically.
        """
        if symbol not in SYMBOL_REGISTRY:
            logger.warning(f"DataManager: unknown symbol {symbol}")
            return None

        cache_path = self._cache_path(symbol, timeframe)
        cached = self._load_cache(cache_path)

        if not force and cached is not None:
            coverage = (cached.index[-1] - cached.index[0]).days
            if coverage >= days * 0.85:
                return cached

        # Check if can resample from base
        if timeframe in RESAMPLE_FROM:
            source_tf, _ = RESAMPLE_FROM[timeframe]
            source_df = self.get(symbol, source_tf, days, force)
            if source_df is not None:
                rule = RESAMPLE_RULES.get(timeframe, timeframe)
                df = resample_ohlcv(source_df, rule)
                merged = self._merge(cached, df)
                if merged is not None:
                    self._save_cache(merged, cache_path)
                return merged

        # Fetch from providers with failover
        result = cached
        for provider in self.providers:
            try:
                logger.info(f"DataManager: {provider.name} → {symbol} {timeframe}")
                df = provider.fetch(symbol, timeframe, days)
                if df is not None and len(df) > 10:
                    result = self._merge(result, df)
                    logger.info(f"  ✅ {len(df)} bars from {provider.name}")
                else:
                    logger.debug(f"  ❌ {provider.name}: no data")
            except Exception as e:
                logger.debug(f"  ❌ {provider.name}: {e}")

        if result is not None:
            result = result.tail(days * 24 * 60 // BASE_TIMEFRAMES.get(timeframe, {}).get("minutes", 60) + 100)
            self._save_cache(result, cache_path)

        return result

    def get_mtf(
        self,
        symbol: str,
        timeframes: list[str] | None = None,
        days: int = 730,
    ) -> dict[str, pd.DataFrame]:
        """
        Get multi-timeframe data dict.
        Automatically resamples from base timeframes.
        """
        if timeframes is None:
            timeframes = ["5m", "15m", "1h", "4h", "1d"]

        result = {}
        for tf in timeframes:
            df = self.get(symbol, tf, days)
            if df is not None and len(df) > 0:
                result[tf] = df
        return result

    def symbol_info(self, symbol: str) -> dict:
        return SYMBOL_REGISTRY.get(symbol, {})

    def available_symbols(self, asset_class: str | None = None) -> list[str]:
        if asset_class:
            return [k for k, v in SYMBOL_REGISTRY.items() if v["class"] == asset_class]
        return list(SYMBOL_REGISTRY.keys())

    def download_all(
        self,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        days: int = 730,
        force: bool = False,
    ) -> dict[str, dict[str, int]]:
        """Download all symbol+timeframe combinations."""
        syms = symbols or list(SYMBOL_REGISTRY.keys())
        tfs  = timeframes or ["5m", "1h", "4h", "1d"]
        results = {}
        for sym in syms:
            results[sym] = {}
            logger.info(f"\n[{sym}]")
            for tf in tfs:
                df = self.get(sym, tf, days, force)
                bars = len(df) if df is not None else 0
                results[sym][tf] = bars
                status = f"✅ {bars} bars" if bars > 0 else "❌ failed"
                logger.info(f"  {tf}: {status}")
        return results
