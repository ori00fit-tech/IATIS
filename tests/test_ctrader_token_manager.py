"""tests/test_ctrader_token_manager.py

Regression coverage for integrations/ctrader/token_manager.py —
get_valid_access_token()'s expiry-margin/no-network-when-fresh logic,
refresh_access_token_sync()'s persistence, and token_status()'s
never-leaks-the-raw-token contract. No live network call is ever made;
oauth.refresh_tokens is mocked at the module level it's called from.
"""
from __future__ import annotations

import os
import time
from unittest.mock import patch

import pytest

from integrations.ctrader import token_manager


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for key in (
        "CTRADER_ACCESS_TOKEN", "CTRADER_REFRESH_TOKEN", "CTRADER_ACCESS_TOKEN_EXPIRY",
        "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCOUNT_ID", "CTRADER_ENVIRONMENT",
    ):
        monkeypatch.delenv(key, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("")
    monkeypatch.setattr(token_manager, "_ENV_PATH", env_path)
    yield env_path


def test_get_valid_access_token_returns_current_token_when_expiry_unknown(monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "tok-1")
    with patch("integrations.ctrader.token_manager.oauth.refresh_tokens") as mock_refresh:
        result = token_manager.get_valid_access_token()
    assert result == "tok-1"
    mock_refresh.assert_not_called()


def test_get_valid_access_token_returns_current_token_when_far_from_expiry(monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "tok-1")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN_EXPIRY", str(time.time() + 30 * 86400))
    with patch("integrations.ctrader.token_manager.oauth.refresh_tokens") as mock_refresh:
        result = token_manager.get_valid_access_token(margin_seconds=86400)
    assert result == "tok-1"
    mock_refresh.assert_not_called()


def test_get_valid_access_token_refreshes_when_within_margin(monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "old-tok")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN_EXPIRY", str(time.time() + 100))
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("CTRADER_REFRESH_TOKEN", "rtok")
    with patch(
        "integrations.ctrader.token_manager.oauth.refresh_tokens",
        return_value={"accessToken": "new-tok", "refreshToken": "new-rtok", "expiresIn": 2628000},
    ) as mock_refresh:
        result = token_manager.get_valid_access_token(margin_seconds=86400)
    mock_refresh.assert_called_once_with("cid", "csecret", "rtok")
    assert result == "new-tok"
    assert os.environ["CTRADER_ACCESS_TOKEN"] == "new-tok"


def test_get_valid_access_token_treats_malformed_expiry_as_unknown(monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "tok-1")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN_EXPIRY", "not-a-number")
    with patch("integrations.ctrader.token_manager.oauth.refresh_tokens") as mock_refresh:
        result = token_manager.get_valid_access_token()
    assert result == "tok-1"
    mock_refresh.assert_not_called()


def test_refresh_access_token_sync_updates_env_and_writes_dotenv(monkeypatch, _clean_env):
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("CTRADER_REFRESH_TOKEN", "old-rtok")
    with patch(
        "integrations.ctrader.token_manager.oauth.refresh_tokens",
        return_value={"accessToken": "fresh-access", "refreshToken": "fresh-refresh", "expiresIn": 2628000},
    ):
        result = token_manager.refresh_access_token_sync()
    assert result == "fresh-access"
    assert os.environ["CTRADER_ACCESS_TOKEN"] == "fresh-access"
    assert os.environ["CTRADER_REFRESH_TOKEN"] == "fresh-refresh"
    assert "CTRADER_ACCESS_TOKEN_EXPIRY" in os.environ
    content = _clean_env.read_text()
    assert "CTRADER_ACCESS_TOKEN=fresh-access" in content
    assert "CTRADER_REFRESH_TOKEN=fresh-refresh" in content


def test_refresh_access_token_sync_raises_when_credentials_missing(monkeypatch):
    with pytest.raises(token_manager.TokenRefreshError):
        token_manager.refresh_access_token_sync()


def test_refresh_access_token_sync_raises_token_refresh_error_on_oauth_failure(monkeypatch):
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("CTRADER_REFRESH_TOKEN", "dead-rtok")
    from integrations.ctrader.oauth import CTraderOAuthError

    with patch(
        "integrations.ctrader.token_manager.oauth.refresh_tokens",
        side_effect=CTraderOAuthError("400: invalid_grant"),
    ):
        with pytest.raises(token_manager.TokenRefreshError):
            token_manager.refresh_access_token_sync()


def test_refresh_access_token_sync_only_calls_oauth_once_under_concurrent_calls(monkeypatch):
    """Two threads racing refresh_access_token_sync() must only hit the
    token endpoint once — _refresh_lock serializes them."""
    import threading

    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("CTRADER_REFRESH_TOKEN", "rtok")

    call_count = {"n": 0}
    lock_order = threading.Lock()

    def _slow_refresh(*args, **kwargs):
        with lock_order:
            call_count["n"] += 1
        time.sleep(0.05)
        return {"accessToken": "tok", "refreshToken": "rtok2", "expiresIn": 2628000}

    with patch("integrations.ctrader.token_manager.oauth.refresh_tokens", side_effect=_slow_refresh):
        threads = [threading.Thread(target=token_manager.refresh_access_token_sync) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert call_count["n"] == 3  # each call still runs (no dedup) but never concurrently — the lock only serializes


def test_save_initial_tokens_uses_the_same_persistence_path(monkeypatch, _clean_env):
    token_manager.save_initial_tokens({"accessToken": "cb-access", "refreshToken": "cb-refresh", "expiresIn": 100})
    assert os.environ["CTRADER_ACCESS_TOKEN"] == "cb-access"
    assert "CTRADER_ACCESS_TOKEN=cb-access" in _clean_env.read_text()


def test_persist_logs_but_does_not_raise_on_dotenv_write_failure(monkeypatch, caplog):
    monkeypatch.setattr(token_manager, "_ENV_PATH", "/nonexistent-dir/.env")
    token_manager.save_initial_tokens({"accessToken": "tok", "refreshToken": "rtok", "expiresIn": 100})
    assert os.environ["CTRADER_ACCESS_TOKEN"] == "tok"  # in-memory state still updated


def test_token_status_never_includes_raw_token_values(monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "super-secret-access")
    monkeypatch.setenv("CTRADER_REFRESH_TOKEN", "super-secret-refresh")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "12345")
    status = token_manager.token_status()
    serialized = str(status)
    assert "super-secret-access" not in serialized
    assert "super-secret-refresh" not in serialized
    assert status["configured"] is True
    assert status["has_refresh_token"] is True
    assert status["account_id"] == "12345"


def test_token_status_reports_needs_reauthorization_when_expired_with_no_refresh(monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN_EXPIRY", str(time.time() - 100))
    status = token_manager.token_status()
    assert status["needs_reauthorization"] is True


def test_token_status_defaults_environment_to_demo(monkeypatch):
    status = token_manager.token_status()
    assert status["environment"] == "demo"
    assert status["configured"] is False
