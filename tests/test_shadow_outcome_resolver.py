"""tests/test_shadow_outcome_resolver.py -- tests for backtest/
shadow_outcome_resolver.py (Hypothesis Discovery Engine, Phase 15D --
SHADOW Decision Outcome Resolver): the locked Outcome Resolution
Contract's chronological gap-before-hit semantics, the strict T_D
exclusion, the single-meaning resolved_bar_time rule, provider-failure
propagation, and structural independence from every sibling-phase/
execution/storage module."""
from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta

import pandas as pd
import pytest

from backtest import shadow_outcome_resolver as sor
from core.data_providers import DataFetchError


def _df(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """rows: list of (iso_timestamp, high, low)."""
    index = pd.to_datetime([r[0] for r in rows], utc=True)
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    return pd.DataFrame(
        {"open": highs, "high": highs, "low": lows, "close": highs, "volume": [0.0] * len(rows)}, index=index,
    )


def _snapshot(**overrides) -> dict:
    base = {
        "request_id": "LIVE-IDENTITY-REQUEST-abc123", "hypothesis_id": "CONFLUENCE-HYPOTHESIS-x",
        "symbol": "EURUSD", "timeframe": "H4", "bar_time": "2026-01-01T00:00:00+00:00",
        "side": "BUY", "entry_price": 1.2345, "stop_loss": 1.2300, "take_profit": 1.2450,
    }
    base.update(overrides)
    return base


def _base_config() -> dict:
    return {"data": {"twelve_data_symbols": [{"internal": "EURUSD", "symbol": "EUR/USD", "rr": 2.0}]}}


def _patch_fetch(monkeypatch, df: pd.DataFrame, capture: dict | None = None):
    def _fake(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return df, "fake_provider"
    monkeypatch.setattr(sor, "fetch_with_failover", _fake)


def _patch_now(monkeypatch, iso_str: str):
    fixed = datetime.fromisoformat(iso_str)
    monkeypatch.setattr(sor, "_now_utc", lambda: fixed)


def _contiguous_no_hit_bars(start_iso: str, count: int, step_hours: int = 4) -> list[tuple[str, float, float]]:
    start = datetime.fromisoformat(start_iso)
    return [
        ((start + timedelta(hours=step_hours * i)).isoformat(), 1.2320, 1.2310) for i in range(1, count + 1)
    ]


# --- 1: bar_time == T_D is never evidence ----------------------------------


def test_bar_at_exactly_T_D_is_ignored(monkeypatch):
    snapshot = _snapshot()
    df = _df([
        ("2026-01-01T00:00:00+00:00", 9.0, 0.0001),     # == T_D -- would be SL_HIT if wrongly considered
        ("2026-01-01T04:00:00+00:00", 1.2320, 1.2310),  # normal, no hit
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.NOT_YET_ASSESSABLE


# --- 2: bars are sorted ascending regardless of provider order -------------


def test_bars_are_sorted_ascending_even_if_provider_returns_them_unordered(monkeypatch):
    snapshot = _snapshot()
    # Chronologically: T_D+4h (no hit) then T_D+8h (SL hit) -- correctly ordered, this is SL_HIT, not DATA_GAP.
    # Supplied to the resolver in DESCENDING order to prove it sorts before evaluating.
    df = _df([
        ("2026-01-01T08:00:00+00:00", 1.2320, 1.2290),  # SL hit (low <= 1.2300)
        ("2026-01-01T04:00:00+00:00", 1.2320, 1.2310),  # no hit
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T09:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.SL_HIT
    assert result["resolved_bar_time"] == "2026-01-01T08:00:00+00:00"


# --- 3/4: chronological gap-before-hit invariant ---------------------------


def test_data_gap_before_later_hit_yields_data_gap(monkeypatch):
    snapshot = _snapshot()
    df = _df([
        ("2026-01-01T04:00:00+00:00", 1.2320, 1.2310),   # no hit
        ("2026-01-01T12:00:00+00:00", 1.2320, 1.2290),   # would be SL_HIT, but T_D+8h is missing first
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T13:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.DATA_GAP
    assert result["resolved_bar_time"] is None


def test_hit_before_a_later_gap_is_final_and_valid(monkeypatch):
    snapshot = _snapshot()
    df = _df([
        ("2026-01-01T04:00:00+00:00", 1.2320, 1.2290),   # SL hit here, resolved immediately
        ("2026-01-01T12:00:00+00:00", 1.2460, 1.2455),   # irrelevant: gap exists here (T_D+8h missing) but too late
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T13:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.SL_HIT
    assert result["resolved_bar_time"] == "2026-01-01T04:00:00+00:00"


# --- 5/6: exact expected_freq boundary -------------------------------------


def test_exact_expected_freq_spacing_is_not_a_gap(monkeypatch):
    snapshot = _snapshot()
    df = _df([
        ("2026-01-01T04:00:00+00:00", 1.2320, 1.2310),
        ("2026-01-01T08:00:00+00:00", 1.2320, 1.2310),  # exactly +4h -- not a gap
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T09:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.NOT_YET_ASSESSABLE  # would be DATA_GAP if +4h were wrongly flagged


def test_expected_freq_plus_one_second_is_a_gap(monkeypatch):
    snapshot = _snapshot()
    df = _df([
        ("2026-01-01T04:00:00+00:00", 1.2320, 1.2310),
        ("2026-01-01T08:00:01+00:00", 1.2320, 1.2310),  # +4h and 1 second -- a gap
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T09:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.DATA_GAP


# --- 7: SL-before-TP same-bar tie-break -------------------------------------


def test_sl_and_tp_touched_in_the_same_bar_resolves_sl_first(monkeypatch):
    snapshot = _snapshot()  # BUY, SL=1.2300, TP=1.2450
    df = _df([("2026-01-01T04:00:00+00:00", 1.2460, 1.2290)])  # both touched in one bar
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.SL_HIT


def test_sl_and_tp_same_bar_sell_side_resolves_sl_first(monkeypatch):
    snapshot = _snapshot(side="SELL", entry_price=1.2345, stop_loss=1.2400, take_profit=1.2250)
    df = _df([("2026-01-01T04:00:00+00:00", 1.2410, 1.2240)])  # both touched
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.SL_HIT


# --- 8/9/10: NOT_YET_ASSESSABLE vs TIMEOUT vs DATA_GAP at the horizon ------


def test_before_horizon_with_no_hit_or_gap_is_not_yet_assessable(monkeypatch):
    snapshot = _snapshot()
    df = _df([("2026-01-01T04:00:00+00:00", 1.2320, 1.2310)])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")  # well before T_D+168h

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.NOT_YET_ASSESSABLE
    assert result["resolved_bar_time"] is None


def test_full_contiguous_coverage_to_horizon_with_no_hit_is_timeout(monkeypatch):
    snapshot = _snapshot()
    rows = _contiguous_no_hit_bars("2026-01-01T00:00:00+00:00", count=42)  # 42 * 4h = 168h exactly
    df = _df(rows)
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-08T01:00:00+00:00")  # just past T_D+168h

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.TIMEOUT
    assert result["resolved_bar_time"] is None


def test_incomplete_coverage_past_horizon_is_data_gap(monkeypatch):
    snapshot = _snapshot()
    rows = _contiguous_no_hit_bars("2026-01-01T00:00:00+00:00", count=25)  # only 100h of the 168h horizon
    df = _df(rows)
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-08T01:00:00+00:00")  # real time is already past T_D+168h

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.DATA_GAP
    assert result["resolved_bar_time"] is None


# --- 11: provider failure propagates unchanged ------------------------------


def test_provider_fetch_failure_propagates_unchanged(monkeypatch):
    snapshot = _snapshot()

    def _boom(**kwargs):
        raise DataFetchError("all providers failed")

    monkeypatch.setattr(sor, "fetch_with_failover", _boom)
    with pytest.raises(DataFetchError, match="all providers failed"):
        sor.resolve_decision_outcome(snapshot, base_config=_base_config())


# --- 12: unsupported timeframe raises explicitly ----------------------------


def test_unsupported_timeframe_raises_explicit_error():
    snapshot = _snapshot(timeframe="M15")
    with pytest.raises(sor.ShadowOutcomeResolverError, match="unsupported timeframe"):
        sor.resolve_decision_outcome(snapshot, base_config=_base_config())


def test_missing_symbol_mapping_raises_explicit_error(monkeypatch):
    snapshot = _snapshot(symbol="GBPUSD")
    with pytest.raises(sor.ShadowOutcomeResolverError, match="no provider symbol mapping"):
        sor.resolve_decision_outcome(snapshot, base_config=_base_config())


# --- 13: symbol translation never alters stored identity -------------------


def test_symbol_translation_is_transient_and_never_mutates_the_snapshot(monkeypatch):
    snapshot = _snapshot()
    original_snapshot = dict(snapshot)
    captured: dict = {}
    df = _df([("2026-01-01T04:00:00+00:00", 1.2320, 1.2310)])
    _patch_fetch(monkeypatch, df, capture=captured)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())

    assert captured["symbol"] == "EUR/USD"  # the PROVIDER symbol was used for the fetch call
    assert snapshot == original_snapshot    # the caller's own snapshot dict is untouched
    assert "symbol" not in result           # the result never re-exposes a (possibly-translated) symbol field


# --- 14: captured_at is never read ------------------------------------------


def test_never_reads_captured_at(monkeypatch):
    snapshot = _snapshot()
    assert "captured_at" not in snapshot  # the resolver must work without this key ever being present
    df = _df([("2026-01-01T04:00:00+00:00", 1.2320, 1.2310)])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    sor.resolve_decision_outcome(snapshot, base_config=_base_config())  # no KeyError


# --- structural: no coupling with sibling phases/execution/storage --------


def _source_without_docstrings() -> str:
    source = inspect.getsource(sor)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_sibling_phase_execution_scheduler_or_main_import():
    body = _source_without_docstrings()
    forbidden = (
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from backtest.execution_attribution", "import backtest.execution_attribution",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "from storage.outcome_tracker", "import storage.outcome_tracker",
        "from storage.shadow_book", "import storage.shadow_book",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_resolver unexpectedly references {pattern!r}"


def test_never_calls_run_pipeline():
    body = _source_without_docstrings()
    assert "run_pipeline(" not in body


# ---------------------------------------------------------------------------
# Historical Outcome Stability + Non-Causal Walk Value Stability (#1) --
# provider_at_observation and walk_ohlc controlled additive extension
# ---------------------------------------------------------------------------

def _df_ohlc(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """rows: list of (iso_timestamp, open, high, low, close) -- distinct
    values per field, unlike this file's own _df() helper (which sets
    open=close=high), so OHLC capture can be verified field-by-field."""
    index = pd.to_datetime([r[0] for r in rows], utc=True)
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows], "high": [r[2] for r in rows],
            "low": [r[3] for r in rows], "close": [r[4] for r in rows],
            "volume": [0.0] * len(rows),
        },
        index=index,
    )


def test_provider_at_observation_is_captured_from_fetch_with_failover(monkeypatch):
    snapshot = _snapshot()
    df = _df([("2026-01-01T04:00:00+00:00", 1.2320, 1.2310)])
    _patch_fetch(monkeypatch, df)  # _patch_fetch's fake returns "fake_provider"
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["provider_at_observation"] == "fake_provider"


def test_walk_ohlc_captures_every_walked_bar_on_sl_hit(monkeypatch):
    snapshot = _snapshot()  # BUY, SL=1.2300, TP=1.2450
    df = _df_ohlc([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),  # walked, no hit
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2290, 1.2295),  # SL hit (low <= 1.2300)
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T09:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.SL_HIT
    walk = result["walk_ohlc"]
    assert walk is not None
    assert len(walk) == 2  # BOTH bars -- the non-causal one too, not just the causal hit
    assert walk[0] == {
        "bar_time": "2026-01-01T04:00:00+00:00",
        "open": 1.2315, "high": 1.2320, "low": 1.2310, "close": 1.2318,
    }
    assert walk[1] == {
        "bar_time": "2026-01-01T08:00:00+00:00",
        "open": 1.2312, "high": 1.2320, "low": 1.2290, "close": 1.2295,
    }


def test_walk_ohlc_captures_single_bar_on_immediate_tp_hit(monkeypatch):
    snapshot = _snapshot()
    df = _df_ohlc([("2026-01-01T04:00:00+00:00", 1.2440, 1.2460, 1.2430, 1.2455)])  # TP hit immediately
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.TP_HIT
    assert result["walk_ohlc"] == [{
        "bar_time": "2026-01-01T04:00:00+00:00",
        "open": 1.2440, "high": 1.2460, "low": 1.2430, "close": 1.2455,
    }]


def test_walk_ohlc_is_none_for_timeout(monkeypatch):
    snapshot = _snapshot()
    rows = _contiguous_no_hit_bars("2026-01-01T00:00:00+00:00", count=42)
    df = _df(rows)
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-08T01:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.TIMEOUT
    assert result["walk_ohlc"] is None


def test_walk_ohlc_is_none_for_data_gap(monkeypatch):
    snapshot = _snapshot()
    df = _df([
        ("2026-01-01T04:00:00+00:00", 1.2320, 1.2310),
        ("2026-01-01T12:00:00+00:00", 1.2320, 1.2290),
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T13:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.DATA_GAP
    assert result["walk_ohlc"] is None


def test_walk_ohlc_is_none_for_not_yet_assessable(monkeypatch):
    snapshot = _snapshot()
    df = _df([("2026-01-01T04:00:00+00:00", 1.2320, 1.2310)])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.NOT_YET_ASSESSABLE
    assert result["walk_ohlc"] is None


def test_non_finite_orthogonal_field_on_an_otherwise_valid_sl_hit_raises(monkeypatch):
    """SL fires on `low` -- `high` is never read by _resolve_sl_before_tp
    for a BUY's SL check, so a NaN there would otherwise pass through
    silently. Capture-time validation must still catch it."""
    snapshot = _snapshot()  # BUY, SL=1.2300
    df = _df_ohlc([("2026-01-01T04:00:00+00:00", 1.2310, float("nan"), 1.2290, 1.2295)])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    with pytest.raises(sor.ShadowOutcomeResolverError, match="non-finite"):
        sor.resolve_decision_outcome(snapshot, base_config=_base_config())


def test_non_finite_field_on_a_non_causal_walked_bar_raises(monkeypatch):
    snapshot = _snapshot()
    df = _df_ohlc([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, float("inf")),  # walked, no hit, bad close
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2290, 1.2295),        # SL hit
    ])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T09:00:00+00:00")

    with pytest.raises(sor.ShadowOutcomeResolverError, match="non-finite"):
        sor.resolve_decision_outcome(snapshot, base_config=_base_config())


def test_non_finite_bar_is_never_converted_into_data_gap_or_any_outcome(monkeypatch):
    """A structural capture error must propagate as an exception, never
    be silently swallowed into DATA_GAP (or any other outcome)."""
    snapshot = _snapshot()
    df = _df_ohlc([("2026-01-01T04:00:00+00:00", float("nan"), 1.2460, 1.2290, 1.2455)])  # TP hit, bad open
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    with pytest.raises(sor.ShadowOutcomeResolverError):
        sor.resolve_decision_outcome(snapshot, base_config=_base_config())


def test_existing_outcome_and_resolved_bar_time_semantics_are_unaffected(monkeypatch):
    """Backward compatibility: the pre-existing contract's own keys keep
    their exact pre-extension values and meaning."""
    snapshot = _snapshot()
    df = _df([("2026-01-01T04:00:00+00:00", 1.2320, 1.2290)])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")

    result = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert result["outcome"] == sor.SL_HIT
    assert result["resolved_bar_time"] == "2026-01-01T04:00:00+00:00"
    assert result["request_id"] == snapshot["request_id"]
    assert result["hypothesis_id"] == snapshot["hypothesis_id"]
    assert set(result.keys()) == {
        "request_id", "hypothesis_id", "outcome", "resolved_bar_time", "evaluated_at",
        "provider_at_observation", "walk_ohlc",
    }


def test_no_storage_write_of_any_kind():
    body = _source_without_docstrings()
    forbidden = ("record_", "insert_", "try_insert_", "update_", "write_", "d1_connection(", "con.execute(")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_resolver unexpectedly references {pattern!r}"


def test_captured_at_never_appears_in_source():
    body = _source_without_docstrings()
    assert "captured_at" not in body


# --- 17: no spread/slippage -------------------------------------------------


def test_no_spread_slippage_or_synthetic_price_logic():
    body = _source_without_docstrings()
    forbidden = ("spread", "slippage", "synthetic")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_resolver unexpectedly references {pattern!r}"


# --- 18: no divergence threshold --------------------------------------------


def test_no_divergence_threshold_logic():
    body = _source_without_docstrings()
    forbidden = ("diverged_catastrophically", "0.65", "divergence")
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_resolver unexpectedly references {pattern!r}"


# --- resolved_bar_time has exactly one meaning across every outcome -------


def test_resolved_bar_time_is_none_for_every_non_hit_outcome(monkeypatch):
    snapshot = _snapshot()
    df = _df([("2026-01-01T04:00:00+00:00", 1.2320, 1.2310)])
    _patch_fetch(monkeypatch, df)
    _patch_now(monkeypatch, "2026-01-01T05:00:00+00:00")
    not_yet = sor.resolve_decision_outcome(snapshot, base_config=_base_config())
    assert not_yet["outcome"] == sor.NOT_YET_ASSESSABLE
    assert not_yet["resolved_bar_time"] is None
