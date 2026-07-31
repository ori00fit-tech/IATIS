"""
tests/test_engine_variants.py
---------------------------------
Confluence Engine Overhaul Track C (Phase 4, 2026-08-01) — the safety-
critical test suite for the ad-hoc PriceAction v2/Wyckoff v2 engine
variants. Non-negotiable properties (per the same safety guarantee
already established for Mission Center's other ephemeral overrides):
no code path in the engine_variants chain may ever write to
config/engines.yaml or config.yaml, a variant reads its OWN thresholds
sub-key (never v1's), an unknown variant raises loudly rather than
silently falling back, and — the load-bearing regression this phase's
own design pass found — a variant engine's vote must resolve to its
base engine's confluence weight slot, not silently zero out.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtesting import backtest_engine
from backtesting.backtest_engine import (
    ENGINE_VARIANT_KEYS,
    BacktestConfig,
    build_engine_config_override,
    run_backtest,
)
from backtest import mission_runner, optimizer
from engines import price_action_engine_v2, wyckoff_engine_v2

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_YAML_PATH = REPO_ROOT / "config.yaml"
ENGINES_YAML_PATH = REPO_ROOT / "config" / "engines.yaml"


def _ohlcv(n: int, seed: int = 3, trend: float = 0.10) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 1.08 + np.linspace(0, trend, n) + np.cumsum(rng.normal(0, 0.0009, n))
    o = np.roll(close, 1)
    o[0] = close[0]
    return pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.0008,
         "low": np.minimum(o, close) - 0.0008, "close": close, "volume": 1000.0},
        index=idx,
    )


# ── Hard-block safety tests ──────────────────────────────────────────────

def test_no_module_contains_a_write_call_near_engines_yaml_or_config_yaml():
    """Source-level guard, same pattern as test_mission_runner.py's own
    registry.json guard: no write-shaped call may appear on a line
    mentioning engines.yaml/config.yaml in any module on the
    engine_variants chain."""
    write_markers = ("write_text", "json.dump", "yaml.dump", "yaml.safe_dump", '"w")', "'w')")
    for module in (backtest_engine, optimizer, mission_runner, price_action_engine_v2, wyckoff_engine_v2):
        source = inspect.getsource(module)
        for line in source.splitlines():
            lower = line.lower()
            if "engines.yaml" in lower or "config.yaml" in lower:
                for marker in write_markers:
                    assert marker not in line, (
                        f"{module.__name__}: possible write near a config reference: {line!r}"
                    )


def test_engines_yaml_and_config_yaml_byte_identical_after_variant_backtest():
    """Live integration: build_engine_config_override(engine_variants=...)
    + run_backtest() with BOTH v2 variants active, on real synthetic
    data — then assert both config files are byte-identical, mtime
    included."""
    before_config = CONFIG_YAML_PATH.read_bytes()
    before_config_mtime = CONFIG_YAML_PATH.stat().st_mtime
    before_engines = ENGINES_YAML_PATH.read_bytes()
    before_engines_mtime = ENGINES_YAML_PATH.stat().st_mtime

    df = _ohlcv(400)
    cfg = BacktestConfig.from_profile("EURUSD")
    engine_config = build_engine_config_override(engine_variants={"price_action": "v2", "wyckoff": "v2"})
    run_backtest(df, cfg, engine_config)

    assert CONFIG_YAML_PATH.read_bytes() == before_config
    assert CONFIG_YAML_PATH.stat().st_mtime == before_config_mtime
    assert ENGINES_YAML_PATH.read_bytes() == before_engines
    assert ENGINES_YAML_PATH.stat().st_mtime == before_engines_mtime


def test_price_action_engine_v2_never_imports_rsi_or_bollinger_bands():
    """Source-scan pinning the 'genuinely pure price-action, no RSI/BB'
    design contract — v1 still uses both; v2 must not."""
    source = inspect.getsource(price_action_engine_v2)
    assert "bollinger_bands" not in source
    assert "import rsi" not in source
    assert " rsi(" not in source
    assert "rsi_wilder" not in source


def test_v1_default_unaffected_when_engine_variants_omitted():
    """build_engine_config_override() with NO engine_variants param must
    still resolve to None (the pre-Track-C default path) when no other
    override is requested either — byte-for-byte the same as before
    this phase."""
    assert build_engine_config_override() is None
    assert build_engine_config_override(engine_variants=None) is None


def test_v1_engines_still_used_when_engine_variants_not_requested():
    """engines_enabled set but NO engine_variants must resolve
    variant_selection to {} — every engine key falls through to "v1" in
    the instantiation loop, matching pre-Track-C behavior exactly."""
    engine_config = build_engine_config_override(
        engines_enabled={"price_action": True, "wyckoff": True, "smc": False, "nnfx": False},
    )
    assert engine_config["engines"].get("variants", {}) == {}
    # A real backtest run on this config must not raise (proves the
    # instantiation loop's variant_selection.get(key, "v1") default path
    # still resolves to real, importable v1 classes).
    df = _ohlcv(400, seed=11)
    cfg = BacktestConfig.from_profile("EURUSD")
    run_backtest(df, cfg, engine_config)


def test_variant_thresholds_key_resolves_to_v2_block_not_v1():
    """engine_variants={'price_action':'v2'} must set the instantiated
    engine's .thresholds from config['engines']['thresholds']
    ['price_action_v2'], never ['price_action'] — the exact fix for the
    thresholds-resolve-by-string-key gap this phase's design pass found."""
    from utils.helpers import load_config

    real_thresholds = load_config()["engines"]["thresholds"]
    v1_block = real_thresholds["price_action"]
    v2_block = real_thresholds["price_action_v2"]
    # Sanity: the two blocks are genuinely different key sets (v1 has RSI/BB
    # keys v2 does not, v2 has pattern keys v1 does not) — if they were
    # identical this test couldn't distinguish a resolution bug.
    assert "rsi_bull" in v1_block and "rsi_bull" not in v2_block
    assert "fakey_score" in v2_block and "fakey_score" not in v1_block

    captured: dict = {}

    class _SpyPriceActionV2(price_action_engine_v2.PriceActionEngineV2):
        def analyze(self, mtf_data):
            captured["thresholds"] = dict(self.thresholds)
            return super().analyze(mtf_data)

    # run_backtest() imports PriceActionEngineV2 fresh (module-level lazy
    # import) each call and builds its own local _ENGINE_VARIANT_CLASS_MAP
    # from that name, so patching the class object referenced by the
    # module (not a local dict inside run_backtest) is what actually
    # takes effect here.
    orig_cls = price_action_engine_v2.PriceActionEngineV2
    try:
        price_action_engine_v2.PriceActionEngineV2 = _SpyPriceActionV2
        df = _ohlcv(400, seed=5)
        cfg = BacktestConfig.from_profile("EURUSD")
        engine_config = build_engine_config_override(
            engines_enabled={"price_action": True, "smc": False, "nnfx": False, "wyckoff": False},
            engine_variants={"price_action": "v2"},
        )
        run_backtest(df, cfg, engine_config)
    finally:
        price_action_engine_v2.PriceActionEngineV2 = orig_cls

    assert captured, "PriceActionEngineV2.analyze() was never called"
    assert captured["thresholds"] == v2_block
    assert captured["thresholds"] != v1_block


def test_unknown_variant_raises_valueerror():
    with pytest.raises(ValueError, match="no variant"):
        build_engine_config_override(engine_variants={"price_action": "v3"})


def test_unknown_engine_in_engine_variants_raises_valueerror():
    with pytest.raises(ValueError, match="unknown engine"):
        build_engine_config_override(engine_variants={"not_a_real_engine": "v2"})


def test_engine_with_no_variants_only_allows_v1():
    with pytest.raises(ValueError, match="no variant"):
        build_engine_config_override(engine_variants={"nnfx": "v2"})


def test_engine_variant_keys_only_lists_price_action_and_wyckoff():
    """Regression pin — if a future phase adds more variants, this test
    should be extended deliberately, not silently drift."""
    assert set(ENGINE_VARIANT_KEYS.keys()) == {"price_action", "wyckoff"}
    assert ENGINE_VARIANT_KEYS["price_action"] == ("v1", "v2")
    assert ENGINE_VARIANT_KEYS["wyckoff"] == ("v1", "v2")


def test_v2_engine_confluence_weight_resolves_to_base_engine_key():
    """Regression pin for the GAP #3 fix found during this phase's design
    pass: PriceActionV2/WyckoffV2 must resolve to their base engine's
    SAME confluence weight key, never fall through to a snake_cased
    variant-specific key (which would silently zero their contribution)."""
    from confluence.score_calculator import _engine_key
    from confluence import voting_system

    assert _engine_key("PriceActionV2") == "price_action"
    assert _engine_key("WyckoffV2") == "wyckoff"
    assert voting_system._NAME_TO_KEY["PriceActionV2"] == "price_action"
    assert voting_system._NAME_TO_KEY["WyckoffV2"] == "wyckoff"


def test_v2_engine_votes_actually_count_toward_score():
    """Live proof the GAP #3 fix works end-to-end, not just at the
    dict-lookup level: a real EngineOutput named 'PriceActionV2' must
    receive the SAME nonzero weight as 'PriceAction' would, in
    calculate_score()."""
    from confluence.score_calculator import calculate_score
    from confluence.voting_system import tally_votes
    from engines.base_engine import Bias, EngineOutput
    from utils.helpers import load_config

    weights = load_config()["confluence"]["weights"]
    assert weights.get("price_action", 0.0) > 0.0, "price_action weight must be nonzero for this test to be meaningful"

    out_v1 = EngineOutput(engine_name="PriceAction", bias=Bias.BULLISH, score=70.0)
    out_v2 = EngineOutput(engine_name="PriceActionV2", bias=Bias.BULLISH, score=70.0)

    vote_v1 = tally_votes([out_v1], weights)
    vote_v2 = tally_votes([out_v2], weights)
    score_v1 = calculate_score([out_v1], weights, winning_bias=vote_v1.winning_bias)
    score_v2 = calculate_score([out_v2], weights, winning_bias=vote_v2.winning_bias)

    assert score_v2.contributions["PriceActionV2"] != 0.0
    assert score_v2.contributions["PriceActionV2"] == score_v1.contributions["PriceAction"]
    assert score_v2.final_score == score_v1.final_score
