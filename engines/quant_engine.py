"""
engines/quant_engine.py
---------------------------
Quantitative/statistical engine — Confluence Engine Overhaul Phase 3a
(2026-08-01) full rebuild.

Regime-aware design: classifies the market as TRENDING, MEAN_REVERTING,
RANDOM, or UNKNOWN using a vote across several independent statistics
(Hurst exponent, variance ratio, ADF stationarity, return autocorrelation,
efficiency ratio, half-life, entropy), computed in extract_features().
decide() then uses THAT classification to choose which family of
directional signals to trust — a mean-reversion z-score extreme only
means something if the market is actually mean-reverting; a momentum
signal only means something if it's actually trending. Even a single
shared indicator (RSI) is read two different ways depending on regime:
contrarian in MEAN_REVERTING, momentum-confirming in TRENDING.

Engine Refinement V1 (#365, QUANT-REFINE, SEMANTIC_FIX, operator-pre-
approved, confirmed in reports/engine_refinement/ENGINE_INVENTORY.md's
Quant entry): RANDOM and UNKNOWN are now distinct classifications.
Before this fix, _classify_regime() returned "RANDOM" both when (a)
too few diagnostics could vote at all (short history, degenerate
input — a data-insufficiency problem) and (b) enough diagnostics DID
vote but the aggregate genuinely showed no trending/mean-reverting
majority (a real statistical finding). Case (a) is now UNKNOWN. Both
still abstain (NEUTRAL/0.0) in decide() — this changes only the
reported classification/reasoning, never the live decision, and Quant
is disabled by default (engines.enabled.quant: false).

NOTE: this engine's `regime` (TRENDING/MEAN_REVERTING/RANDOM) is an
ENGINE-INTERNAL classification, computed from this engine's own
statistics on one symbol's own price series. It is a completely
different concept from confluence/regime_weights.py's system-level
regime (TRENDING/RANGING/...), which reweights engine votes across the
whole confluence layer — the two must never be confused or merged.

Architecturally out of scope for this engine's interface: cointegration
(for pairs) and cross-sectional ranking. Both require observing
MULTIPLE symbols simultaneously, but BaseEngine.analyze(self, mtf_data)
receives exactly one symbol's data per call (true of every engine in
this codebase) — building a universe-level analysis needs a different
engine interface entirely, a separate future design problem.

This engine is DISABLED (config/engines.yaml engines.enabled.quant:
false) — nothing here touches live trading. No golden-value regression
requirement: this is a full rebuild, not a refactor. Correctness here
means real, independently-verified statistics (see
tests/test_indicators_quant_stats.py), not bit-identical output to v1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engines.base_engine import BaseEngine, Bias, EngineOutput
from utils.indicators import (
    adf_pvalue,
    atr as _atr_series,
    autocorrelation,
    efficiency_ratio,
    half_life,
    hurst_exponent,
    realized_volatility,
    rsi as _rsi_series,
    shannon_entropy,
    variance_ratio,
    zscore,
)

# Mirrors core/timeframe_sync.py's private _TF_MINUTES — copied, not
# imported, since that name is underscore-private to its own module.
_TF_MINUTES_LOCAL = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}

# Mirrors core/market_quality.py's own inline is_crypto pattern (not an
# exported function there — reproduced locally, same precedent already
# used for BUG-008's is_usd_base_symbol()).
_CRYPTO_TICKERS = ("BTC", "ETH", "XRP", "LTC", "SOL")


def _is_24_7_asset(symbol: str) -> bool:
    return any(c in symbol.upper() for c in _CRYPTO_TICKERS)


def _bars_per_year(tf: str, t: dict, symbol: str = "") -> float:
    """Forensic Audit follow-up (2026-08-04): realized-vol annualization
    was always assuming a 365-day trading calendar, correct for crypto
    but wrong for FX/metals, which trade only ~261 days/year (52 weeks x
    5 days). Using 365 instead of 261 makes sqrt(bars_per_year) — and
    therefore realized_vol_annualized — ~18% TOO HIGH for FX, not
    "understated" as an earlier external audit claimed; independently
    re-derived here, not taken on the audit's word. `symbol` is optional
    and defaults to "" (unknown context), which preserves the exact
    prior 365-day behavior for every existing caller/test that doesn't
    pass one — only a caller that positively identifies a non-24/7
    symbol gets the corrected 261-day count.

    Currently zero live-decision impact either way: realized_vol_
    annualized/bars_per_year_used are informational-only fields, never
    read by decide() (grep-confirmed) — and Quant is a disabled engine
    (config/engines.yaml engines.enabled.quant: false). Fixed at the
    source anyway so a future re-enable doesn't inherit a known-wrong
    constant.
    """
    minutes = _TF_MINUTES_LOCAL.get(tf)
    if minutes is None:
        return t.get("bars_per_year_default", 8760.0)
    if symbol and not _is_24_7_asset(symbol):
        days_per_year = t.get("trading_days_per_year_fx", 261.0)
    else:
        days_per_year = 365.0
    return (days_per_year * 24 * 60) / minutes


def _atr_percentile(df: pd.DataFrame, period: int = 14, lookback: int = 100) -> float:
    """Current ATR as percentile of its own recent history. Thin
    composition over utils.indicators.atr — not generic enough to move
    there (same category as e.g. wyckoff_engine.py's own private
    _identify_trading_range)."""
    atr_series = _atr_series(df, period)
    current_atr = atr_series.iloc[-1]
    historical = atr_series.dropna().tail(lookback)
    if len(historical) < 10 or pd.isna(current_atr):
        return 0.5
    return round(float((historical <= current_atr).mean()), 3)


def _classify_vol_regime(pct: float, t: dict) -> str:
    if pct < t.get("vol_regime_low_pct", 0.25):
        return "LOW"
    if pct > t.get("vol_regime_high_pct", 0.80):
        return "HIGH"
    return "NORMAL"


def _trend_direction(close: pd.Series, lookback: int) -> str:
    if len(close) < lookback + 1:
        return "flat"
    diff = float(close.iloc[-1]) - float(close.iloc[-1 - lookback])
    if diff > 0:
        return "up"
    if diff < 0:
        return "down"
    return "flat"


def _classify_regime(
    hurst: float | None,
    variance_ratio_val: float | None,
    adf_p: float | None,
    autocorr_val: float | None,
    er: float | None,
    hl: float | None,
    entropy_val: float | None,
    t: dict,
) -> tuple[str, dict, float]:
    """Vote-based regime classification. Each signal casts TRENDING,
    MEAN_REVERTING, or abstains (a genuine no-vote, not a silent lean).
    ADF and half-life are one-sided: absence of mean-reversion evidence
    is not evidence of trending (matches base_engine.py's "unclear data
    -> no opinion" principle). Entropy casts a separate RANDOM vote.
    Returns (regime, votes_dict, confidence).

    regime is one of TRENDING/MEAN_REVERTING/RANDOM/UNKNOWN. UNKNOWN
    means too few diagnostics could vote at all to form ANY opinion
    (data insufficiency) — distinct from RANDOM, which means enough
    diagnostics DID vote but their aggregate genuinely shows no
    trending/mean-reverting majority (a real statistical finding).
    Engine Refinement V1 (#365) — these were conflated before this fix."""
    votes = {"trending": 0, "mean_reverting": 0, "random": 0, "abstain": 0}

    hurst_trend = t.get("hurst_trend_threshold", 0.55)
    hurst_mr = t.get("hurst_mr_threshold", 0.45)
    if hurst is None:
        votes["abstain"] += 1
    elif hurst > hurst_trend:
        votes["trending"] += 1
    elif hurst < hurst_mr:
        votes["mean_reverting"] += 1
    else:
        votes["abstain"] += 1

    vr_trend = t.get("vr_trend_threshold", 1.10)
    vr_mr = t.get("vr_mr_threshold", 0.90)
    if variance_ratio_val is None:
        votes["abstain"] += 1
    elif variance_ratio_val > vr_trend:
        votes["trending"] += 1
    elif variance_ratio_val < vr_mr:
        votes["mean_reverting"] += 1
    else:
        votes["abstain"] += 1

    adf_threshold = t.get("adf_p_threshold", 0.05)
    if adf_p is None:
        votes["abstain"] += 1
    elif adf_p < adf_threshold:
        votes["mean_reverting"] += 1
    else:
        votes["abstain"] += 1

    autocorr_threshold = t.get("autocorr_threshold", 0.10)
    if autocorr_val is None:
        votes["abstain"] += 1
    elif autocorr_val > autocorr_threshold:
        votes["trending"] += 1
    elif autocorr_val < -autocorr_threshold:
        votes["mean_reverting"] += 1
    else:
        votes["abstain"] += 1

    er_trend = t.get("er_trend_threshold", 0.30)
    er_mr = t.get("er_mr_threshold", 0.15)
    if er is None:
        votes["abstain"] += 1
    elif er > er_trend:
        votes["trending"] += 1
    elif er < er_mr:
        votes["mean_reverting"] += 1
    else:
        votes["abstain"] += 1

    halflife_max = t.get("halflife_max_bars_for_mr_vote", 20)
    if hl is not None and hl <= halflife_max:
        votes["mean_reverting"] += 1
    else:
        votes["abstain"] += 1

    entropy_threshold = t.get("entropy_random_threshold", 0.90)
    if entropy_val is not None and entropy_val >= entropy_threshold:
        votes["random"] += 1
    else:
        votes["abstain"] += 1

    total_cast = votes["trending"] + votes["mean_reverting"] + votes["random"]
    min_votes = t.get("regime_min_votes", 2)
    margin = t.get("regime_margin", 1)

    if total_cast < min_votes:
        return "UNKNOWN", votes, 0.0
    if votes["trending"] > votes["mean_reverting"] + margin and votes["trending"] > votes["random"]:
        return "TRENDING", votes, votes["trending"] / total_cast
    if votes["mean_reverting"] > votes["trending"] + margin and votes["mean_reverting"] > votes["random"]:
        return "MEAN_REVERTING", votes, votes["mean_reverting"] / total_cast
    confidence = votes["random"] / total_cast if total_cast else 0.0
    return "RANDOM", votes, confidence


def extract_features(df: pd.DataFrame, t: dict, tf: str, symbol: str = "") -> dict:
    """Feature Extraction layer — every statistic decide() needs,
    including the regime classification itself (a deterministic fact
    derived from other pure facts, same category as ict_engine.py's
    `zone` classification). Pure function of (df, thresholds, timeframe
    label, optional symbol); zero bias/score logic. 3-required-arg
    signature (not Wyckoff's 2-arg shape) because realized-vol
    annualization is genuinely timeframe-dependent — ict_engine.py's own
    extract_features(mtf_data, t) already establishes "whatever inputs
    the facts need" as the real pattern, not literally always 2 args.
    `symbol` is optional/keyword-friendly and defaults to "" (no
    behavior change for any pre-existing 3-positional-arg caller) — see
    _bars_per_year()'s own docstring for why it matters."""
    close = df["close"]
    log_close = np.log(close)
    log_returns = log_close.diff()

    zscore_series = zscore(close, t.get("zscore_window", 20))
    zscore_val = float(zscore_series.iloc[-1]) if not pd.isna(zscore_series.iloc[-1]) else None

    hurst = hurst_exponent(
        log_returns.dropna().tail(t.get("hurst_lookback", 500)),
        lags=t.get("hurst_lags", [10, 20, 40, 80, 160]),
        min_lags=t.get("hurst_min_lags", 4),
    )

    autocorr_val = autocorrelation(
        log_returns.tail(t.get("autocorr_lookback", 100)), lag=1,
    )

    atr_pct = _atr_percentile(df, t.get("atr_period", 14), t.get("atr_lookback", 100))
    vol_regime = _classify_vol_regime(atr_pct, t)

    entropy_val = shannon_entropy(
        log_returns.dropna().tail(t.get("entropy_lookback", 100)),
        bins=t.get("entropy_bins", 10),
    )

    er_series = efficiency_ratio(close, t.get("er_period", 10))
    er_val = float(er_series.iloc[-1]) if not pd.isna(er_series.iloc[-1]) else None

    vr_val = variance_ratio(
        close.tail(t.get("variance_ratio_lookback", 200)),
        q=t.get("variance_ratio_q", 10),
    )

    adf_stat, adf_p = adf_pvalue(
        close.tail(t.get("adf_lookback", 100)), min_obs=t.get("adf_min_bars", 30),
    )

    hl = half_life(
        log_close.tail(t.get("halflife_lookback", 100)), min_obs=t.get("halflife_min_bars", 30),
    )

    bars_per_year = _bars_per_year(tf, t, symbol)
    realized_vol = realized_volatility(
        log_returns.tail(t.get("realized_vol_window", 20)), bars_per_year,
    )

    vol_clustering = autocorrelation(
        log_returns.abs().tail(t.get("vol_clustering_lookback", 100)), lag=1,
    )

    rsi_series = _rsi_series(close, t.get("rsi_period", 14))
    rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None

    trend_direction = _trend_direction(close, t.get("trend_direction_lookback", 20))

    regime, regime_votes, regime_confidence = _classify_regime(
        hurst, vr_val, adf_p, autocorr_val, er_val, hl, entropy_val, t,
    )

    return {
        "n_bars": len(df),
        "zscore": zscore_val,
        "hurst": hurst,
        "autocorr_lag1": autocorr_val,
        "atr_percentile": atr_pct,
        "vol_regime": vol_regime,
        "entropy_normalized": entropy_val,
        "efficiency_ratio": er_val,
        "variance_ratio": vr_val,
        "adf_pvalue": adf_p,
        "adf_stat": adf_stat,
        "half_life_bars": hl,
        "realized_vol_annualized": realized_vol,
        "bars_per_year_used": bars_per_year,
        "vol_clustering": vol_clustering,
        "rsi": rsi_val,
        "trend_direction": trend_direction,
        "regime": regime,
        "regime_votes": regime_votes,
        "regime_confidence": round(regime_confidence, 3),
    }


def decide(features: dict, t: dict) -> tuple[Bias, float, list[str]]:
    """Decision Logic layer — turns an extract_features() snapshot into
    a bias/score opinion. Pure function of (features, thresholds); never
    touches a DataFrame. The regime decides which family of directional
    signals gets trusted, and even changes how RSI is read (contrarian
    vs. momentum)."""
    regime = features["regime"]
    votes = features["regime_votes"]
    confidence_pct = features["regime_confidence"] * 100

    reasons: list[str] = [
        f"Regime classified {regime} (votes: trending={votes['trending']}, "
        f"mean_reverting={votes['mean_reverting']}, random={votes['random']}, "
        f"abstain={votes['abstain']}, confidence={confidence_pct:.0f}%)",
        f"Volatility regime: {features['vol_regime']} (ATR percentile={features['atr_percentile']:.0%})",
    ]

    rsi_val = features["rsi"]
    score_cap = t.get("score_cap", 70.0)
    score_neutral_floor = t.get("score_neutral_floor", 15.0)
    rsi_confirm_score = t.get("rsi_confirm_score", 15.0)
    rsi_conflict_penalty = t.get("rsi_conflict_penalty", 10.0)

    bias = Bias.NEUTRAL
    score = 0.0

    if regime == "MEAN_REVERTING":
        z = features["zscore"]
        entry = t.get("mr_zscore_entry", 2.0)
        if z is None:
            reasons.append("Mean-reverting regime but z-score unavailable — no opinion")
        elif z <= -entry:
            bias = Bias.BULLISH
            score = t.get("mr_base_score", 40.0)
            score += min((abs(z) - entry) * t.get("mr_zscore_scale", 10.0), t.get("mr_zscore_bonus_cap", 20.0))
            reasons.append(f"Z-score={z:.2f} <= -{entry} — price far below mean, mean-reversion BUY")
        elif z >= entry:
            bias = Bias.BEARISH
            score = t.get("mr_base_score", 40.0)
            score += min((abs(z) - entry) * t.get("mr_zscore_scale", 10.0), t.get("mr_zscore_bonus_cap", 20.0))
            reasons.append(f"Z-score={z:.2f} >= {entry} — price far above mean, mean-reversion SELL")
        else:
            reasons.append(f"Mean-reverting regime but z-score={z:.2f} not extreme — no opinion")

        if bias != Bias.NEUTRAL and rsi_val is not None:
            rsi_oversold = t.get("rsi_oversold", 30)
            rsi_overbought = t.get("rsi_overbought", 70)
            if bias == Bias.BULLISH and rsi_val < rsi_oversold:
                score += rsi_confirm_score
                reasons.append(f"RSI={rsi_val:.1f} oversold — confirms contrarian BUY")
            elif bias == Bias.BEARISH and rsi_val > rsi_overbought:
                score += rsi_confirm_score
                reasons.append(f"RSI={rsi_val:.1f} overbought — confirms contrarian SELL")
            elif bias == Bias.BULLISH and rsi_val > rsi_overbought:
                score -= rsi_conflict_penalty
                reasons.append(f"RSI={rsi_val:.1f} overbought — conflicts with contrarian BUY")
            elif bias == Bias.BEARISH and rsi_val < rsi_oversold:
                score -= rsi_conflict_penalty
                reasons.append(f"RSI={rsi_val:.1f} oversold — conflicts with contrarian SELL")

        hl = features["half_life_bars"]
        if bias != Bias.NEUTRAL and hl is not None and hl <= t.get("halflife_fast_bars", 10):
            score += t.get("mr_halflife_confirm_score", 5.0)
            reasons.append(f"Half-life={hl:.1f} bars — fast mean reversion, tradeable window")

    elif regime == "TRENDING":
        direction = features["trend_direction"]
        if direction == "up":
            bias = Bias.BULLISH
        elif direction == "down":
            bias = Bias.BEARISH
        else:
            reasons.append("Trending regime but no net directional move — no opinion")

        if bias != Bias.NEUTRAL:
            score = t.get("trend_base_score", 35.0)
            er = features["efficiency_ratio"]
            if er is not None:
                score += min(er * t.get("trend_er_scale", 40.0), t.get("trend_er_bonus_cap", 25.0))
            reasons.append(f"Trend direction={direction}, efficiency_ratio={er}")

            if rsi_val is not None:
                rsi_bull_confirm = t.get("rsi_bull_confirm", 55)
                rsi_bear_confirm = t.get("rsi_bear_confirm", 45)
                if bias == Bias.BULLISH and rsi_val > rsi_bull_confirm:
                    score += rsi_confirm_score
                    reasons.append(f"RSI={rsi_val:.1f} confirms bullish momentum (>{rsi_bull_confirm})")
                elif bias == Bias.BEARISH and rsi_val < rsi_bear_confirm:
                    score += rsi_confirm_score
                    reasons.append(f"RSI={rsi_val:.1f} confirms bearish momentum (<{rsi_bear_confirm})")
                elif bias == Bias.BULLISH and rsi_val < rsi_bear_confirm:
                    score -= rsi_conflict_penalty
                    reasons.append(f"RSI={rsi_val:.1f} conflicts with bullish trend")
                elif bias == Bias.BEARISH and rsi_val > rsi_bull_confirm:
                    score -= rsi_conflict_penalty
                    reasons.append(f"RSI={rsi_val:.1f} conflicts with bearish trend")

    elif regime == "UNKNOWN":
        reasons.append(
            "Too few diagnostics could vote to classify a regime (data "
            "insufficiency, e.g. short history) — abstaining per no-guess policy"
        )

    else:
        reasons.append("No statistically significant regime detected — abstaining per no-guess policy")

    vol_clustering = features["vol_clustering"]
    if vol_clustering is not None:
        reasons.append(
            f"Volatility clustering (|returns| autocorr)={vol_clustering:.2f} "
            f"— informational only, not used in direction/regime vote"
        )

    score = max(0.0, min(round(score, 1), score_cap))
    if score < score_neutral_floor:
        bias = Bias.NEUTRAL

    return bias, score, reasons


class QuantEngine(BaseEngine):
    name = "Quant"

    def analyze(self, mtf_data: dict[str, pd.DataFrame]) -> EngineOutput:
        t = self.thresholds
        tf, df = self.decision_frame(mtf_data)

        min_bars = t.get("min_bars", 100)
        if len(df) < min_bars:
            return EngineOutput(
                engine_name=self.name,
                bias=Bias.NEUTRAL,
                score=0.0,
                reasons=[f"Insufficient data for quant analysis (need {min_bars}+ bars)"],
            )

        features = extract_features(df, t, tf, getattr(self, "_symbol", ""))
        bias, score, reasons = decide(features, t)

        raw = {**features, "timeframe_used": tf}

        return EngineOutput(
            engine_name=self.name,
            bias=bias,
            score=score,
            reasons=reasons,
            raw=raw,
            features=features,
        )
