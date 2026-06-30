"""
utils/feature_store.py

Feature Store infrastructure: declarative registry-backed feature store with
thread-local per-run instance, metrics, dependency tracking and implementation
binding.

This file implements:
- FeatureStore: register_descriptor, register_implementation, get_or_compute,
  compute, metrics, dependency checks
- Thread-local helpers: set_current_store/get_current_store/clear_current_store

Note: registry (YAML) is declarative only and does not contain compute_fn.
Implementations must be registered programmatically (e.g. in features/* or in
engines during PR2).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from utils.feature_def import FeatureDescriptor, FeatureKey


_thread_local = threading.local()


@dataclass
class FeatureStats:
    hits: int = 0
    misses: int = 0
    total_compute_time: float = 0.0
    last_computed_at: Optional[float] = None
    engines_using: Set[str] = field(default_factory=set)
    memory_bytes: int = 0


class FeatureStore:
    """In-memory per-run feature store.

    Usage:
      store = FeatureStore()
      store.register_descriptor(descriptor)    # from registry (declarative)
      store.register_implementation("EMA", impl_fn)
      set_current_store(store)
      ... engines call store.get_or_compute(key, mtf_data, engine_name=...)

    The registry is declarative: FeatureDescriptor must not embed compute_fn.
    Implementations are bound by name (descriptor.key.name -> implementation fn)
    by calling register_implementation(name, fn).
    """

    def __init__(self) -> None:
        self._descriptors: Dict[Tuple, FeatureDescriptor] = {}
        self._cache: Dict[Tuple, Any] = {}
        self._stats: Dict[Tuple, FeatureStats] = {}
        self._impls: Dict[str, Callable[[Dict[str, Any]], Any]] = {}

    # Registry operations
    def register_descriptor(self, descriptor: FeatureDescriptor) -> None:
        k = descriptor.key.canonical()
        self._descriptors[k] = descriptor
        if k not in self._stats:
            self._stats[k] = FeatureStats()

    def register_descriptors(self, descriptors: Iterable[FeatureDescriptor]) -> None:
        for d in descriptors:
            self.register_descriptor(d)

    def get_descriptor(self, key: FeatureKey) -> Optional[FeatureDescriptor]:
        return self._descriptors.get(key.canonical())

    # Implementation binding
    def register_implementation(self, name: str, impl_fn: Callable[[Dict[str, Any]], Any]) -> None:
        """Bind a compute function to a feature name (not key).

        impl_fn receives mtf_data dict and params may be closed over or read
        from the FeatureKey params during invocation.
        """
        self._impls[name] = impl_fn

    def has_implementation(self, name: str) -> bool:
        return name in self._impls

    # Compute / cache
    def get_or_compute(self, key: FeatureKey, mtf_data: Dict[str, Any], engine_name: Optional[str] = None) -> Any:
        kc = key.canonical()
        stats = self._stats.setdefault(kc, FeatureStats())
        if kc in self._cache:
            stats.hits += 1
            if engine_name:
                stats.engines_using.add(engine_name)
            return self._cache[kc]
        # Miss
        stats.misses += 1
        # Find descriptor
        desc = self.get_descriptor(key)
        if desc is None:
            raise KeyError(f"Feature not registered: {key}")
        impl = self._impls.get(desc.key.name)
        if impl is None:
            raise KeyError(f"No implementation registered for feature name: {desc.key.name}")
        # Compute dependencies first
        if desc.dependencies:
            for dep in desc.dependencies:
                # recursive compute
                self.get_or_compute(dep, mtf_data, engine_name=engine_name)
        start = time.time()
        val = impl(key, mtf_data)
        elapsed = time.time() - start
        stats.total_compute_time += elapsed
        stats.last_computed_at = time.time()
        # rough memory estimate (if pandas Series/DataFrame)
        try:
            m = int(val.memory_usage(deep=True).sum()) if hasattr(val, "memory_usage") else 0
        except Exception:
            m = 0
        stats.memory_bytes = m
        if engine_name:
            stats.engines_using.add(engine_name)
        self._cache[kc] = val
        return val

    def compute(self, key: FeatureKey, mtf_data: Dict[str, Any]) -> Any:
        # force recompute
        if key.canonical() in self._cache:
            del self._cache[key.canonical()]
        return self.get_or_compute(key, mtf_data)

    def metrics(self) -> Dict[Tuple, Dict[str, Any]]:
        out: Dict[Tuple, Dict[str, Any]] = {}
        for k, s in self._stats.items():
            out[k] = {
                "hits": s.hits,
                "misses": s.misses,
                "total_compute_time": s.total_compute_time,
                "last_computed_at": s.last_computed_at,
                "engines_using": sorted(list(s.engines_using)),
                "memory_bytes": s.memory_bytes,
            }
        return out


# Thread-local context helpers
def set_current_store(store: FeatureStore) -> None:
    _thread_local.feature_store = store


def get_current_store() -> Optional[FeatureStore]:
    return getattr(_thread_local, "feature_store", None)


def clear_current_store() -> None:
    if hasattr(_thread_local, "feature_store"):
        try:
            del _thread_local.feature_store
        except Exception:
            pass
