"""
research/experiments/H008_bos_fvg.py
---------------------------------------
H008: BOS (Break of Structure) + FVG (Fair Value Gap) confluence entry.

Built on the lessons of H001, H002, H002b (all FAILED):
- Entering ON a sweep has no edge (WR=46.12% combined, worse than random)
- This tests entering AFTER BOS confirms the sweep AND at FVG retracement

Three-step entry model:
  1. Sweep: price takes out a prior swing high/low (liquidity)
  2. BOS: price then breaks the OPPOSITE swing within BOS_MAX_BARS bars
  3. FVG entry: price retraces into the imbalance left by the BOS candle

Pre-registered falsification (before running):
  PASS: p <= 0.05, improvement >= 10pp over 49.78%, n >= 50
  FAIL: p > 0.05 at n >= 50
  INCONCLUSIVE: n < 50
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from engines.smc_engine import find_swing_points

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "H008_result.json"

H001_BASELINE = 0.4978
MIN_SAMPLE = 50
MIN_IMPROVEMENT_PP = 0.10   # 10pp — higher bar for complex pattern
FORWARD_BARS = 20
BOS_MAX_BARS = 10           # BOS must happen within 10 bars of sweep
SWING_WINDOW = 3


class SyntheticDataNotAllowedError(Exception):
    pass


@dataclass
class H008Result:
    hypothesis_id: str
    status: str
    n_sweeps: int
    n_bos_confirmed: int
    n_fvg_entries: int
    win_rate: float | None
    h001_baseline: float
    improvement: float | None
    p_value: float | None
    data_source: str
    notes: str
    filter_stats: dict


def _two_proportion_p(p1: float, n1: int, p2: float, n2: int) -> float:
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return float(2 * (1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))))


def _detect_fvg(df: pd.DataFrame, bos_bar: int, direction: str) -> tuple[float, float] | None:
    """Detect FVG at the BOS candle.

    Bullish FVG: gap between candle[bos-1].high and candle[bos+1].low
    Bearish FVG: gap between candle[bos+1].high and candle[bos-1].low

    Returns (fvg_low, fvg_high) or None if no FVG exists.
    """
    if bos_bar < 1 or bos_bar + 1 >= len(df):
        return None

    prev = df.iloc[bos_bar - 1]
    # curr = df.iloc[bos_bar]  # the BOS candle itself
    nxt = df.iloc[bos_bar + 1]

    if direction == "BULLISH":
        fvg_low = float(prev["high"])
        fvg_high = float(nxt["low"])
        if fvg_high > fvg_low:
            return fvg_low, fvg_high
    else:  # BEARISH
        fvg_high = float(prev["low"])
        fvg_low = float(nxt["high"])
        if fvg_low < fvg_high:
            return fvg_low, fvg_high

    return None


def detect_bos_fvg_setups(df: pd.DataFrame) -> list[dict]:
    """Find all BOS+FVG setups on the given M15 dataframe.

    Returns list of setup dicts with:
        bar_index, direction, entry_low, entry_high, forward_close
    """
    swings = find_swing_points(df, window=SWING_WINDOW)
    swing_highs_idx = df.index[swings["swing_high"]].tolist()
    swing_lows_idx = df.index[swings["swing_low"]].tolist()

    swing_high_prices = {idx: float(df.loc[idx, "high"]) for idx in swing_highs_idx}
    swing_low_prices = {idx: float(df.loc[idx, "low"]) for idx in swing_lows_idx}

    setups = []

    for i in range(SWING_WINDOW * 2, len(df) - FORWARD_BARS - BOS_MAX_BARS - 2):
        bar = df.iloc[i]
        bar_idx = df.index[i]

        # Get recent swing levels before this bar
        prior_swing_lows = [(idx, p) for idx, p in swing_low_prices.items()
                            if idx < bar_idx]
        prior_swing_highs = [(idx, p) for idx, p in swing_high_prices.items()
                             if idx < bar_idx]

        if not prior_swing_lows or not prior_swing_highs:
            continue

        recent_swing_low = max(prior_swing_lows, key=lambda x: x[0])
        recent_swing_high = max(prior_swing_highs, key=lambda x: x[0])

        swept_low = (float(bar["low"]) < recent_swing_low[1]
                     and float(bar["close"]) > recent_swing_low[1])
        swept_high = (float(bar["high"]) > recent_swing_high[1]
                      and float(bar["close"]) < recent_swing_high[1])

        if not swept_low and not swept_high:
            continue

        # Look for BOS within next BOS_MAX_BARS
        direction = "BULLISH" if swept_low else "BEARISH"
        bos_level = recent_swing_high[1] if direction == "BULLISH" else recent_swing_low[1]
        bos_bar = None

        for j in range(i + 1, min(i + BOS_MAX_BARS + 1, len(df))):
            jbar = df.iloc[j]
            if direction == "BULLISH" and float(jbar["close"]) > bos_level:
                bos_bar = j
                break
            elif direction == "BEARISH" and float(jbar["close"]) < bos_level:
                bos_bar = j
                break

        if bos_bar is None:
            continue

        # Look for FVG at BOS candle
        fvg = _detect_fvg(df, bos_bar, direction)
        if fvg is None:
            continue

        fvg_low, fvg_high = fvg

        # Look for price entering FVG zone within 15 bars of BOS
        entry_bar = None
        for k in range(bos_bar + 1, min(bos_bar + 16, len(df) - FORWARD_BARS)):
            kbar = df.iloc[k]
            if direction == "BULLISH":
                # Price retraces into FVG (bearish candle touching FVG zone)
                if float(kbar["low"]) <= fvg_high and float(kbar["low"]) >= fvg_low:
                    entry_bar = k
                    break
            else:
                # Price retraces into FVG (bullish candle touching FVG zone)
                if float(kbar["high"]) >= fvg_low and float(kbar["high"]) <= fvg_high:
                    entry_bar = k
                    break

        if entry_bar is None or entry_bar + FORWARD_BARS >= len(df):
            continue

        entry_close = float(df.iloc[entry_bar]["close"])
        fwd_close = float(df.iloc[entry_bar + FORWARD_BARS]["close"])
        fwd_return = fwd_close - entry_close

        won = fwd_return > 0 if direction == "BULLISH" else fwd_return < 0

        setups.append({
            "bar_index": entry_bar,
            "direction": direction,
            "fvg_low": fvg_low,
            "fvg_high": fvg_high,
            "entry_close": entry_close,
            "fwd_return": fwd_return,
            "won": won,
        })

    return setups


def run_experiment(
    df_m15: pd.DataFrame,
    source: str,
    df_h1: pd.DataFrame | None = None,
) -> H008Result:
    """Run H008 experiment.

    Args:
        df_m15: M15 OHLCV data
        source: data source description (must start with 'real:')
        df_h1: optional H1 data for regime filter (not used in base version)
    """
    if not source.startswith("real:"):
        raise SyntheticDataNotAllowedError("H008 requires real data only.")

    setups = detect_bos_fvg_setups(df_m15)
    n_fvg = len(setups)

    # count intermediate steps for transparency
    swings = find_swing_points(df_m15, window=SWING_WINDOW)
    n_sweeps = int(swings["swing_high"].sum() + swings["swing_low"].sum())

    if n_fvg < MIN_SAMPLE:
        result = H008Result(
            hypothesis_id="H008",
            status="INCONCLUSIVE",
            n_sweeps=n_sweeps,
            n_bos_confirmed=len([s for s in setups]),  # all have BOS by definition
            n_fvg_entries=n_fvg,
            win_rate=None,
            h001_baseline=H001_BASELINE,
            improvement=None,
            p_value=None,
            data_source=source,
            notes=(
                f"Pattern too rare: {n_fvg} BOS+FVG setups found, "
                f"need {MIN_SAMPLE}. "
                f"Try longer dataset or lower SWING_WINDOW."
            ),
            filter_stats={"n_sweeps": n_sweeps, "n_bos_fvg": n_fvg},
        )
    else:
        outcomes = [s["won"] for s in setups]
        wr = sum(outcomes) / n_fvg
        imp = wr - H001_BASELINE
        p = _two_proportion_p(wr, n_fvg, H001_BASELINE, 225)

        if p <= 0.05 and imp >= MIN_IMPROVEMENT_PP:
            status = "PASSED"
            notes = (
                f"PASSED: WR={wr:.4f} (+{imp:.4f} over baseline), "
                f"p={p:.4f} at n={n_fvg}. "
                f"BOS+FVG has a statistically significant edge."
            )
        elif p > 0.05:
            status = "FAILED"
            notes = f"WR={wr:.4f}, improvement={imp:+.4f}, p={p:.4f} — not significant."
        else:
            status = "FAILED"
            notes = (
                f"Improvement {imp:+.4f} < {MIN_IMPROVEMENT_PP} minimum, "
                f"p={p:.4f}."
            )

        result = H008Result(
            hypothesis_id="H008",
            status=status,
            n_sweeps=n_sweeps,
            n_bos_confirmed=n_fvg,
            n_fvg_entries=n_fvg,
            win_rate=round(wr, 4),
            h001_baseline=H001_BASELINE,
            improvement=round(imp, 4),
            p_value=round(p, 4),
            data_source=source,
            notes=notes,
            filter_stats={
                "n_swing_points": n_sweeps,
                "n_bos_fvg_setups": n_fvg,
                "direction_breakdown": {
                    "bullish": sum(1 for s in setups if s["direction"] == "BULLISH"),
                    "bearish": sum(1 for s in setups if s["direction"] == "BEARISH"),
                },
            },
        )

    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(asdict(result), indent=2, default=str))
    return result


if __name__ == "__main__":
    raise SystemExit("Run via run_h008.py")
