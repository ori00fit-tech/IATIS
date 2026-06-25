"""
research/experiments/H002b_multisymbol_sweep.py
-------------------------------------------------
H002b: Multi-symbol qualified sweep aggregation.

H002 showed WR=57.89% (+8.11pp over H001) but p=0.2209 at n=76 —
promising direction, insufficient statistical power.

H002b aggregates qualified sweeps across EURUSD + GBPUSD + XAUUSD
to reach n≥200 for meaningful p-value testing.

Hypothesis: The ATR-filtered + regime-filtered sweep edge is
real but requires a larger sample to confirm. The direction of
improvement (8.11pp) is consistent with a true edge.

Pre-registered falsification criteria (before running):
- PASS if: p ≤ 0.05 AND improvement ≥ 5pp AND n ≥ 150
- FAIL if: p > 0.05 at n ≥ 150
- INCONCLUSIVE if: n < 150 even after multi-symbol aggregation
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from math import erf, sqrt
from typing import Any

import pandas as pd

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "H002b_result.json"

H001_BASELINE = 0.4978
MIN_SAMPLE = 150
MIN_IMPROVEMENT = 0.05
FORWARD_BARS = 20
ATR_MULTIPLIER = 0.5


class SyntheticDataNotAllowedError(Exception):
    pass


@dataclass
class H002bResult:
    hypothesis_id: str
    status: str
    total_n: int
    per_symbol: dict[str, dict]
    combined_win_rate: float | None
    h001_baseline: float
    p_value: float | None
    improvement: float | None
    data_sources: list[str]
    notes: str


def _two_proportion_p(p1: float, n1: int, p2: float, n2: int) -> float:
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return float(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))))


def run_single_symbol(
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    symbol: str,
) -> dict[str, Any]:
    """Run qualified sweep detection on one symbol. Returns outcomes list."""
    from research.experiments.H002_qualified_sweep import (
        detect_qualified_sweeps, FORWARD_BARS, ATR_MULTIPLIER
    )
    from engines.smc_engine import structural_bias
    from engines.base_engine import Bias

    swept = detect_qualified_sweeps(df_m15, df_h1, atr_multiplier=ATR_MULTIPLIER)
    htf_bias, _, _ = structural_bias(df_h1)

    outcomes = []
    for i in range(len(swept) - FORWARD_BARS):
        row = swept.iloc[i]
        fwd = swept["close"].iloc[i + FORWARD_BARS] - swept["close"].iloc[i]
        if row["swept_low_qualified"] and htf_bias == Bias.BULLISH:
            outcomes.append(fwd > 0)
        elif row["swept_high_qualified"] and htf_bias == Bias.BEARISH:
            outcomes.append(fwd < 0)

    n = len(outcomes)
    wr = sum(outcomes) / n if n > 0 else None
    raw = int(swept["raw_swept_low"].sum() + swept["raw_swept_high"].sum())

    return {
        "symbol": symbol,
        "raw_sweeps": raw,
        "qualified_n": n,
        "win_rate": round(wr, 4) if wr is not None else None,
        "outcomes": outcomes,
    }


def run_experiment(
    symbols_data: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    sources: list[str],
) -> H002bResult:
    """
    Args:
        symbols_data: {symbol: (df_m15, df_h1)}
        sources: list of data source descriptions
    """
    for s in sources:
        if not s.startswith("real:"):
            raise SyntheticDataNotAllowedError("H002b requires real data only.")

    per_symbol = {}
    all_outcomes: list[bool] = []

    for symbol, (df_m15, df_h1) in symbols_data.items():
        result = run_single_symbol(df_m15, df_h1, symbol)
        per_symbol[symbol] = {k: v for k, v in result.items() if k != "outcomes"}
        all_outcomes.extend(result["outcomes"])

    total_n = len(all_outcomes)

    if total_n < MIN_SAMPLE:
        return H002bResult(
            hypothesis_id="H002b",
            status="INCONCLUSIVE",
            total_n=total_n,
            per_symbol=per_symbol,
            combined_win_rate=None,
            h001_baseline=H001_BASELINE,
            p_value=None,
            improvement=None,
            data_sources=sources,
            notes=(
                f"Still insufficient after multi-symbol aggregation: "
                f"n={total_n} < {MIN_SAMPLE}. "
                f"Consider extending to 3+ years or more symbols."
            ),
        )

    combined_wr = sum(all_outcomes) / total_n
    improvement = combined_wr - H001_BASELINE
    p_value = _two_proportion_p(combined_wr, total_n, H001_BASELINE, 225)

    if p_value <= 0.05 and improvement >= MIN_IMPROVEMENT:
        status = "PASSED"
        notes = (
            f"PASSED: combined WR={combined_wr:.4f} (+{improvement:.4f} over H001), "
            f"p={p_value:.4f} at n={total_n} across {len(symbols_data)} symbols."
        )
    elif p_value > 0.05:
        status = "FAILED"
        notes = (
            f"No significant edge at n={total_n}: WR={combined_wr:.4f}, "
            f"improvement={improvement:+.4f}, p={p_value:.4f}."
        )
    else:
        status = "FAILED"
        notes = (
            f"Improvement too small: {improvement:+.4f} < {MIN_IMPROVEMENT} minimum, "
            f"p={p_value:.4f}."
        )

    result = H002bResult(
        hypothesis_id="H002b",
        status=status,
        total_n=total_n,
        per_symbol=per_symbol,
        combined_win_rate=round(combined_wr, 4),
        h001_baseline=H001_BASELINE,
        p_value=round(p_value, 4),
        improvement=round(improvement, 4),
        data_sources=sources,
        notes=notes,
    )

    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(asdict(result), indent=2, default=str))
    return result


if __name__ == "__main__":
    raise SystemExit(
        "Run via run_h002b.py — see research/hypotheses/H002_qualified_sweep.md"
    )
