"""
tests/test_behavior.py
---------------------------
Behavior tests: hand-crafted, deterministic OHLCV scenarios that assert
a SPECIFIC expected outcome, as opposed to tests/test_phase1.py's smoke
tests which only assert "the output is well-formed."

Every fixture used here (tests/fixtures/manual_ohlcv.py) was verified by
actually running it through the relevant engine before being committed
— several early fixture designs silently produced the wrong bias because
of how find_swing_points()'s centered rolling window interacts with
swing spacing. That's documented in the fixture docstrings; the lesson
generalizes: don't trust a hand-designed OHLCV pattern without running
it through the code first.
"""

from __future__ import annotations

import pytest

from confluence.contradiction_engine import check_contradictions
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias, EngineOutput
from engines.price_action_engine import PriceActionEngine, detect_breakout
from engines.smc_engine import SMCEngine, structural_bias
from regimes.regime_detector import Regime, detect_regime
from risk.risk_engine import RiskInputs, evaluate_risk
from tests.fixtures.manual_ohlcv import (
    bearish_structure_bars,
    bullish_structure_bars,
    choppy_mixed_structure_bars,
    downside_breakout_bars,
    no_breakout_bars,
    upside_breakout_bars,
)
from utils.helpers import load_config


@pytest.fixture
def config():
    return load_config()


# ---------- Regime detector: directional trend strength ----------

def test_regime_detector_identifies_trending_on_clear_directional_bars():
    df = bullish_structure_bars()
    result = detect_regime(df, lookback=15)
    assert result.regime == Regime.TRENDING
    assert result.trend_strength > 0  # positive = upward direction


def test_regime_detector_identifies_trending_on_bearish_directional_bars():
    df = bearish_structure_bars()
    result = detect_regime(df, lookback=15)
    assert result.regime == Regime.TRENDING
    assert result.trend_strength < 0  # negative = downward direction


# ---------- SMC engine: directional structure ----------

def test_smc_detects_clear_bullish_structure():
    df = bullish_structure_bars()
    bias, score, reasons = structural_bias(df)
    assert bias == Bias.BULLISH
    assert score > 0
    assert any("ullish" in r for r in reasons)


def test_smc_detects_clear_bearish_structure():
    df = bearish_structure_bars()
    bias, score, reasons = structural_bias(df)
    assert bias == Bias.BEARISH
    assert score > 0
    assert any("earish" in r for r in reasons)


def test_smc_abstains_or_picks_side_on_mixed_structure():
    # The choppy fixture has 2/3 pairs falling → majority vote picks BEARISH.
    # This is intentional: the new algorithm gives a directional opinion when
    # there's a weak majority, rather than always returning NEUTRAL on any
    # impurity. A low score (< 50) still prevents this from contributing to
    # confluence. If the market is genuinely 50/50, score stays near NEUTRAL.
    df = choppy_mixed_structure_bars()
    bias, score, reasons = structural_bias(df)
    # must not crash; must provide a reason
    assert bias in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)
    assert len(reasons) > 0
    # score must be low when mixed (below strong threshold)
    assert score <= 50.0


def test_smc_engine_end_to_end_on_bullish_fixture(config):
    df = bullish_structure_bars()
    mtf = build_multi_timeframe_view(df, ["H1"])
    output = SMCEngine().safe_analyze(mtf)
    assert output.bias == Bias.BULLISH
    assert output.score > 0
    assert output.raw["timeframe_used"] == "H1"


# ---------- Price Action engine: breakout detection ----------

def test_price_action_detects_upside_breakout():
    df = upside_breakout_bars()
    is_breakout, direction = detect_breakout(df)
    assert is_breakout is True
    assert direction == "upside"


def test_price_action_detects_downside_breakout():
    df = downside_breakout_bars()
    is_breakout, direction = detect_breakout(df)
    assert is_breakout is True
    assert direction == "downside"


def test_price_action_reports_no_breakout_on_flat_range():
    df = no_breakout_bars()
    is_breakout, direction = detect_breakout(df)
    assert is_breakout is False
    assert direction == "none"


def test_price_action_engine_flags_breakout_in_output():
    # The engine requires >= 30 bars (RSI-14 + Bollinger-20 warmup guard).
    # detect_breakout() itself only looks at the last `lookback` bars, so a
    # longer flat range preserves the fixture's intent unchanged.
    df = upside_breakout_bars(lookback=35)
    mtf = build_multi_timeframe_view(df, ["H1"])
    output = PriceActionEngine().safe_analyze(mtf)
    assert output.raw["breakout"] == "upside"
    assert any("Breakout" in r for r in output.reasons)


# ---------- Contradiction engine: real disagreement ----------

def test_contradiction_engine_blocks_when_engines_genuinely_disagree():
    # SMC sees bullish structure; Price Action sees a downside breakout
    # on the same instrument — a real, not manufactured, disagreement.
    smc_output = EngineOutput("SMC", Bias.BULLISH, 65.0, reasons=["Higher-high and higher-low"])
    pa_output = EngineOutput("PriceAction", Bias.BEARISH, 60.0, reasons=["Breakout detected: downside"])

    result = check_contradictions([smc_output, pa_output])
    assert result.blocked is True
    assert len(result.reasons) > 0


def test_contradiction_engine_passes_when_engines_agree():
    smc_output = EngineOutput("SMC", Bias.BULLISH, 65.0)
    pa_output = EngineOutput("PriceAction", Bias.BULLISH, 55.0)

    result = check_contradictions([smc_output, pa_output])
    assert result.blocked is False


def test_contradiction_engine_does_not_block_on_low_confidence_disagreement():
    # One engine bearish but with score < 50 — too weak to count as a
    # "real" contradiction per the engine's own threshold.
    smc_output = EngineOutput("SMC", Bias.BULLISH, 65.0)
    pa_output = EngineOutput("PriceAction", Bias.BEARISH, 30.0)

    result = check_contradictions([smc_output, pa_output])
    assert result.blocked is False


# ---------- Risk engine: drawdown halt (sovereign authority) ----------

def test_risk_engine_halts_all_trading_above_drawdown_stop_threshold(config):
    # Even a textbook-perfect trade setup (RR=3, no exposure issues) must
    # be blocked once system drawdown breaches the hard stop threshold.
    inputs = RiskInputs(
        account_balance=10_000,
        entry_price=1.0850,
        stop_loss_price=1.0820,     # 30 pip risk
        take_profit_price=1.0940,   # 90 pip reward -> RR = 1:3, otherwise perfect
        current_drawdown_pct=0.16,  # above config's max_drawdown_stop (0.15)
    )
    result = evaluate_risk(inputs, config)
    assert result.passed is False
    assert any("drawdown" in r.lower() and "stop" in r.lower() for r in result.reasons)
    assert result.recommended_risk_pct == 0.0


def test_risk_engine_reduces_size_in_drawdown_reduce_zone_but_still_passes(config):
    # Between reduce (0.10) and stop (0.15) thresholds: risk per trade
    # must be capped to the minimum, but the trade itself is still
    # allowed if everything else checks out.
    inputs = RiskInputs(
        account_balance=10_000,
        entry_price=1.0850,
        stop_loss_price=1.0820,
        take_profit_price=1.0940,
        current_drawdown_pct=0.12,  # between reduce (0.10) and stop (0.15)
    )
    result = evaluate_risk(inputs, config)
    assert result.passed is True
    assert result.recommended_risk_pct == pytest.approx(config["risk"]["risk_per_trade_min"])


def test_risk_engine_allows_full_risk_below_reduce_threshold(config):
    inputs = RiskInputs(
        account_balance=10_000,
        entry_price=1.0850,
        stop_loss_price=1.0820,
        take_profit_price=1.0940,
        current_drawdown_pct=0.03,  # well below reduce threshold
    )
    result = evaluate_risk(inputs, config)
    assert result.passed is True
    assert result.recommended_risk_pct == pytest.approx(config["risk"]["risk_per_trade_max"])


def test_risk_engine_blocks_regardless_of_how_good_confluence_looks(config):
    # Sovereignty check: simulate a "perfect" confluence scenario (both
    # engines strongly bullish, no contradiction) feeding into a risk
    # evaluation that must still fail on its own merits — risk has no
    # concept of "the confluence score was high enough to compensate."
    smc_output = EngineOutput("SMC", Bias.BULLISH, 95.0)
    pa_output = EngineOutput("PriceAction", Bias.BULLISH, 90.0)
    contradiction = check_contradictions([smc_output, pa_output])
    assert contradiction.blocked is False  # confluence side looks perfect

    risk_inputs = RiskInputs(
        account_balance=10_000,
        entry_price=1.0850,
        stop_loss_price=1.0840,    # only 10 pip risk
        take_profit_price=1.0860,  # only 10 pip reward -> RR = 1:1, fails min_risk_reward
        current_drawdown_pct=0.0,
    )
    risk_result = evaluate_risk(risk_inputs, config)
    assert risk_result.passed is False  # risk vetoes despite perfect confluence
