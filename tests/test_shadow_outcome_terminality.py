"""tests/test_shadow_outcome_terminality.py -- tests for backtest/
shadow_outcome_terminality.py (Terminality Semantics Design Gate): the
locked TERMINAL_CONFIRMED / PROVISIONAL / CONTRADICTED /
NOT_YET_ASSESSABLE classification, the pairwise/current-state-only
scope, the "at least one reproduction, never a count" requirement, and
independence from storage/execution/scheduler/live-fetch logic."""
from __future__ import annotations

import inspect
import re

import pytest

from backtest import shadow_outcome_terminality as term
from backtest.shadow_outcome_revision import ShadowOutcomeRevisionError


def _obs(**overrides) -> dict:
    base = {
        "request_id": "LIVE-IDENTITY-REQUEST-abc123", "hypothesis_id": "CONFLUENCE-HYPOTHESIS-x",
        "outcome": "TP_HIT", "resolved_bar_time": "2026-01-01T10:00:00+00:00",
        "evaluated_at": "2026-01-01T11:00:00+00:00",
        "provider_at_observation": "twelve_data",
        "walk_ohlc": [{"bar_time": "2026-01-01T10:00:00+00:00", "open": 1.24, "high": 1.246,
                       "low": 1.243, "close": 1.2455}],
    }
    base.update(overrides)
    return base


def _verif(**overrides) -> dict:
    base = {
        "request_id": "LIVE-IDENTITY-REQUEST-abc123", "hypothesis_id": "CONFLUENCE-HYPOTHESIS-x",
        "provider_at_observation": "twelve_data", "provider_at_verification": "twelve_data",
        "provider_state": "SAME_PROVIDER", "verification_evaluated_at": "2026-02-01T00:00:00+00:00",
        "ohlc_state": None,
        "bars": [{"bar_time": "2026-01-01T10:00:00+00:00", "state": "OHLC_STABLE",
                  "baseline": {}, "verification": {}}],
        "composition_state": "COMPOSITION_STABLE", "extra_bars": None,
    }
    base.update(overrides)
    return base


# --- NOT_YET_ASSESSABLE: no walk evidence / no verification / wrong provider ---


def test_no_walk_ohlc_is_not_yet_assessable():
    observation = _obs(outcome="TIMEOUT", resolved_bar_time=None, walk_ohlc=None)
    result = term.assess_terminality(observation, None, None)
    assert result["terminality_state"] == term.NOT_YET_ASSESSABLE


def test_no_verification_attempt_is_not_yet_assessable():
    observation = _obs()
    result = term.assess_terminality(observation, None, None)
    assert result["terminality_state"] == term.NOT_YET_ASSESSABLE


def test_not_yet_verifiable_ohlc_state_is_not_yet_assessable():
    observation = _obs(provider_at_observation="finnhub")
    verification = _verif(
        provider_at_observation="finnhub", provider_at_verification=None, provider_state=None,
        verification_evaluated_at=None, ohlc_state="NOT_YET_VERIFIABLE", bars=None,
        composition_state="NOT_YET_VERIFIABLE",
    )
    result = term.assess_terminality(observation, None, verification)
    assert result["terminality_state"] == term.NOT_YET_ASSESSABLE


# --- CONTRADICTED: unstable raw data alone, no previous observation needed ---


def test_ohlc_unstable_bar_is_contradicted_even_with_no_history():
    observation = _obs()
    verification = _verif(bars=[{"bar_time": "2026-01-01T10:00:00+00:00", "state": "OHLC_UNSTABLE",
                                  "baseline": {}, "verification": {}}])
    result = term.assess_terminality(observation, None, verification)
    assert result["terminality_state"] == term.CONTRADICTED


def test_composition_unstable_is_contradicted_even_with_no_history():
    observation = _obs()
    verification = _verif(composition_state="COMPOSITION_UNSTABLE")
    result = term.assess_terminality(observation, None, verification)
    assert result["terminality_state"] == term.CONTRADICTED


def test_outcome_revised_relationship_is_contradicted_even_if_ohlc_stable():
    earlier = _obs(outcome="TP_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                   evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(outcome="SL_HIT", resolved_bar_time="2026-01-01T10:00:00+00:00",
                 evaluated_at="2026-01-02T00:00:00+00:00")
    verification = _verif()  # OHLC/composition both stable
    result = term.assess_terminality(later, earlier, verification)
    assert result["terminality_state"] == term.CONTRADICTED
    assert result["relationship"] == "OUTCOME_REVISED"


# --- TERMINAL_CONFIRMED: requires actual reproduction, not just stable data ---


def test_stable_data_plus_reproduction_is_terminal_confirmed():
    earlier = _obs(evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(evaluated_at="2026-01-02T00:00:00+00:00")
    verification = _verif()
    result = term.assess_terminality(later, earlier, verification)
    assert result["terminality_state"] == term.TERMINAL_CONFIRMED
    assert result["relationship"] == "OUTCOME_REPRODUCED"


# --- PROVISIONAL: stable but not yet confirmed, for each distinct reason ---


def test_stable_data_with_no_previous_observation_is_provisional():
    observation = _obs()
    verification = _verif()
    result = term.assess_terminality(observation, None, verification)
    assert result["terminality_state"] == term.PROVISIONAL
    assert result["relationship"] is None


def test_stable_data_with_legitimate_progression_is_provisional():
    earlier = _obs(outcome="NOT_YET_ASSESSABLE", resolved_bar_time=None, walk_ohlc=None,
                   evaluated_at="2026-01-01T05:00:00+00:00")
    later = _obs(evaluated_at="2026-01-02T00:00:00+00:00")
    verification = _verif()
    result = term.assess_terminality(later, earlier, verification)
    assert result["terminality_state"] == term.PROVISIONAL
    assert result["relationship"] == "LEGITIMATE_PROGRESSION"


def test_verification_data_gap_bar_prevents_confirmation_without_contradicting():
    """Not every bar reaching OHLC_STABLE (one is unconfirmed, not
    unstable) must land on PROVISIONAL, never CONTRADICTED and never
    TERMINAL_CONFIRMED -- 'unconfirmed' is not 'disproven'."""
    earlier = _obs(evaluated_at="2026-01-01T11:00:00+00:00")
    later = _obs(evaluated_at="2026-01-02T00:00:00+00:00")
    verification = _verif(bars=[
        {"bar_time": "2026-01-01T10:00:00+00:00", "state": "OHLC_STABLE", "baseline": {}, "verification": {}},
        {"bar_time": "2026-01-01T14:00:00+00:00", "state": "VERIFICATION_DATA_GAP",
         "baseline": {}, "verification": None},
    ])
    result = term.assess_terminality(later, earlier, verification)
    assert result["terminality_state"] == term.PROVISIONAL
    assert result["relationship"] == "OUTCOME_REPRODUCED"


# --- Current-state-only: a past revision does not permanently disqualify ---


def test_resettled_decision_can_reach_terminal_confirmed_again():
    """History TP_HIT -&gt; SL_HIT(REVISED) -&gt; SL_HIT(REPRODUCED). This
    function only ever sees the LATEST pair, so a decision that
    revised once and has since resettled can still reach
    TERMINAL_CONFIRMED -- current-state-only, never sticky."""
    middle = _obs(outcome="SL_HIT", resolved_bar_time="2026-01-01T14:00:00+00:00",
                  evaluated_at="2026-01-02T00:00:00+00:00")
    latest = _obs(outcome="SL_HIT", resolved_bar_time="2026-01-01T14:00:00+00:00",
                  evaluated_at="2026-01-03T00:00:00+00:00")
    verification = _verif()
    result = term.assess_terminality(latest, middle, verification)
    assert result["terminality_state"] == term.TERMINAL_CONFIRMED
    assert result["relationship"] == "OUTCOME_REPRODUCED"


def test_function_signature_is_strictly_pairwise_not_a_sequence():
    """Structural proof of the locked 'pairwise, current-state-only'
    scope: the function accepts exactly two single observations, never
    a list/history -- there is no way to pass more than one prior
    observation even if a caller wanted to."""
    sig = inspect.signature(term.assess_terminality)
    params = list(sig.parameters)
    assert params == ["latest_observation", "previous_observation", "latest_verification"]


# --- structural misuse ------------------------------------------------------


def test_verification_request_id_mismatch_raises():
    observation = _obs()
    verification = _verif(request_id="LIVE-IDENTITY-REQUEST-different")
    with pytest.raises(term.ShadowOutcomeTerminalityError, match="does not match"):
        term.assess_terminality(observation, None, verification)


def test_revision_error_from_mismatched_previous_observation_propagates_unchanged():
    """A request_id mismatch between previous/latest observations is
    backtest.shadow_outcome_revision's own error -- never caught or
    re-wrapped here."""
    later = _obs(evaluated_at="2026-01-02T00:00:00+00:00")
    earlier = _obs(request_id="LIVE-IDENTITY-REQUEST-different", evaluated_at="2026-01-01T11:00:00+00:00")
    verification = _verif()
    with pytest.raises(ShadowOutcomeRevisionError, match="request_id mismatch"):
        term.assess_terminality(later, earlier, verification)


# --- structural: no coupling with storage/execution/scheduler/live-fetch ---


def _source_without_docstrings() -> str:
    source = inspect.getsource(term)
    return re.sub(r'""".*?"""', "", source, flags=re.DOTALL)


def test_no_storage_execution_scheduler_or_live_fetch_coupling():
    body = _source_without_docstrings()
    forbidden = (
        "import storage", "from storage",
        "from backtest.promotion_gate", "import backtest.promotion_gate",
        "from backtest.policy_health", "import backtest.policy_health",
        "from execution.authorization", "import execution.authorization",
        "from execution.trade_executor", "import execution.trade_executor",
        "import scheduler", "from scheduler", "import main", "from main",
        "fetch_with_failover", "TwelveDataClient",
    )
    for pattern in forbidden:
        assert pattern not in body, f"shadow_outcome_terminality unexpectedly references {pattern!r}"


def test_never_calls_resolver_or_verifier_directly():
    body = _source_without_docstrings()
    assert "resolve_decision_outcome(" not in body
    assert "verify_historical_stability(" not in body
