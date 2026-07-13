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
