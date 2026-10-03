"""tests/test_shadow_outcome_verification.py -- tests for backtest/
shadow_outcome_verification.py: the Historical Targeting Implementation
Gate's Twelve-Data-only per-bar OHLC verification + orthogonal provider
identity comparison, and structural independence from every
sibling-phase/execution/storage module and from fetch_with_failover/
resolve_decision_outcome."""
from __future__ import annotations

import inspect
import re
from datetime import datetime

import pandas as pd
import pytest

from backtest import shadow_outcome_verification as sov


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


def _walk_ohlc() -> list[dict]:
    return [
        {"bar_time": "2026-01-01T04:00:00+00:00", "open": 1.2315, "high": 1.2320, "low": 1.2310, "close": 1.2318},
        {"bar_time": "2026-01-01T08:00:00+00:00", "open": 1.2312, "high": 1.2320, "low": 1.2290, "close": 1.2295},
    ]


def _resolver_result(**overrides) -> dict:
    base = {
        "request_id": "LIVE-IDENTITY-REQUEST-abc123", "hypothesis_id": "CONFLUENCE-HYPOTHESIS-x",
        "outcome": "SL_HIT", "resolved_bar_time": "2026-01-01T08:00:00+00:00",
        "evaluated_at": "2026-01-01T09:00:00+00:00",
        "provider_at_observation": "twelve_data",
        "walk_ohlc": _walk_ohlc(),
    }
    base.update(overrides)
    return base


def _verification_df(rows: list[tuple]) -> pd.DataFrame:
    """rows: list of (timestamp_or_str, open, high, low, close)."""
    index = pd.to_datetime([r[0] for r in rows], utc=True)
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows], "high": [r[2] for r in rows],
            "low": [r[3] for r in rows], "close": [r[4] for r in rows],
            "volume": [0.0] * len(rows),
        },
        index=index,
    )


class _FakeTwelveDataClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def time_series(self, symbol, interval, outputsize=500, use_cache=True, start_date=None, end_date=None):
        if _FakeTwelveDataClient._capture is not None:
            _FakeTwelveDataClient._capture.update(dict(
                api_key=self.api_key, symbol=symbol, interval=interval, outputsize=outputsize,
                use_cache=use_cache, start_date=start_date, end_date=end_date,
            ))
        if _FakeTwelveDataClient._raise_exc is not None:
            raise _FakeTwelveDataClient._raise_exc
        return _FakeTwelveDataClient._df

    _df: pd.DataFrame | None = None
    _capture: dict | None = None
    _raise_exc: Exception | None = None


def _patch_client(monkeypatch, df: pd.DataFrame | None = None, capture: dict | None = None,
                   raise_exc: Exception | None = None):
    _FakeTwelveDataClient._df = df
    _FakeTwelveDataClient._capture = capture
    _FakeTwelveDataClient._raise_exc = raise_exc
    monkeypatch.setattr(sov, "TwelveDataClient", _FakeTwelveDataClient)


def _patch_now(monkeypatch, iso_str: str):
    fixed = datetime.fromisoformat(iso_str)
    monkeypatch.setattr(sov, "_now_utc", lambda: fixed)


@pytest.fixture(autouse=True)
def _default_api_key(monkeypatch):
    """Every test gets a usable env-var API key by default, so tests not
    specifically about key handling don't need to pass one explicitly.
    The one test that checks missing-key behavior explicitly deletes it."""
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "default_test_key")


# --- no baseline -> permanent NOT_YET_VERIFIABLE ----------------------------


@pytest.mark.parametrize("outcome", ["TIMEOUT", "DATA_GAP", "NOT_YET_ASSESSABLE"])
def test_no_baseline_outcomes_are_permanently_not_yet_verifiable(monkeypatch, outcome):
    resolver_result = _resolver_result(outcome=outcome, walk_ohlc=None, resolved_bar_time=None)

    def _boom(*a, **k):
        raise AssertionError("must never construct a client when there is no baseline to verify")
    monkeypatch.setattr(sov, "TwelveDataClient", _boom)

    result = sov.verify_historical_stability(_snapshot(), resolver_result, base_config=_base_config())
    assert result["ohlc_state"] == sov.NOT_YET_VERIFIABLE
    assert result["bars"] is None
    assert result["verification_evaluated_at"] is None


# --- Historical Targeting scope: Twelve Data only ---------------------------


def test_non_twelve_data_baseline_is_not_yet_verifiable_never_substituted(monkeypatch):
    resolver_result = _resolver_result(provider_at_observation="alpha_vantage")

    def _boom(*a, **k):
        raise AssertionError("must never call a client for a baseline this phase cannot target")
    monkeypatch.setattr(sov, "TwelveDataClient", _boom)

    result = sov.verify_historical_stability(_snapshot(), resolver_result, base_config=_base_config())
    assert result["ohlc_state"] == sov.NOT_YET_VERIFIABLE
    assert result["provider_at_observation"] == "alpha_vantage"
    assert result["provider_at_verification"] is None
    assert result["provider_state"] is None


def test_legacy_observation_missing_provider_field_is_provider_not_available(monkeypatch):
    resolver_result = _resolver_result()
    del resolver_result["provider_at_observation"]

    def _boom(*a, **k):
        raise AssertionError("must never call a client for a pre-capture legacy observation")
    monkeypatch.setattr(sov, "TwelveDataClient", _boom)

    result = sov.verify_historical_stability(_snapshot(), resolver_result, base_config=_base_config())
    assert result["provider_state"] == sov.PROVIDER_NOT_AVAILABLE
    assert result["ohlc_state"] == sov.NOT_YET_VERIFIABLE


# --- exact-instant OHLC comparison -------------------------------------------


def test_exact_match_on_every_field_is_ohlc_stable(monkeypatch):
    df = _verification_df([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2290, 1.2295),
    ])
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    assert result["ohlc_state"] is None  # no invented top-level rollup once bars exist
    assert len(result["bars"]) == 2
    assert all(b["state"] == sov.OHLC_STABLE for b in result["bars"])


def test_single_field_mismatch_is_ohlc_unstable_only_for_that_bar(monkeypatch):
    df = _verification_df([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),   # unchanged
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2291, 1.2295),   # low differs: 1.2291 vs baseline 1.2290
    ])
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    bars = {b["bar_time"]: b for b in result["bars"]}
    assert bars["2026-01-01T04:00:00+00:00"]["state"] == sov.OHLC_STABLE
    assert bars["2026-01-01T08:00:00+00:00"]["state"] == sov.OHLC_UNSTABLE


def test_timezone_z_suffix_and_plus_offset_are_the_same_instant(monkeypatch):
    """Baseline bar_time is stored with a '+00:00' offset; the
    verification response's own index is built from 'Z'-suffixed
    strings -- both must resolve to the identical instant under the
    locked canonical-Timestamp comparison, never a string match."""
    df = _verification_df([
        ("2026-01-01T04:00:00Z", 1.2315, 1.2320, 1.2310, 1.2318),
        ("2026-01-01T08:00:00Z", 1.2312, 1.2320, 1.2290, 1.2295),
    ])
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    assert all(b["state"] == sov.OHLC_STABLE for b in result["bars"])


# --- missing / malformed / duplicate verification bars -----------------------


def test_missing_bar_in_verification_response_is_verification_data_gap(monkeypatch):
    df = _verification_df([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),
        # 08:00 bar absent entirely
    ])
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    bars = {b["bar_time"]: b for b in result["bars"]}
    assert bars["2026-01-01T04:00:00+00:00"]["state"] == sov.OHLC_STABLE
    assert bars["2026-01-01T08:00:00+00:00"]["state"] == sov.VERIFICATION_DATA_GAP
    assert bars["2026-01-01T08:00:00+00:00"]["verification"] is None


def test_malformed_verification_value_is_verification_data_gap(monkeypatch):
    df = _verification_df([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),
        ("2026-01-01T08:00:00+00:00", 1.2312, float("nan"), 1.2290, 1.2295),
    ])
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    bars = {b["bar_time"]: b for b in result["bars"]}
    assert bars["2026-01-01T08:00:00+00:00"]["state"] == sov.VERIFICATION_DATA_GAP


def test_duplicate_timestamp_in_verification_response_is_verification_data_gap(monkeypatch):
    """Simulates a provider glitch (two rows for the exact same bar_time,
    with conflicting values) -- must never be silently resolved by
    picking one; collapses to VERIFICATION_DATA_GAP, same as absent."""
    idx = pd.to_datetime(["2026-01-01T04:00:00+00:00",
                          "2026-01-01T08:00:00+00:00", "2026-01-01T08:00:00+00:00"], utc=True)
    df = pd.DataFrame({
        "open": [1.2315, 1.2312, 1.2313], "high": [1.2320, 1.2320, 1.2321],
        "low": [1.2310, 1.2290, 1.2291], "close": [1.2318, 1.2295, 1.2296],
        "volume": [0.0, 0.0, 0.0],
    }, index=idx)
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    bars = {b["bar_time"]: b for b in result["bars"]}
    assert bars["2026-01-01T08:00:00+00:00"]["state"] == sov.VERIFICATION_DATA_GAP


def test_extra_bars_beyond_baseline_walk_are_never_inspected(monkeypatch):
    """A verification response with MORE bars than the baseline ever
    walked must change nothing -- this is per-bar lookup, never a
    whole-range set diff (Design Gate #2's own, separate, undesigned
    question)."""
    df = _verification_df([
        ("2026-01-01T00:30:00+00:00", 9.9, 9.9, 9.9, 9.9),  # never walked by baseline -- irrelevant
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2290, 1.2295),
        ("2026-01-02T00:00:00+00:00", 9.9, 9.9, 9.9, 9.9),  # never walked by baseline -- irrelevant
    ])
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    assert len(result["bars"]) == 2  # exactly the baseline's own walked bars, nothing more
    assert all(b["state"] == sov.OHLC_STABLE for b in result["bars"])


# --- provider identity: orthogonal, never merged with OHLC -----------------


def test_same_provider_is_reported_alongside_ohlc_never_merged(monkeypatch):
    df = _verification_df([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2291, 1.2295),  # UNSTABLE on this bar
    ])
    _patch_client(monkeypatch, df=df)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    result = sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    # provider match holds even though one bar is OHLC_UNSTABLE -- never merged into one verdict
    assert result["provider_state"] == sov.SAME_PROVIDER
    assert result["provider_at_verification"] == "twelve_data"
    assert any(b["state"] == sov.OHLC_UNSTABLE for b in result["bars"])


# --- use_cache=False enforced, start_date/end_date supplied -----------------


def test_verification_fetch_always_uses_cache_false_and_explicit_dates(monkeypatch):
    captured: dict = {}
    df = _verification_df([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2290, 1.2295),
    ])
    _patch_client(monkeypatch, df=df, capture=captured)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config(),
                                     api_key="test_key")

    assert captured["use_cache"] is False
    assert captured["start_date"] is not None
    assert captured["end_date"] is not None
    assert captured["symbol"] == "EUR/USD"  # the PROVIDER symbol, not the internal "EURUSD"
    assert captured["api_key"] == "test_key"


# --- independence ordering ---------------------------------------------------


def test_verification_not_strictly_later_than_observation_raises(monkeypatch):
    _patch_client(monkeypatch, df=_verification_df([]))
    _patch_now(monkeypatch, "2026-01-01T09:00:00+00:00")  # == original evaluated_at, not strictly later

    with pytest.raises(sov.ShadowOutcomeVerificationError, match="not strictly later"):
        sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config(),
                                         api_key="test_key")


def test_verification_before_observation_raises(monkeypatch):
    _patch_client(monkeypatch, df=_verification_df([]))
    _patch_now(monkeypatch, "2026-01-01T08:59:59+00:00")  # before original evaluated_at

    with pytest.raises(sov.ShadowOutcomeVerificationError, match="not strictly later"):
        sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config(),
                                         api_key="test_key")


# --- API key / structural misuse --------------------------------------------


def test_missing_api_key_raises_structural_error(monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    with pytest.raises(sov.ShadowOutcomeVerificationError, match="TWELVE_DATA_API_KEY"):
        sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())


def test_api_key_from_environment_is_used_when_not_passed_explicitly(monkeypatch):
    captured: dict = {}
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "env_key_789")
    _patch_client(monkeypatch, df=_verification_df([
        ("2026-01-01T04:00:00+00:00", 1.2315, 1.2320, 1.2310, 1.2318),
        ("2026-01-01T08:00:00+00:00", 1.2312, 1.2320, 1.2290, 1.2295),
    ]), capture=captured)
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config())
    assert captured["api_key"] == "env_key_789"


# --- genuine fetch exception propagates unchanged ----------------------------


def test_genuine_fetch_exception_propagates_unchanged_never_becomes_data_gap(monkeypatch):
    from core.twelve_data_client import TwelveDataError
    _patch_client(monkeypatch, raise_exc=TwelveDataError("wholesale empty historical response"))
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")

    with pytest.raises(TwelveDataError, match="wholesale empty"):
        sov.verify_historical_stability(_snapshot(), _resolver_result(), base_config=_base_config(),
                                         api_key="test_key")


def test_unsupported_timeframe_raises_explicit_error(monkeypatch):
    _patch_now(monkeypatch, "2026-02-01T00:00:00+00:00")
    snapshot = _snapshot(timeframe="M15")

    with pytest.raises(sov.ShadowOutcomeVerificationError, match="unsupported timeframe"):
        sov.verify_historical_stability(snapshot, _resolver_result(), base_config=_base_config(),
                                         api_key="test_key")


# --- structural: no coupling with sibling phases/execution/storage/resolver -


def _source_without_docstrings() -> str:
    source = inspect.getsource(sov)
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
        assert pattern not in body, f"shadow_outcome_verification unexpectedly references {pattern!r}"


def test_never_uses_fetch_with_failover():
    body = _source_without_docstrings()
    assert "fetch_with_failover" not in body


def test_never_calls_resolve_decision_outcome():
    body = _source_without_docstrings()
    assert "resolve_decision_outcome(" not in body


def test_never_writes_to_storage():
    """No storage.* import at all -- this module performs no persistence
    (the locked 'Schema = NO' status)."""
    body = _source_without_docstrings()
    assert "import storage" not in body
    assert "from storage" not in body
