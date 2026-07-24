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
from dataclasses import dataclass
from typing import Any

from confluence.contradiction_engine import check_contradictions
from confluence.crypto_positioning_modulator import (
    compute_funding_zscore, crypto_positioning_penalty,
)
from confluence.mtf_confirmation import check_mtf_confirmation
from confluence.meta_decision import evaluate_meta_decision
from confluence.regime_weights import apply_regime_weights
from confluence.score_calculator import calculate_score, validate_confluence_config
from confluence.voting_system import informative_weight_share, tally_votes
from core.data_loader import load_data, load_multi_timeframe_with_failover
from core.data_validator import DataValidationError, validate_ohlcv
from core.market_quality import assess_market_quality, MQS_THRESHOLD_FAIR, MQS_THRESHOLD_GOOD
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
from risk.live_portfolio_state import compute_portfolio_state
from risk.risk_engine import RiskInputs, evaluate_risk
from storage.decision_log import log_decision
from storage.decision_db import log_decision_db, execute_alert_exists_for_bar
from storage.engine_tracker import record_engine_votes
from storage.outcome_tracker import log_signal as log_outcome_signal
from storage.experience_db import record_experience, find_similar
from fundamentals.news_risk import assess_news_risk, risk_level_icon
from execution.telegram_bot import send_signal as telegram_send
from utils.helpers import load_config
from utils.logger import get_logger
from utils.provenance import build_provenance

logger = get_logger(__name__)

# One retry after a short pause covers transient network blips to the D1
# Worker; anything longer is an outage and must not hold up the decision.
_STORE_RETRY_DELAY_S = 2.0


def _safe_store(what: str, fn, *args) -> bool:
    """Run a persistence call so that its failure can never abort the
    pipeline run. The decision and its Telegram delivery are the product;
    storage (D1 behind a Cloudflare Worker, or the local JSONL) failing is
    an ops incident to log loudly, not a reason to lose the signal.

    Returns True if the write eventually succeeded.
    """
    import time as _time

    for attempt in (1, 2):
        try:
            fn(*args)
            return True
        except Exception as exc:
            if attempt == 1:
                logger.warning(f"{what} failed (attempt 1/2, retrying): {exc}")
                _time.sleep(_STORE_RETRY_DELAY_S)
            else:
                logger.error(
                    f"{what} failed after retry — decision NOT persisted "
                    f"there, pipeline continues: {exc}"
                )
    return False


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


def decision_timeframe(config: dict) -> str:
    """The timeframe engine votes are computed on: data.timeframes[0]
    (D1 in the D1-primary setup; H4/H1 stay in mtf_data as auxiliary
    context for engines that want structure/timing detail)."""
    tfs = config.get("data", {}).get("timeframes") or ["H1"]
    return tfs[0]


def build_active_engines(config: dict) -> list:
    enabled = config.get("engines", {}).get("enabled", {})

    # Hard gate: refuse to enable any engine without a proven edge.
    # Raises EdgeNotProvenError loudly rather than silently trading on
    # an unproven idea. See research/edge_gate.py.
    check_edge_gate(enabled)

    dtf = decision_timeframe(config)
    symbol = config.get("data", {}).get("symbol", "")
    smc_full_spec = bool(config.get("engines", {}).get("smc_full_spec", False))
    engines = []
    for key, cls in _ALL_ENGINES.items():
        if enabled.get(key, False):
            engine = cls()
            engine.decision_tf = dtf
            # Symbol context — SentimentEngine keys its COT cache on this;
            # it previously defaulted to "UNKNOWN" so COT could never load.
            engine._symbol = symbol
            if key == "smc":
                # H017: full-spec SMC (OB+FVG+BOS/CHoCH as internal
                # confluence) — off by default until the A/B justifies it.
                engine.full_spec = smc_full_spec
            engines.append(engine)
    return engines


# ---------------------------------------------------------------------------
# Pipeline stages (audit item H6). run_pipeline() was a single function at
# cyclomatic complexity 71; each stage below is the same code operating on
# the same inputs, extracted so gates can be reasoned about and tested
# individually. run_pipeline() at the bottom is the orchestrator.
# ---------------------------------------------------------------------------

# Symbols that require Yahoo Finance (404 on Twelve Data Free).
# Failover handles this automatically, but we need the correct YF symbol.
_YF_ONLY = {
    "USOIL": "WTI/USD",   # CL=F on Yahoo
    "US30":  "DJI",       # ^DJI on Yahoo
    "NAS100": "NDX",      # ^IXIC on Yahoo
    "SPX500": "SPX",      # ^GSPC on Yahoo
    "XAGUSD": "XAG/USD",  # SI=F on Yahoo
}


def _symbol_config(config: dict) -> dict:
    """Per-symbol overrides (min_score, rr, regime_filter) from
    data.twelve_data_symbols for the currently configured symbol."""
    symbol = config["data"].get("symbol", "")
    return next(
        (s for s in config.get("data", {}).get("twelve_data_symbols", [])
         if s.get("internal") == symbol),
        {},
    )


def _load_market_data(config: dict, timeframes: list[str]):
    """Stage 1: fetch (or inject) OHLCV and build the multi-timeframe view.

    Returns (df_base, mtf_data)."""
    internal_sym = config["data"].get("symbol", "EURUSD")
    td_symbol = (
        config["data"].get("twelve_data_symbol")
        or _YF_ONLY.get(internal_sym)
        or (internal_sym[:3] + "/" + internal_sym[3:] if len(internal_sym) == 6 else internal_sym)
    )

    # Backtest injection: skip all API calls, use pre-sliced DataFrame directly
    if config["data"].get("source") == "injected":
        # Replay injection (research/replay.py): the EXACT per-timeframe
        # frames a past live run saw. Live TFs may come from independent
        # fetches, so rebuilding the view from one base frame would not be
        # faithful — inject the whole dict.
        injected_mtf = config["data"].get("_injected_mtf")
        if injected_mtf:
            return injected_mtf[timeframes[0]], injected_mtf
        injected_df = config["data"].get("_injected_df")
        if injected_df is not None and len(injected_df) > 0:
            df_base = injected_df.copy()
        else:
            df_base = load_data(config)
        return df_base, build_multi_timeframe_view(df_base, timeframes)

    # Fetch with asset-class-aware failover (core/data_providers.py):
    # crypto → ccxt/Binance first (native H4/D1); fx/metals/indices →
    # broker feed (cTrader) first when configured; then Twelve Data →
    # Yahoo → Alpha Vantage → Finnhub. Chains overridable per class via
    # config.yaml data.provider_chains.
    try:
        from core.data_providers import provider_chain_for
        chain = provider_chain_for(
            internal_sym, config["data"].get("provider_chains")
        )
        mtf_data = load_multi_timeframe_with_failover(
            td_symbol, timeframes,
            outputsize=config["data"].get("bars_to_load", 500),
            providers=chain,
        )
        return mtf_data[timeframes[0]], mtf_data
    except Exception as exc:
        logger.warning(f"Failover fetch failed, trying load_data: {exc}")
        df_base = load_data(config)
        return df_base, build_multi_timeframe_view(df_base, timeframes)


def _market_quality_gate(config: dict, df_base) -> tuple[Any, dict | None]:
    """Stage 2: Market Quality Score — gate before running 9 engines.

    Returns (mqs_result, report): a non-None report means the gate
    rejected the session and the pipeline should stop with that NO_TRADE."""
    mq_cfg = config.get("market_quality", {})
    # Replay determinism (research/replay.py): MQS session/day scoring uses
    # wall-clock time — a replayed decision must be scored at the ORIGINAL
    # decision time, not at whatever hour the replay happens to run.
    replay_now = None
    _rn = config.get("system", {}).get("_replay_now")
    if _rn:
        from datetime import datetime as _dt
        replay_now = _dt.fromisoformat(_rn)
    mqs_result = assess_market_quality(
        df=df_base,
        symbol=config["data"].get("symbol", ""),
        now=replay_now,
        timeframe=decision_timeframe(config),
        threshold_good=mq_cfg.get("threshold_good", MQS_THRESHOLD_GOOD),
        threshold_fair=mq_cfg.get("threshold_fair", MQS_THRESHOLD_FAIR),
    )
    features_cfg = config.get("features", {})
    if features_cfg.get("market_quality_gate", True) and not mqs_result.should_trade:
        return mqs_result, {
            "final_verdict": "NO_TRADE",
            "symbol": config["data"].get("symbol", ""),
            "summary": f"NO_TRADE: Market Quality Score={mqs_result.score:.0f}/100 ({mqs_result.grade}) — {'; '.join(mqs_result.reasons)}",
            "market_quality": mqs_result.to_dict(),
            # current_price must be on EVERY report — the scheduler's
            # auto-close evaluates open outcomes from it, and MQS-blocked
            # runs (weekends, dead sessions) would otherwise leave open
            # trades unpriced for that whole tick.
            "current_price": float(df_base["close"].iloc[-1]),
            # Decision-bar range for intrabar TP/SL detection (open-outcome
            # hygiene): a level touched inside the bar and retraced is
            # invisible to a close-only check.
            "bar_high": float(df_base["high"].iloc[-1]),
            "bar_low": float(df_base["low"].iloc[-1]),
            "bar_time": str(df_base.index[-1]),
        }
    return mqs_result, None


@dataclass
class _ConfluenceEval:
    """Everything the confluence stage decided, for the stages after it."""
    vote_result: Any
    score_result: Any
    contradiction_result: Any
    mtf_result: Any
    active_weights: dict
    adjusted_score: float
    fail_reasons: list[str]
    passed: bool
    symbol_cfg: dict
    informative_weight_share: float = 1.0
    positioning_result: Any = None


def _crypto_positioning_adjustment(config: dict, winning_bias) -> tuple[float, Any]:
    """H019 (research/results/registry.json, feasibility resolved
    2026-07-24) — engines.crypto_positioning_modulator, default FALSE.
    Only ever applies to BTCUSD/ETHUSD, and only ever REDUCES the score
    (squeeze-risk penalty from crowded funding-rate positioning, never a
    boost — confluence/crypto_positioning_modulator.py).

    Reads an INJECTED context only — config["data"]["_crypto_positioning_
    context"]: {"funding_rate_history": [...], "current_funding_rate":
    float, "fear_greed_value": int | None}. Every value in it must already
    be strictly prior to the decision bar — that causal look-ahead guard
    is the CALLER's responsibility (the backtest A/B harness), not this
    function's; it has no timestamps to check. No live data source exists
    yet, so this stays inert in live trading (no context is ever injected
    outside a backtest run) regardless of the flag, until a live funding-
    rate/Fear-Greed feed is built AND this hypothesis passes its
    pre-registered decision rule (CLAUDE.md rule 6)."""
    if not config.get("engines", {}).get("crypto_positioning_modulator", False):
        return 0.0, None
    symbol = config.get("data", {}).get("symbol", "")
    if symbol not in ("BTCUSD", "ETHUSD"):
        return 0.0, None
    ctx = config.get("data", {}).get("_crypto_positioning_context")
    if not ctx:
        return 0.0, None
    z = compute_funding_zscore(
        ctx.get("funding_rate_history", []), ctx.get("current_funding_rate", 0.0),
    )
    result = crypto_positioning_penalty(z, ctx.get("fear_greed_value"), winning_bias.value)
    return result.score_adjustment, result


def _evaluate_confluence(
    config: dict,
    outputs: list[EngineOutput],
    mtf_data: dict,
    regime_state: str,
    regime_volatility: str,
) -> _ConfluenceEval:
    """Stage 3: vote + regime-aware weighted score + contradiction check +
    MTF confirmation."""
    # Adjust weights based on current market regime
    base_weights = config["confluence"]["weights"]
    active_weights = apply_regime_weights(base_weights, regime_state, regime_volatility)

    vote_result = tally_votes(outputs, active_weights)
    # winning_bias passed explicitly: the score always describes the side
    # the vote chose — never the opposite side (audit Axis 6 unification).
    score_result = calculate_score(outputs, active_weights, vote_result.winning_bias)
    contradiction_result = check_contradictions(outputs)

    # A2: Multi-TF Confirmation — D1 trend must align with the signal.
    # When the decision timeframe IS D1, this is skipped inside (comparing
    # D1 with itself would just self-confirm every vote by +8 points).
    mtf_result = check_mtf_confirmation(
        h1_bias=vote_result.winning_bias.value,
        mtf_data=mtf_data,
        signal_tf=decision_timeframe(config),
    )
    positioning_adj, positioning_result = _crypto_positioning_adjustment(
        config, vote_result.winning_bias,
    )
    adjusted_score = round(
        max(0.0, min(100.0,
            score_result.final_score + mtf_result.score_adjustment + positioning_adj)),
        2,
    )

    # Per-symbol min_score override
    symbol_cfg = _symbol_config(config)
    min_score = symbol_cfg.get("min_score") or config["confluence"]["min_score_to_trade"]
    min_engines = config["confluence"]["min_engines_agreeing"]

    fail_reasons: list[str] = []
    if adjusted_score < min_score:
        fail_reasons.append(
            f"Confluence score {adjusted_score} below minimum required {min_score}"
            + (f" (MTF adjustment: {mtf_result.score_adjustment:+.1f})" if mtf_result.score_adjustment != 0 else "")
        )
    if vote_result.agree_count < min_engines:
        fail_reasons.append(
            f"Only {vote_result.agree_count} engine(s) agree, minimum required is {min_engines}"
        )

    # Axis-8 gate (philosophy audit): confluence requires a SPEAKING panel.
    # A quorum met while most of the enabled weight is mute (NEUTRAL or
    # below the conviction threshold) is co-signature, not confluence.
    min_info_share = config["confluence"].get("min_informative_weight_share", 0.0)
    info_share = informative_weight_share(outputs, active_weights)
    if min_info_share > 0 and info_share < min_info_share:
        fail_reasons.append(
            f"Only {info_share:.0%} of enabled engine weight voted informatively "
            f"(minimum {min_info_share:.0%}) — panel mostly mute"
        )
    if contradiction_result.blocked:
        fail_reasons.extend(contradiction_result.reasons)

    # H013 reversal veto removed from the live path (2026-07-23,
    # PHILOSOPHY_AUDIT_2026-07.md:140): needs 2+ of {Divergence, Wyckoff,
    # Sentiment} active to do anything, but config/engines.yaml only
    # enables wyckoff — structurally a zero-behavioral-change no-op, so
    # removing it here is not a live-decision change (CLAUDE.md rule 6
    # doesn't apply). check_reversal_veto() itself stays in
    # confluence/reversal_veto.py for backtesting/backtest_engine.py and
    # scripts/engine_ablation.py, which still A/B it in research.

    return _ConfluenceEval(
        vote_result=vote_result,
        score_result=score_result,
        contradiction_result=contradiction_result,
        mtf_result=mtf_result,
        active_weights=active_weights,
        adjusted_score=adjusted_score,
        fail_reasons=fail_reasons,
        passed=len(fail_reasons) == 0,
        symbol_cfg=symbol_cfg,
        informative_weight_share=round(info_share, 3),
        positioning_result=positioning_result,
    )


def _risk_gate(config: dict, df_base, conf: _ConfluenceEval):
    """Stage 4: sovereign risk veto on live portfolio state.

    Returns (risk_result, entry, stop, target) — all None when confluence
    already failed (risk inputs don't exist without a direction)."""
    if not conf.passed:
        return None, None, None, None

    entry = df_base["close"].iloc[-1]
    # range_atr, NOT true ATR — the SL/TP distances the whole validated
    # system (and rule 6's frozen thresholds) are built on. See
    # utils/indicators.py before ever "upgrading" this.
    from utils.indicators import range_atr
    atr_estimate = range_atr(df_base, 14)
    direction = 1 if conf.vote_result.winning_bias == Bias.BULLISH else -1

    # Per-symbol RR override
    symbol_rr = conf.symbol_cfg.get("rr") or config["risk"]["min_risk_reward"]
    sl_multiplier = config.get("risk", {}).get("sl_atr_multiplier", 2.5)
    stop = entry - direction * atr_estimate * sl_multiplier
    target = entry + direction * atr_estimate * sl_multiplier * symbol_rr

    # Live portfolio state (drawdown / open risk / correlated exposure)
    # derived from the outcomes DB — previously hardcoded zeros, which
    # silently disabled the drawdown stop and exposure caps.
    portfolio_state = compute_portfolio_state(
        symbol=config["data"].get("symbol", ""),
        config=config,
    )
    risk_inputs = RiskInputs(
        account_balance=portfolio_state.account_balance,
        entry_price=float(entry),
        stop_loss_price=float(stop),
        take_profit_price=float(target),
        current_open_risk_pct=portfolio_state.current_open_risk_pct,
        current_drawdown_pct=portfolio_state.current_drawdown_pct,
        correlated_exposure_pct=portfolio_state.correlated_exposure_pct,
        symbol_already_open=config["data"].get("symbol", "") in portfolio_state.open_symbols,
    )
    return evaluate_risk(risk_inputs, config), entry, stop, target


def _news_gate(config: dict, confluence_passed: bool):
    """Stage 5: news blackout veto. Only runs when confluence passed
    (saves API calls on NO_TRADE). Returns (news_risk, news_blocked)."""
    news_risk = None
    news_blocked = False
    if confluence_passed and config.get("fundamentals", {}).get("news_filter_enabled", True):
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
    return news_risk, news_blocked


def _final_verdict(
    config: dict,
    conf: _ConfluenceEval,
    risk_pass: bool,
    news_blocked: bool,
    regime_state: str,
    mqs_result: Any,
    outputs: list[EngineOutput],
):
    """Stage 6: combine the gates, apply the per-symbol regime filter and
    the Meta Decision layer. Returns (final_verdict, meta, downgrade_reason).

    downgrade_reason is non-None exactly when an EXECUTE was downgraded to
    NO_TRADE by this stage (regime filter or Meta BLOCK) — previously these
    downgrades left no fail_reason in the decision record, so they were
    unauditable (philosophy audit addendum, Axis 1 check 1.3)."""
    final_verdict = "EXECUTE" if (conf.passed and risk_pass and not news_blocked) else "NO_TRADE"
    downgrade_reason: str | None = None

    # Apply per-symbol regime filter (Tier 2 symbols: TRENDING only)
    symbol = config["data"].get("symbol", "")
    if final_verdict == "EXECUTE" and conf.symbol_cfg.get("regime_filter"):
        required_regime = conf.symbol_cfg["regime_filter"]
        if regime_state != required_regime:
            final_verdict = "NO_TRADE"
            downgrade_reason = (
                f"Per-symbol regime filter: {symbol} requires {required_regime}, "
                f"got {regime_state}"
            )
            logger.info(f"{downgrade_reason} → NO_TRADE")

    # H024 — global HARD regime gate (research, pre-registered, FROZEN).
    # Ships OFF: features.regime_gate default false, so this block is inert
    # both live and across every existing test. When enabled it emits NO_TRADE
    # for any decision whose detected regime is in the blocked set (pre-registered
    # default: RANGING → trade only TRENDING). This is the GLOBAL counterpart to
    # the per-symbol regime_filter above; unlike regime_weights it BLOCKS rather
    # than reweights. Entries/exits are unchanged — only the take/skip flips —
    # so an H024 A/B run attributes any ΔPF solely to the gate. Never touches
    # live decisions until the forward-demo milestone (CLAUDE.md rule 6).
    features_cfg = config.get("features", {})
    if final_verdict == "EXECUTE" and features_cfg.get("regime_gate", False):
        blocked_regimes = features_cfg.get("regime_gate_block", ["RANGING"])
        if regime_state in blocked_regimes:
            final_verdict = "NO_TRADE"
            downgrade_reason = (
                f"H024 regime gate: {regime_state} is in blocked set "
                f"{list(blocked_regimes)}"
            )
            logger.info(f"{downgrade_reason} → NO_TRADE")

    # Meta Decision Layer — confidence + stability + engine contributions.
    # H103 (research/results/registry.json, PLANNED): tests whether this
    # gate's downgrade is worth keeping, or double-counts information
    # min_score_to_trade/min_engines_agreeing already gated. meta is ALWAYS
    # computed (for logging/comparison in either arm); features.
    # meta_decision_gate (default TRUE — this preserves exactly today's
    # live behavior) controls only whether a BLOCK verdict is allowed to
    # downgrade the decision. Default stays true until H103 resolves and
    # is promoted through the normal process (CLAUDE.md rule 6) — this is
    # NOT a new default-off mechanism like H024/H019, it's an A/B toggle on
    # an EXISTING live gate, so removing it is the one-sided change that
    # needs evidence, not adding it.
    meta_decision_gate = features_cfg.get("meta_decision_gate", True)
    meta = None
    if final_verdict == "EXECUTE":
        try:
            meta = evaluate_meta_decision(
                outputs=outputs,
                weights=conf.active_weights,
                adjusted_score=conf.adjusted_score,
                vote_result=conf.vote_result,
                report_context={"market_quality": mqs_result.to_dict()},
            )
            # Meta can downgrade EXECUTE to NO_TRADE if confidence too low
            if meta.verdict == "BLOCK" and meta_decision_gate:
                final_verdict = "NO_TRADE"
                downgrade_reason = f"Meta Decision blocked: {meta.reason}"
                logger.info(f"Meta Decision BLOCKED: {meta.reason}")
        except Exception as exc:
            logger.warning(f"Meta Decision failed (non-fatal): {exc}")

    return final_verdict, meta, downgrade_reason


def _build_report(
    config: dict,
    df_base,
    regime_result,
    outputs: list[EngineOutput],
    disabled: list[str],
    conf: _ConfluenceEval,
    risk_result,
    news_risk,
    news_blocked: bool,
    entry,
    stop,
    target,
    final_verdict: str,
    meta,
    downgrade_reason: str | None = None,
    mtf_data: dict | None = None,
) -> dict:
    """Stage 7: assemble the human summary and the full decision report."""
    vote_result = conf.vote_result
    score_result = conf.score_result
    mtf_result = conf.mtf_result

    if final_verdict == "EXECUTE":
        summary = (
            f"EXECUTE {vote_result.winning_bias.value}: "
            f"{vote_result.agree_count}/{score_result.engines_participating} active engines agreed, "
            f"confluence score {conf.adjusted_score}/100, risk checks passed."
        )
        if news_risk:
            summary += f" News risk: {news_risk.risk_level} ({news_risk.news_risk_score:.0f}/100)."
        if mtf_result.score_adjustment > 0:
            summary += f" D1 confirms direction (+{mtf_result.score_adjustment:.0f}pts)."
    elif news_blocked and news_risk:
        summary = f"NO_TRADE: {news_risk.blackout_reason}"
    elif not conf.passed:
        summary = "NO_TRADE: " + "; ".join(conf.fail_reasons)
    elif downgrade_reason:
        # Regime-filter / Meta downgrade — previously this fell through to
        # the risk branch below and produced the actively misleading
        # "risk gate rejected — All risk checks passed".
        summary = "NO_TRADE: " + downgrade_reason
    else:
        summary = "NO_TRADE: risk gate rejected — " + "; ".join(risk_result.reasons if risk_result else [])

    return {
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
            "score": conf.adjusted_score,
            "raw_score": score_result.final_score,
            "mtf": {
                "d1_bias": mtf_result.d1_bias,
                "d1_adx": mtf_result.d1_adx,
                "adjustment": mtf_result.score_adjustment,
                "confirming": mtf_result.confirming,
                "reason": mtf_result.reason,
            },
            # H019 — engines.crypto_positioning_modulator (default FALSE).
            # None whenever the flag is off, symbol isn't BTCUSD/ETHUSD, or
            # no context was injected (i.e. always None in live trading
            # today — see main._crypto_positioning_adjustment).
            "crypto_positioning": (
                {
                    "adjustment": conf.positioning_result.score_adjustment,
                    "funding_z_score": conf.positioning_result.funding_z_score,
                    "fear_greed_value": conf.positioning_result.fear_greed_value,
                    "reason": conf.positioning_result.reason,
                } if conf.positioning_result else None
            ),
            "directional_score": score_result.directional_score,
            "contributions": score_result.contributions,
            "engines_participating": score_result.engines_participating,
            "engines_total": score_result.engines_total,
            "participating_weight_share": score_result.participating_weight_share,
            "informative_weight_share": conf.informative_weight_share,
            "regime_weights_applied": conf.active_weights,
            "contradiction": {
                "blocked": conf.contradiction_result.blocked,
                "reasons": conf.contradiction_result.reasons,
            },
            "passed": conf.passed,
            "fail_reasons": conf.fail_reasons,
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
        # Which provider actually served each timeframe this run (set by
        # fetch_multi_timeframe_with_failover via df.attrs) — data-layer
        # transparency for the dashboard and per-decision auditability.
        "data_providers": {tf: str(df.attrs.get("provider", "unknown"))
                           for tf, df in (mtf_data or {}).items()},
        # Provenance fingerprints (utils/provenance.py): the exact code
        # version, config hash, and per-TF data version this decision was
        # made under. What makes rule 6 ("never change mid-sample")
        # verifiable instead of promised — persisted by decision_db.
        "provenance": build_provenance(config, mtf_data),
        # Latest close — populated on EVERY report (EXECUTE and NO_TRADE)
        # so the scheduler's auto-close can evaluate open outcomes even
        # when this run produced no trade.
        "current_price": float(df_base["close"].iloc[-1]),
        # Decision-bar range for intrabar TP/SL detection (open-outcome
        # hygiene, philosophy audit priority 4).
        "bar_high": float(df_base["high"].iloc[-1]),
        "bar_low": float(df_base["low"].iloc[-1]),
        # Timestamp of the decision bar (last closed candle of the decision
        # timeframe). Used to deduplicate alerts: with a D1 decision TF and
        # a 2-hourly scheduler, the same daily bar is re-evaluated ~12
        # times — the signal should only be sent once per bar.
        "bar_time": str(df_base.index[-1]),
        # Trade levels — populated only when confluence passed (risk inputs exist).
        # Derived from ATR-based estimate in Phase 2; Phase 3 will use SMC levels.
        "entry_price": float(entry) if conf.passed else None,
        "stop_loss": float(stop) if conf.passed else None,
        "take_profit": float(target) if conf.passed else None,
        "risk_reward": f"1:{config['risk']['min_risk_reward']:.0f}" if conf.passed else None,
        "final_verdict": final_verdict,
        "meta_decision": meta.to_dict() if meta else None,
        # Non-None exactly when an EXECUTE was downgraded post-gates
        # (regime filter / Meta BLOCK) — decision_db persists this as the
        # fail_reason so downgrades are auditable (audit Axis 1.3).
        "downgrade_reason": downgrade_reason,
    }


def run_pipeline(config: dict) -> dict:
    logger.info("=== IATIS pipeline starting ===")

    # Replay/backtest runs must never touch the live store — not at the end of
    # the pipeline (handled below) and not on the early-exit paths (validation
    # failure, MQS gate) either. Writing there both hammered an unreachable D1
    # with 2s retries per step AND polluted the live decisions.jsonl with
    # thousands of backtest rows.
    _sys_cfg = config.get("system", {})
    _no_persist = bool(_sys_cfg.get("replay_mode") or _sys_cfg.get("backtest_mode"))

    # Fail loudly at boot if confluence config is internally inconsistent
    # (e.g. requiring more agreeing engines than are enabled), rather than
    # silently guaranteeing NO_TRADE forever. See confluence/score_calculator.py.
    validate_confluence_config(config)

    # 1. Load data
    timeframes = config["data"]["timeframes"]
    df_base, mtf_data = _load_market_data(config, timeframes)

    # 2a. Data-depth guard: NNFX needs 210+ decision-TF bars (EMA200) and
    # the MTF gate needs 50+ D1 bars — below these they degrade SILENTLY
    # (NEUTRAL vote / zero adjustment), which starved live decisions for
    # weeks before the philosophy audit caught it. Warn loudly instead.
    dtf = decision_timeframe(config)
    dtf_bars = len(mtf_data.get(dtf, ()))
    d1_bars = len(mtf_data.get("D1", ()))
    if dtf_bars < 210:
        logger.warning(
            f"DATA STARVATION: only {dtf_bars} {dtf} bars (<210) — NNFX will "
            f"vote NEUTRAL on every run. Raise data.bars_to_load "
            f"(currently {config['data'].get('bars_to_load')})."
        )
    if "D1" in timeframes and dtf != "D1" and d1_bars < 50:
        logger.warning(
            f"DATA STARVATION: only {d1_bars} D1 bars (<50) — the MTF "
            f"confirmation gate is inert. Raise data.bars_to_load."
        )

    # 2b. Validate base timeframe (applies to both paths)
    try:
        validate_ohlcv(df_base)
    except DataValidationError as exc:
        logger.error(f"Data validation failed: {exc}")
        failure_report = {"final_verdict": "NO_TRADE", "reason": f"Data validation failed: {exc}"}
        if not _no_persist:
            _safe_store("decision_log (validation failure)", log_decision, failure_report)
        return failure_report

    # 3. Market Quality Score — gate before running 9 engines
    mqs_result, mqs_report = _market_quality_gate(config, df_base)
    if mqs_report is not None:
        if not _no_persist:
            _safe_store("decision_log (MQS gate)", log_decision, mqs_report)
            _safe_store("decision_db (MQS gate)", log_decision_db, mqs_report)
        return mqs_report

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

    # 6. Confluence: vote + regime-aware weighted score + contradiction +
    #    MTF confirmation + H013 reversal veto
    regime_state = regime_result.regime.value if regime_result else "TRENDING"
    regime_volatility = regime_result.volatility if regime_result else "normal"
    conf = _evaluate_confluence(config, outputs, mtf_data, regime_state, regime_volatility)

    # 7. Risk gate (sovereign veto on live portfolio state)
    risk_result, entry, stop, target = _risk_gate(config, df_base, conf)
    risk_pass = risk_result.passed if risk_result else False

    # 8. News Risk Gate — veto EXECUTE if high-impact event imminent
    news_risk, news_blocked = _news_gate(config, conf.passed)

    # 9. Final verdict: combine gates + per-symbol regime filter + Meta Decision
    final_verdict, meta, downgrade_reason = _final_verdict(
        config, conf, risk_pass, news_blocked, regime_state, mqs_result, outputs
    )

    # 10. Assemble summary + full decision report
    report = _build_report(
        config, df_base, regime_result, outputs, disabled, conf,
        risk_result, news_risk, news_blocked, entry, stop, target,
        final_verdict, meta, downgrade_reason, mtf_data,
    )

    logger.info(f"=== IATIS pipeline complete: final_verdict={final_verdict} ===")

    # Replay mode (research/replay.py): the pipeline ran purely to compare
    # its output against a stored decision. Nothing below this line may
    # happen — no persistence, no outcome logging, no alerts. Absent flag
    # (all production/normal paths) = behavior unchanged.
    #
    # backtest_mode does the same for offline backtests. A backtest steps
    # run_pipeline() thousands of times, and each live-persistence call retries
    # with a 2s backoff on failure — against an unreachable D1 that turns an
    # ~0.1s step into ~4s and a full-universe backtest into an 8-hour hang.
    # Backtests must never write to the live decision store anyway. (Kept
    # distinct from source=="injected", which the replay-window generator also
    # uses and which DOES need this block to run.)
    if _no_persist:
        logger.info("REPLAY/BACKTEST MODE: skipping persistence, outcome logging and alerts")
        return report

    # Replay window capture: persist the exact per-TF input frames + config
    # so this decision can be re-run bit-for-bit later (gap analysis S2).
    # Default: EXECUTE decisions only (~1.6% of runs); 'all'/'off' via
    # system.persist_replay_windows.
    _rw_mode = str(config.get("system", {}).get("persist_replay_windows", "execute")).lower()
    if _rw_mode == "all" or (_rw_mode == "execute" and final_verdict == "EXECUTE"):
        from research.replay import persist_window
        _safe_store("replay_window", persist_window, report, mtf_data, config)

    # Alert dedup must be evaluated BEFORE this decision is persisted —
    # afterwards the query would always find the row we just wrote.
    already_alerted = (
        final_verdict == "EXECUTE"
        and execute_alert_exists_for_bar(report.get("symbol", ""), report.get("bar_time", ""))
    )

    _safe_store("decision_log", log_decision, report)
    _safe_store("decision_db", log_decision_db, report)
    _safe_store("engine_tracker", record_engine_votes, report)

    # Shadow book — counterfactual for every rejected DIRECTIONAL decision
    # (philosophy audit: the gates rejected ~98% of candidates with no
    # outcome tracking, making threshold calibration permanently blind).
    if final_verdict == "NO_TRADE":
        from storage.shadow_book import log_shadow_signal
        _safe_store("shadow_book", log_shadow_signal, report, config)

    # Experience Database — record EVERY decision (EXECUTE + NO_TRADE)
    # This is the foundation for MROS: learning from every decision
    try:
        exp_id = record_experience(report)

        # For EXECUTE signals: check historical similarity (Market Memory)
        if final_verdict == "EXECUTE":
            similar = find_similar(report)
            if isinstance(similar, dict) and similar.get("similar_count", 0) >= 5:
                report["historical_similarity"] = {
                    "matches": similar["similar_count"],
                    "historical_wr": similar["historical_wr"],
                    "recommendation": similar["recommendation"],
                }
                logger.info(
                    f"Market Memory: {similar['similar_count']} similar situations, "
                    f"historical WR={similar['historical_wr']}% → {similar['recommendation']}"
                )
    except Exception as exc:
        logger.debug(f"Experience DB recording skipped: {exc}")

    # Log EXECUTE signals to outcome tracker (for calibration + regime matrix)
    if final_verdict == "EXECUTE":
        try:
            log_outcome_signal(report)
        except Exception as exc:
            logger.warning(f"Outcome tracker log failed (non-fatal): {exc}")

    # Telegram: EXECUTE signals only — no NO_TRADE spam
    # 9 symbols × 12 runs/day = 108 msgs/day if all sent → filter to EXECUTE only
    if config.get("telegram", {}).get("enabled", True):
        if final_verdict == "EXECUTE" and already_alerted:
            logger.info(
                f"Telegram dedup: EXECUTE for {report.get('symbol')} on bar "
                f"{report.get('bar_time')} already alerted this bar — skipping resend"
            )
        elif final_verdict == "EXECUTE":
            telegram_send(report)
        else:
            logger.debug(
                f"Telegram skipped ({final_verdict}) for "
                f"{config['data'].get('symbol','?')} — only EXECUTE signals sent"
            )

    return report


def main() -> None:
    config = load_config()
    report = run_pipeline(config)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
