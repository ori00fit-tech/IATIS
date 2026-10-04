"""
backtest/provider_symbol_resolution.py
---------------------------------------
Generic Symbol Resolution (operator's own locked Design Gate).

TWO-STAGE CONTRACT, locked:

  Stage 1 (config-driven): internal symbol -> shared slash-form symbol
    (e.g. "XAUUSD" -> "XAU/USD"). Reuses backtest.shadow_outcome_
    resolver._provider_symbol() VERBATIM -- never redefines, never
    duplicates its logic, never changes any value it returns. The
    operator's own locked clarifying finding: that function was never
    fundamentally a Twelve-Data-only translator; `twelve_data_symbols`
    is an inaccurate implementation/configuration NAME for what is
    actually the shared intermediate symbol format every slash-based
    provider (Twelve Data, Alpha Vantage, Finnhub) already consumes. A
    missing Stage 1 mapping raises ShadowOutcomeResolverError --
    propagated completely unchanged from that function, never caught or
    re-wrapped here. A configuration/identity problem, never silently
    converted into a state.

  Stage 2 (provider-code-driven): shared slash-form symbol -> that
    provider's own wire format. A per-symbol coverage gap (the shared
    symbol is known, but THIS provider's own translator cannot
    represent it) returns the single, generic PROVIDER_SYMBOL_
    UNSUPPORTED state -- never a provider-specific variant (no
    FINNHUB_SYMBOL_UNSUPPORTED, no AV_SYMBOL_UNSUPPORTED, locked
    explicitly as a single, non-multiplying name in the resolver's
    public contract). Finnhub's own closed symbol map remains an
    internal implementation detail of *how* that state might eventually
    be reached for Finnhub specifically -- never part of this module's
    own public shape.

WHAT THIS PHASE WIRES IN, and NOTHING MORE (operator's own locked
scope): Stage 2 is implemented for "twelve_data" ONLY -- a pass-through,
since the shared slash-form symbol already IS Twelve Data's own wire
format (confirmed by _provider_symbol()'s own existing, unmodified
behavior and every caller that already uses its return value directly
as the Twelve Data request symbol).

Calling resolve_provider_symbol() with any OTHER provider name raises
ProviderSymbolResolutionError -- a DIFFERENT fact from PROVIDER_SYMBOL_
UNSUPPORTED: it means "no Stage 2 translator is wired into this
dispatcher for that provider yet," never a claim about whether that
provider could, in principle, serve the symbol. Alpha Vantage's own
_to_av_symbol() and Finnhub's own symbol map are explicitly NOT wired in
here -- per the operator's own locked instruction, they remain future
consumers of this same contract, not something this phase builds. This
module never imports core.data_providers at all (no _to_av_symbol, no
Finnhub map, no fetch_with_failover) -- confirmed by this module's own
structural tests.

TWELVE DATA REGRESSION PROTECTION (locked): this module changes no
value backtest.shadow_outcome_resolver._provider_symbol() or
backtest.shadow_outcome_verification.verify_historical_stability()
already return or use for Twelve Data. Neither existing module is
modified by this phase -- this is a purely additive, parallel entry
point, reusing _provider_symbol() by direct call, not by copy.

NON-NEGOTIABLE: no date-range behavior change, no Finnhub coverage
expansion, no fetch/governance logic, no change to any provider's own
fetch function, no network or storage access anywhere in this module.
"""
from __future__ import annotations

from typing import Any

from backtest.shadow_outcome_resolver import _provider_symbol

PROVIDER_SYMBOL_UNSUPPORTED = "PROVIDER_SYMBOL_UNSUPPORTED"

__all__ = [
    "PROVIDER_SYMBOL_UNSUPPORTED", "ProviderSymbolResolutionError",
    "resolve_provider_symbol",
]


class ProviderSymbolResolutionError(Exception):
    """Structural misuse only -- no Stage 2 translator is wired into
    this dispatcher for the requested provider name. NEVER a claim
    about whether that provider could, in principle, serve this symbol
    -- that is exactly what PROVIDER_SYMBOL_UNSUPPORTED means, and it is
    a deliberately different fact. This error means "not yet
    implemented in this dispatcher," never "unsupported by the
    provider.\""""


def _resolve_twelve_data(shared_symbol: str) -> str:
    """Stage 2 for twelve_data: a pass-through. The shared slash-form
    symbol (e.g. "XAU/USD") already IS Twelve Data's own wire format --
    this performs no transformation and can never fail to represent its
    own input, so it never returns None (never produces
    PROVIDER_SYMBOL_UNSUPPORTED)."""
    return shared_symbol


# Wired-in Stage 2 translators, this phase only. Adding "alpha_vantage"
# or "finnhub" here is explicitly future work (per the locked Design
# Gate) -- not something this phase authorizes.
_STAGE_2_RESOLVERS = {
    "twelve_data": _resolve_twelve_data,
}


def resolve_provider_symbol(
    internal_symbol: str,
    provider: str,
    base_config: dict[str, Any],
) -> dict[str, Any]:
    """The SOLE entry point. Stage 1 resolves `internal_symbol` to the
    shared slash-form symbol by calling backtest.shadow_outcome_
    resolver._provider_symbol() directly (never a copy of its logic) --
    a missing Stage 1 mapping raises ShadowOutcomeResolverError,
    propagated completely unchanged.

    Stage 2 dispatches on `provider`. If no Stage 2 translator is wired
    in for that provider name, raises ProviderSymbolResolutionError (a
    "not implemented here" structural fact, never a coverage claim).
    This phase wires in ONLY "twelve_data".

    Returns exactly: {internal_symbol, provider, shared_symbol,
    provider_symbol, resolution_state}. `resolution_state` is None when
    fully resolved, or PROVIDER_SYMBOL_UNSUPPORTED when Stage 2
    recognized the provider but could not represent this specific
    shared_symbol -- unreachable in this phase (twelve_data's
    pass-through can never fail), reserved as honest, non-dead
    vocabulary for providers wired in later.

    Pure -- no network call, no storage read or write, no randomness.
    """
    if provider not in _STAGE_2_RESOLVERS:
        raise ProviderSymbolResolutionError(
            f"resolve_provider_symbol: no Stage 2 translator is wired into this dispatcher for "
            f"provider {provider!r} yet (only {sorted(_STAGE_2_RESOLVERS)} are wired in this "
            f"phase) -- this is a 'not yet implemented here' fact, never a claim that "
            f"{provider!r} could not, in principle, serve this symbol."
        )

    shared_symbol = _provider_symbol(internal_symbol, base_config)
    provider_symbol = _STAGE_2_RESOLVERS[provider](shared_symbol)

    if provider_symbol is None:
        return {
            "internal_symbol": internal_symbol,
            "provider": provider,
            "shared_symbol": shared_symbol,
            "provider_symbol": None,
            "resolution_state": PROVIDER_SYMBOL_UNSUPPORTED,
        }

    return {
        "internal_symbol": internal_symbol,
        "provider": provider,
        "shared_symbol": shared_symbol,
        "provider_symbol": provider_symbol,
        "resolution_state": None,
    }
