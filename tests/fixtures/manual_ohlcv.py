"""
tests/fixtures/manual_ohlcv.py
----------------------------------
Hand-crafted OHLCV builders for behavior tests. Unlike core.data_loader's
synthetic generator (random walk, used for pipeline smoke-testing), these
functions build small, deterministic, exact-pattern DataFrames so we can
assert "given THIS specific structure, the engine MUST produce THAT
specific bias" — not just "the engine produces *some* well-formed output."

Each builder returns a plain OHLCV DataFrame satisfying the project-wide
contract (UTC datetime index, open/high/low/close/volume columns) so it
can be fed directly into engines, regime detection, or the full pipeline.
"""

from __future__ import annotations

import pandas as pd


def _bars_to_df(bars: list[dict], start: str = "2026-01-01", freq: str = "1h") -> pd.DataFrame:
    index = pd.date_range(start=start, periods=len(bars), freq=freq, tz="UTC")
    df = pd.DataFrame(bars, index=index)
    df.index.name = "datetime"
    if "volume" not in df.columns:
        df["volume"] = 1000
    return df[["open", "high", "low", "close", "volume"]]


def bullish_structure_bars() -> pd.DataFrame:
    """A clean, unambiguous higher-high / higher-low sequence.

    IMPORTANT: find_swing_points() uses a centered rolling window
    (default window=3, i.e. a 7-bar window). Swing points placed too
    close together fall inside each other's comparison windows and
    silently fail to register as swings — this was discovered by
    actually running this fixture through the engine, not assumed.
    Each swing point below is separated by >=7 bars from the next, with
    a clearly larger price excursion than its neighbors, so it reliably
    registers regardless of minor tuning changes to the window size.

    Verified pattern (swing points marked *):
        low* 1.0750 -> high* 1.0900 (HH) -> low* 1.0780 (HL, > 1.0750)
        -> high* 1.0950 (HH, > 1.0900)
    """
    bars = [
        {"open": 1.0808, "high": 1.0810, "low": 1.0805, "close": 1.0808},
        {"open": 1.0808, "high": 1.0810, "low": 1.0805, "close": 1.0808},
        {"open": 1.0808, "high": 1.0810, "low": 1.0805, "close": 1.0808},
        {"open": 1.0808, "high": 1.0810, "low": 1.0750, "close": 1.0790},  # swing low #1: 1.0750
        {"open": 1.0790, "high": 1.0800, "low": 1.0785, "close": 1.0795},
        {"open": 1.0795, "high": 1.0800, "low": 1.0790, "close": 1.0795},
        {"open": 1.0795, "high": 1.0800, "low": 1.0790, "close": 1.0795},
        {"open": 1.0795, "high": 1.0900, "low": 1.0790, "close": 1.0890},  # swing high #1: 1.0900
        {"open": 1.0890, "high": 1.0895, "low": 1.0850, "close": 1.0860},
        {"open": 1.0860, "high": 1.0865, "low": 1.0840, "close": 1.0850},
        {"open": 1.0850, "high": 1.0855, "low": 1.0840, "close": 1.0845},
        {"open": 1.0845, "high": 1.0850, "low": 1.0780, "close": 1.0820},  # swing low #2: 1.0780 (> 1.0750)
        {"open": 1.0820, "high": 1.0830, "low": 1.0815, "close": 1.0825},
        {"open": 1.0825, "high": 1.0830, "low": 1.0815, "close": 1.0825},
        {"open": 1.0825, "high": 1.0830, "low": 1.0815, "close": 1.0825},
        {"open": 1.0825, "high": 1.0950, "low": 1.0820, "close": 1.0940},  # swing high #2: 1.0950 (> 1.0900)
        {"open": 1.0940, "high": 1.0945, "low": 1.0900, "close": 1.0910},
        {"open": 1.0910, "high": 1.0915, "low": 1.0895, "close": 1.0905},
        {"open": 1.0905, "high": 1.0910, "low": 1.0895, "close": 1.0905},
    ]
    return _bars_to_df(bars)


def bearish_structure_bars() -> pd.DataFrame:
    """A clean, unambiguous lower-high / lower-low sequence — the mirror
    image of bullish_structure_bars(). Same spacing/excursion rules
    apply (see that function's docstring) and this fixture was likewise
    verified by actually running it through find_swing_points()/
    structural_bias() before being committed here.

    Verified pattern (swing points marked *):
        high* 1.0950 -> low* 1.0800 (LL) -> high* 1.0900 (LH, < 1.0950)
        -> low* 1.0750 (LL, < 1.0800)
    """
    bars = [
        {"open": 1.0900, "high": 1.0905, "low": 1.0895, "close": 1.0898},
        {"open": 1.0900, "high": 1.0905, "low": 1.0895, "close": 1.0898},
        {"open": 1.0900, "high": 1.0905, "low": 1.0895, "close": 1.0898},
        {"open": 1.0898, "high": 1.0950, "low": 1.0895, "close": 1.0910},  # swing high #1: 1.0950
        {"open": 1.0910, "high": 1.0915, "low": 1.0900, "close": 1.0905},
        {"open": 1.0905, "high": 1.0910, "low": 1.0900, "close": 1.0905},
        {"open": 1.0905, "high": 1.0910, "low": 1.0900, "close": 1.0905},
        {"open": 1.0905, "high": 1.0910, "low": 1.0800, "close": 1.0810},  # swing low #1: 1.0800
        {"open": 1.0810, "high": 1.0850, "low": 1.0805, "close": 1.0840},
        {"open": 1.0840, "high": 1.0860, "low": 1.0835, "close": 1.0850},
        {"open": 1.0850, "high": 1.0860, "low": 1.0840, "close": 1.0845},
        {"open": 1.0845, "high": 1.0900, "low": 1.0840, "close": 1.0870},  # swing high #2: 1.0900 (< 1.0950)
        {"open": 1.0870, "high": 1.0875, "low": 1.0860, "close": 1.0865},
        {"open": 1.0865, "high": 1.0870, "low": 1.0860, "close": 1.0865},
        {"open": 1.0865, "high": 1.0870, "low": 1.0860, "close": 1.0865},
        {"open": 1.0865, "high": 1.0868, "low": 1.0750, "close": 1.0780},  # swing low #2: 1.0750 (< 1.0800)
        {"open": 1.0780, "high": 1.0785, "low": 1.0760, "close": 1.0770},
        {"open": 1.0770, "high": 1.0775, "low": 1.0755, "close": 1.0765},
        {"open": 1.0765, "high": 1.0770, "low": 1.0755, "close": 1.0765},
    ]
    return _bars_to_df(bars)


def choppy_mixed_structure_bars() -> pd.DataFrame:
    """A genuine structural contradiction — NOT just insufficient data.

    Verified pattern: swing high #2 (1.0870) is LOWER than swing high #1
    (1.0900) — a bearish signal — while swing low #2 (1.0820) is HIGHER
    than swing low #1 (1.0800) — a bullish signal. This mixed signal
    must produce Bias.NEUTRAL via the "Mixed swing structure" reason
    path in structural_bias(), not the separate "not enough swing
    points" path. Confirmed by actually running this fixture through
    find_swing_points()/structural_bias() before committing it here —
    two earlier attempts at this fixture silently failed to register
    the intended swing points and produced the wrong bias.
    """
    bars = [
        {"open": 1.0850, "high": 1.0855, "low": 1.0845, "close": 1.0850},
        {"open": 1.0850, "high": 1.0855, "low": 1.0845, "close": 1.0850},
        {"open": 1.0850, "high": 1.0855, "low": 1.0845, "close": 1.0850},
        {"open": 1.0850, "high": 1.0900, "low": 1.0845, "close": 1.0890},  # swing high #1: 1.0900
        {"open": 1.0890, "high": 1.0895, "low": 1.0860, "close": 1.0870},
        {"open": 1.0870, "high": 1.0875, "low": 1.0860, "close": 1.0865},
        {"open": 1.0865, "high": 1.0870, "low": 1.0860, "close": 1.0865},
        {"open": 1.0865, "high": 1.0868, "low": 1.0800, "close": 1.0820},  # swing low #1: 1.0800
        {"open": 1.0820, "high": 1.0850, "low": 1.0815, "close": 1.0840},
        {"open": 1.0840, "high": 1.0855, "low": 1.0835, "close": 1.0845},
        {"open": 1.0845, "high": 1.0850, "low": 1.0835, "close": 1.0840},
        {"open": 1.0840, "high": 1.0870, "low": 1.0835, "close": 1.0860},  # swing high #2: 1.0870 (< 1.0900, bearish signal)
        {"open": 1.0860, "high": 1.0865, "low": 1.0840, "close": 1.0850},
        {"open": 1.0850, "high": 1.0855, "low": 1.0840, "close": 1.0845},
        {"open": 1.0845, "high": 1.0848, "low": 1.0840, "close": 1.0843},
        {"open": 1.0843, "high": 1.0848, "low": 1.0820, "close": 1.0830},  # swing low #2: 1.0820 (> 1.0800, bullish signal)
        {"open": 1.0830, "high": 1.0835, "low": 1.0825, "close": 1.0828},
        {"open": 1.0828, "high": 1.0832, "low": 1.0825, "close": 1.0828},
        {"open": 1.0828, "high": 1.0832, "low": 1.0825, "close": 1.0828},
    ]
    return _bars_to_df(bars)


def upside_breakout_bars(lookback: int = 20) -> pd.DataFrame:
    """`lookback` bars of a tight, flat range, followed by one bar that
    closes decisively above the entire prior range's high.
    """
    bars = []
    for _ in range(lookback):
        bars.append({"open": 1.0850, "high": 1.0858, "low": 1.0845, "close": 1.0852})
    # breakout bar: closes well above the prior range high (1.0858)
    bars.append({"open": 1.0855, "high": 1.0900, "low": 1.0853, "close": 1.0895})
    return _bars_to_df(bars)


def downside_breakout_bars(lookback: int = 20) -> pd.DataFrame:
    """Mirror of upside_breakout_bars(): flat range then a decisive
    close below the prior range's low.
    """
    bars = []
    for _ in range(lookback):
        bars.append({"open": 1.0850, "high": 1.0858, "low": 1.0845, "close": 1.0848})
    bars.append({"open": 1.0846, "high": 1.0847, "low": 1.0800, "close": 1.0805})
    return _bars_to_df(bars)


def no_breakout_bars(lookback: int = 20) -> pd.DataFrame:
    """Flat range with no bar breaking outside it — used to confirm the
    breakout detector correctly reports "none" rather than a false positive.
    """
    bars = []
    for _ in range(lookback + 1):
        bars.append({"open": 1.0850, "high": 1.0858, "low": 1.0845, "close": 1.0852})
    return _bars_to_df(bars)
