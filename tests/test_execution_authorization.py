"""tests/test_execution_authorization.py -- unit/structural tests for
execution/authorization.py (Hypothesis Discovery Engine, Phase 9 --
Governed Execution Authorization)."""
from __future__ import annotations

import inspect

import pytest

from execution import authorization as auth


def _issue(**overrides) -> dict:
    base = dict(
        decision_id="LIVE-IDENTITY-REQUEST-abc", policy_event_id="POLICY-EVENT-abc",
        hypothesis_id="SINGLE-ENGINE-HYPOTHESIS-abc", symbol="EURUSD", timeframe="H4",
        side="BUY", entry_type="MARKET", risk_preset="balanced", risk_definition_hash="hash123",
        ttl_seconds=60.0,
    )
    base.update(overrides)
    return auth.issue_authorization(**base)


# --- issue_authorization -----------------------------------------------


def test_issue_authorization_creates_an_issued_row():
    row = _issue()
    assert row["status"] == auth.ISSUED
    assert row["authorization_id"].startswith("EXEC-AUTH-")


def test_issue_authorization_derives_client_order_id_deterministically_from_authorization_id():
    row = _issue()
    assert row["client_order_id"] == auth._derive_client_order_id(row["authorization_id"])


def test_issue_authorization_rejects_invalid_side():
    with pytest.raises(auth.ExecutionAuthorizationError, match="side"):
        _issue(side="HOLD")


def test_issue_authorization_rejects_missing_identity_fields():
    with pytest.raises(auth.ExecutionAuthorizationError):
        _issue(decision_id="")


def test_issue_authorization_rejects_non_positive_ttl():
    with pytest.raises(auth.ExecutionAuthorizationError, match="ttl_seconds"):
        _issue(ttl_seconds=0)


def test_issue_authorization_expires_at_reflects_ttl():
    from datetime import datetime

    row = _issue(ttl_seconds=30.0)
    issued = datetime.fromisoformat(row["issued_at"])
    expires = datetime.fromisoformat(row["expires_at"])
    assert (expires - issued).total_seconds() == pytest.approx(30.0, abs=1.0)


# --- claim ---------------------------------------------------------------


def test_claim_succeeds_from_issued():
    row = _issue()
    claimed = auth.claim(row["authorization_id"])
    assert claimed["status"] == auth.CLAIMED


def test_claim_unknown_id_raises():
    with pytest.raises(auth.ExecutionAuthorizationError, match="unknown authorization_id"):
        auth.claim("EXEC-AUTH-ghost")


def test_claim_on_expired_materializes_expired_and_does_not_raise():
    row = _issue(ttl_seconds=0.001)
    import time

    time.sleep(0.05)
    result = auth.claim(row["authorization_id"])
    assert result["status"] == auth.EXPIRED


# --- revoke ----------------------------------------------------------------


def test_revoke_succeeds_from_issued():
    row = _issue()
    revoked = auth.revoke(row["authorization_id"], "operator cancelled")
    assert revoked["status"] == auth.REVOKED


def test_revoke_requires_a_reason():
    row = _issue()
    with pytest.raises(auth.ExecutionAuthorizationError, match="reason"):
        auth.revoke(row["authorization_id"], "")


def test_revoke_after_claim_is_refused_by_the_locked_contract():
    row = _issue()
    auth.claim(row["authorization_id"])
    result = auth.revoke(row["authorization_id"], "too late")
    assert result["status"] == auth.CLAIMED  # unchanged -- never revoked


# --- begin_submission / resolve / mark_unknown / reconcile ------------------


def test_begin_submission_succeeds_from_claimed():
    row = _issue()
    auth.claim(row["authorization_id"])
    submitted = auth.begin_submission(row["authorization_id"])
    assert submitted["status"] == auth.SUBMITTED


def test_begin_submission_without_claim_is_refused():
    row = _issue()
    result = auth.begin_submission(row["authorization_id"])
    assert result["status"] == auth.ISSUED


def test_resolve_rejects_an_invalid_outcome():
    row = _issue()
    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    with pytest.raises(auth.ExecutionAuthorizationError, match="outcome"):
        auth.resolve(row["authorization_id"], "FILLED_PARTIALLY")


def test_resolve_confirmed_sets_resolved_at():
    row = _issue()
    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    result = auth.resolve(row["authorization_id"], auth.CONFIRMED, broker_order_id="BRK-9")
    assert result["status"] == auth.CONFIRMED
    assert result["resolved_at"] is not None


def test_mark_unknown_leaves_resolved_at_null():
    row = _issue()
    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    result = auth.mark_unknown(row["authorization_id"], broker_state="TIMEOUT")
    assert result["status"] == auth.UNKNOWN
    assert result["resolved_at"] is None


def test_reconcile_rejects_an_invalid_outcome():
    row = _issue()
    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    auth.mark_unknown(row["authorization_id"])
    with pytest.raises(auth.ExecutionAuthorizationError, match="outcome"):
        auth.reconcile(row["authorization_id"], "PARTIALLY_FILLED")


def test_reconcile_resolves_unknown_to_rejected():
    row = _issue()
    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    auth.mark_unknown(row["authorization_id"])
    result = auth.reconcile(row["authorization_id"], auth.REJECTED)
    assert result["status"] == auth.REJECTED
    assert result["resolved_at"] is not None


def test_list_unknown_authorizations_surfaces_unresolved_rows():
    row = _issue()
    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    auth.mark_unknown(row["authorization_id"])
    ids = {r["authorization_id"] for r in auth.list_unknown_authorizations()}
    assert row["authorization_id"] in ids


# --- terminal immutability sweep -------------------------------------------


@pytest.mark.parametrize("make_terminal", [
    lambda aid: auth.revoke(aid, "cancelled"),
])
def test_every_transition_against_a_terminal_status_is_a_no_op(make_terminal):
    row = _issue()
    make_terminal(row["authorization_id"])
    before = auth.get_authorization(row["authorization_id"])

    auth.claim(row["authorization_id"])
    auth.begin_submission(row["authorization_id"])
    try:
        auth.resolve(row["authorization_id"], auth.CONFIRMED)
    except auth.ExecutionAuthorizationError:
        pass
    auth.mark_unknown(row["authorization_id"])

    after = auth.get_authorization(row["authorization_id"])
    assert after == before


# --- structural: no reuse of TradeExecutor concepts, no recovery functions --


def _source_without_module_docstring() -> str:
    """Strips the module's own leading docstring -- it legitimately
    explains, in prose, several concepts this module must NOT contain in
    actual code (e.g. naming the exact forbidden modules/fields), which
    would otherwise self-trip a naive substring scan."""
    source = inspect.getsource(auth)
    return source.split('"""', 2)[-1]


def test_no_import_of_trade_executor_or_broker_clients_or_scheduler():
    """Checked as actual import statements, not bare substrings -- this
    module's own docstrings legitimately NAME execution/ctrader_client.py
    as the historical bug this design avoids, which is prose, not an
    import."""
    source = _source_without_module_docstring()
    forbidden_imports = (
        "import execution.trade_executor", "from execution.trade_executor", "from execution import trade_executor",
        "import execution.ctrader_client", "from execution.ctrader_client", "from execution import ctrader_client",
        "import execution.oanda_client", "from execution.oanda_client", "from execution import oanda_client",
        "import execution.dukascopy_jforex_client", "from execution.dukascopy_jforex_client",
        "from execution import dukascopy_jforex_client",
        "import scheduler", "from scheduler", "import main", "from main",
    )
    for pattern in forbidden_imports:
        assert pattern not in source, f"execution.authorization unexpectedly imports via {pattern!r}"


def test_no_reclaim_or_retry_or_auto_expire_function_exists():
    public_names = [n for n in dir(auth) if not n.startswith("_")]
    forbidden_substrings = ("reclaim", "retry", "resubmit", "force_expire", "recover")
    for name in public_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower(), f"execution.authorization unexpectedly exposes {name!r}"


def test_no_reuse_of_trade_executor_thresholds():
    source = _source_without_module_docstring()
    forbidden = ("min_score_to_execute", "max_open_trades", "allow_live_trading")
    for pattern in forbidden:
        assert pattern not in source, f"execution.authorization unexpectedly references {pattern!r}"
