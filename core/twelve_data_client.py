"""
core/twelve_data_client.py
-----------------------------
Twelve Data REST API client — Phase 2.

Design decisions driven by the Free plan constraints (800 req/day):

1. STRICT RATE LIMITING — every request goes through RateLimiter before
   execution. The system tracks daily usage and refuses to make calls
   that would exceed the daily cap. This is enforced in code, not just
   documented in comments.

2. RESPONSE CACHING — responses are cached to storage/td_cache/ with a
   TTL keyed to the requested interval. A cached M1 response is valid
   for 1 minute; H4 for 4 hours. This means the pipeline can be re-run
   within the same interval without burning a request.

3. REQUEST BUDGETING — the client exposes remaining_today() so main.py
   (and future Telegram alerts) can warn when the daily budget is running
   low before making a batch of requests.

4. HONEST FAILURE — if the API returns an error, the client raises a
   typed exception (TwelveDataError) with the full API error message.
   It never returns partial or stale data silently.

Free plan limits (as of 2026):
  - 800 API credits/day
  - 8 requests/minute
  - No WebSocket
  - Endpoints used here: /time_series (1 credit per request)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd
import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://api.twelvedata.com"
CACHE_DIR = Path(__file__).resolve().parent.parent / "storage" / "td_cache"

# Free plan hard limits — never exceed these in any code path.
MAX_REQUESTS_PER_DAY = 800
MAX_REQUESTS_PER_MINUTE = 8

# Cache TTL per interval (seconds). Responses older than this are stale.
_CACHE_TTL: dict[str, int] = {
    "1min":  60,
    "5min":  300,
    "15min": 900,
    "1h":    3600,
    "4h":    14400,
    "1day":  86400,
}

# Twelve Data interval strings that map from our internal labels
INTERVAL_MAP: dict[str, str] = {
    "M1":  "1min",
    "M5":  "5min",
    "M15": "15min",
    "H1":  "1h",
    "H4":  "4h",
    "D1":  "1day",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TwelveDataError(Exception):
    """API returned an error response."""


class RateLimitExceeded(Exception):
    """Request refused because it would exceed the daily or per-minute cap."""


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Thread-safe daily + per-minute request counter persisted to disk so
    limits survive process restarts within the same UTC day.
    """

    _USAGE_FILE = Path(__file__).resolve().parent.parent / "storage" / "td_usage.json"

    def __init__(self) -> None:
        self._lock = Lock()
        self._minute_timestamps: list[float] = []

    def _load(self) -> dict:
        if self._USAGE_FILE.exists():
            try:
                with open(self._USAGE_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {"date": "", "count": 0}

    def _save(self, data: dict) -> None:
        self._USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self._USAGE_FILE, "w") as f:
            json.dump(data, f)

    def check_and_increment(self) -> int:
        """Check limits and increment counter. Returns remaining credits.

        Raises RateLimitExceeded if either daily or per-minute cap is hit.
        """
        with self._lock:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            usage = self._load()

            # reset counter on new UTC day
            if usage["date"] != today:
                usage = {"date": today, "count": 0}

            if usage["count"] >= MAX_REQUESTS_PER_DAY:
                raise RateLimitExceeded(
                    f"Daily limit reached ({MAX_REQUESTS_PER_DAY} requests). "
                    f"Resets at UTC midnight."
                )

            # per-minute sliding window
            now = time.monotonic()
            self._minute_timestamps = [t for t in self._minute_timestamps if now - t < 60]
            if len(self._minute_timestamps) >= MAX_REQUESTS_PER_MINUTE:
                oldest = self._minute_timestamps[0]
                wait = 60 - (now - oldest)
                raise RateLimitExceeded(
                    f"Per-minute limit hit ({MAX_REQUESTS_PER_MINUTE} req/min). "
                    f"Retry in {wait:.1f}s."
                )

            usage["count"] += 1
            self._minute_timestamps.append(now)
            self._save(usage)

            remaining = MAX_REQUESTS_PER_DAY - usage["count"]
            logger.info(f"Twelve Data request #{usage['count']} today ({remaining} remaining)")
            return remaining

    def remaining_today(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = self._load()
        if usage["date"] != today:
            return MAX_REQUESTS_PER_DAY
        return max(0, MAX_REQUESTS_PER_DAY - usage["count"])


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------

def _cache_key(symbol: str, interval: str, outputsize: int) -> str:
    # Replace "/" with "_" so "EUR/USD" doesn't create a subdirectory
    safe_symbol = symbol.replace("/", "_")
    return f"{safe_symbol}_{interval}_{outputsize}"


def _cache_path(key: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{key}.json"


def _load_from_cache(symbol: str, interval: str, outputsize: int) -> dict | None:
    key = _cache_key(symbol, interval, outputsize)
    path = _cache_path(key)
    if not path.exists():
        return None

    try:
        with open(path) as f:
            cached = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    ttl = _CACHE_TTL.get(interval, 60)
    cached_at = datetime.fromisoformat(cached["cached_at"])
    age = (datetime.now(timezone.utc) - cached_at).total_seconds()

    if age > ttl:
        logger.debug(f"Cache stale for {key} (age={age:.0f}s > ttl={ttl}s)")
        return None

    logger.info(f"Cache hit for {key} (age={age:.0f}s)")
    return cached["data"]


def _save_to_cache(symbol: str, interval: str, outputsize: int, data: dict) -> None:
    key = _cache_key(symbol, interval, outputsize)
    path = _cache_path(key)
    payload = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    try:
        with open(path, "w") as f:
            json.dump(payload, f)
        logger.debug(f"Cached response for {key}")
    except OSError as e:
        logger.warning(f"Failed to cache response for {key}: {e}")


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class TwelveDataClient:
    """Thin wrapper around Twelve Data's REST API with rate limiting
    and response caching built in.

    Usage:
        client = TwelveDataClient(api_key="your_key")
        df = client.time_series("EURUSD", "H1", outputsize=500)
        # df is a standard OHLCV DataFrame, same contract as load_synthetic()
    """

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("Twelve Data API key must not be empty")
        self._api_key = api_key
        self._rate_limiter = RateLimiter()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "IATIS/1.0"})

    def remaining_today(self) -> int:
        return self._rate_limiter.remaining_today()

    def time_series(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 500,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch OHLCV time series from Twelve Data.

        Args:
            symbol: e.g. "EUR/USD", "XAU/USD", "BTC/USD"
            interval: internal label ("M1","M15","H1","H4","D1") or
                      Twelve Data label ("1min","15min","1h","4h","1day")
            outputsize: number of bars to fetch (max 5000 on Free plan)
            use_cache: skip the API call if a fresh cached response exists.

        Returns:
            Standard OHLCV DataFrame (same contract as load_synthetic()).

        Raises:
            TwelveDataError: API returned an error.
            RateLimitExceeded: would exceed daily or per-minute cap.
        """
        td_interval = INTERVAL_MAP.get(interval, interval)

        if use_cache:
            cached = _load_from_cache(symbol, td_interval, outputsize)
            if cached:
                return _parse_response(cached)

        # check + increment BEFORE making the network call
        self._rate_limiter.check_and_increment()

        params = {
            "symbol":     symbol,
            "interval":   td_interval,
            "outputsize": outputsize,
            "apikey":     self._api_key,
            "timezone":   "UTC",
            "order":      "ASC",
        }

        try:
            resp = self._session.get(
                f"{BASE_URL}/time_series",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise TwelveDataError(f"Network error fetching {symbol}/{td_interval}: {e}") from e

        if data.get("status") == "error":
            raise TwelveDataError(
                f"Twelve Data API error for {symbol}/{td_interval}: "
                f"{data.get('message', 'unknown error')} "
                f"(code={data.get('code')})"
            )

        _save_to_cache(symbol, td_interval, outputsize, data)
        return _parse_response(data)

    def validate_key(self) -> dict[str, Any]:
        """Quick call to verify the API key is valid and return plan info.
        Costs 1 credit.
        """
        self._rate_limiter.check_and_increment()
        try:
            resp = self._session.get(
                f"{BASE_URL}/api_usage",
                params={"apikey": self._api_key},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise TwelveDataError(f"Could not validate API key: {e}") from e


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(data: dict) -> pd.DataFrame:
    """Convert a Twelve Data time_series JSON response to an OHLCV DataFrame.

    Twelve Data returns newest-first by default; we request ASC order so
    the index is already chronological. Either way, we sort defensively.
    """
    values = data.get("values")
    if not values:
        raise TwelveDataError(
            f"Twelve Data response has no 'values' key. "
            f"Status: {data.get('status')}. Full response: {data}"
        )

    rows = []
    for v in values:
        rows.append({
            "datetime": v["datetime"],
            "open":     float(v["open"]),
            "high":     float(v["high"]),
            "low":      float(v["low"]),
            "close":    float(v["close"]),
            "volume":   float(v.get("volume", 0)),
        })

    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["datetime"], utc=True)
    df.index.name = "datetime"
    df = df.drop(columns=["datetime"])
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]

    return df
