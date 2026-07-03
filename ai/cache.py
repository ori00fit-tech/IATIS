"""
ai/cache.py
------------
Small in-memory TTL cache so AIAnalyzer doesn't call a paid LLM API on
every tick. Process-local and intentionally simple — this system is a
single scheduler process, and AI results are inherently soft/advisory
(explanation text, not a trading decision), so losing the cache on a
restart is harmless.

Typical TTLs (see config.yaml `ai.cache`):
    news:  15-30 min  — economic news doesn't change faster than that
    macro: ~1 hour     — cross-asset context is slow-moving
    trade explanations are not cached at all — each EXECUTE signal is
    unique, so the caller should key by signal_id/decision_id if it
    wants idempotency, not blanket TTL caching.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    """Thread-safe get-or-compute cache with a fixed TTL per entry."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() >= expires_at:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            self._store[key] = (time.time() + ttl_seconds, value)

    def get_or_compute(self, key: str, ttl_seconds: float, compute_fn: Callable[[], Any]) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = compute_fn()
        self.set(key, value, ttl_seconds)
        return value

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
