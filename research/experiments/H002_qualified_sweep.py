"""
research/experiments/H002_qualified_sweep.py
----------------------------------------------
Experiment for H002: qualified liquidity sweep (minimum ATR size +
trending regime) vs H001's unfiltered definition.

H001 failed (win_rate=0.4978, p=0.6251) using any wick beyond a swing.
H002 adds two qualification filters:
  1. Sweep wick >= 1x ATR(14) in size
  2. H1 regime must be TRENDING at time of sweep

Same guardrail as H001: refuses to run on synthetic data.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from engines.smc_engine import find_swing_points
from engines.base_engine import Bias
from engines.smc_engine import structural_bias
from regimes.regime_detector import Regime, detect_regime
from regimes.volatility_classifier import atr
from utils.logger import get_logger

logger = get_logger(__name__)

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "H002_result.json"

# Pre-registered thresholds (must not be changed after first run)
MIN_SAMPLE_SIZE = 30
MIN_WIN_RATE_IMPROVEMENT = 0.05   # 5pp over H001 baseline
H001_BASELINE_WIN_RATE = 0.4978   # from H001_result.json
FORWARD_BARS = 20
ATR_MULTIPLIER = 1.0              # sweep wick must be >= 1× ATR(14)


class SyntheticDataNotAllowedError(Exception):
    pass


@dataclass
class H002Result:
    hypothesis_id: str
    status: str
    sample_size_unfiltered: int
    sample_size_qualified: int
    qualified_win_rate: float | None
    h001_baseline_win_rate: float
    p_value: float | None
    win_rate_improvement: float | None
    data_source: str
    notes: str
    filter_stats: dict


def _two_proportion_p_value(p1: float, n1: int, p2: float, n2: int) -> float:
    if n1 == 0 or n2 == 0:
        return 1.0
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return float(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))))


def detect_qualified_sweeps(
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    atr_multiplier: float = ATR_MULTIPLIER,
    swing_window: int = 3,
) -> pd.DataFrame:
    """Detect sweep events that pass both qualification filters.

    Returns df_m15 with added columns:
      swept_low_qualified, swept_high_qualified: bool
      sweep_size: float (wick size / ATR)
    """
    # ATR on M15 for sweep size
    atr_m15 = atr(df_m15, period=14)
    swings = find_swing_points(df_m15, window=swing_window)

    recent_swing_low = df_m15["low"].where(swings["swing_low"]).ffill().shift(1)
    recent_swing_high = df_m15["high"].where(swings["swing_high"]).ffill().shift(1)

    # Basic sweep detection (same as H001)
    raw_swept_low = (df_m15["low"] < recent_swing_low) & (df_m15["close"] > recent_swing_low)
    raw_swept_high = (df_m15["high"] > recent_swing_high) & (df_m15["close"] < recent_swing_high)

    # Filter 1: sweep wick >= ATR_MULTIPLIER × ATR(14)
    wick_down = recent_swing_low - df_m15["low"]
    wick_up = df_m15["high"] - recent_swing_high
    sweep_size_low = wick_down / atr_m15.clip(lower=1e-10)
    sweep_size_high = wick_up / atr_m15.clip(lower=1e-10)

    size_qualified_low = sweep_size_low >= atr_multiplier
    size_qualified_high = sweep_size_high >= atr_multiplier

    # Filter 2: H1 regime must be TRENDING at time of sweep
    # We check regime on rolling 100-bar H1 window ending at the M15 bar's time
    h1_regime_at_time = _compute_h1_regime_series(df_h1, df_m15)
    regime_qualified = h1_regime_at_time == "TRENDING"

    out = df_m15.copy()
    out["swept_low_qualified"] = (raw_swept_low & size_qualified_low & regime_qualified).fillna(False)
    out["swept_high_qualified"] = (raw_swept_high & size_qualified_high & regime_qualified).fillna(False)
    out["sweep_size_low"] = sweep_size_low
    out["sweep_size_high"] = sweep_size_high
    out["h1_regime"] = h1_regime_at_time
    out["raw_swept_low"] = raw_swept_low.fillna(False)
    out["raw_swept_high"] = raw_swept_high.fillna(False)

    return out


def _compute_h1_regime_series(df_h1: pd.DataFrame, df_m15: pd.DataFrame) -> pd.Series:
    """Compute H1 regime for each M15 timestamp using a rolling window.

    Uses the most recent H1 bar's rolling regime at each M15 timestamp.
    Returns a Series indexed like df_m15 with regime string values.
    """
    regimes = {}
    lookback = 100

    for i in range(lookback, len(df_h1)):
        window = df_h1.iloc[i - lookback: i]
        result = detect_regime(window, lookback=lookback)
        regimes[df_h1.index[i]] = result.regime.value

    regime_series_h1 = pd.Series(regimes)
    # forward-fill to M15 timestamps
    combined = regime_series_h1.reindex(
        regime_series_h1.index.union(df_m15.index)
    ).ffill()
    return combined.reindex(df_m15.index).fillna("UNKNOWN")


def run_experiment(
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    source: str,
    forward_bars: int = FORWARD_BARS,
) -> H002Result:
    """Run H002. Raises SyntheticDataNotAllowedError unless source='real:...'"""
    if not source.startswith("real:"):
        raise SyntheticDataNotAllowedError(
            "H002 may only be tested against real historical data. "
            "Pass source='real:<description>' once real data is available."
        )

    logger.info(f"Running H002 experiment on {source}")
    swept = detect_qualified_sweeps(df_m15, df_h1)

    htf_bias, _, _ = structural_bias(df_h1)

    qualified_outcomes = []
    unfiltered_count = int(swept["raw_swept_low"].sum() + swept["raw_swept_high"].sum())

    for i in range(len(swept) - forward_bars):
        row = swept.iloc[i]
        fwd_return = swept["close"].iloc[i + forward_bars] - swept["close"].iloc[i]

        if row["swept_low_qualified"] and htf_bias == Bias.BULLISH:
            qualified_outcomes.append(fwd_return > 0)
        elif row["swept_high_qualified"] and htf_bias == Bias.BEARISH:
            qualified_outcomes.append(fwd_return < 0)

    n_qualified = len(qualified_outcomes)
    filter_stats = {
        "total_raw_sweeps": unfiltered_count,
        "passed_size_filter": int(
            swept["raw_swept_low"].sum() & (swept["sweep_size_low"] >= ATR_MULTIPLIER).sum()
            + swept["raw_swept_high"].sum() & (swept["sweep_size_high"] >= ATR_MULTIPLIER).sum()
        ),
        "passed_regime_filter_pct": float(
            (swept["h1_regime"] == "TRENDING").mean()
        ),
        "qualified": n_qualified,
        "h001_sample": 225,
        "filter_retention_pct": round(n_qualified / max(unfiltered_count, 1) * 100, 1),
    }

    if n_qualified < MIN_SAMPLE_SIZE:
        result = H002Result(
            hypothesis_id="H002",
            status="INCONCLUSIVE",
            sample_size_unfiltered=unfiltered_count,
            sample_size_qualified=n_qualified,
            qualified_win_rate=None,
            h001_baseline_win_rate=H001_BASELINE_WIN_RATE,
            p_value=None,
            win_rate_improvement=None,
            data_source=source,
            notes=(
                f"Sample too small after qualification filters: "
                f"{n_qualified} < {MIN_SAMPLE_SIZE} minimum. "
                f"Filter retained {filter_stats['filter_retention_pct']}% of raw sweeps. "
                f"Try longer date range or lower ATR_MULTIPLIER threshold."
            ),
            filter_stats=filter_stats,
        )
        _save(result)
        return result

    win_rate = sum(qualified_outcomes) / n_qualified
    improvement = win_rate - H001_BASELINE_WIN_RATE
    p_value = _two_proportion_p_value(
        win_rate, n_qualified,
        H001_BASELINE_WIN_RATE, 225,
    )

    if p_value <= 0.05 and improvement >= MIN_WIN_RATE_IMPROVEMENT:
        status = "PASSED"
        notes = (
            f"Qualified sweeps show statistically significant improvement over H001: "
            f"win_rate={win_rate:.4f} vs H001={H001_BASELINE_WIN_RATE:.4f}, "
            f"improvement={improvement:+.4f}, p={p_value:.4f}"
        )
    elif p_value > 0.05:
        status = "FAILED"
        notes = (
            f"No statistically significant improvement over H001 baseline "
            f"(p={p_value:.4f} > 0.05). win_rate={win_rate:.4f}"
        )
    else:
        status = "FAILED"
        notes = (
            f"Improvement too small despite significance: "
            f"{improvement:+.4f} < {MIN_WIN_RATE_IMPROVEMENT} minimum threshold. "
            f"p={p_value:.4f}"
        )

    result = H002Result(
        hypothesis_id="H002",
        status=status,
        sample_size_unfiltered=unfiltered_count,
        sample_size_qualified=n_qualified,
        qualified_win_rate=round(win_rate, 4),
        h001_baseline_win_rate=H001_BASELINE_WIN_RATE,
        p_value=round(p_value, 4),
        win_rate_improvement=round(improvement, 4),
        data_source=source,
        notes=notes,
        filter_stats=filter_stats,
    )
    _save(result)
    return result


def _save(result: H002Result) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(asdict(result), f, indent=2)
    logger.info(f"H002 result: status={result.status} → {RESULTS_PATH}")


if __name__ == "__main__":
    raise SystemExit(
        "Run H002 by importing run_experiment() with real historical data. "
        "See research/hypotheses/H002_qualified_sweep.md for details."
    )
