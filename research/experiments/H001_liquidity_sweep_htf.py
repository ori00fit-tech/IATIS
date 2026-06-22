"""
research/experiments/H001_liquidity_sweep_htf.py
----------------------------------------------------
Experiment for hypothesis H001 (see ../hypotheses/H001_liquidity_sweep_htf.md).

DELIBERATE GUARDRAIL: this script refuses to run against synthetic data
(core.data_loader.load_synthetic). Synthetic price has no real liquidity
behavior — a "sweep" detected in it is statistical noise, not a market
phenomenon. Running this experiment now and recording a result would be
exactly the kind of self-deception research/README.md exists to prevent.

To actually run this experiment:
    1. Wire up a real data source (Phase 2: CSV or Twelve Data)
    2. Pass real historical OHLCV via `run_experiment(df_m15, df_h4, source="real:<description>")`
    3. The `source` string is logged into the result file so nobody can
       mistake a synthetic-data run for a real finding later.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from engines.smc_engine import find_swing_points, structural_bias
from engines.base_engine import Bias
from utils.logger import get_logger

logger = get_logger(__name__)

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "H001_result.json"

MIN_SAMPLE_SIZE = 100


@dataclass
class ExperimentResult:
    hypothesis_id: str
    status: str               # PASSED | FAILED | INCONCLUSIVE
    sample_size: int
    sweep_win_rate: float | None
    baseline_win_rate: float | None
    p_value: float | None
    data_source: str
    notes: str


class SyntheticDataNotAllowedError(Exception):
    """Raised when this experiment is invoked against synthetic data."""


def detect_liquidity_sweep(df_m15: pd.DataFrame, window: int = 3) -> pd.DataFrame:
    """Flag bars where price wicks beyond a recent swing high/low and
    closes back inside it (a basic liquidity-sweep definition).

    Returns df_m15 with added boolean columns 'swept_low' / 'swept_high'.
    """
    swings = find_swing_points(df_m15, window=window)
    recent_swing_low = df_m15["low"].where(swings["swing_low"]).ffill().shift(1)
    recent_swing_high = df_m15["high"].where(swings["swing_high"]).ffill().shift(1)

    swept_low = (df_m15["low"] < recent_swing_low) & (df_m15["close"] > recent_swing_low)
    swept_high = (df_m15["high"] > recent_swing_high) & (df_m15["close"] < recent_swing_high)

    out = df_m15.copy()
    out["swept_low"] = swept_low.fillna(False)
    out["swept_high"] = swept_high.fillna(False)
    return out


def _two_proportion_p_value(p1: float, n1: int, p2: float, n2: int) -> float:
    """Two-proportion z-test p-value (two-tailed). No scipy dependency —
    implemented directly since this is the only stats test this module needs.
    """
    if n1 == 0 or n2 == 0:
        return 1.0

    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = np.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0

    z = (p1 - p2) / se
    # two-tailed p-value from the standard normal CDF, via erf
    from math import erf

    p_value = 2 * (1 - 0.5 * (1 + erf(abs(z) / np.sqrt(2))))
    return float(p_value)


def run_experiment(
    df_m15: pd.DataFrame,
    df_h4: pd.DataFrame,
    source: str,
    forward_bars: int = 20,
) -> ExperimentResult:
    """Run the H001 experiment. Raises SyntheticDataNotAllowedError unless
    `source` is explicitly marked as real data.
    """
    if not source.startswith("real:"):
        raise SyntheticDataNotAllowedError(
            "H001 may only be tested against real historical data. "
            "Pass source='real:<description>' once a real data source is wired up. "
            "See module docstring."
        )

    swept = detect_liquidity_sweep(df_m15)
    htf_bias, _, _ = structural_bias(df_h4)

    sweep_outcomes = []
    baseline_outcomes = []

    for i in range(len(swept) - forward_bars):
        row = swept.iloc[i]
        forward_return = swept["close"].iloc[i + forward_bars] - swept["close"].iloc[i]

        if row["swept_low"] and htf_bias == Bias.BULLISH:
            sweep_outcomes.append(forward_return > 0)
        elif row["swept_high"] and htf_bias == Bias.BEARISH:
            sweep_outcomes.append(forward_return < 0)
        else:
            # baseline: any bar, did price move in the HTF bias direction?
            if htf_bias == Bias.BULLISH:
                baseline_outcomes.append(forward_return > 0)
            elif htf_bias == Bias.BEARISH:
                baseline_outcomes.append(forward_return < 0)

    n_sweep = len(sweep_outcomes)
    n_baseline = len(baseline_outcomes)

    if n_sweep < MIN_SAMPLE_SIZE:
        result = ExperimentResult(
            hypothesis_id="H001",
            status="INCONCLUSIVE",
            sample_size=n_sweep,
            sweep_win_rate=None,
            baseline_win_rate=None,
            p_value=None,
            data_source=source,
            notes=f"Sample size {n_sweep} below minimum required {MIN_SAMPLE_SIZE}",
        )
        _save_result(result)
        return result

    sweep_win_rate = sum(sweep_outcomes) / n_sweep
    baseline_win_rate = sum(baseline_outcomes) / n_baseline if n_baseline else 0.0
    p_value = _two_proportion_p_value(sweep_win_rate, n_sweep, baseline_win_rate, n_baseline)

    if p_value <= 0.05 and sweep_win_rate > baseline_win_rate:
        status = "PASSED"
        notes = "Sweep-confirmed entries statistically outperform baseline at p<=0.05"
    else:
        status = "FAILED"
        notes = "No statistically significant edge over baseline"

    result = ExperimentResult(
        hypothesis_id="H001",
        status=status,
        sample_size=n_sweep,
        sweep_win_rate=round(sweep_win_rate, 4),
        baseline_win_rate=round(baseline_win_rate, 4),
        p_value=round(p_value, 4),
        data_source=source,
        notes=notes,
    )
    _save_result(result)
    return result


def _save_result(result: ExperimentResult) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, indent=2)
    logger.info(f"H001 result saved: status={result.status} -> {RESULTS_PATH}")


if __name__ == "__main__":
    raise SystemExit(
        "H001 experiment requires real historical data (Phase 2+). "
        "Import run_experiment() and call it explicitly with source='real:...' "
        "once a real data source is available. See module docstring."
    )
