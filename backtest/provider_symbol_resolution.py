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
    public contract). Finnhub's own closed symbol map (see below) is
    the FIRST real exercise of this state -- it was reachable in theory
    since this module's creation, never exercised by twelve_data or
    alpha_vantage, both of which can never fail.

WHAT THIS PHASE WIRES IN, and NOTHING MORE (operator's own locked
scope): Stage 2 is implemented for "twelve_data" (a pass-through, since
the shared slash-form symbol already IS Twelve Data's own wire format),
"alpha_vantage" (operator's own locked AV Stage 2 Design Gate, 2026),
"finnhub" (operator's own locked Finnhub Stage 2 Design Gate, 2026),
AND "alpaca" (operator's own locked Alpaca Stage 2 Design Gate, 2026)
-- see below.

Calling resolve_provider_symbol() with any OTHER provider name raises
ProviderSymbolResolutionError -- a DIFFERENT fact from PROVIDER_SYMBOL_
UNSUPPORTED: it means "no Stage 2 translator is wired into this
dispatcher for that provider yet," never a claim about whether that
provider could, in principle, serve the symbol.

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
  is imported directly from core.data_providers for Alpha Vantage, by
  direct call, never copied or reimplemented. _fetch_alpha_vantage,
  fetch_with_failover, and every other core.data_providers name remain
  unimported here -- confirmed by this module's own structural tests.

FINNHUB STAGE 2 (operator's own locked Design Gate, distinct from both
contracts above -- read carefully before touching this provider's
wiring):

  _resolve_finnhub() reads core.data_providers.FINNHUB_SYMBOL_MAP
  DIRECTLY -- the SAME 12-entry constant core.data_providers._fetch_
  finnhub() itself reads, extracted from that function's own body to
  module scope BY THIS GATE (it was previously a local dict re-created
  on every _fetch_finnhub() call) so this module can reuse it by direct
  reference, never a second copy. core.data_providers.py IS modified by
  this phase -- the one and only exception, across every phase in this
  Design Gate chain, to the otherwise-universal "zero diff on
  core/data_providers.py" closing check. The replacement closing
  standard for this phase is BEHAVIOR-EQUIVALENCE of _fetch_finnhub():
  all 12 previously-covered symbols must produce the exact same
  fh_symbol value, and an unmapped symbol must raise the exact same
  DataFetchError type and message, both before and after the
  extraction -- proven by tests, not by an empty diff.

  Unlike Alpha Vantage's _to_av_symbol() (no coverage predicate at
  all), Finnhub's map IS a genuine, pre-existing, closed coverage
  predicate -- a dict with exactly 12 keys, all FX pairs plus XAU/USD,
  BTC/USD, ETH/USD. _resolve_finnhub() is nothing but a lookup against
  it: shared_symbol in the map -> that entry's value; shared_symbol NOT
  in the map -> None, which resolve_provider_symbol() then correctly
  reports as PROVIDER_SYMBOL_UNSUPPORTED. This is the FIRST Stage 2
  resolver to ever return None, and therefore the first to actually
  exercise that branch.

  PROVIDER_SYMBOL_UNSUPPORTED for "finnhub" reflects ACTUAL map
  coverage, never provider-chain membership (operator's own locked
  distinction): config.yaml lists "finnhub" in the metals, energy, and
  indices chains, but FINNHUB_SYMBOL_MAP has no XAG/USD entry and no
  energy or index entries at all -- so XAG/USD, USOIL, US30/NAS100/
  SPX500 (and any other non-FX/metals/crypto shared symbol) all
  correctly resolve to PROVIDER_SYMBOL_UNSUPPORTED here, honestly
  exposing a real gap the provider chain's own membership list does
  not reveal. This phase does NOT add a single new mapping to close
  that gap -- expanding coverage is explicitly out of scope.

  No equity special-casing exists in _resolve_finnhub() (operator's own
  locked instruction): it is a plain dict lookup with no
  _is_equity_symbol() branch of any kind. An equity-shaped shared
  symbol reaching this function is simply absent from the map -- an
  ordinary miss, resolved to PROVIDER_SYMBOL_UNSUPPORTED like any other
  unmapped symbol, not a special case requiring its own logic.

ALPACA STAGE 2 (operator's own locked Design Gate, distinct from all
three contracts above -- read carefully before touching this
provider's wiring):

  Stage 1 required NO changes for this gate: config/symbols.yaml's
  twelve_data_symbols already has entries for every alpaca-relevant
  internal symbol (BTCUSD/ETHUSD -> "BTC/USD"/"ETH/USD", AAPL/NVDA/
  SPY/QQQ -> themselves, unchanged) -- confirmed by direct evidence
  before this gate was locked, not assumed.

  _resolve_alpaca() dispatches on the SAME two-path split
  core.data_providers._fetch_alpaca() itself uses: equity ->
  core.data_providers._is_equity_symbol() (already a module-level,
  pure, reusable function -- no extraction needed); crypto ->
  core.data_providers._alpaca_crypto_symbol() (NEWLY extracted BY THIS
  GATE from what was previously an inline check-and-reconstruct inside
  _fetch_alpaca's body, mirroring the Finnhub extraction precedent).
  core.data_providers.py IS modified by this phase -- the second
  exception (after Finnhub) to the otherwise-universal "zero diff on
  core/data_providers.py" closing check; the replacement standard is
  again BEHAVIOR-EQUIVALENCE, proven by tests: _fetch_alpaca's exact
  raise type and message for an unsupported crypto symbol are
  unchanged, and every previously-working symbol still produces the
  exact same alpaca_symbol value.

  For the equity path, provider_symbol is the shared_symbol UNCHANGED
  (identity) -- _fetch_alpaca_equity() does no translation at all,
  interpolating the raw symbol straight into its request URL. For the
  crypto path, provider_symbol is also, after round-tripping through
  _internal_symbol() and _alpaca_crypto_symbol(), the SAME string the
  shared_symbol started as (e.g. "BTC/USD" -> "BTCUSD" -> "BTC/USD") --
  so unlike Alpha Vantage, provider_symbol for "alpaca" is ALWAYS a
  `str`, never a tuple; no new type heterogeneity is introduced by this
  gate.

  _CRYPTO (core.data_providers's own module-level set, {"BTCUSD",
  "ETHUSD"}) remains the SOLE source of truth for crypto coverage --
  _alpaca_crypto_symbol() reads it, never redefines or shadows it with
  a second whitelist. Likewise _STOCKS/_ETF (via _is_equity_symbol())
  are reused, never duplicated. This phase adds NO new entries to any
  of these three sets -- expanding coverage is explicitly out of
  scope, exactly as it was for Finnhub's map.

  resolution_state is genuinely reachable as PROVIDER_SYMBOL_UNSUPPORTED
  for "alpaca" -- the SECOND Stage 2 resolver (after Finnhub) to
  actually exercise that branch, for ANY shared_symbol that is neither
  a recognized equity/ETF nor a recognized crypto pair.

  EXPLICIT EPISTEMIC BOUNDARY (operator's own locked instruction,
  stated for the record): a successful resolution for "alpaca" --
  including the identity case, e.g. "AAPL" -> "AAPL" -- proves ONLY
  that the symbol passes Alpaca's own known coverage predicate
  (_CRYPTO/_STOCKS/_ETF membership). It is NEVER a claim that Alpaca
  will actually serve this instrument at fetch time -- that remains
  the fetch layer's own responsibility (API keys, network, rate
  limits, endpoint availability), exactly as "syntactically
  transformable" was never conflated with "provider capability
  confirmed" for Alpha Vantage.

TWELVE DATA REGRESSION PROTECTION (locked): this module changes no
value backtest.shadow_outcome_resolver._provider_symbol() or
backtest.shadow_outcome_verification.verify_historical_stability()
already return or use for Twelve Data. Neither existing module is
modified by this phase -- this is a purely additive, parallel entry
point, reusing _provider_symbol() by direct call, not by copy.

NON-NEGOTIABLE: no date-range behavior change, no Finnhub coverage
expansion (no new XAG/energy/index mappings), no equity redesign, no
HTTP-403 repair, no provider-chain changes, no fetch/failover redesign,
no _CRYPTO/_STOCKS/_ETF expansion, no network or storage access
anywhere in this module.
"""
from __future__ import annotations

from typing import Any, Callable

from backtest.shadow_outcome_resolver import _provider_symbol
from core.data_providers import (
    FINNHUB_SYMBOL_MAP,
    _alpaca_crypto_symbol,
    _internal_symbol,
    _is_equity_symbol,
    _to_av_symbol,
)

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


def _resolve_finnhub(shared_symbol: str) -> str | None:
    """Stage 2 for finnhub: a plain dict lookup against
    core.data_providers.FINNHUB_SYMBOL_MAP -- the SAME 12-entry constant
    core.data_providers._fetch_finnhub() itself reads, read directly,
    never copied.

    Unlike _to_av_symbol(), this map IS a genuine, pre-existing, closed
    coverage predicate: shared_symbol in the map -> that entry's own
    Finnhub wire-format value; shared_symbol NOT in the map -> None,
    which resolve_provider_symbol() reports as PROVIDER_SYMBOL_
    UNSUPPORTED. This correctly reflects ACTUAL Finnhub coverage (only
    FX pairs + XAU/USD + BTC/USD + ETH/USD), never provider-chain
    membership -- config.yaml lists "finnhub" in metals/energy/indices
    chains the map does not actually cover (no XAG/USD, no energy, no
    index entries), and this function does not paper over that gap.

    No equity special-casing: an equity-shaped shared_symbol is simply
    absent from the map, an ordinary miss like any other unmapped
    symbol -- there is no _is_equity_symbol() branch here."""
    return FINNHUB_SYMBOL_MAP.get(shared_symbol)


def _resolve_alpaca(shared_symbol: str) -> str | None:
    """Stage 2 for alpaca: reuses the SAME two-path split
    core.data_providers._fetch_alpaca() itself uses, never reimplementing
    either decision.

    Equity/ETF (core.data_providers._is_equity_symbol(), already
    module-level and pure): provider_symbol is the shared_symbol
    UNCHANGED -- _fetch_alpaca_equity() does no translation of its own,
    passing the raw ticker straight through.

    Crypto (core.data_providers._alpaca_crypto_symbol(), NEWLY extracted
    by this Design Gate from what was an inline check inside
    _fetch_alpaca's body): shared_symbol -> _internal_symbol() ->
    _alpaca_crypto_symbol() -> the SAME string the shared_symbol started
    as (e.g. "BTC/USD" round-trips to "BTC/USD"), or None if
    _CRYPTO does not recognize it.

    _CRYPTO/_STOCKS/_ETF (core.data_providers's own sets) remain the
    SOLE source of truth for coverage -- this function adds no new
    whitelist of its own. Any shared_symbol that is neither a
    recognized equity/ETF nor a recognized crypto pair returns None,
    which resolve_provider_symbol() reports as PROVIDER_SYMBOL_
    UNSUPPORTED -- the second Stage 2 resolver (after Finnhub) to
    actually exercise that branch.

    EXPLICIT EPISTEMIC BOUNDARY: a non-None result here -- including
    the equity identity case -- means ONLY "this symbol passes Alpaca's
    own known coverage predicate." It is NEVER a claim that Alpaca will
    actually serve this instrument at fetch time; that remains the
    fetch layer's own responsibility."""
    if _is_equity_symbol(shared_symbol):
        return shared_symbol
    internal = _internal_symbol(shared_symbol)
    return _alpaca_crypto_symbol(internal)


# Wired-in Stage 2 translators. Return types are deliberately
# heterogeneous across providers (str for twelve_data, tuple[str, str]
# for alpha_vantage, str | None for finnhub and alpaca) -- provider_symbol
# is provider-shaped, not provider-uniform, per the operator's own locked
# AV Stage 2 Design Gate: forcing Alpha Vantage's genuine (from_symbol,
# to_symbol) wire format into a single string would be an unauthorized
# normalization layer this gate does not grant.
_STAGE_2_RESOLVERS: dict[str, Callable[[str], Any]] = {
    "twelve_data": _resolve_twelve_data,
    "alpha_vantage": _resolve_alpha_vantage,
    "finnhub": _resolve_finnhub,
    "alpaca": _resolve_alpaca,
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
    This phase wires in "twelve_data", "alpha_vantage", "finnhub", and
    "alpaca".

    Returns exactly: {internal_symbol, provider, shared_symbol,
    provider_symbol, resolution_state}. `provider_symbol` is
    provider-shaped, not provider-uniform: a `str` for "twelve_data", a
    `tuple[str, str]` for "alpha_vantage" (Alpha Vantage's own wire
    format genuinely is a (from_symbol, to_symbol) pair -- passed
    through unchanged, never reconstructed into a string), a `str` for
    "finnhub"/"alpaca" when mapped. `resolution_state` is None when
    fully resolved, or PROVIDER_SYMBOL_UNSUPPORTED when Stage 2
    recognized the provider but could not represent this specific
    shared_symbol -- unreachable for "twelve_data" and "alpha_vantage"
    (neither translator can ever fail: twelve_data's pass-through
    trivially, alpha_vantage's _to_av_symbol() because it has no
    coverage predicate at all, see _resolve_alpha_vantage()'s own
    docstring), but genuinely reachable for "finnhub" (its closed
    symbol map CAN and does fail closed for any shared_symbol outside
    its 12 entries) and for "alpaca" (any shared_symbol that is neither
    a recognized equity/ETF nor a recognized crypto pair) -- see each
    resolver's own docstring.

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
