"""
research/edge_gate.py
-------------------------
Enforces the research-layer rule in code, not just in README prose:
an engine may only be enabled in config.yaml if its backing hypothesis
in results/registry.json has status "PASSED" or "RESEARCH".

Every enabled engine now carries a hypothesis — including SMC and Price
Action, which used to bypass this gate entirely via EXEMPT_ENGINES on the
rationale that they were "plain technical reads" claiming no edge. That
rationale didn't survive scrutiny (docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md
P1-1): both are weighted and vote inside the scored confluence system
exactly like every other gated engine, so as of 2026-07-23 they carry
H101/H102 (RESEARCH status, existing H015 evidence cited, no code/weight/
threshold change) instead of a bare bypass. EXEMPT_ENGINES stays empty and
present only so a future engine can't quietly recreate the same loophole
without a deliberate, reviewed decision to reintroduce it.

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

# Deliberately empty (2026-07-23) — every enabled engine now carries a
# hypothesis, closing the smc/price_action bypass. Kept as a named set
# (not deleted) so reintroducing an exemption is a visible, reviewable
# one-line diff instead of a re-derivation from scratch.
EXEMPT_ENGINES: set[str] = set()

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
    "smc":              "H101",  # SMC swing-structure — RESEARCH (governance closure, H015 evidence)
    "price_action":     "H102",  # Price Action — RESEARCH (governance closure, H015 evidence)
}

# Hypothesis statuses that allow engine activation
# "PASSED" = proven edge on real data
# "RESEARCH" = approved for paper trading / data collection only (not live)
ALLOWED_STATUSES = {"PASSED", "RESEARCH"}

# ── Promotion criteria (codified 2026-07-09, philosophy-audit follow-up) ──
# A hypothesis may only be TRUSTED as PASSED when its registry entry carries
# an `evidence` block meeting every bar below. This turns the promotion bar
# from prose into code: a PASSED entry without qualifying evidence is
# flagged loudly at boot and should be read as RESEARCH. (Enabling stays
# allowed — trust is what's withheld, so boot never breaks on legacy rows.)
PROMOTION_CRITERIA = {
    "min_trades": 300,          # sample size before PF means anything
    "min_oos_pf": 1.2,          # PF on an out-of-sample / forward slice
    "require_walk_forward": True,
    "require_monte_carlo": True,
}


def audit_passed_hypotheses(hypotheses: dict) -> list[str]:
    """One warning per PASSED hypothesis whose `evidence` block fails the
    codified promotion criteria. Non-fatal by design: it flags stale or
    under-evidenced PASSED statuses (e.g. H009's PF-3.08 walk-forward that
    the production audit found non-reproducible) without breaking boot."""
    warnings = []
    for hid, h in hypotheses.items():
        if h.get("status") != "PASSED":
            continue
        ev = h.get("evidence") or {}
        problems = []
        if (ev.get("oos_trades") or 0) < PROMOTION_CRITERIA["min_trades"]:
            problems.append(
                f"oos_trades={ev.get('oos_trades', 'missing')} < {PROMOTION_CRITERIA['min_trades']}")
        if (ev.get("oos_pf") or 0) < PROMOTION_CRITERIA["min_oos_pf"]:
            problems.append(
                f"oos_pf={ev.get('oos_pf', 'missing')} < {PROMOTION_CRITERIA['min_oos_pf']}")
        if PROMOTION_CRITERIA["require_walk_forward"] and not ev.get("walk_forward"):
            problems.append("walk_forward evidence missing")
        if PROMOTION_CRITERIA["require_monte_carlo"] and not ev.get("monte_carlo"):
            problems.append("monte_carlo evidence missing")
        if problems:
            warnings.append(
                f"{hid} is PASSED but fails the codified promotion criteria "
                f"({'; '.join(problems)}) — treat as RESEARCH until re-validated."
            )
    return warnings


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

    # Trust audit: loud, non-fatal. A PASSED status without qualifying
    # evidence must never silently launder itself into "proven".
    for warning in audit_passed_hypotheses(hypotheses):
        logger.warning(f"EDGE GATE TRUST AUDIT: {warning}")

    logger.info("Edge gate check passed — all enabled engines are exempt or proven.")
