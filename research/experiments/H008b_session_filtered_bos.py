"""
research/experiments/H008b_session_filtered_bos.py
------------------------------------------------------
H008b: BOS+FVG with London session + ATR quality filters.

H008 showed +5.4pp edge on EURUSD+XAUUSD (WR=55.2%, p=0.23).
Insufficient statistical power with 2yr H1 data (need n≥600).

H008b strategy: RAISE win rate instead of collecting more data.
If we can filter to WR≥60%, significance is achievable at n≥50.

Two additional filters:
1. London session (02:00-10:00 UTC): BOS during London open is more
   reliable because institutional orders are placed at session start,
   creating clean structural breaks.

2. ATR quality filter: BOS candle must be ≥1.5× ATR(14) in size.
   Weak/indecisive BOS candles lead to false signals.

Pre-registered criteria:
  PASS: p<=0.05, WR>=60%, n>=50 (EURUSD or EURUSD+XAUUSD)
  FAIL: p>0.05 at n>=50
  INCONCLUSIVE: n<50 even with session+ATR filters
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from research.experiments.H008_bos_fvg import detect_bos_fvg_setups

RESULTS_PATH = Path(__file__).resolve().parent.parent / "results" / "H008b_result.json"

H001_BASELINE = 0.4978
MIN_SAMPLE = 50
TARGET_WR = 0.60
LONDON_START = 2   # UTC hour
LONDON_END = 10    # UTC hour
ATR_MULTIPLIER = 1.5  # BOS candle must be ≥ 1.5×ATR


class SyntheticDataNotAllowedError(Exception):
    pass


@dataclass
class H008bResult:
    hypothesis_id: str
    status: str
    n_total_setups: int
    n_session_filtered: int
    n_atr_filtered: int
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


def _compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def detect_filtered_setups(df_m15: pd.DataFrame) -> list[dict]:
    """Find BOS+FVG setups with London session + ATR quality filters."""
    # Get all setups from base H008 detector
    all_setups = detect_bos_fvg_setups(df_m15)

    # Compute ATR for quality filter
    atr = _compute_atr(df_m15)

    filtered = []
    session_rejected = 0
    atr_rejected = 0

    for setup in all_setups:
        bar_idx = setup["bar_index"]
        if bar_idx >= len(df_m15):
            continue

        bar_time = df_m15.index[bar_idx]

        # Filter 1: London session
        try:
            hour = bar_time.hour if hasattr(bar_time, 'hour') else pd.Timestamp(bar_time).hour
            if not (LONDON_START <= hour < LONDON_END):
                session_rejected += 1
                continue
        except Exception:
            session_rejected += 1
            continue

        # Filter 2: ATR quality — BOS candle size
        if bar_idx >= 14:  # Need ATR period
            bar = df_m15.iloc[bar_idx]
            candle_size = abs(float(bar["close"]) - float(bar["open"]))
            atr_val = float(atr.iloc[bar_idx]) if not pd.isna(atr.iloc[bar_idx]) else 0
            if atr_val > 0 and candle_size < ATR_MULTIPLIER * atr_val:
                atr_rejected += 1
                continue

        setup["session_rejected"] = 0
        setup["atr_rejected"] = 0
        filtered.append(setup)

    return filtered, session_rejected, atr_rejected


def run_experiment(
    df_m15: pd.DataFrame,
    source: str,
    symbol: str = "UNKNOWN",
) -> H008bResult:
    """Run H008b experiment with session + ATR filters."""
    if not source.startswith("real:"):
        raise SyntheticDataNotAllowedError("H008b requires real data only.")

    # Base setups (unfiltered)
    all_setups = detect_bos_fvg_setups(df_m15)
    n_total = len(all_setups)

    # Filtered setups
    filtered, session_rej, atr_rej = detect_filtered_setups(df_m15)
    n_filtered = len(filtered)

    filter_stats = {
        "n_total_bos_fvg": n_total,
        "session_rejected": session_rej,
        "atr_rejected": atr_rej,
        "n_final": n_filtered,
        "filter_retention_pct": round(n_filtered / n_total * 100, 1) if n_total > 0 else 0,
        "london_hours": f"{LONDON_START:02d}:00-{LONDON_END:02d}:00 UTC",
        "atr_multiplier": ATR_MULTIPLIER,
    }

    if n_filtered < MIN_SAMPLE:
        result = H008bResult(
            hypothesis_id="H008b",
            status="INCONCLUSIVE",
            n_total_setups=n_total,
            n_session_filtered=n_filtered,
            n_atr_filtered=n_filtered,
            win_rate=None,
            h001_baseline=H001_BASELINE,
            improvement=None,
            p_value=None,
            data_source=source,
            notes=(
                f"Insufficient setups after filtering: {n_filtered} < {MIN_SAMPLE}. "
                f"Session filter removed {session_rej}, ATR filter removed {atr_rej}."
            ),
            filter_stats=filter_stats,
        )
    else:
        outcomes = [s["won"] for s in filtered]
        wr = sum(outcomes) / n_filtered
        imp = wr - H001_BASELINE
        p = _two_proportion_p(wr, n_filtered, H001_BASELINE, 225)

        if p <= 0.05 and wr >= TARGET_WR:
            status = "PASSED"
            notes = (
                f"PASSED: {symbol} WR={wr:.2%} (+{imp:.2%} over baseline), "
                f"p={p:.4f} at n={n_filtered}. "
                f"London+ATR filters raise WR to {wr:.1%}."
            )
        elif p > 0.05:
            status = "FAILED"
            notes = (
                f"WR={wr:.2%}, improvement={imp:+.2%}, p={p:.4f} — "
                f"not significant even with session+ATR filters."
            )
        else:
            status = "FAILED"
            notes = (
                f"WR={wr:.2%} < {TARGET_WR:.0%} target despite significant p={p:.4f}. "
                f"Edge exists but below commercial threshold."
            )

        result = H008bResult(
            hypothesis_id="H008b",
            status=status,
            n_total_setups=n_total,
            n_session_filtered=n_filtered,
            n_atr_filtered=n_filtered,
            win_rate=round(wr, 4),
            h001_baseline=H001_BASELINE,
            improvement=round(imp, 4),
            p_value=round(p, 4),
            data_source=source,
            notes=notes,
            filter_stats=filter_stats,
        )

    RESULTS_PATH.parent.mkdir(exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(asdict(result), indent=2, default=str))
    return result
