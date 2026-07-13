"""
tests/test_config_governance.py
---------------------------------
config.yaml was split into config/{symbols,engines,risk,ai}.yaml
(2026-07-12) so the symbol universe and per-domain settings don't live
in one monolithic file. This is a pure file-layout change — these tests
pin the effective, merged config produced by load_config() to the exact
values the old single-file config.yaml carried, so a future edit to the
loader or the split files can't silently change what the system trades
on. See utils/helpers.py::load_config and each config/*.yaml header.
"""
from __future__ import annotations

from utils.config_validator import validate_config
from utils.helpers import load_config

# The exact (enabled, internal, min_score, rr, symbol) tuples the
# pre-split monolithic config.yaml carried, in order.
_EXPECTED_SYMBOLS = [
    (True, "EURUSD", 60, 2.0, "EUR/USD"),
    (True, "GBPUSD", 58, 2.0, "GBP/USD"),
    (True, "USDJPY", 58, 2.0, "USD/JPY"),
    (True, "USDCHF", 58, 2.0, "USD/CHF"),
    (False, "AUDUSD", 55, 2.0, "AUD/USD"),
    (False, "USDCAD", 58, 2.0, "USD/CAD"),
    (False, "NZDUSD", 60, 2.0, "NZD/USD"),
    (True, "EURJPY", 58, 2.0, "EUR/JPY"),
    (True, "GBPJPY", 60, 2.0, "GBP/JPY"),
    (True, "AUDJPY", 58, 2.0, "AUD/JPY"),
    (False, "EURGBP", 58, 2.0, "EUR/GBP"),
    (False, "EURCHF", 58, 2.0, "EUR/CHF"),
    (True, "XAUUSD", 55, 2.5, "XAU/USD"),
    (True, "XAGUSD", 55, 2.5, "XAG/USD"),
    (True, "USOIL", 55, 2.5, "WTI/USD"),
    (True, "US30", 55, 2.5, "DJI"),
    (True, "NAS100", 60, 2.5, "NDX"),
    (True, "SPX500", 55, 2.5, "SPX"),
    (True, "BTCUSD", 55, 2.5, "BTC/USD"),
    (True, "ETHUSD", 60, 2.0, "ETH/USD"),
]

_EXPECTED_ENGINES_ENABLED = {
    "divergence": False,
    "ict": False,
    "macro": False,
    "market_structure": False,
    "nnfx": True,
    "price_action": True,
    "quant": False,
    "sentiment": False,
    "smc": True,
    "wyckoff": True,
}

_EXPECTED_RISK = {
    "starting_balance": 10000.0,
    "max_drawdown_reduce": 0.1,
    "max_drawdown_stop": 0.15,
    "max_exposure": 0.05,
    "min_risk_reward": 2.0,
    "risk_per_trade_max": 0.01,
    "risk_per_trade_min": 0.0025,
    "sl_atr_multiplier": 2.5,
}


def test_load_config_symbols_match_pre_split_values():
    config = load_config()
    symbols = config["data"]["twelve_data_symbols"]
    assert len(symbols) == len(_EXPECTED_SYMBOLS)
    actual = [
        (s["enabled"], s["internal"], s["min_score"], s["rr"], s["symbol"])
        for s in symbols
    ]
    assert actual == _EXPECTED_SYMBOLS


def test_load_config_symbols_carry_governance_metadata():
    config = load_config()
    symbols = {s["internal"]: s for s in config["data"]["twelve_data_symbols"]}

    assert symbols["EURUSD"]["asset_class"] == "fx_major"
    assert symbols["EURUSD"]["status"] == "ACTIVE"
    assert symbols["EURGBP"]["asset_class"] == "fx_minor"
    assert symbols["EURGBP"]["status"] == "RETIRED"
    assert symbols["AUDUSD"]["status"] == "PAUSED"
    assert symbols["XAUUSD"]["asset_class"] == "metals"
    assert symbols["USOIL"]["asset_class"] == "energy"
    assert symbols["NAS100"]["asset_class"] == "indices"
    assert symbols["BTCUSD"]["asset_class"] == "crypto"

    # Metadata fields must never be the fields code reads for trading
    # decisions — `enabled` remains the sole authoritative field.
    for entry in symbols.values():
        assert isinstance(entry["enabled"], bool)
        assert entry["status"] in {"ACTIVE", "WATCHLIST", "PAUSED", "RETIRED", "EXPERIMENTAL"}


def test_load_config_engines_match_pre_split_values():
    config = load_config()
    assert config["engines"]["enabled"] == _EXPECTED_ENGINES_ENABLED
    assert config["engines"]["smc_full_spec"] is False


def test_load_config_engines_carry_version_metadata_without_touching_enabled_shape():
    config = load_config()
    versions = config["engines"]["versions"]
    assert set(versions.keys()) == set(_EXPECTED_ENGINES_ENABLED.keys())
    for name, is_enabled in config["engines"]["enabled"].items():
        assert isinstance(is_enabled, bool)  # unchanged shape — bool, not a dict


def test_load_config_risk_matches_pre_split_values():
    config = load_config()
    assert config["risk"] == _EXPECTED_RISK


def test_load_config_ai_matches_pre_split_behavior():
    config = load_config()
    ai_cfg = config["ai"]
    assert ai_cfg["enabled"] is True
    assert ai_cfg["model"] == "gemini-flash-latest"
    assert ai_cfg["temperature"] == 0.1
    assert ai_cfg["max_tokens"] == 1200
    assert ai_cfg["timeout"] == 20
    assert ai_cfg["cache"] == {"news_ttl_min": 20, "macro_ttl_min": 60}
    # New provider-resolution structure resolves to the same effective
    # provider ("gemini") the old single `provider: gemini` string gave.
    assert ai_cfg["providers"]["gemini"]["enabled"] is True
    assert ai_cfg["fallback_order"][0] == "gemini"


def test_load_config_untouched_sections_still_present():
    config = load_config()
    assert config["confluence"]["min_score_to_trade"] == 58
    assert config["execution"]["min_score_to_execute"] == 60.0
    assert config["system"]["mode"] == "live"


def test_validate_config_flags_known_disabled_engines_with_nonzero_weight():
    # Documents current, pre-existing state (governance issue #12 from the
    # config-upgrade proposal) — this is a warn-only visibility check, not
    # an assertion that the mismatch is fixed. macro is excluded: its
    # weight is already 0, consistent with being disabled.
    warnings = validate_config(load_config())
    flagged = {w.split(".enabled.")[1].split("=")[0] for w in warnings if "false but" in w}
    assert flagged == {"divergence", "ict", "market_structure", "quant", "sentiment"}
