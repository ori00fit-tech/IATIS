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
scope): Stage 2 is implemented for "twelve_data" (a pass-through, since
the shared slash-form symbol already IS Twelve Data's own wire format)
AND "alpha_vantage" (operator's own locked AV Stage 2 Design Gate,
2026) -- see below.

Calling resolve_provider_symbol() with any OTHER provider name raises
ProviderSymbolResolutionError -- a DIFFERENT fact from PROVIDER_SYMBOL_
UNSUPPORTED: it means "no Stage 2 translator is wired into this
dispatcher for that provider yet," never a claim about whether that
provider could, in principle, serve the symbol. Finnhub's own symbol
map is explicitly NOT wired in here -- per the operator's own locked
instruction, it remains a future consumer of this same contract, not
something this phase builds.

ALPHA VANTAGE STAGE 2 (operator's own locked Design Gate, distinct from
the generic contract above -- read carefully before touching this
provider's wiring):

  _resolve_alpha_vantage() calls core.data_providers's own
  _to_av_symbol() DIRECTLY -- never a copy or reimplementation of its
  splitting logic. Gate 0 established (forensically, by direct code
  reading) that _to_av_symbol() has NO coverage predicate: it is a pure
  syntactic transformer (slash-split, or a blind symbol[:3]/symbol[3:]
  slice otherwise) that ALWAYS returns a 2-tuple of strings and NEVER
  raises or returns None -- structurally unlike Finnhub's own closed
  symbol map, which can and does fail closed.

  Because of this, AV's resolution_state is LOCKED to always be None in
  this phase -- there is no invented PROVIDER_SYMBOL_UNSUPPORTED path
  for Alpha Vantage, since inventing one would require inventing a
  coverage predicate that does not exist in the real translator. A
  non-None provider_symbol for "alpha_vantage" means ONLY "syntactically
  transformable by _to_av_symbol()" -- it is NEVER a claim that Alpha
  Vantage actually supports serving this instrument/endpoint. Successful
  syntactic translation and confirmed provider capability are two
  different facts, and this module asserts only the former.

  provider_symbol is PROVIDER-SHAPED, not provider-uniform, by the
  operator's own locked instruction: it is a `str` for "twelve_data" and
  a `tuple[str, str]` for "alpha_vantage" (Alpha Vantage's own wire
  format genuinely is a (from_symbol, to_symbol) pair, not a single
  string -- forcing it into a string would be an unauthorized
  normalization layer). The tuple _to_av_symbol() returns is passed
  through completely unchanged -- no reconstruction, no re-joining into
  a slash-string, no serialization of any kind.

  _to_av_symbol()'s existing edge/malformed-input behavior (silently
  dropping a third slash-separated segment, producing an empty
  to_symbol for short no-slash inputs, etc.) is FROZEN for this gate --
  explicitly not "fixed," not validated, not redesigned. Any future
  tightening of that behavior is its own separate, future Design Gate.

  This module's "no core.data_providers coupling" invariant is narrowed,
  not dropped, by this gate: _to_av_symbol -- and ONLY _to_av_symbol --
  is now imported directly from core.data_providers, by direct call,
  never copied or reimplemented. _fetch_alpha_vantage,
  fetch_with_failover, FINNHUB_SYMBOL_MAP, and every other
  core.data_providers name remain unimported here -- confirmed by this
  module's own structural tests, which now assert the forbidden-name
  list excluding only this one locked exception.

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

from typing import Any, Callable

from backtest.shadow_outcome_resolver import _provider_symbol
from core.data_providers import _to_av_symbol

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


def _resolve_alpha_vantage(shared_symbol: str) -> tuple[str, str]:
    """Stage 2 for alpha_vantage: calls core.data_providers._to_av_symbol()
    DIRECTLY (never a copy of its splitting logic) and returns its
    (from_symbol, to_symbol) tuple completely unchanged -- no
    reconstruction, no re-joining into a slash-string, no serialization.

    _to_av_symbol() has NO coverage predicate (Gate 0 finding, confirmed
    by direct code reading): it always returns a 2-tuple of strings and
    never raises or returns None, even for malformed input. Because of
    this, this function never returns None and this provider's
    resolution_state is therefore always None in resolve_provider_symbol()
    -- there is no invented PROVIDER_SYMBOL_UNSUPPORTED path for Alpha
    Vantage. A non-None result here means ONLY "syntactically
    transformable by _to_av_symbol()" -- it is NEVER a claim that Alpha
    Vantage actually supports serving this instrument/endpoint.
    _to_av_symbol()'s existing edge/malformed-input behavior is frozen
    and unvalidated here, exactly as it is in core.data_providers itself
    -- changing it is out of scope for this Design Gate."""
    return _to_av_symbol(shared_symbol)


# Wired-in Stage 2 translators, this phase only. Adding "finnhub" here
# is explicitly future work (per the locked Design Gate) -- not
# something this phase authorizes. Return types are deliberately
# heterogeneous across providers (str for twelve_data, tuple[str, str]
# for alpha_vantage) -- provider_symbol is provider-shaped, not
# provider-uniform, per the operator's own locked AV Stage 2 Design
# Gate: forcing Alpha Vantage's genuine (from_symbol, to_symbol) wire
# format into a single string would be an unauthorized normalization
# layer this gate does not grant.
_STAGE_2_RESOLVERS: dict[str, Callable[[str], Any]] = {
    "twelve_data": _resolve_twelve_data,
    "alpha_vantage": _resolve_alpha_vantage,
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
    This phase wires in "twelve_data" and "alpha_vantage" only.

    Returns exactly: {internal_symbol, provider, shared_symbol,
    provider_symbol, resolution_state}. `provider_symbol` is
    provider-shaped, not provider-uniform: a `str` for "twelve_data", a
    `tuple[str, str]` for "alpha_vantage" (Alpha Vantage's own wire
    format genuinely is a (from_symbol, to_symbol) pair -- passed
    through unchanged, never reconstructed into a string).
    `resolution_state` is None when fully resolved, or
    PROVIDER_SYMBOL_UNSUPPORTED when Stage 2 recognized the provider but
    could not represent this specific shared_symbol -- unreachable for
    both providers wired in this phase (neither translator can fail:
    twelve_data's pass-through trivially, alpha_vantage's
    _to_av_symbol() because it has no coverage predicate at all, see
    _resolve_alpha_vantage()'s own docstring), reserved as honest,
    non-dead vocabulary for providers wired in later (e.g. Finnhub's own
    closed symbol map, which CAN fail closed).

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
