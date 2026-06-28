"""
core/ccxt_provider.py
----------------------
CCXT-based data provider — FREE unlimited historical OHLCV.

Sources:
  Crypto:  Binance / Bybit (free, unlimited, since 2017)
  Forex:   OANDA via ccxt (needs API key)
  Metals:  Binance XAU/USDT (gold) — limited pairs

Coverage per timeframe (Binance, unlimited history):
  1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
  Max per request: 1000 candles
  Rate limit: 1200 req/min (very generous)
"""
from __future__ import annotations
import time
import pandas as pd
from datetime import datetime, timedelta, timezone
from utils.logger import get_logger

logger = get_logger(__name__)

# CCXT symbol mapping
CCXT_SYMBOLS = {
    "BTCUSD":  {"exchange": "binance", "symbol": "BTC/USDT"},
    "ETHUSD":  {"exchange": "binance", "symbol": "ETH/USDT"},
    "XAUUSD":  {"exchange": "binance", "symbol": "PAXG/USDT"},  # Gold proxy
    # Add more as needed
}

TF_MAP = {
    "M1":  "1m", "M5":  "5m", "M15": "15m", "M30": "30m",
    "1m":  "1m", "5m":  "5m", "15m": "15m", "30m": "30m",
    "H1":  "1h", "H4":  "4h", "D1":  "1d",
    "1h":  "1h", "4h":  "4h", "1d":  "1d",
}


def fetch_ccxt(
    internal_symbol: str,
    timeframe: str = "1h",
    days: int = 730,
    exchange_id: str | None = None,
) -> pd.DataFrame | None:
    """
    Fetch historical OHLCV using ccxt.

    Args:
        internal_symbol: e.g. 'BTCUSD', 'ETHUSD'
        timeframe: e.g. '1h', '15m', '4h'
        days: how many days of history
        exchange_id: override exchange (default from CCXT_SYMBOLS)

    Returns:
        DataFrame with columns [open, high, low, close, volume]
    """
    try:
        import ccxt
    except ImportError:
        logger.warning("ccxt not installed: pip install ccxt")
        return None

    mapping = CCXT_SYMBOLS.get(internal_symbol)
    if not mapping:
        logger.debug(f"ccxt: no mapping for {internal_symbol}")
        return None

    ex_id = exchange_id or mapping["exchange"]
    ccxt_sym = mapping["symbol"]
    tf = TF_MAP.get(timeframe, timeframe)

    try:
        exchange = getattr(ccxt, ex_id)({"enableRateLimit": True})
        tf_minutes = {"1m":1,"5m":5,"15m":15,"30m":30,"1h":60,"4h":240,"1d":1440}.get(tf,60)
        bars_needed = int(days * 24 * 60 / tf_minutes)
        batch_size = 1000

        now = datetime.now(timezone.utc)
        since_dt = now - timedelta(days=days)
        since_ms = int(since_dt.timestamp() * 1000)

        all_ohlcv = []
        current_since = since_ms

        logger.info(f"ccxt {ex_id}: fetching {ccxt_sym} {tf} ({days}d = ~{bars_needed} bars)")

        while True:
            batch = exchange.fetch_ohlcv(ccxt_sym, tf, since=current_since, limit=batch_size)
            if not batch:
                break
            all_ohlcv.extend(batch)
            if len(batch) < batch_size:
                break
            current_since = batch[-1][0] + 1
            time.sleep(exchange.rateLimit / 1000)

        if not all_ohlcv:
            return None

        df = pd.DataFrame(all_ohlcv, columns=["timestamp","open","high","low","close","volume"])
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("datetime")[["open","high","low","close","volume"]]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df[df.index >= pd.Timestamp(since_dt)]

        logger.info(f"ccxt {ex_id}: got {len(df)} bars for {ccxt_sym} {tf}")
        return df

    except Exception as e:
        logger.warning(f"ccxt {ex_id} error for {ccxt_sym} {tf}: {e}")
        return None


def fetch_crypto_full_history(
    internal_symbol: str,
    timeframe: str = "1h",
    output_path: str | None = None,
) -> pd.DataFrame | None:
    """
    Fetch maximum available history for crypto (since exchange launched).
    Binance BTC/USDT has data since 2017.

    Saves to CSV if output_path provided.
    """
    df = fetch_ccxt(internal_symbol, timeframe, days=3650)  # 10 years
    if df is None:
        return None

    if output_path:
        from pathlib import Path
        Path(output_path).parent.mkdir(exist_ok=True)
        df.to_csv(output_path)
        logger.info(f"Saved {len(df)} bars to {output_path}")

    return df
