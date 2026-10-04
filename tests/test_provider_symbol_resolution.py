"""tests/test_provider_symbol_resolution.py -- tests for backtest/
provider_symbol_resolution.py (Alpha Vantage / Finnhub Targeting Design
Gate, generic-symbol-resolution phase): the locked two-stage contract
(Stage 1 = backtest.shadow_outcome_resolver._provider_symbol(), reused
verbatim; Stage 2 = per-provider dispatch, "twelve_data" wired in this
phase only), Stage 1 failure propagation unchanged, Stage 2 pass-through
behavior for twelve_data, ProviderSymbolResolutionError for any provider
not yet wired in, the exact result shape, and structural independence
from core.data_providers / network / storage / execution / scheduler."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import provider_symbol_resolution as psr
from backtest.shadow_outcome_resolver import ShadowOutcomeResolverError, _provider_symbol


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


def test_unwired_provider_raises_provider_symbol_resolution_error():
    config = _base_config()
    with pytest.raises(psr.ProviderSymbolResolutionError, match="alpha_vantage"):
        psr.resolve_provider_symbol("EURUSD", "alpha_vantage", config)


def test_finnhub_also_unwired_in_this_phase():
    config = _base_config()
    with pytest.raises(psr.ProviderSymbolResolutionError, match="finnhub"):
        psr.resolve_provider_symbol("EURUSD", "finnhub", config)


def test_unwired_provider_error_is_distinct_from_unsupported_state():
    """ProviderSymbolResolutionError means 'not yet implemented in this
    dispatcher' -- a structurally different fact from
    PROVIDER_SYMBOL_UNSUPPORTED, which this error message must never
    claim."""
    config = _base_config()
    with pytest.raises(psr.ProviderSymbolResolutionError) as exc_info:
        psr.resolve_provider_symbol("EURUSD", "alpha_vantage", config)
    message = str(exc_info.value)
    assert "could not, in principle, serve this symbol" in message


def test_unwired_provider_check_happens_before_stage_1_lookup():
    """An unwired provider raises ProviderSymbolResolutionError even when
    Stage 1 would also have failed -- the provider-wiring check is a
    structural precondition evaluated first."""
    config = {"data": {"twelve_data_symbols": []}}
    with pytest.raises(psr.ProviderSymbolResolutionError):
        psr.resolve_provider_symbol("UNKNOWNSYMBOL", "alpha_vantage", config)


# --- _STAGE_2_RESOLVERS registry content (this phase only) -----------------


def test_stage_2_resolvers_registry_contains_only_twelve_data():
    assert set(psr._STAGE_2_RESOLVERS) == {"twelve_data"}


# --- result shape ------------------------------------------------------------


def test_result_shape_has_exactly_five_keys():
    result = psr.resolve_provider_symbol("EURUSD", "twelve_data", _base_config())
    assert set(result.keys()) == {
        "internal_symbol", "provider", "shared_symbol", "provider_symbol", "resolution_state",
    }


# --- structural independence -------------------------------------------------


def _source_without_docstrings() -> str:
    source = inspect.getsource(psr)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_core_data_providers_import():
    """This module must never import core.data_providers -- no
    _to_av_symbol, no FINNHUB_SYMBOL_MAP, no fetch_with_failover. Stage 1
    is reused via backtest.shadow_outcome_resolver only."""
    body = _source_without_docstrings()
    forbidden = (
        "import core.data_providers", "from core.data_providers",
        "fetch_with_failover", "_to_av_symbol", "FINNHUB_SYMBOL_MAP",
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
