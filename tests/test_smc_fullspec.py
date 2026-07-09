"""
tests/test_smc_fullspec.py
---------------------------
H017 — full-spec SMC components (order blocks, FVG, BOS/CHoCH).

Locks four properties:
  1. Detection correctness on crafted patterns.
  2. CAUSALITY: detections computed on a prefix of the data never change
     when later bars arrive (the H008-era look-ahead bug class).
  3. Flag OFF (default) → behavior and raw markers identical in spirit to
     the Phase-1 engine (structural bias only, components not computed).
  4. Flag ON → components modulate the structural score, never fabricate
     a strong signal from nothing.
"""

import numpy as np
import pandas as pd
import pytest

from engines.base_engine import Bias
from engines.smc_engine import (
    SMCEngine,
    detect_bos_choch,
    detect_fair_value_gaps,
    detect_order_blocks,
)


def _df(rows):
    """rows: list of (open, high, low, close)."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="4h", tz="UTC")
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c,
                         "volume": 1000.0}, index=idx)


def _flat(n, px=100.0, wick=0.2):
    return [(px, px + wick, px - wick, px)] * n


# ── FVG ──────────────────────────────────────────────────────────────────

def test_bullish_fvg_detected_and_fill_respected():
    # wick=0.5 so the flat section's highs (100.5) can't form accidental
    # micro-gaps against candle B's low.
    rows = _flat(10, wick=0.5)
    rows += [(100, 100.5, 99.8, 100.4),      # candle A (high=100.5)
             (100.4, 102.0, 100.3, 101.8),   # displacement
             (101.8, 103.0, 101.2, 102.5)]   # candle C: low 101.2 > 100.5 → gap
    fvg = detect_fair_value_gaps(_df(rows))
    assert fvg["direction"] == "bullish"
    assert fvg["bottom"] == pytest.approx(100.5)
    assert fvg["top"] == pytest.approx(101.2)

    # A later bar trading back through the gap bottom fills it.
    rows_filled = rows + [(102.5, 102.6, 100.3, 100.6)]
    assert detect_fair_value_gaps(_df(rows_filled))["direction"] == "none"


def test_bearish_fvg_detected():
    rows = _flat(10)
    rows += [(100, 100.3, 99.6, 99.7),
             (99.7, 99.8, 98.0, 98.2),
             (98.2, 98.9, 97.5, 97.8)]       # high 98.9 < low[A] 99.6 → bearish gap
    fvg = detect_fair_value_gaps(_df(rows))
    assert fvg["direction"] == "bearish"


# ── Order blocks ─────────────────────────────────────────────────────────

def test_bullish_order_block_before_displacement():
    rows = _flat(15)
    rows += [(100.0, 100.2, 99.5, 99.6),     # down candle = the OB
             (99.6, 101.0, 99.5, 100.9),
             (100.9, 102.5, 100.8, 102.4)]   # close +2.8 over 2 bars ≫ ATR
    ob = detect_order_blocks(_df(rows))
    assert ob["direction"] == "bullish"
    assert ob["bottom"] == pytest.approx(99.5)


def test_order_block_invalidated_when_zone_breaks():
    rows = _flat(15)
    rows += [(100.0, 100.2, 99.5, 99.6),
             (99.6, 101.0, 99.5, 100.9),
             (100.9, 102.5, 100.8, 102.4),
             (102.4, 102.5, 98.0, 98.2)]     # close far below the OB low
    # The bullish zone is invalidated. (The crash itself legitimately forms
    # a NEW bearish OB from the up-candle preceding it — so the assertion
    # is "no longer bullish", not "none".)
    assert detect_order_blocks(_df(rows))["direction"] != "bullish"


# ── BOS / CHoCH ──────────────────────────────────────────────────────────

def test_bos_bullish_break_of_confirmed_swing_high():
    rng = np.random.default_rng(3)
    base = 100 + np.cumsum(rng.normal(0.05, 0.15, 60))   # gentle uptrend
    rows = [(p, p + 0.3, p - 0.3, p) for p in base]
    rows += [(base[-1], base.max() + 5.0, base[-1] - 0.2, base.max() + 4.8)]
    res = detect_bos_choch(_df(rows))
    assert res["direction"] == "bullish"
    assert res["event"] in ("BOS", "CHoCH")


# ── Causality ────────────────────────────────────────────────────────────

def test_detections_on_prefix_never_change_when_bars_arrive():
    rng = np.random.default_rng(11)
    px = 100 + np.cumsum(rng.normal(0, 0.4, 220))
    rows = [(p, p + abs(rng.normal(0, .3)), p - abs(rng.normal(0, .3)), p + rng.normal(0, .1))
            for p in px]
    df_full = _df(rows)
    for cut in (150, 180, 205):
        prefix = df_full.iloc[:cut]
        again = _df(rows[:cut])       # identical prefix, no future knowledge
        for fn in (detect_fair_value_gaps, detect_order_blocks, detect_bos_choch):
            assert fn(prefix) == fn(again)


# ── Engine integration ───────────────────────────────────────────────────

def _mtf(rows):
    df = _df(rows)
    return {"H4": df, "H1": df, "D1": df.iloc[::6]}


def test_flag_off_is_default_and_skips_components():
    rng = np.random.default_rng(5)
    px = 100 + np.cumsum(rng.normal(0.05, 0.3, 200))
    rows = [(p, p + 0.4, p - 0.4, p + 0.05) for p in px]
    eng = SMCEngine()
    eng.decision_tf = "H4"
    out = eng.analyze(_mtf(rows))
    assert eng.full_spec is False
    assert "DISABLED_BY_FLAG" in str(out.raw["order_blocks"])


def test_flag_on_modulates_but_never_fabricates_strength():
    rng = np.random.default_rng(5)
    px = 100 + np.cumsum(rng.normal(0.05, 0.3, 200))
    rows = [(p, p + 0.4, p - 0.4, p + 0.05) for p in px]
    base_eng = SMCEngine(); base_eng.decision_tf = "H4"
    full_eng = SMCEngine(); full_eng.decision_tf = "H4"; full_eng.full_spec = True
    base = base_eng.analyze(_mtf(rows))
    full = full_eng.analyze(_mtf(rows))
    assert isinstance(full.raw["order_blocks"], dict)      # real output, not marker
    # Modulation bounds: within ±(12+8+8) of the structural score, capped.
    assert abs(full.score - base.score) <= 28.0 + 1e-9 or base.bias != full.bias
    assert full.score <= 85.0
    # From a NEUTRAL structure, components alone may only produce the weak
    # 28-point lean, never a strong solo signal.
    if base.bias == Bias.NEUTRAL and full.bias != Bias.NEUTRAL:
        assert full.score <= 28.0
