"""tests/test_provider_symbol_resolution.py -- tests for backtest/
provider_symbol_resolution.py (Alpha Vantage / Finnhub Targeting Design
Gate, generic-symbol-resolution phase): the locked two-stage contract
(Stage 1 = backtest.shadow_outcome_resolver._provider_symbol(), reused
verbatim; Stage 2 = per-provider dispatch, "twelve_data", "alpha_vantage",
and "finnhub" all wired in), Stage 1 failure propagation unchanged, Stage
2 pass-through behavior for twelve_data, Stage 2 direct-call-through-to-
_to_av_symbol() for alpha_vantage (including its provider-shaped, non-str
provider_symbol and its always-None resolution_state), Stage 2
direct-lookup-against-FINNHUB_SYMBOL_MAP for finnhub (including its
genuinely reachable PROVIDER_SYMBOL_UNSUPPORTED path -- the first real
exercise of that branch -- and the absence of any equity special-casing),
ProviderSymbolResolutionError for any provider not yet wired in,
the exact result shape, and structural independence from
core.data_providers (beyond the two locked imports) / network / storage /
execution / scheduler."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import provider_symbol_resolution as psr
from backtest.shadow_outcome_resolver import ShadowOutcomeResolverError, _provider_symbol
from core.data_providers import FINNHUB_SYMBOL_MAP, _to_av_symbol


def _base_config() -> dict:
    return {"data": {"twelve_data_symbols": [{"internal": "EURUSD", "symbol": "EUR/USD", "rr": 2.0}]}}


# --- Stage 1 + Stage 2 happy path (twelve_data) -----------------------------


def test_twelve_data_resolves_to_shared_symbol_pass_through():
    result = psr.resolve_provider_symbol("EURUSD", "twelve_data", _base_config())
    assert result == {
        "internal_symbol": "EURUSD",
        "provider": "twelve_data",
        "shared_symbol": "EUR/USD",
        "provider_symbol": "EUR/USD",
        "resolution_state": None,
    }


def test_twelve_data_provider_symbol_equals_shared_symbol_regression():
    """Regression-equivalence: the provider_symbol returned here must be
    byte-identical to what _provider_symbol() itself returns directly --
    this module must never transform Twelve Data's own value."""
    config = _base_config()
    direct = _provider_symbol("EURUSD", config)
    result = psr.resolve_provider_symbol("EURUSD", "twelve_data", config)
    assert result["provider_symbol"] == direct
    assert result["shared_symbol"] == direct


def test_twelve_data_never_produces_unsupported_state():
    result = psr.resolve_provider_symbol("EURUSD", "twelve_data", _base_config())
    assert result["resolution_state"] is None
    assert result["provider_symbol"] is not None


# --- Stage 1 + Stage 2 happy path (alpha_vantage) ---------------------------


def test_alpha_vantage_resolves_to_from_to_tuple():
    result = psr.resolve_provider_symbol("EURUSD", "alpha_vantage", _base_config())
    assert result == {
        "internal_symbol": "EURUSD",
        "provider": "alpha_vantage",
        "shared_symbol": "EUR/USD",
        "provider_symbol": ("EUR", "USD"),
        "resolution_state": None,
    }


def test_alpha_vantage_provider_symbol_equals_direct_to_av_symbol_call_regression():
    """Regression-equivalence: the tuple returned here must be
    byte-identical to calling _to_av_symbol() directly on the same
    shared_symbol -- this module must never transform Alpha Vantage's
    own translated value."""
    config = _base_config()
    shared_symbol = _provider_symbol("EURUSD", config)
    direct = _to_av_symbol(shared_symbol)
    result = psr.resolve_provider_symbol("EURUSD", "alpha_vantage", config)
    assert result["provider_symbol"] == direct


def test_alpha_vantage_provider_symbol_is_a_tuple_not_a_string():
    """provider_symbol is provider-shaped, not provider-uniform (locked
    AV Stage 2 Design Gate): Alpha Vantage's genuine wire format is a
    (from_symbol, to_symbol) pair, never reconstructed into a slash-string
    or any other single-string normalization."""
    result = psr.resolve_provider_symbol("EURUSD", "alpha_vantage", _base_config())
    assert isinstance(result["provider_symbol"], tuple)
    assert result["provider_symbol"] == ("EUR", "USD")


def test_alpha_vantage_never_produces_unsupported_state():
    """_to_av_symbol() has no coverage predicate -- AV's
    resolution_state must always be None, never PROVIDER_SYMBOL_
    UNSUPPORTED, since inventing that path would require inventing a
    coverage predicate that does not exist in the real translator."""
    result = psr.resolve_provider_symbol("EURUSD", "alpha_vantage", _base_config())
    assert result["resolution_state"] is None


def test_alpha_vantage_malformed_input_passes_through_frozen_behavior_unchanged():
    """_to_av_symbol()'s existing malformed-input behavior (e.g. a
    no-slash internal-style symbol slicing blindly) is frozen, not
    validated or redesigned by this dispatcher -- the same tuple
    _to_av_symbol() itself would produce is returned, whatever it is."""
    config = {"data": {"twelve_data_symbols": [{"internal": "WEIRD", "symbol": "EURUSD"}]}}
    result = psr.resolve_provider_symbol("WEIRD", "alpha_vantage", config)
    assert result["provider_symbol"] == _to_av_symbol("EURUSD") == ("EUR", "USD")
    assert result["resolution_state"] is None


# --- Stage 1 + Stage 2 happy path (finnhub, mapped symbols) -----------------


def test_finnhub_resolves_mapped_fx_symbol():
    result = psr.resolve_provider_symbol("EURUSD", "finnhub", _base_config())
    assert result == {
        "internal_symbol": "EURUSD",
        "provider": "finnhub",
        "shared_symbol": "EUR/USD",
        "provider_symbol": "OANDA:EUR_USD",
        "resolution_state": None,
    }


def test_finnhub_resolves_mapped_metal_and_crypto_symbols():
    config = {"data": {"twelve_data_symbols": [
        {"internal": "XAUUSD", "symbol": "XAU/USD"},
        {"internal": "BTCUSD", "symbol": "BTC/USD"},
    ]}}
    xau = psr.resolve_provider_symbol("XAUUSD", "finnhub", config)
    btc = psr.resolve_provider_symbol("BTCUSD", "finnhub", config)
    assert xau["provider_symbol"] == "OANDA:XAU_USD"
    assert xau["resolution_state"] is None
    assert btc["provider_symbol"] == "BINANCE:BTCUSDT"
    assert btc["resolution_state"] is None


def test_finnhub_provider_symbol_equals_direct_map_lookup_regression():
    """Regression-equivalence: the value returned here must be
    byte-identical to looking the shared_symbol up in
    core.data_providers.FINNHUB_SYMBOL_MAP directly -- this module must
    never transform Finnhub's own mapped value."""
    config = _base_config()
    shared_symbol = _provider_symbol("EURUSD", config)
    direct = FINNHUB_SYMBOL_MAP[shared_symbol]
    result = psr.resolve_provider_symbol("EURUSD", "finnhub", config)
    assert result["provider_symbol"] == direct


# --- Stage 1 + Stage 2: finnhub genuinely produces PROVIDER_SYMBOL_UNSUPPORTED --


def test_finnhub_unmapped_metal_produces_unsupported_state():
    """XAG/USD is listed in config.yaml's metals chain for finnhub, but
    FINNHUB_SYMBOL_MAP has no entry for it -- PROVIDER_SYMBOL_UNSUPPORTED
    must reflect ACTUAL map coverage, never provider-chain membership."""
    config = {"data": {"twelve_data_symbols": [{"internal": "XAGUSD", "symbol": "XAG/USD"}]}}
    result = psr.resolve_provider_symbol("XAGUSD", "finnhub", config)
    assert result == {
        "internal_symbol": "XAGUSD",
        "provider": "finnhub",
        "shared_symbol": "XAG/USD",
        "provider_symbol": None,
        "resolution_state": psr.PROVIDER_SYMBOL_UNSUPPORTED,
    }


def test_finnhub_unmapped_energy_symbol_produces_unsupported_state():
    config = {"data": {"twelve_data_symbols": [{"internal": "USOIL", "symbol": "WTI/USD"}]}}
    result = psr.resolve_provider_symbol("USOIL", "finnhub", config)
    assert result["provider_symbol"] is None
    assert result["resolution_state"] == psr.PROVIDER_SYMBOL_UNSUPPORTED


def test_finnhub_unmapped_index_symbol_produces_unsupported_state():
    config = {"data": {"twelve_data_symbols": [{"internal": "US30", "symbol": "DJI/USD"}]}}
    result = psr.resolve_provider_symbol("US30", "finnhub", config)
    assert result["provider_symbol"] is None
    assert result["resolution_state"] == psr.PROVIDER_SYMBOL_UNSUPPORTED


def test_finnhub_equity_shaped_symbol_is_an_ordinary_miss_no_special_casing():
    """Locked instruction: no _is_equity_symbol() branch exists in
    _resolve_finnhub(). An equity-shaped shared_symbol reaching this
    resolver is simply absent from the map -- an ordinary miss, exactly
    like XAG/USD or any other unmapped symbol, not a distinct code path."""
    config = {"data": {"twelve_data_symbols": [{"internal": "AAPL", "symbol": "AAPL/USD"}]}}
    result = psr.resolve_provider_symbol("AAPL", "finnhub", config)
    assert result["provider_symbol"] is None
    assert result["resolution_state"] == psr.PROVIDER_SYMBOL_UNSUPPORTED


def test_resolve_finnhub_has_no_equity_detection_call_in_its_own_source():
    """Structural confirmation of the no-equity-special-casing lock:
    _resolve_finnhub's own function body (not the module docstring)
    never calls _is_equity_symbol."""
    source = inspect.getsource(psr._resolve_finnhub)
    body = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    assert "_is_equity_symbol" not in body


def test_finnhub_is_the_first_resolver_to_return_none():
    """Documents and locks in the Finnhub Design Gate finding: twelve_data
    and alpha_vantage can never produce None from their own Stage 2
    function; finnhub was the first resolver wired in this chain that
    can. (alpaca, wired in a later phase, is the second -- see
    tests/test_alpaca_provider.py::test_resolve_alpaca_is_second_resolver_to_return_none.)"""
    assert psr._resolve_twelve_data("XAU/USD") is not None
    assert psr._resolve_alpha_vantage("XAU/USD") is not None
    assert psr._resolve_finnhub("XAG/USD") is None


# --- Stage 1 failure propagation (unchanged) --------------------------------


def test_missing_stage_1_mapping_raises_shadow_outcome_resolver_error():
    """A missing Stage 1 mapping is Stage 1's own structural failure --
    propagated completely unchanged, never caught or re-wrapped by this
    module."""
    config = _base_config()
    with pytest.raises(ShadowOutcomeResolverError, match="no provider symbol mapping found"):
        psr.resolve_provider_symbol("UNKNOWNSYMBOL", "twelve_data", config)


def test_stage_1_failure_raised_before_stage_2_dispatch_check_is_irrelevant():
    """Even with a provider wired into Stage 2, a Stage 1 identity problem
    still raises ShadowOutcomeResolverError, not ProviderSymbolResolutionError."""
    config = {"data": {"twelve_data_symbols": []}}
    with pytest.raises(ShadowOutcomeResolverError):
        psr.resolve_provider_symbol("EURUSD", "twelve_data", config)


# --- unwired provider: ProviderSymbolResolutionError ------------------------
# (every REAL provider wired in so far -- twelve_data, alpha_vantage,
# finnhub -- is now wired; "fcs_api" is a real provider.chain name
# (config.yaml) that is simply not wired into THIS dispatcher yet, used
# here only to exercise the unwired-provider path.)


def test_unwired_provider_raises_provider_symbol_resolution_error():
    config = _base_config()
    with pytest.raises(psr.ProviderSymbolResolutionError, match="fcs_api"):
        psr.resolve_provider_symbol("EURUSD", "fcs_api", config)


def test_unwired_provider_error_is_distinct_from_unsupported_state():
    """ProviderSymbolResolutionError means 'not yet implemented in this
    dispatcher' -- a structurally different fact from
    PROVIDER_SYMBOL_UNSUPPORTED, which this error message must never
    claim."""
    config = _base_config()
    with pytest.raises(psr.ProviderSymbolResolutionError) as exc_info:
        psr.resolve_provider_symbol("EURUSD", "fcs_api", config)
    message = str(exc_info.value)
    assert "could not, in principle, serve this symbol" in message


def test_unwired_provider_check_happens_before_stage_1_lookup():
    """An unwired provider raises ProviderSymbolResolutionError even when
    Stage 1 would also have failed -- the provider-wiring check is a
    structural precondition evaluated first."""
    config = {"data": {"twelve_data_symbols": []}}
    with pytest.raises(psr.ProviderSymbolResolutionError):
        psr.resolve_provider_symbol("UNKNOWNSYMBOL", "fcs_api", config)


# --- _STAGE_2_RESOLVERS registry content (this phase only) -----------------


def test_stage_2_resolvers_registry_contains_exactly_four_wired_providers():
    assert set(psr._STAGE_2_RESOLVERS) == {"twelve_data", "alpha_vantage", "finnhub", "alpaca"}


# --- result shape ------------------------------------------------------------


@pytest.mark.parametrize("provider", ["twelve_data", "alpha_vantage", "finnhub", "alpaca"])
def test_result_shape_has_exactly_five_keys(provider):
    result = psr.resolve_provider_symbol("EURUSD", provider, _base_config())
    assert set(result.keys()) == {
        "internal_symbol", "provider", "shared_symbol", "provider_symbol", "resolution_state",
    }


# --- structural independence -------------------------------------------------


def _source_without_docstrings() -> str:
    source = inspect.getsource(psr)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_core_data_providers_coupling_is_exactly_the_locked_imports():
    """This module's core.data_providers coupling is narrowed, not
    absent: the AV, Finnhub, and Alpaca Stage 2 Design Gates authorize
    importing ONLY FINNHUB_SYMBOL_MAP, _to_av_symbol, _alpaca_crypto_
    symbol, _internal_symbol, and _is_equity_symbol, by direct
    reference, never copied/reimplemented. Every other
    core.data_providers name (fetch_with_failover, _fetch_alpha_vantage,
    _fetch_finnhub, _fetch_alpaca, DataFetchError, etc.) stays
    unimported, and Stage 1 is still reused via
    backtest.shadow_outcome_resolver only."""
    body = _source_without_docstrings()
    expected_import = (
        "from core.data_providers import (\n"
        "    FINNHUB_SYMBOL_MAP,\n"
        "    _alpaca_crypto_symbol,\n"
        "    _internal_symbol,\n"
        "    _is_equity_symbol,\n"
        "    _to_av_symbol,\n"
        ")"
    )
    assert body.count(expected_import) == 1
    forbidden = (
        "import core.data_providers\n", "fetch_with_failover",
        "_fetch_alpha_vantage", "_fetch_finnhub", "_fetch_alpaca", "DataFetchError",
    )
    for pattern in forbidden:
        assert pattern not in body, f"provider_symbol_resolution unexpectedly references {pattern!r}"


def test_no_storage_execution_scheduler_or_main_import():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "from storage",
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"provider_symbol_resolution unexpectedly references {pattern!r}"


def test_stage_1_reused_by_direct_call_never_copied():
    """The operator's own locked invariant: Stage 1 logic must be reused
    by direct import/call of _provider_symbol(), never redefined or
    duplicated in this module."""
    body = _source_without_docstrings()
    assert "from backtest.shadow_outcome_resolver import _provider_symbol" in body
    # No second implementation of the twelve_data_symbols lookup loop.
    assert "twelve_data_symbols" not in body


def test_module_is_pure_no_network_or_randomness_markers():
    body = _source_without_docstrings()
    forbidden = ("requests.", "httpx.", "urllib", "random.", "open(", "sqlite3", "d1_connection")
    for pattern in forbidden:
        assert pattern not in body, f"provider_symbol_resolution unexpectedly references {pattern!r}"
