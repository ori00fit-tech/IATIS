"""tests/test_crypto_positioning_wiring.py — main.py::_crypto_positioning_
adjustment(), the gate between H019's pure modulator logic
(confluence/crypto_positioning_modulator.py, tested separately in
tests/test_crypto_positioning_modulator.py) and the live pipeline. This is
what makes engines.crypto_positioning_modulator=False (the shipped
default) provably inert regardless of symbol or data — pinned directly
rather than trusted."""
from __future__ import annotations

from engines.base_engine import Bias
from main import _crypto_positioning_adjustment

_CTX = {
    "funding_rate_history": [x * 0.0001 for x in range(-10, 10)],
    "current_funding_rate": 0.01,  # far outside that history -> extreme z
    "fear_greed_value": 50,
}


def _config(symbol="BTCUSD", enabled=True, ctx=_CTX):
    cfg = {"engines": {"crypto_positioning_modulator": enabled},
           "data": {"symbol": symbol}}
    if ctx is not None:
        cfg["data"]["_crypto_positioning_context"] = ctx
    return cfg


def test_flag_off_is_inert_even_with_context_and_matching_symbol():
    adj, result = _crypto_positioning_adjustment(_config(enabled=False), Bias.BULLISH)
    assert adj == 0.0
    assert result is None


def test_flag_on_but_wrong_symbol_is_inert():
    adj, result = _crypto_positioning_adjustment(_config(symbol="EURUSD"), Bias.BULLISH)
    assert adj == 0.0
    assert result is None


def test_flag_on_matching_symbol_but_no_injected_context_is_inert():
    """The exact state of every live pipeline run today: no context
    source is ever injected outside a backtest, so this stays inert
    regardless of the flag's value."""
    adj, result = _crypto_positioning_adjustment(_config(ctx=None), Bias.BULLISH)
    assert adj == 0.0
    assert result is None


def test_flag_on_symbol_matches_context_present_applies_penalty():
    adj, result = _crypto_positioning_adjustment(_config(), Bias.BULLISH)
    assert adj < 0.0
    assert result is not None
    assert result.score_adjustment == adj


def test_default_engines_config_missing_the_key_entirely_is_inert():
    """config.get("engines", {}).get(..., False) must default safely even
    when the key doesn't exist in engines_config at all (e.g. an older
    cached config dict from before this flag existed)."""
    cfg = {"engines": {}, "data": {"symbol": "BTCUSD",
                                    "_crypto_positioning_context": _CTX}}
    adj, result = _crypto_positioning_adjustment(cfg, Bias.BULLISH)
    assert adj == 0.0
    assert result is None


def test_ethusd_is_also_covered():
    adj, result = _crypto_positioning_adjustment(_config(symbol="ETHUSD"), Bias.BULLISH)
    assert adj < 0.0
