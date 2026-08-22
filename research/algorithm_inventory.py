"""
research/algorithm_inventory.py
----------------------------------
Algorithm & Control Inventory — an RTS 6 Art.5 / PRA SS5/18-style register
of every trading-decision algorithm ("engine") this system contains,
computed FRESH from the live config and code on every call so it can
never drift stale the way a hand-maintained document would. Read-only:
this module has no write path anywhere in it (pinned by
tests/test_algorithm_inventory.py's source-scan).

Sourced from, never duplicated ahead of:
  - config/engines.yaml  (engines.enabled / engines.versions /
    engines.thresholds — the real, persisted activation/version/
    parameter state)
  - config.yaml           (confluence.weights / min_engines_agreeing /
    min_score_to_trade / min_informative_weight_share — the real
    consensus rules that combine every algorithm's vote into one
    decision)
  - main.py::_ALL_ENGINES (the exact set of algorithms the LIVE pipeline
    can construct — nothing here is invented; an engine absent from
    that dict cannot reach a live decision no matter what this file
    says)
  - backtesting/backtest_engine.py::ENGINE_VARIANT_KEYS (the exact set
    of ad-hoc, research-only engine variants that exist — v2 engines
    are never in _ALL_ENGINES and never in engines.enabled, so they are
    structurally incapable of reaching a live decision; see
    build_engine_config_override's own docstring)
  - research/edge_gate.py (the hypothesis-backing / promotion-criteria
    gate that decides whether an engine may be enabled at all, and
    whether it may ever receive live capital)
  - confluence/score_calculator.py::_ENGINE_NAME_TO_CONFIG_KEY (the
    real weight-key mapping a v2 variant's vote is scored under — so a
    reported "confluence_weight" for e.g. price_action_v2 matches what
    the scoring code actually consults, not a guess)

Purpose text per algorithm is transcribed verbatim from that engine
module's own top-of-file docstring (file:line cited in each entry) —
never invented here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Frozen per CLAUDE.md's "Current frozen state": the only 4 engines kept
# live by burden-of-proof (H015 closed twice: no robust alternative
# exists, every addition measured as dilution). Mirrors execution/routes/
# research.py's own _PROD4_ENGINES constant — kept as a separate literal
# here rather than imported, since a FastAPI route module is not a safe
# import target for a pure research/ module (avoids a route-layer ->
# research-layer -> route-layer import cycle).
PROD4_ENGINES: frozenset[str] = frozenset({"smc", "price_action", "nnfx", "wyckoff"})

_PURPOSE: dict[str, str] = {
    "smc": (
        "Smart Money Concepts: swing-point-based structural bias "
        "(higher-highs/higher-lows vs lower-highs/lower-lows), with "
        "optional full-spec order-block / fair-value-gap / BOS-CHoCH "
        "components gated behind engines.smc_full_spec (off — H017 "
        "FAILED). (engines/smc_engine.py, module docstring)"
    ),
    "price_action": (
        "Pure price-action engine: candlestick patterns, RSI momentum, "
        "Bollinger Band position, short-term momentum. Deliberately a "
        "different indicator set from NNFX/SMC — measured 0.975 "
        "correlation with NNFX before this design, since resolved. "
        "(engines/price_action_engine.py, module docstring)"
    ),
    "nnfx": (
        "No-Nonsense-Forex methodology: layered confirmation — 200EMA "
        "baseline trend, ADX trend-strength confirmation, ATR-based "
        "sizing context, OHLCV-only implementation. "
        "(engines/nnfx_engine.py, module docstring)"
    ),
    "wyckoff": (
        "Wyckoff methodology: price/volume relationship and "
        "institutional-intent (\"Composite Operator\") reasoning — "
        "spring/upthrust detection, range position, stopping-volume. "
        "Price-only on FX (no reliable volume data), full volume "
        "analysis on metals/indices/crypto. "
        "(engines/wyckoff_engine.py, module docstring)"
    ),
    "ict": (
        "ICT (Inner Circle Trader) concepts: killzone session bias, "
        "premium/discount dealing-range position, Judas-swing "
        "false-breakout detection, higher-timeframe bias alignment. "
        "(engines/ict_engine.py, module docstring)"
    ),
    "market_structure": (
        "Structural-shift detection: BOS (Break of Structure — trend "
        "continuation), CHoCH (Change of Character — first reversal "
        "sign), MSS (confirmed reversal), evaluated across dual "
        "timeframes. (engines/market_structure_engine.py, module docstring)"
    ),
    "divergence": (
        "RSI/MACD divergence detection: price makes a new swing "
        "extreme but momentum doesn't confirm it, with Triple-"
        "divergence and multi-timeframe confirmation. "
        "(engines/divergence_engine.py, module docstring)"
    ),
    "quant": (
        "Regime-aware statistical engine: classifies the market as "
        "TRENDING / MEAN_REVERTING / RANDOM / UNKNOWN via a vote across "
        "Hurst exponent, variance ratio, ADF stationarity, "
        "autocorrelation, efficiency ratio, half-life, and entropy — "
        "then selects which family of directional signals to trust "
        "based on that classification. (engines/quant_engine.py, module docstring)"
    ),
    "macro": (
        "Top-level market-context engine (no price-pattern analysis): "
        "dollar strength (DXY), risk-on/off appetite (SPY/VIX/Gold, "
        "yield-curve inversion, credit-spread direction, Fed "
        "balance-sheet direction), commodity trend (informational "
        "only, never scored). (engines/macro_engine.py, module docstring)"
    ),
    "sentiment": (
        "Market sentiment from CFTC COT (Commitments of Traders) Large "
        "Speculator net-positioning trend (primary, weekly), with a "
        "retail-positioning price-proxy as fallback. "
        "(engines/sentiment_engine.py, module docstring)"
    ),
    "price_action_v2": (
        "AD-HOC RESEARCH VARIANT ONLY. Genuinely pure price-action — "
        "no RSI, no Bollinger Bands (v1 uses both; their absence here "
        "is itself part of this engine's contract): Inside/Outside "
        "Bar, NR4/NR7, Fakey, Three-Bar-Play, Micro-Trend, Volatility "
        "Contraction, Failed Breakout, Opening Drive, Closing "
        "Strength. Never wired into the live pipeline — reachable "
        "only through Mission Center's ephemeral engine_variants "
        "override. (engines/price_action_engine_v2.py, module docstring)"
    ),
    "wyckoff_v2": (
        "AD-HOC RESEARCH VARIANT ONLY. Additive Wyckoff extension: a "
        "real Phase A→E schematic reconstruction (Selling/Buying "
        "Climax, Automatic Rally/Reaction, Secondary Test, Sign of "
        "Strength/Weakness, Phase E markup/markdown confirmation) "
        "layered on top of v1's proven spring/upthrust logic (reused, "
        "not rewritten), plus a Composite-Operator-footprint "
        "heuristic. Never wired into the live pipeline. "
        "(engines/wyckoff_engine_v2.py, module docstring)"
    ),
}


def _approval_basis(hyp_id: str | None, status: str | None, unmet: list[str]) -> str:
    """Plain-language statement of why an algorithm may or may not be
    enabled/live today, derived from research/edge_gate.py's own
    enforcement logic — never a separate, potentially-drifting opinion."""
    if hyp_id is None:
        return (
            "No hypothesis is registered for this engine in "
            "research/results/registry.json — research/edge_gate.py "
            "raises EdgeNotProvenError and refuses to build it if "
            "config/engines.yaml ever tries to enable it."
        )
    if status not in ("PASSED", "RESEARCH"):
        return (
            f"Backing hypothesis {hyp_id} has status "
            f"'{status or 'missing'}', not PASSED or RESEARCH — "
            "research/edge_gate.py blocks activation."
        )
    if status == "PASSED" and not unmet:
        return (
            f"Backing hypothesis {hyp_id} is PASSED with qualifying "
            "evidence (≥300 OOS trades, OOS profit factor ≥1.2, "
            "walk-forward AND Monte Carlo evidence present)."
        )
    if status == "PASSED":
        return (
            f"Backing hypothesis {hyp_id} is marked PASSED but its "
            f"evidence fails the codified promotion bar "
            f"({'; '.join(unmet)}) — research/edge_gate.py treats this "
            "the same as RESEARCH: it may run, it may NOT receive live "
            "capital while execution.allow_live_trading is True."
        )
    return (
        f"Backing hypothesis {hyp_id} is RESEARCH status — approved "
        "for paper trading / demo data collection only. It cannot "
        "receive live capital: research/edge_gate.py::check_edge_gate "
        "raises EdgeNotProvenError at boot if execution."
        "allow_live_trading is True while any RESEARCH-status engine "
        "is enabled."
    )


def build_algorithm_inventory(config: dict) -> dict[str, Any]:
    """Pure function of an already-loaded config dict (utils.helpers.
    load_config()'s own output) — never loads or writes any file
    itself. Returns a JSON-serializable inventory covering every
    algorithm that exists in code today, live-eligible or not."""
    # Imported here, not at module scope, so importing this pure
    # research/ module never triggers main.py's / the live broker
    # clients' own import-time side effects unless an inventory is
    # actually being built (mirrors this codebase's established
    # lazy-import-for-heavy-modules convention).
    from main import _ALL_ENGINES
    from backtesting.backtest_engine import ENGINE_VARIANT_KEYS
    from confluence.score_calculator import _ENGINE_NAME_TO_CONFIG_KEY
    from engines.price_action_engine_v2 import PriceActionEngineV2
    from engines.wyckoff_engine_v2 import WyckoffEngineV2
    from research.edge_gate import (
        ALLOWED_STATUSES,
        ENGINE_HYPOTHESIS_MAP,
        PROMOTION_CRITERIA,
        _load_registry,
        _promotion_criteria_unmet,
    )

    engines_cfg = config.get("engines", {}) or {}
    enabled = engines_cfg.get("enabled", {}) or {}
    versions = engines_cfg.get("versions", {}) or {}
    thresholds_cfg = engines_cfg.get("thresholds", {}) or {}
    confluence_cfg = config.get("confluence", {}) or {}
    weights = confluence_cfg.get("weights", {}) or {}

    hypotheses = (_load_registry() or {}).get("hypotheses", {}) or {}

    entries: list[dict[str, Any]] = []

    # ── Base algorithms: every one main.py's live pipeline can construct ──
    for key, cls in _ALL_ENGINES.items():
        hyp_id = ENGINE_HYPOTHESIS_MAP.get(key)
        hyp = hypotheses.get(hyp_id, {}) if hyp_id else {}
        status = hyp.get("status") if hyp_id else None
        unmet = _promotion_criteria_unmet(hyp) if status == "PASSED" else []
        is_enabled = bool(enabled.get(key, False))
        entries.append({
            "key": key,
            "class_name": cls.__name__,
            "display_name": getattr(cls, "name", key),
            "purpose": _PURPOSE.get(key, "(no purpose text catalogued — see engines/{}.py's own docstring)".format(key)),
            "variant_of": None,
            "is_live_eligible": True,
            "enabled": is_enabled,
            "prod4": key in PROD4_ENGINES,
            "version": versions.get(key),
            "confluence_weight": weights.get(key),
            "num_parameters": len(thresholds_cfg.get(key, {}) or {}),
            "parameters": dict(thresholds_cfg.get(key, {}) or {}),
            "hypothesis_id": hyp_id,
            "hypothesis_title": hyp.get("title"),
            "hypothesis_status": status,
            "approval_basis": _approval_basis(hyp_id, status, unmet),
            "live_capital_eligible": bool(hyp_id and status == "PASSED" and not unmet),
            "reachable_via": (
                "Live pipeline (main.py::build_active_engines)" if is_enabled
                else "Constructible by main.py's live pipeline but not currently enabled (config/engines.yaml engines.enabled.{}: false)".format(key)
            ),
        })

    # ── AD-HOC research variants: never in _ALL_ENGINES / enabled block ──
    _variant_classes = {"price_action_v2": PriceActionEngineV2, "wyckoff_v2": WyckoffEngineV2}
    for base_key, variants in ENGINE_VARIANT_KEYS.items():
        for variant in variants:
            if variant == "v1":
                continue  # not a distinct algorithm — the base engine itself
            variant_key = f"{base_key}_{variant}"
            cls = _variant_classes.get(variant_key)
            display_name = getattr(cls, "name", variant_key) if cls else variant_key
            config_key = _ENGINE_NAME_TO_CONFIG_KEY.get(display_name, base_key)
            entries.append({
                "key": variant_key,
                "class_name": cls.__name__ if cls else None,
                "display_name": display_name,
                "purpose": _PURPOSE.get(variant_key, "(no purpose text catalogued — see engines/{}.py's own docstring)".format(variant_key)),
                "variant_of": base_key,
                "is_live_eligible": False,
                "enabled": False,
                "prod4": False,
                "version": versions.get(variant_key),
                # Shares the BASE engine's weight slot the instant it is
                # ever activated ad hoc — confluence/score_calculator.py's
                # own _ENGINE_NAME_TO_CONFIG_KEY mapping, not a separate
                # weight, so this figure is what the vote is actually
                # scored under, not a guess.
                "confluence_weight": weights.get(config_key),
                "num_parameters": len(thresholds_cfg.get(variant_key, {}) or {}),
                "parameters": dict(thresholds_cfg.get(variant_key, {}) or {}),
                "hypothesis_id": None,
                "hypothesis_title": None,
                "hypothesis_status": None,
                "approval_basis": (
                    "Not gated by research/edge_gate.py — never appears "
                    "in config/engines.yaml's enabled: block, so the "
                    "hard edge-gate check never runs against it. "
                    "Structurally incapable of reaching a live decision "
                    "without a code change (adding it to main.py's "
                    "_ALL_ENGINES) plus a new pre-registered hypothesis."
                ),
                "live_capital_eligible": False,
                "reachable_via": (
                    "Mission Center ad-hoc research override ONLY "
                    "(backtesting.backtest_engine.build_engine_config_"
                    "override's engine_variants parameter) — ephemeral, "
                    "in-memory, never written to config/engines.yaml."
                ),
            })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "consensus_rules": {
            "min_engines_agreeing": confluence_cfg.get("min_engines_agreeing"),
            "min_score_to_trade": confluence_cfg.get("min_score_to_trade"),
            "min_informative_weight_share": confluence_cfg.get("min_informative_weight_share"),
        },
        "governance": {
            "smc_full_spec": bool(engines_cfg.get("smc_full_spec", False)),
            "crypto_positioning_modulator": bool(engines_cfg.get("crypto_positioning_modulator", False)),
            "allowed_hypothesis_statuses": sorted(ALLOWED_STATUSES),
            "promotion_criteria": dict(PROMOTION_CRITERIA),
        },
        "algorithms": entries,
        "counts": {
            "total": len(entries),
            "base_algorithms": sum(1 for e in entries if e["variant_of"] is None),
            "research_variants": sum(1 for e in entries if e["variant_of"] is not None),
            "live_enabled": sum(1 for e in entries if e["enabled"]),
            "prod4": sum(1 for e in entries if e["prod4"]),
            "live_capital_eligible": sum(1 for e in entries if e["live_capital_eligible"]),
        },
    }
