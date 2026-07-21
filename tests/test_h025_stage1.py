"""H025 Stage-1 unit tests — the LZ76 measure and the pre-registered
verdict logic are pure functions; these tests pin them to the registry
text so the verdict cannot drift when the runner is touched."""
import math

import numpy as np
import pandas as pd
import pytest

from research.experiments.H025_information_compression import (
    MIN_POOLED_QUINTILE_N,
    compute_symbol_frame,
    lz76_complexity,
    normalized_lz76,
    shannon_entropy_8bin,
    stage1_verdict,
    trailing_percentile,
    zlib_ratio,
)


# ------------------------------------------------------------------ LZ76

def test_lz76_edge_cases():
    assert lz76_complexity("") == 0
    assert lz76_complexity("0") == 1
    assert lz76_complexity("00") == 2
    assert lz76_complexity("0" * 100) == 2  # constant parses as 0 | 000...


def test_lz76_kaspar_schuster_example():
    # canonical example from Kaspar & Schuster (1987): c = 6
    assert lz76_complexity("0001101001000101") == 6


def test_lz76_ordering_constant_lt_periodic_lt_random():
    rng = np.random.default_rng(7)
    constant = np.zeros(64, dtype=bool)
    periodic = np.tile([True, False], 32)
    random = rng.integers(0, 2, 64).astype(bool)
    c_const = normalized_lz76(constant)
    c_per = normalized_lz76(periodic)
    c_rand = normalized_lz76(random)
    assert c_const < c_per < c_rand
    # random binary sequences sit near the n/log2(n) normalization
    assert 0.6 < c_rand < 1.5
    assert c_const < 0.3


def test_normalized_lz76_short_sequences_are_nan():
    assert math.isnan(normalized_lz76(np.array([], dtype=bool)))
    assert math.isnan(normalized_lz76(np.array([True])))


# ------------------------------------------------- secondary measures

def test_entropy_bounds():
    assert shannon_entropy_8bin(np.zeros(64)) == 0.0
    rng = np.random.default_rng(3)
    e = shannon_entropy_8bin(rng.normal(size=64))
    assert 0.0 < e <= 1.0


def test_zlib_ratio_orders_constant_below_random():
    rng = np.random.default_rng(3)
    # constant returns short-circuit to 0; a repeating pattern must
    # compress better than noise
    pattern = np.tile([0.01, -0.01, 0.02, -0.02], 64)
    noise = rng.normal(size=256)
    assert zlib_ratio(pattern) < zlib_ratio(noise)


# ------------------------------------------------- trailing percentile

def test_trailing_percentile_strictly_prior_window():
    v = np.array([1.0, 2.0, 3.0, 4.0, 0.5])
    p = trailing_percentile(v, lookback=3)
    assert np.isnan(p[:3]).all()          # warm-up
    assert p[3] == 1.0                    # 4 above {1,2,3}
    assert p[4] == 0.0                    # 0.5 below {2,3,4}


def test_trailing_percentile_nan_propagates():
    v = np.array([1.0, np.nan, 3.0, 4.0, 5.0])
    p = trailing_percentile(v, lookback=3)
    assert np.isnan(p[3])  # NaN inside the window -> NaN
    assert np.isnan(p[4])


# ------------------------------------------------------ verdict logic

GOOD_RATIOS = {f"S{i}": 1.2 for i in range(10)}


def test_verdict_passes_when_all_criteria_hold():
    v, reasons = stage1_verdict(1.15, 0.01, GOOD_RATIOS, MIN_POOLED_QUINTILE_N)
    assert v == "PROCEED_TO_STAGE2" and reasons == []


def test_verdict_insufficient_data_short_circuits():
    v, reasons = stage1_verdict(2.0, 0.001, GOOD_RATIOS, MIN_POOLED_QUINTILE_N - 1)
    assert v == "INSUFFICIENT_DATA"
    assert "quintile n" in reasons[0]


def test_verdict_fails_on_ratio_below_1_10():
    v, reasons = stage1_verdict(1.09, 0.01, GOOD_RATIOS, 1000)
    assert v == "FAILED" and any("ratio" in r for r in reasons)


def test_verdict_fails_on_p_value():
    v, reasons = stage1_verdict(1.5, 0.05, GOOD_RATIOS, 1000)
    assert v == "FAILED" and any("bootstrap p" in r for r in reasons)


def test_verdict_fails_on_symbol_breadth():
    ratios = {f"S{i}": (1.5 if i < 5 else 0.9) for i in range(10)}  # 50% < 60%
    v, reasons = stage1_verdict(1.5, 0.01, ratios, 1000)
    assert v == "FAILED" and any("symbols" in r for r in reasons)


def test_verdict_collects_multiple_failures():
    ratios = {f"S{i}": 0.9 for i in range(10)}
    v, reasons = stage1_verdict(1.0, 0.5, ratios, 1000)
    assert v == "FAILED" and len(reasons) == 3


# ------------------------------------------------------- frame smoke test

def _synthetic_ohlc(n: int = 700, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    spread = np.abs(rng.normal(0, 0.002, n)) * close
    high = close + spread
    low = close - spread
    idx = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close}, index=idx
    )


def test_compute_symbol_frame_shapes_and_ranges():
    frame = compute_symbol_frame(_synthetic_ohlc())
    # warm-up: 64 (window) + 500 (percentile) and 20 forward bars trimmed
    assert 0 < len(frame) <= 700 - 500 - 20
    assert frame["complexity_pctl"].between(0, 1).all()
    assert (frame["fwd_move"] > 0).all()
    assert frame["complexity"].notna().all()


def test_compute_symbol_frame_too_short_is_empty():
    frame = compute_symbol_frame(_synthetic_ohlc(n=300))
    assert frame.empty  # percentile lookback can never fill


def test_ordered_market_scores_lower_complexity_than_random():
    # a monotone drift produces an almost-constant sign sequence -> the
    # complexity gate's entire premise, pinned as a property test
    n = 700
    idx = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    rng = np.random.default_rng(5)
    trend_close = 100 + np.arange(n) * 0.1 + rng.normal(0, 0.001, n)
    noise_close = 100 + rng.normal(0, 1.0, n)
    frames = {}
    for name, close in [("trend", trend_close), ("noise", noise_close)]:
        df = pd.DataFrame(
            {"open": close, "high": close + 0.05, "low": close - 0.05,
             "close": close},
            index=idx,
        )
        frames[name] = compute_symbol_frame(df)
    assert (
        frames["trend"]["complexity"].median()
        < frames["noise"]["complexity"].median()
    )
