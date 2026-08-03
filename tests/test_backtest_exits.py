"""
Tests for backtesting.backtest_engine.check_exit and config alignment.

Proves the Major fixes:
- Gaps through SL fill at the open (worse), not at the SL price.
- SL exits incur slippage against the trader; TP exits do not.
- Intrabar both-touched → SL wins (pessimistic assumption).
- BacktestConfig defaults are aligned with production config.yaml.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backtesting.backtest_engine import BacktestConfig, Trade, check_exit

PIP = 0.0001
SLIP = 0.5 * PIP  # 0.5 pips in price units


def _trade(direction: str, entry: float, sl: float, tp: float) -> Trade:
    return Trade(
        entry_bar=0, entry_time=None, direction=direction,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        risk_pct=0.01, position_size=1.0,
    )


def _bar(o: float, h: float, l: float) -> pd.Series:
    return pd.Series({"open": o, "high": h, "low": l, "close": o})


# ── Gap modeling ────────────────────────────────────────────────────────

def test_buy_gap_through_sl_fills_at_open_not_sl():
    t = _trade("BUY", entry=1.1000, sl=1.0950, tp=1.1100)
    # Bar opens BELOW the stop — a weekend/news gap.
    exit_price, reason = check_exit(t, _bar(o=1.0900, h=1.0920, l=1.0880), SLIP)
    assert reason == "SL_GAP"
    assert exit_price == 1.0900 - SLIP          # open, NOT 1.0950
    assert exit_price < t.stop_loss             # strictly worse than SL


def test_sell_gap_through_sl_fills_at_open_not_sl():
    t = _trade("SELL", entry=1.1000, sl=1.1050, tp=1.0900)
    exit_price, reason = check_exit(t, _bar(o=1.1120, h=1.1150, l=1.1100), SLIP)
    assert reason == "SL_GAP"
    assert exit_price == 1.1120 + SLIP
    assert exit_price > t.stop_loss


def test_buy_gap_through_tp_fills_at_open():
    t = _trade("BUY", entry=1.1000, sl=1.0950, tp=1.1100)
    exit_price, reason = check_exit(t, _bar(o=1.1150, h=1.1180, l=1.1130), SLIP)
    assert reason == "TP_GAP"
    assert exit_price == 1.1150                 # favorable gap, no slippage


# ── Normal intrabar exits ───────────────────────────────────────────────

def test_buy_intrabar_sl_incurs_slippage():
    t = _trade("BUY", entry=1.1000, sl=1.0950, tp=1.1100)
    exit_price, reason = check_exit(t, _bar(o=1.0990, h=1.0995, l=1.0940), SLIP)
    assert reason == "SL"
    assert exit_price == 1.0950 - SLIP


def test_buy_intrabar_tp_fills_at_price_no_slippage():
    t = _trade("BUY", entry=1.1000, sl=1.0950, tp=1.1100)
    exit_price, reason = check_exit(t, _bar(o=1.1050, h=1.1110, l=1.1040), SLIP)
    assert reason == "TP"
    assert exit_price == 1.1100


def test_both_touched_in_one_bar_sl_wins_pessimistic():
    t = _trade("BUY", entry=1.1000, sl=1.0950, tp=1.1100)
    # Wide bar touches both SL and TP — ambiguous from OHLC.
    exit_price, reason = check_exit(t, _bar(o=1.1000, h=1.1120, l=1.0940), SLIP)
    assert reason == "SL"


def test_no_exit_when_neither_level_touched():
    t = _trade("BUY", entry=1.1000, sl=1.0950, tp=1.1100)
    assert check_exit(t, _bar(o=1.1010, h=1.1040, l=1.0990), SLIP) is None


def test_zero_slippage_supported():
    t = _trade("BUY", entry=1.1000, sl=1.0950, tp=1.1100)
    exit_price, reason = check_exit(t, _bar(o=1.0990, h=1.0995, l=1.0940), 0.0)
    assert exit_price == 1.0950


# ── Production alignment ────────────────────────────────────────────────

# ── Same-bar exit check (Forensic Audit, 2026-08-03 — BUG-002) ────────────
# The entry bar's OWN post-open excursion must be checked against SL/TP,
# not just bars strictly after it — a resting stop order is live from the
# instant of entry. Before the fix, run_backtest()'s next-iteration exit
# check only ever inspected df.iloc[i+2] onward, permanently skipping
# df.iloc[i+1] (a newly-opened trade's own entry bar).

def test_run_backtest_checks_exit_on_the_entry_bar_itself():
    """Authoritative behavior-change proof: real synthetic OHLCV with wide
    intrabar wicks relative to a tight SL, run through the REAL
    run_backtest() pipeline (not a mock) — asserts at least one real trade
    exits ON its own entry bar, which was structurally impossible before
    the fix (every trade's first-ever exit check used to be its
    entry_bar + 1)."""
    import logging

    import numpy as np

    from backtesting.backtest_engine import BacktestConfig, run_backtest

    logging.disable(logging.CRITICAL)
    try:
        n = 300
        idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        rng = np.random.default_rng(3)
        close = 1.10 + np.cumsum(rng.normal(0, 0.0006, n)) + np.linspace(0, 0.02, n)
        o = np.roll(close, 1)
        o[0] = close[0]
        df = pd.DataFrame(
            {
                "open": o,
                # Wide intrabar wicks relative to a tight SL below — makes
                # a same-bar SL/TP touch very likely, the exact adversarial
                # shape that exposed the bug.
                "high": np.maximum(o, close) + 0.004,
                "low": np.minimum(o, close) - 0.004,
                "close": close,
                "volume": 1000.0,
            },
            index=idx,
        )
        cfg = BacktestConfig.from_profile(
            "EURUSD", warmup_bars=60, step_bars=1, sl_atr_multiplier=0.3,
        )
        result = run_backtest(df, cfg)
    finally:
        logging.disable(logging.NOTSET)

    assert len(result.trades) > 0, "test data must produce at least one real trade"
    same_bar_exits = [t for t in result.trades if t.exit_bar == t.entry_bar]
    assert len(same_bar_exits) > 0, (
        "expected at least one trade to exit on its own entry bar — "
        "if this fails, the same-bar exit check regressed"
    )
    for t in same_bar_exits:
        assert t.exit_reason in ("SL", "TP", "SL_GAP", "TP_GAP")


def test_run_backtest_entry_bar_exit_flips_a_would_be_missed_stopout():
    """The severe variant: proves the fix doesn't just re-check the entry
    bar, it can change the trade's FINAL reported outcome. Directly
    compares check_exit() called on the entry bar (now wired into the
    loop) against the bar sequence the pre-fix loop would have checked
    (entry_bar + 1 onward) — a same-bar stop-hunt wick that recovers
    before the next bar was previously invisible to the simulation."""
    idx = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
    bars = pd.DataFrame(
        {
            "open": [1.1000, 1.1010, 1.1040, 1.1080],
            "high": [1.1015, 1.1050, 1.1070, 1.1110],
            "low": [1.0950, 1.1000, 1.1030, 1.1075],  # bar[0]'s low pierces SL
            "close": [1.1010, 1.1040, 1.1060, 1.1100],
        },
        index=idx,
    )
    entry_price, sl, tp = 1.1000, 1.0980, 1.1100
    trade = _trade("BUY", entry=entry_price, sl=sl, tp=tp)

    # Pre-fix behavior: only bars AFTER the entry bar were ever checked.
    pre_fix_result = None
    for i in (1, 2, 3):
        pre_fix_result = check_exit(trade, bars.iloc[i], SLIP)
        if pre_fix_result is not None:
            break
    assert pre_fix_result is not None and pre_fix_result[1] == "TP", (
        "sanity check: without the entry-bar check, this scenario reports a win"
    )

    # Fixed behavior: the entry bar itself is checked first (as
    # run_backtest()'s loop now does immediately after opening a trade).
    fixed_result = check_exit(trade, bars.iloc[0], SLIP)
    assert fixed_result is not None and fixed_result[1] == "SL", (
        "the entry bar's own excursion must be caught — this is the real loss "
        "the pre-fix loop silently turned into a reported win"
    )


# ── Same-bar equity_curve correction (Forensic Audit, 2026-08-04 — BUG-003) ──
# A direct side-effect of the BUG-002 fix above: once the entry bar itself
# could close a trade same-bar, the equity_curve point already appended for
# that bar (before the same-bar close ran) was stale — it kept showing the
# pre-trade balance, and the trade's real PnL only appeared one entry late.

def test_same_bar_exit_updates_equity_curve_immediately_not_one_bar_late():
    """Authoritative proof: for every trade that opens AND exits on the same
    bar, the equity_curve entry recorded for that bar must already reflect
    the trade's PnL — not the pre-trade balance, corrected only on the next
    entry. Uses the same wide-wick/tight-SL synthetic setup as the BUG-002
    same-bar-exit test (guaranteed to produce real same-bar exits)."""
    import logging

    import numpy as np

    from backtesting.backtest_engine import BacktestConfig, run_backtest

    logging.disable(logging.CRITICAL)
    try:
        n = 300
        idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        rng = np.random.default_rng(3)
        close = 1.10 + np.cumsum(rng.normal(0, 0.0006, n)) + np.linspace(0, 0.02, n)
        o = np.roll(close, 1)
        o[0] = close[0]
        df = pd.DataFrame(
            {
                "open": o,
                "high": np.maximum(o, close) + 0.004,
                "low": np.minimum(o, close) - 0.004,
                "close": close,
                "volume": 1000.0,
            },
            index=idx,
        )
        cfg = BacktestConfig.from_profile(
            "EURUSD", warmup_bars=60, step_bars=1, sl_atr_multiplier=0.3,
        )
        result = run_backtest(df, cfg)
    finally:
        logging.disable(logging.NOTSET)

    same_bar_exits = [t for t in result.trades if t.exit_bar == t.entry_bar]
    assert len(same_bar_exits) > 0, (
        "test data must produce same-bar exits — if this fails, the "
        "BUG-002 fix regressed and this test can no longer exercise BUG-003"
    )
    for t in same_bar_exits:
        i = t.entry_bar - 1  # the loop's `i` when this trade opened (entry_bar = i+1)
        idx_at_open_iter = i - cfg.warmup_bars + 1
        prev_val = result.equity_curve[idx_at_open_iter - 1]
        entry_val = result.equity_curve[idx_at_open_iter]
        assert entry_val - prev_val == pytest.approx(t.pnl_usd, abs=1e-6), (
            f"trade entry_bar={t.entry_bar} pnl_usd={t.pnl_usd} not reflected "
            f"in its own bar's equity_curve entry (prev={prev_val}, "
            f"this_bar={entry_val}) — the equity point is stale/one-bar-late"
        )


def test_backtest_defaults_match_production_config():
    """Guards against silent drift between the validated system and the
    production system (previous drift: min_rr 3.0 vs 2.0, SL mult 1.5 vs 2.5).

    Uses load_config() (not a raw yaml.safe_load of config.yaml) since
    `risk:` lives in config/risk.yaml and is merged in at load time —
    see utils/helpers.py::load_config."""
    from utils.helpers import load_config

    cfg = load_config()
    bt = BacktestConfig()
    assert bt.min_rr == cfg["risk"]["min_risk_reward"]
    assert bt.sl_atr_multiplier == cfg["risk"]["sl_atr_multiplier"]
    assert bt.risk_per_trade == cfg["risk"]["risk_per_trade_max"]
