"""
tests/test_phase1.py
------------------------
Smoke tests for Phase 1 architecture. These don't test trading "correctness"
(there's nothing real to grade against yet) — they verify that every piece
of the pipeline produces well-formed output and that the hard invariants
(no-trade-on-bad-data, risk gate authority) actually hold.

Run with: python -m pytest tests/ -v
"""

from __future__ import annotations

import pandas as pd
import pytest

from confluence.contradiction_engine import check_contradictions
from confluence.score_calculator import ConfluenceConfigError, calculate_score, validate_confluence_config
from confluence.voting_system import tally_votes
from core.data_loader import load_synthetic
from core.data_validator import DataValidationError, validate_ohlcv
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias, EngineOutput
from engines.price_action_engine import PriceActionEngine
from engines.smc_engine import SMCEngine
from regimes.regime_detector import Regime, detect_regime
from risk.risk_engine import RiskInputs, evaluate_risk
from utils.helpers import load_config


# ---------- fixtures ----------

@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def synthetic_df():
    return load_synthetic(bars=300, timeframe="H1", seed=42)


# ---------- data layer ----------

def test_synthetic_data_passes_validation(synthetic_df):
    assert validate_ohlcv(synthetic_df) is True


def test_validator_rejects_bad_data():
    df = pd.DataFrame(
        {
            "open": [1.0],
            "high": [0.5],   # high < low — invalid
            "low": [0.9],
            "close": [1.0],
            "volume": [100],
        },
        index=pd.date_range("2026-01-01", periods=1, freq="1h"),
    )
    with pytest.raises(DataValidationError):
        validate_ohlcv(df)


def test_multi_timeframe_view_builds(synthetic_df, config):
    views = build_multi_timeframe_view(synthetic_df, config["data"]["timeframes"])
    for tf in config["data"]["timeframes"]:
        assert tf in views
        assert len(views[tf]) > 0


# ---------- regime ----------

def test_regime_detector_returns_valid_state(synthetic_df):
    result = detect_regime(synthetic_df)
    assert result.regime in list(Regime)
    assert 0.0 <= result.confidence <= 1.0


def test_regime_detector_handles_insufficient_data():
    tiny_df = load_synthetic(bars=5, timeframe="H1", seed=1)
    result = detect_regime(tiny_df, lookback=100)
    assert result.regime == Regime.UNKNOWN


# ---------- engines ----------

def test_smc_engine_returns_valid_output(synthetic_df, config):
    mtf = build_multi_timeframe_view(synthetic_df, config["data"]["timeframes"])
    output = SMCEngine().safe_analyze(mtf)
    assert isinstance(output, EngineOutput)
    assert output.bias in list(Bias)
    assert 0.0 <= output.score <= 100.0


def test_price_action_engine_returns_valid_output(synthetic_df, config):
    mtf = build_multi_timeframe_view(synthetic_df, config["data"]["timeframes"])
    output = PriceActionEngine().safe_analyze(mtf)
    assert isinstance(output, EngineOutput)
    assert output.bias in list(Bias)


def test_engine_abstains_on_crash():
    class BrokenEngine(SMCEngine):
        def analyze(self, mtf_data):
            raise ValueError("simulated failure")

    output = BrokenEngine().safe_analyze({})
    assert output.bias == Bias.NEUTRAL
    assert output.score == 0.0


# ---------- confluence ----------

def test_voting_system_picks_majority():
    outputs = [
        EngineOutput("A", Bias.BULLISH, 70),
        EngineOutput("B", Bias.BULLISH, 60),
        EngineOutput("C", Bias.BEARISH, 80),
    ]
    result = tally_votes(outputs)
    assert result.winning_bias == Bias.BULLISH
    assert result.agree_count == 2


def test_contradiction_engine_blocks_on_disagreement():
    outputs = [
        EngineOutput("A", Bias.BULLISH, 70),
        EngineOutput("B", Bias.BEARISH, 65),
    ]
    result = check_contradictions(outputs)
    assert result.blocked is True


def test_contradiction_engine_passes_on_agreement():
    outputs = [
        EngineOutput("A", Bias.BULLISH, 70),
        EngineOutput("B", Bias.BULLISH, 60),
    ]
    result = check_contradictions(outputs)
    assert result.blocked is False


def test_score_calculator_renormalizes_over_participating_engines():
    # Only SMC voted (score=100). After the re-normalization fix, a single
    # fully-confident engine should be able to reach 100, not be capped at
    # its raw weight share (25) — that cap was the bug that made EXECUTE
    # mathematically unreachable with fewer than ~4 active engines.
    outputs = [EngineOutput("SMC", Bias.BULLISH, 100)]
    weights = {"smc": 0.25, "price_action": 0.20, "nnfx": 0.15, "ict": 0.15, "quant": 0.15, "macro": 0.10}
    result = calculate_score(outputs, weights)
    assert result.final_score == pytest.approx(100.0)
    assert result.engines_participating == 1
    assert result.participating_weight_share == pytest.approx(0.25)


def test_score_calculator_discloses_participation_transparently():
    # Two engines vote, two don't (neither enabled nor present) — final
    # score must be re-normalized over the 2 that voted, while still
    # disclosing exactly how many engines participated vs total weight.
    outputs = [
        EngineOutput("SMC", Bias.BULLISH, 80),
        EngineOutput("PriceAction", Bias.BULLISH, 40),
    ]
    weights = {"smc": 0.25, "price_action": 0.20, "nnfx": 0.15, "ict": 0.15, "quant": 0.15, "macro": 0.10}
    result = calculate_score(outputs, weights)

    expected = (0.25 * 80 + 0.20 * 40) / (0.25 + 0.20)
    assert result.final_score == pytest.approx(round(expected, 2))
    assert result.engines_participating == 2
    assert result.participating_weight_share == pytest.approx(0.45)


# ---------- risk (sovereign layer) ----------

def test_risk_engine_blocks_on_low_rr(config):
    inputs = RiskInputs(
        account_balance=10_000,
        entry_price=1.0850,
        stop_loss_price=1.0840,   # 10 pip risk
        take_profit_price=1.0860,  # 10 pip reward -> RR = 1:1, below min 1:3
    )
    result = evaluate_risk(inputs, config)
    assert result.passed is False


def test_risk_engine_blocks_on_drawdown_stop(config):
    inputs = RiskInputs(
        account_balance=10_000,
        entry_price=1.0850,
        stop_loss_price=1.0800,
        take_profit_price=1.1000,
        current_drawdown_pct=0.20,  # above 15% stop threshold
    )
    result = evaluate_risk(inputs, config)
    assert result.passed is False
    assert any("drawdown" in r.lower() for r in result.reasons)


def test_risk_engine_passes_on_good_trade(config):
    inputs = RiskInputs(
        account_balance=10_000,
        entry_price=1.0850,
        stop_loss_price=1.0820,    # 30 pip risk
        take_profit_price=1.0940,  # 90 pip reward -> RR = 1:3
    )
    result = evaluate_risk(inputs, config)
    assert result.passed is True
    assert result.position_size_units is not None


# ---------- confluence config sanity (prevents EXECUTE-unreachable bug) ----------

def test_validate_confluence_config_passes_when_consistent():
    good_config = {
        "engines": {"enabled": {"smc": True, "price_action": True, "ict": False}},
        "confluence": {"min_engines_agreeing": 2},
    }
    validate_confluence_config(good_config)  # must not raise


def test_validate_confluence_config_rejects_unreachable_threshold():
    # Exactly the scenario flagged in review: min_engines_agreeing=3 but
    # only 2 engines enabled would make EXECUTE mathematically impossible.
    bad_config = {
        "engines": {"enabled": {"smc": True, "price_action": True, "ict": False, "nnfx": False}},
        "confluence": {"min_engines_agreeing": 3},
    }
    with pytest.raises(ConfluenceConfigError, match="unreachable"):
        validate_confluence_config(bad_config)


def test_validate_confluence_config_allows_equal_threshold():
    # min_engines_agreeing == enabled_count is the tight-but-valid edge case.
    edge_config = {
        "engines": {"enabled": {"smc": True, "price_action": True}},
        "confluence": {"min_engines_agreeing": 2, "min_score_to_trade": 0,
                       "weights": {"smc": 0.25, "price_action": 0.20}},
    }
    validate_confluence_config(edge_config)  # must not raise


def test_validate_confluence_config_blocks_unreachable_score():
    """min_score_to_trade=75 with SMC+PA only is unreachable (max≈71.7)."""
    bad_config = {
        "engines": {"enabled": {"smc": True, "price_action": True, "ict": False}},
        "confluence": {
            "min_engines_agreeing": 2,
            "min_score_to_trade": 75,
            "weights": {"smc": 0.25, "price_action": 0.20, "ict": 0.15,
                        "nnfx": 0.15, "quant": 0.15, "macro": 0.10},
        },
    }
    with pytest.raises(ConfluenceConfigError, match="max achievable"):
        validate_confluence_config(bad_config)


def test_validate_confluence_config_allows_achievable_score():
    """min_score_to_trade=60 with SMC+PA is achievable (max≈71.7)."""
    good_config = {
        "engines": {"enabled": {"smc": True, "price_action": True, "ict": False}},
        "confluence": {
            "min_engines_agreeing": 2,
            "min_score_to_trade": 60,
            "weights": {"smc": 0.25, "price_action": 0.20, "ict": 0.15,
                        "nnfx": 0.15, "quant": 0.15, "macro": 0.10},
        },
    }
    validate_confluence_config(good_config)  # must not raise


def test_main_pipeline_raises_before_any_engine_runs_on_bad_config(tmp_path, monkeypatch):
    """Integration-level check: run_pipeline() itself — not just the
    standalone validator — must fail fast on a misconfigured
    min_engines_agreeing, before touching data or engines. This directly
    answers the question "is this actually wired into main.py at
    runtime, or just defined and never called?"
    """
    import main as main_module

    config = load_config()
    config["confluence"]["min_engines_agreeing"] = len(
        [v for v in config["engines"]["enabled"].values() if v]
    ) + 1  # one more than the number of enabled engines -> unreachable

    with pytest.raises(ConfluenceConfigError):
        main_module.run_pipeline(config)
