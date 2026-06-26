"""
main.py
----------
IATIS Phase 1 entry point.

Runs the full pipeline end to end on synthetic data:

    data_loader -> data_validator -> timeframe_sync -> regime_detector
    -> strategy engines (parallel) -> confluence (vote + score + contradiction)
    -> risk_engine -> final decision

This is meant to prove the architecture wires together correctly, not to
produce a real trading signal — see README.md for what's real vs. stubbed
in Phase 1.
"""

from __future__ import annotations

import json

from confluence.contradiction_engine import check_contradictions
from confluence.mtf_confirmation import check_mtf_confirmation
from confluence.regime_weights import apply_regime_weights
from confluence.score_calculator import calculate_score, validate_confluence_config
from confluence.voting_system import tally_votes
from core.data_loader import load_data, load_multi_timeframe_with_failover
from core.data_validator import DataValidationError, validate_ohlcv
from core.timeframe_sync import build_multi_timeframe_view
from engines.base_engine import Bias, EngineOutput
from engines.divergence_engine import DivergenceEngine
from engines.ict_engine import ICTEngine
from engines.macro_engine import MacroEngine
from engines.market_structure_engine import MarketStructureEngine
from engines.nnfx_engine import NNFXEngine
from engines.price_action_engine import PriceActionEngine
from engines.quant_engine import QuantEngine
from engines.sentiment_engine import SentimentEngine
from engines.smc_engine import SMCEngine
from engines.wyckoff_engine import WyckoffEngine
from regimes.regime_detector import detect_regime
from research.edge_gate import check_edge_gate
from risk.risk_engine import RiskInputs, evaluate_risk
from storage.decision_log import log_decision
from storage.decision_db import log_decision_db
from storage.engine_tracker import record_engine_votes
from fundamentals.news_risk import assess_news_risk, risk_level_icon
from execution.telegram_bot import send_signal as telegram_send
from utils.helpers import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

_ALL_ENGINES = {
    "smc": SMCEngine,
    "ict": ICTEngine,
    "nnfx": NNFXEngine,
    "price_action": PriceActionEngine,
    "quant": QuantEngine,
    "wyckoff":          WyckoffEngine,
    "divergence":       DivergenceEngine,
    "market_structure": MarketStructureEngine,
    "sentiment":        SentimentEngine,
    "macro": MacroEngine,
}


def build_active_engines(config: dict) -> list:
    enabled = config.get("engines", {}).get("enabled", {})

    # Hard gate: refuse to enable any engine without a proven edge.
    # Raises EdgeNotProvenError loudly rather than silently trading on
    # an unproven idea. See research/edge_gate.py.
    check_edge_gate(enabled)

    engines = []
    for key, cls in _ALL_ENGINES.items():
        if enabled.get(key, False):
            engines.append(cls())
    return engines


def run_pipeline(config: dict) -> dict:
    logger.info("=== IATIS pipeline starting ===")

    # Fail loudly at boot if confluence config is internally inconsistent
    # (e.g. requiring more agreeing engines than are enabled), rather than
    # silently guaranteeing NO_TRADE forever. See confluence/score_calculator.py.
    validate_confluence_config(config)

    # 1. Load data
    source = config.get("data", {}).get("source", "synthetic")
    timeframes = config["data"]["timeframes"]

    if source == "twelve_data":
        # Fetch each timeframe natively from Twelve Data so each level
        # has its full depth (500 bars) rather than being resampled from
        # the lowest timeframe and ending up with only outputsize/N bars.
        # Cost: 1 API credit per timeframe per run (cached within TTL).
        import os
        api_key = (
            config["data"].get("twelve_data_api_key")
            or os.environ.get("TWELVE_DATA_API_KEY", "")
        )
    # Symbols that require Yahoo Finance (404 on Twelve Data Free)
    # Failover handles this automatically, but we need correct YF symbol
    _YF_ONLY = {
        "USOIL": "WTI/USD",   # CL=F on Yahoo
        "US30":  "DJI",       # ^DJI on Yahoo
        "NAS100": "NDX",      # ^IXIC on Yahoo
        "SPX500": "SPX",      # ^GSPC on Yahoo
        "XAGUSD": "XAG/USD",  # SI=F on Yahoo
    }

    internal_sym = config["data"].get("symbol", "EURUSD")
    td_symbol = (
        config["data"].get("twelve_data_symbol")
        or _YF_ONLY.get(internal_sym)
        or (internal_sym[:3] + "/" + internal_sym[3:] if len(internal_sym) == 6 else internal_sym)
    )

    # Fetch with failover: Twelve Data → Yahoo Finance → Alpha Vantage → Finnhub
    try:
        mtf_data = load_multi_timeframe_with_failover(
            td_symbol, timeframes,
            outputsize=config["data"].get("bars_to_load", 500),
        )
        df_base = mtf_data[timeframes[0]]
    except Exception as exc:
        logger.warning(f"Failover fetch failed, trying load_data: {exc}")
        df_base = load_data(config)
        mtf_data = build_multi_timeframe_view(df_base, timeframes)

    # 2b. Validate base timeframe (applies to both paths)
    try:
        validate_ohlcv(df_base)
    except DataValidationError as exc:
        logger.error(f"Data validation failed: {exc}")
        failure_report = {"final_verdict": "NO_TRADE", "reason": f"Data validation failed: {exc}"}
        log_decision(failure_report)
        return failure_report

    # 4. Regime detection
    regime_cfg = config.get("regime", {})
    regime_result = detect_regime(
        df_base,
        atr_period=regime_cfg.get("atr_period", 14),
        lookback=regime_cfg.get("lookback", 100),
    )

    # 5. Run active strategy engines
    active_engines = build_active_engines(config)
    outputs: list[EngineOutput] = [e.safe_analyze(mtf_data) for e in active_engines]

    # Include disabled/not-yet-implemented engines as explicit NEUTRAL
    # entries in the report so the output never hides that they didn't vote.
    disabled = [k for k, v in config.get("engines", {}).get("enabled", {}).items() if not v]

    # 6. Confluence: vote + regime-aware weighted score + contradiction check
    regime_state = regime_result.regime.value if regime_result else "TRENDING"
    regime_volatility = regime_result.volatility if regime_result else "normal"

    # Adjust weights based on current market regime
    base_weights = config["confluence"]["weights"]
    active_weights = apply_regime_weights(base_weights, regime_state, regime_volatility)

    vote_result = tally_votes(outputs)
    score_result = calculate_score(outputs, active_weights)
    contradiction_result = check_contradictions(outputs)

    # A2: Multi-TF Confirmation — D1 trend must align with H1 signal
    mtf_result = check_mtf_confirmation(
        h1_bias=vote_result.winning_bias.value,
        mtf_data=mtf_data,
    )
    # Apply MTF score adjustment
    adjusted_score = round(
        max(0.0, min(100.0, score_result.final_score + mtf_result.score_adjustment)), 2
    )

    min_score = config["confluence"]["min_score_to_trade"]
    min_engines = config["confluence"]["min_engines_agreeing"]

    confluence_fail_reasons: list[str] = []
    if adjusted_score < min_score:
        confluence_fail_reasons.append(
            f"Confluence score {adjusted_score} below minimum required {min_score}"
            + (f" (MTF adjustment: {mtf_result.score_adjustment:+.1f})" if mtf_result.score_adjustment != 0 else "")
        )
    if vote_result.agree_count < min_engines:
        confluence_fail_reasons.append(
            f"Only {vote_result.agree_count} engine(s) agree, minimum required is {min_engines}"
        )
    if contradiction_result.blocked:
        confluence_fail_reasons.extend(contradiction_result.reasons)

    confluence_pass = len(confluence_fail_reasons) == 0

    # 7. Risk gate (only meaningful if confluence passed — but in Phase 1
    #    we still demonstrate the risk engine running on illustrative inputs)
    risk_result = None
    if confluence_pass:
        entry = df_base["close"].iloc[-1]
        # illustrative SL/TP — Phase 3 should derive these from SMC/ATR levels
        atr_estimate = (df_base["high"] - df_base["low"]).tail(14).mean()
        direction = 1 if vote_result.winning_bias == Bias.BULLISH else -1
        stop = entry - direction * atr_estimate * 1.5
        target = entry + direction * atr_estimate * 1.5 * config["risk"]["min_risk_reward"]

        risk_inputs = RiskInputs(
            account_balance=10_000.0,
            entry_price=float(entry),
            stop_loss_price=float(stop),
            take_profit_price=float(target),
            current_open_risk_pct=0.0,
            current_drawdown_pct=0.0,
            correlated_exposure_pct=0.0,
        )
        risk_result = evaluate_risk(risk_inputs, config)

    risk_pass = risk_result.passed if risk_result else False

    # 8. News Risk Gate — veto EXECUTE if high-impact event imminent
    # Only runs when confluence passed (saves API calls on NO_TRADE)
    news_risk = None
    news_blocked = False
    if confluence_pass and config.get("fundamentals", {}).get("news_filter_enabled", True):
        try:
            news_risk = assess_news_risk(
                symbol=config["data"]["symbol"],
                look_ahead_minutes=config.get("fundamentals", {}).get(
                    "blackout_look_ahead_min", 60
                ),
            )
            news_blocked = news_risk.should_block
            if news_blocked:
                logger.info(
                    f"News blackout for {config['data']['symbol']}: "
                    f"score={news_risk.news_risk_score} reason={news_risk.blackout_reason}"
                )
        except Exception as exc:
            logger.warning(f"News risk check failed (non-fatal): {exc}")

    final_verdict = "EXECUTE" if (confluence_pass and risk_pass and not news_blocked) else "NO_TRADE"

    # Build summary
    if final_verdict == "EXECUTE":
        summary = (
            f"EXECUTE {vote_result.winning_bias.value}: "
            f"{vote_result.agree_count}/{score_result.engines_participating} active engines agreed, "
            f"confluence score {adjusted_score}/100, risk checks passed."
        )
        if news_risk:
            summary += f" News risk: {news_risk.risk_level} ({news_risk.news_risk_score:.0f}/100)."
        if mtf_result.score_adjustment > 0:
            summary += f" D1 confirms direction (+{mtf_result.score_adjustment:.0f}pts)."
    elif news_blocked and news_risk:
        summary = f"NO_TRADE: {news_risk.blackout_reason}"
    elif not confluence_pass:
        summary = "NO_TRADE: " + "; ".join(confluence_fail_reasons)
    else:
        summary = "NO_TRADE: risk gate rejected — " + "; ".join(risk_result.reasons if risk_result else [])

    report = {
        "symbol": config["data"]["symbol"],
        "summary": summary,
        "regime": {
            "state": regime_result.regime.value,
            "confidence": regime_result.confidence,
            "volatility": regime_result.volatility,
            "trend_strength": regime_result.trend_strength,
            "notes": regime_result.notes,
        },
        "engine_outputs": [o.to_dict() for o in outputs],
        "disabled_engines": disabled,
        "confluence": {
            "vote": {
                "winning_bias": vote_result.winning_bias.value,
                "agree_count": vote_result.agree_count,
                "total_engines": vote_result.total_engines,
                "breakdown": vote_result.breakdown,
            },
            "score": adjusted_score,
            "raw_score": score_result.final_score,
            "mtf": {
                "d1_bias": mtf_result.d1_bias,
                "d1_adx": mtf_result.d1_adx,
                "adjustment": mtf_result.score_adjustment,
                "confirming": mtf_result.confirming,
                "reason": mtf_result.reason,
            },
            "directional_score": score_result.directional_score,
            "contributions": score_result.contributions,
            "engines_participating": score_result.engines_participating,
            "engines_total": score_result.engines_total,
            "participating_weight_share": score_result.participating_weight_share,
            "regime_weights_applied": active_weights,
            "contradiction": {
                "blocked": contradiction_result.blocked,
                "reasons": contradiction_result.reasons,
            },
            "passed": confluence_pass,
            "fail_reasons": confluence_fail_reasons,
        },
        "risk": {
            "passed": risk_result.passed if risk_result else None,
            "reasons": risk_result.reasons if risk_result else ["Risk gate not evaluated — confluence failed first"],
            "recommended_risk_pct": risk_result.recommended_risk_pct if risk_result else None,
            "position_size_units": risk_result.position_size_units if risk_result else None,
        },
        "news": news_risk.to_dict() if news_risk else {
            "news_risk_score": 0,
            "risk_level": "LOW",
            "blackout_active": False,
            "blackout_reason": "News filter not evaluated",
            "upcoming_events_count": 0,
            "next_high_impact": None,
        },
        # Trade levels — populated only when confluence_pass (risk inputs exist).
        # Derived from ATR-based estimate in Phase 2; Phase 3 will use SMC levels.
        "entry_price": float(entry) if confluence_pass else None,
        "stop_loss": float(stop) if confluence_pass else None,
        "take_profit": float(target) if confluence_pass else None,
        "risk_reward": f"1:{config['risk']['min_risk_reward']:.0f}" if confluence_pass else None,
        "final_verdict": final_verdict,
    }

    logger.info(f"=== IATIS pipeline complete: final_verdict={final_verdict} ===")
    log_decision(report)
    log_decision_db(report)
    record_engine_votes(report)   # per-engine performance attribution

    # Send to Telegram — non-fatal if credentials missing or network fails.
    # Controlled by TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env.
    # Set telegram.enabled: false in config.yaml to disable without
    # touching .env.
    if config.get("telegram", {}).get("enabled", True):
        telegram_send(report)

    return report


def main() -> None:
    config = load_config()
    report = run_pipeline(config)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
