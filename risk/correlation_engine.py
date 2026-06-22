"""
risk/correlation_engine.py
------------------------------
STUB — Phase 2+.

Computing real correlation requires synchronized price series across
multiple instruments, which Phase 1 (single-symbol synthetic data)
doesn't have. risk_engine.py already accepts a `correlated_exposure_pct`
input so this can be wired in later without changing the risk gate's
interface — for now callers should pass 0.0 (i.e. "unknown, assume no
extra correlated exposure") explicitly rather than this module guessing.

TODO (Phase 2+):
    - compute_correlation_matrix(price_data: dict[symbol, DataFrame]) -> DataFrame
    - correlated_exposure(symbol, portfolio, correlation_matrix, threshold) -> float
"""

from __future__ import annotations


def compute_correlation_matrix(*args, **kwargs):
    raise NotImplementedError("Correlation engine requires multi-symbol data — planned for Phase 2+.")
