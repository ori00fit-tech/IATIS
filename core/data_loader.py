"""
core/data_loader.py
--------------------
Phase 1: synthetic OHLCV generator only. This exists purely so the rest of
the pipeline (regime detector, engines, confluence, risk) has something
real to run against while we build the architecture.

Phase 2+: implement load_from_csv() and load_from_twelve_data() following
the exact same return contract as load_synthetic(), so nothing downstream
needs to change when we swap data sources.

Return contract (all loaders must honor this):
    pandas.DataFrame indexed by UTC datetime, columns:
    ["open", "high", "low", "close", "volume"]
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)


def load_synthetic(
    bars: int = 500,
    start_price: float = 1.0850,
    timeframe: str = "H1",
    seed: int | None = None,
) -> pd.DataFrame:
    """Generate a synthetic but structurally plausible OHLCV series.

    Not a price predictor, not calibrated to any real instrument — only
    meant to exercise the pipeline end to end during Phase 1.
    """
    rng = np.random.default_rng(seed)

    freq_map = {"M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D"}
    freq = freq_map.get(timeframe, "1h")

    timestamps = pd.date_range(end=pd.Timestamp.now("UTC"), periods=bars, freq=freq)

    # random walk with mild drift + volatility clustering, just enough
    # structure for regime/SMC stub logic to have something to chew on
    returns = rng.normal(loc=0.0, scale=0.0008, size=bars)
    vol_regime = np.abs(rng.normal(loc=1.0, scale=0.3, size=bars)).clip(0.3, 2.5)
    returns = returns * vol_regime

    close = start_price * np.exp(np.cumsum(returns))
    open_ = np.roll(close, 1)
    open_[0] = start_price

    # derive high/low from the actual open/close of each bar so high is
    # always >= max(open, close) and low is always <= min(open, close)
    wick_up = np.abs(rng.normal(0, 0.0006, size=bars))
    wick_down = np.abs(rng.normal(0, 0.0006, size=bars))
    bar_max = np.maximum(open_, close)
    bar_min = np.minimum(open_, close)
    high = bar_max * (1 + wick_up)
    low = bar_min * (1 - wick_down)

    volume = rng.integers(100, 5000, size=bars)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=timestamps,
    )
    df.index.name = "datetime"

    logger.info(f"Generated synthetic data: {bars} bars @ {timeframe}")
    return df


def load_from_csv(
    path: str,
    datetime_column: str | None = None,
    column_map: dict[str, str] | None = None,
    has_header: bool = True,
    sep: str | None = None,
    no_header_columns: list[str] | None = None,
) -> pd.DataFrame:
    """Load real historical OHLCV data from a CSV file.

    Designed to be tolerant of common export formats (MT4/MT5, generic
    "Date,Open,High,Low,Close,Volume" exports) without guessing silently:
    if column names can't be confidently matched, this raises rather than
    fabricating a mapping.

    Args:
        path: path to the CSV file.
        datetime_column: name of the datetime column, if not auto-detected.
        column_map: optional explicit override, e.g.
            {"datetime": "Date", "open": "O", "high": "H", "low": "L",
             "close": "C", "volume": "Vol"}
            If provided, this takes precedence over auto-detection.
        has_header: set False for headerless exports (some broker/platform
            exports ship raw rows with no column-name row at all). When
            False, `no_header_columns` determines column order.
        sep: explicit field separator (e.g. "\\t" for tab-separated files).
            If not given, pandas' "python" engine auto-detects between
            comma/tab/semicolon — explicit is safer for ambiguous files.
        no_header_columns: column order to assume when has_header=False.
            Defaults to ["datetime", "open", "high", "low", "close", "volume"],
            the most common broker-export column order.

    Returns:
        DataFrame matching the project-wide OHLCV contract: indexed by
        UTC datetime, columns ["open", "high", "low", "close", "volume"].

    Raises:
        FileNotFoundError: if `path` doesn't exist.
        ValueError: if required columns can't be identified, or the
            resulting data fails validate_ohlcv().
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    if has_header:
        raw = pd.read_csv(csv_path, sep=sep, engine="python" if sep is None else "c")
    else:
        columns = no_header_columns or ["datetime", "open", "high", "low", "close", "volume"]
        raw = pd.read_csv(
            csv_path, sep=sep, engine="python" if sep is None else "c",
            header=None, names=columns,
        )

    if raw.empty:
        raise ValueError(f"CSV file is empty: {path}")

    resolved = column_map or _auto_detect_columns(raw.columns.tolist())
    dt_col = datetime_column or resolved.get("datetime")
    time_col = resolved.get("time")

    required = ["datetime", "open", "high", "low", "close"]
    missing = [k for k in required if resolved.get(k) is None and k != "datetime"]
    if dt_col is None:
        missing.insert(0, "datetime")
    if missing:
        raise ValueError(
            f"Could not identify required columns {missing} in CSV header {raw.columns.tolist()}. "
            f"Pass an explicit column_map to load_from_csv()."
        )

    # MT4/MT5-style exports often split date and time into two separate
    # columns (e.g. "Date","Time"). If both are present, combine them —
    # using the date column alone would silently collapse every bar on
    # the same calendar day into one duplicate timestamp and drop data.
    if time_col and time_col in raw.columns:
        datetime_strings = raw[dt_col].astype(str) + " " + raw[time_col].astype(str)
    else:
        datetime_strings = raw[dt_col]

    df = pd.DataFrame(
        {
            "open": pd.to_numeric(raw[resolved["open"]], errors="coerce"),
            "high": pd.to_numeric(raw[resolved["high"]], errors="coerce"),
            "low": pd.to_numeric(raw[resolved["low"]], errors="coerce"),
            "close": pd.to_numeric(raw[resolved["close"]], errors="coerce"),
            "volume": pd.to_numeric(raw[resolved.get("volume")], errors="coerce")
            if resolved.get("volume") and resolved["volume"] in raw.columns
            else 0,
        }
    )

    df.index = pd.to_datetime(datetime_strings, utc=True, errors="coerce")
    df.index.name = "datetime"

    # Drop rows that failed to parse (datetime or any OHLC value) rather
    # than silently propagating NaT/NaN into validate_ohlcv() with a
    # confusing downstream error.
    before = len(df)
    df = df[df.index.notna()]
    df = df.dropna(subset=["open", "high", "low", "close"])
    dropped = before - len(df)
    if dropped > 0:
        logger.warning(f"Dropped {dropped} unparseable row(s) while loading {path}")

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]

    if df.empty:
        raise ValueError(f"No valid rows remained after parsing CSV: {path}")

    logger.info(f"Loaded real CSV data: {len(df)} bars from {path} "
                f"({df.index.min()} to {df.index.max()})")
    return df


def _auto_detect_columns(columns: list[str]) -> dict[str, str | None]:
    """Best-effort case-insensitive column name matching for common
    OHLCV export formats (generic, MT4/MT5-style). Returns None for any
    field it can't confidently match — callers must treat that as a
    hard failure, not fall back to a guess.
    """
    normalized = {c.lower().strip(): c for c in columns}

    def find(*candidates: str) -> str | None:
        for cand in candidates:
            if cand in normalized:
                return normalized[cand]
        return None

    return {
        "datetime": find("datetime", "date", "date_time", "timestamp"),
        "time": find("time"),
        "open": find("open", "o"),
        "high": find("high", "h"),
        "low": find("low", "l"),
        "close": find("close", "c", "price"),
        "volume": find("volume", "vol", "v", "tick_volume"),
    }


def load_from_twelve_data(
    symbol: str,
    interval: str,
    api_key: str,
    outputsize: int = 500,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch real-time OHLCV data from Twelve Data REST API (single timeframe).

    Phase 2 implementation. Respects Free plan limits (800 req/day,
    8 req/min) via the built-in rate limiter in TwelveDataClient.
    """
    from core.twelve_data_client import TwelveDataClient

    client = TwelveDataClient(api_key=api_key)
    logger.info(
        f"Fetching {symbol} @ {interval} from Twelve Data "
        f"({client.remaining_today()} credits remaining today)"
    )
    return client.time_series(symbol, interval, outputsize=outputsize, use_cache=use_cache)


def load_multi_timeframe_with_failover(
    symbol: str,
    timeframes: list[str],
    outputsize: int = 500,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fetch multiple timeframes with automatic provider failover.

    Provider order: Twelve Data → Yahoo Finance → Alpha Vantage
    Falls back automatically if primary provider fails.
    """
    from core.data_providers import fetch_multi_timeframe_with_failover
    return fetch_multi_timeframe_with_failover(
        symbol=symbol,
        timeframes=timeframes,
        outputsize=outputsize,
        use_cache=use_cache,
    )


def load_multi_timeframe_from_twelve_data(
    symbol: str,
    timeframes: list[str],
    api_key: str,
    outputsize: int = 500,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fetch supported timeframes from Twelve Data and resample the rest.

    Twelve Data Free plan supports: M1, M5, M15, H1 natively.
    H4 and D1 return 403 Forbidden on Free plan, so we:
      1. Fetch the finest available native timeframe (usually H1 or M15)
      2. Resample upward for H4 / D1

    This means H4/D1 bars are derived from H1 data, not independently
    fetched — which is fine for structural analysis but means H4 will
    only cover the same date range as H1 (500 H1 bars ≈ 125 H4 bars).
    When more timeframe depth is needed, increase outputsize or upgrade
    the Twelve Data plan.

    Args:
        symbol: Twelve Data symbol string, e.g. "EUR/USD"
        timeframes: internal labels, e.g. ["M15", "H1", "H4", "D1"]
        api_key: Twelve Data API key
        outputsize: bars per natively-fetched timeframe (default 500)
        use_cache: use cached response if fresh

    Returns:
        dict {timeframe_label: OHLCV DataFrame} — same contract as
        build_multi_timeframe_view().
    """
    from core.twelve_data_client import TwelveDataClient
    from core.timeframe_sync import resample

    # Twelve Data Free plan: only these intervals work reliably
    FREE_PLAN_NATIVE = {"M1", "M5", "M15", "H1"}

    client = TwelveDataClient(api_key=api_key)
    views: dict[str, pd.DataFrame] = {}
    # For resampling H4/D1, use the COARSEST native timeframe (H1 > M15)
    # because resampling from H1 gives more accurate H4/D1 bars.
    # Timeframe order from coarsest to finest:
    _TF_ORDER = {"D1": 0, "H4": 1, "H1": 2, "M15": 3, "M5": 4, "M1": 5}
    best_base_df = None
    best_base_label = None

    for tf in timeframes:
        if tf in FREE_PLAN_NATIVE:
            logger.info(
                f"Fetching {symbol} @ {tf} natively "
                f"({client.remaining_today()} credits remaining)"
            )
            df = client.time_series(symbol, tf, outputsize=outputsize, use_cache=use_cache)
            views[tf] = df
            # prefer the coarsest native TF for resampling (H1 > M15)
            current_order = _TF_ORDER.get(tf, 99)
            best_order = _TF_ORDER.get(best_base_label, 99) if best_base_label else 99
            if best_base_df is None or current_order < best_order:
                best_base_df = df
                best_base_label = tf

    base_df = best_base_df
    base_label = best_base_label

    # resample any higher timeframes that aren't natively available
    if base_df is not None:
        for tf in timeframes:
            if tf not in views:
                logger.info(
                    f"Resampling {symbol} @ {tf} from {base_label} "
                    f"(not available on Free plan natively)"
                )
                try:
                    views[tf] = resample(base_df, tf)
                except Exception as exc:
                    logger.warning(f"Could not resample {tf}: {exc} — skipping")

    if not views:
        raise ValueError(
            f"No timeframes could be fetched for {symbol}. "
            f"Check API key and plan limits."
        )

    return views


def load_data(config: dict) -> pd.DataFrame:
    """Dispatch to the correct loader based on config['data']['source']."""
    source = config.get("data", {}).get("source", "synthetic")
    bars = config.get("data", {}).get("bars_to_load", 500)
    symbol = config.get("data", {}).get("symbol", "EURUSD")

    if source == "synthetic":
        return load_synthetic(bars=bars, timeframe=config["data"]["timeframes"][1])

    elif source == "csv":
        csv_path = config["data"].get("csv_path")
        if not csv_path:
            raise ValueError("config.yaml data.source is 'csv' but data.csv_path is not set")
        return load_from_csv(
            csv_path,
            has_header=config["data"].get("csv_has_header", True),
            sep=config["data"].get("csv_separator"),
            no_header_columns=config["data"].get("csv_columns"),
        )

    elif source == "twelve_data":
        import os
        api_key = (
            config["data"].get("twelve_data_api_key")
            or os.environ.get("TWELVE_DATA_API_KEY", "")
        )
        if not api_key:
            raise ValueError(
                "Twelve Data API key not found. Set TWELVE_DATA_API_KEY in .env "
                "or config.yaml data.twelve_data_api_key"
            )
        # Twelve Data uses slash-separated symbols: EUR/USD not EURUSD
        td_symbol = config["data"].get("twelve_data_symbol") or _to_td_symbol(symbol)
        interval = config["data"]["timeframes"][1]
        return load_from_twelve_data(
            td_symbol,
            interval,
            api_key=api_key,
            outputsize=bars,
        )

    else:
        raise ValueError(f"Unknown data source: {source}")


def _to_td_symbol(symbol: str) -> str:
    """Convert internal symbol format to Twelve Data format.

    EURUSD  -> EUR/USD
    XAUUSD  -> XAU/USD
    BTCUSD  -> BTC/USD
    EUR/USD -> EUR/USD  (already correct, pass through)
    """
    if "/" in symbol:
        return symbol
    if len(symbol) == 6:
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol
