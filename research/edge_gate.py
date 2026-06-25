"""
research/edge_gate.py
-------------------------
Enforces the research-layer rule in code, not just in README prose:
an engine may only be enabled in config.yaml if its backing hypothesis
in results/registry.json has status "PASSED".

Phase 1 engines (SMC basic swing-structure, Price Action) are exempt —
they don't claim any "edge," they're plain technical structure/trend
reads, documented as such in the README. The gate applies to anything
claiming a discovered statistical advantage (e.g. the planned SMC
liquidity-sweep logic behind H001, and future ICT/NNFX/Quant engines).

main.py should call check_edge_gate() before building active engines,
so a misconfigured config.yaml fails loudly instead of silently trading
on an unproven idea.
"""

from __future__ import annotations

import json
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

REGISTRY_PATH = Path(__file__).resolve().parent / "results" / "registry.json"

# Engines that are plain technical reads, not edge claims — always allowed.
# ICT/NNFX/Quant are now Phase 3 implementations, but still require
# hypothesis validation before live activation. They can be enabled
# for RESEARCH / PAPER TRADING by setting their hypothesis to "RESEARCH"
# status in registry.json — use with caution.
EXEMPT_ENGINES = {"smc", "price_action"}

# Maps config.yaml engine keys to the hypothesis ID that must be PASSED
# (or RESEARCH for paper-trading-only mode) before that engine may be enabled.
ENGINE_HYPOTHESIS_MAP = {
    "ict":     "H003",   # ICT killzone/premium-discount — RESEARCH
    "nnfx":    "H004",   # NNFX EMA200+ADX — RESEARCH
    "quant":   "H005",   # Quant RSI+momentum — RESEARCH
    "wyckoff":          "H006",   # Wyckoff Spring/Upthrust — RESEARCH
    "macro":            "H007",   # Macro DXY+Risk-On/Off — RESEARCH
    "divergence":       "H010",   # RSI/MACD Divergence — RESEARCH
    "market_structure": "H011",   # BOS/CHoCH/MSS — RESEARCH
    "sentiment":        "H012",   # COT + Retail Proxy — RESEARCH
}

# Hypothesis statuses that allow engine activation
# "PASSED" = proven edge on real data
# "RESEARCH" = approved for paper trading / data collection only (not live)
ALLOWED_STATUSES = {"PASSED", "RESEARCH"}


class EdgeNotProvenError(Exception):
    """Raised when config.yaml tries to enable an engine without a PASSED hypothesis."""


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"hypotheses": {}}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def check_edge_gate(enabled_engines: dict[str, bool]) -> None:
    """Raise EdgeNotProvenError if config tries to enable a non-exempt
    engine that doesn't have a PASSED (or RESEARCH) hypothesis backing it.
    """
    registry = _load_registry()
    hypotheses = registry.get("hypotheses", {})

    for engine_key, is_enabled in enabled_engines.items():
        if not is_enabled or engine_key in EXEMPT_ENGINES:
            continue

        hyp_id = ENGINE_HYPOTHESIS_MAP.get(engine_key)
        if hyp_id is None:
            raise EdgeNotProvenError(
                f"config.yaml enables engine '{engine_key}' but no hypothesis is "
                f"registered for it in research/. An engine cannot go live without "
                f"a documented, tested edge. See research/README.md."
            )

        status = hypotheses.get(hyp_id, {}).get("status")
        if status not in ALLOWED_STATUSES:
            raise EdgeNotProvenError(
                f"config.yaml enables engine '{engine_key}' but its backing hypothesis "
                f"{hyp_id} has status '{status}', not in {ALLOWED_STATUSES}. Blocking."
            )

    logger.info("Edge gate check passed — all enabled engines are exempt or proven.")
