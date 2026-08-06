"""tests/test_ctrader_auth_routes.py

Contract + hard-block safety tests for execution/routes/ctrader_auth.py —
the cTrader OAuth 2.0 web flow. Matches tests/test_api_contract.py's
client/HDR fixture conventions. No live network call to id.ctrader.com/
openapi.ctrader.com is ever made — integrations.ctrader.oauth's exchange/
account-discovery calls are mocked.
"""
from __future__ import annotations

import inspect
import os

import pytest

os.environ.setdefault("ENV", "development")
os.environ.setdefault("API_SERVER_KEY", "test-key-123")

try:
    from fastapi.testclient import TestClient
    import execution.api_server as _api_mod
    _api_mod._ENV = "development"
    from execution.api_server import app
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

pytestmark = pytest.mark.skipif(not AVAILABLE, reason="fastapi not installed")

HDR = {"X-API-Key": "test-key-123"}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_ctrader_env(monkeypatch):
    for key in (
        "CTRADER_ACCESS_TOKEN", "CTRADER_REFRESH_TOKEN", "CTRADER_ACCESS_TOKEN_EXPIRY",
        "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_REDIRECT_URI",
        "CTRADER_ACCOUNT_ID", "CTRADER_ENVIRONMENT", "CTRADER_OAUTH_SCOPE",
    ):
        monkeypatch.delenv(key, raising=False)
    import execution.routes.ctrader_auth as m
    m._pending_states.clear()
    yield
    m._pending_states.clear()


def _login(client) -> None:
    r = client.post("/login", json={"key": "test-key-123"})
    assert r.status_code == 200


def test_ctrader_status_requires_auth(client):
    r = client.get("/ctrader/status")
    assert r.status_code == 401


def test_ctrader_status_returns_shape_and_never_the_raw_token(client, monkeypatch):
    monkeypatch.setenv("CTRADER_ACCESS_TOKEN", "very-secret-token-value")
    r = client.get("/ctrader/status", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert "very-secret-token-value" not in r.text


def test_ctrader_authorize_requires_auth(client):
    r = client.get("/ctrader/authorize")
    assert r.status_code == 401


def test_ctrader_authorize_returns_500_when_not_configured(client):
    r = client.get("/ctrader/authorize", headers=HDR)
    assert r.status_code == 500


def test_ctrader_authorize_redirects_with_state(client, monkeypatch):
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_REDIRECT_URI", "https://iatis.example/ctrader/callback")
    r = client.get("/ctrader/authorize", headers=HDR)
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://id.ctrader.com/my/settings/openapi/grantingaccess/")
    assert "client_id=cid" in location
    assert "state=" in location

    import execution.routes.ctrader_auth as m
    assert len(m._pending_states) == 1


def test_ctrader_callback_requires_session_cookie(client, monkeypatch):
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_REDIRECT_URI", "https://iatis.example/ctrader/callback")
    r = client.get("/ctrader/callback", params={"code": "abc", "state": "whatever"})
    assert r.status_code == 401


def test_ctrader_callback_rejects_unknown_state(client, monkeypatch):
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_REDIRECT_URI", "https://iatis.example/ctrader/callback")
    _login(client)
    r = client.get("/ctrader/callback", params={"code": "abc", "state": "not-a-real-state"})
    assert r.status_code == 400


def test_ctrader_callback_forwards_provider_error(client, monkeypatch):
    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_REDIRECT_URI", "https://iatis.example/ctrader/callback")
    _login(client)
    r = client.get("/ctrader/callback", params={"error": "access_denied"})
    assert r.status_code == 302
    assert "ctrader_error=access_denied" in r.headers["location"]


def test_ctrader_callback_success_exchanges_code_and_never_leaks_it(client, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("CTRADER_REDIRECT_URI", "https://iatis.example/ctrader/callback")
    _login(client)

    r1 = client.get("/ctrader/authorize", headers=HDR)
    state = r1.headers["location"].split("state=")[1].split("&")[0]

    with patch(
        "integrations.ctrader.oauth.exchange_code",
        return_value={"accessToken": "new-access-xyz", "refreshToken": "new-refresh-xyz", "expiresIn": 2628000},
    ) as mock_exchange, patch(
        "integrations.ctrader.account.discover_accounts", return_value=[],
    ):
        r2 = client.get("/ctrader/callback", params={"code": "one-shot-code", "state": state})

    assert r2.status_code == 302
    assert "ctrader_connected=1" in r2.headers["location"]
    assert "new-access-xyz" not in r2.text
    assert "one-shot-code" not in r2.text
    mock_exchange.assert_called_once_with("cid", "csecret", "one-shot-code", "https://iatis.example/ctrader/callback")
    assert os.environ["CTRADER_ACCESS_TOKEN"] == "new-access-xyz"

    # state is single-use — replaying the same callback URL must now fail.
    r3 = client.get("/ctrader/callback", params={"code": "one-shot-code", "state": state})
    assert r3.status_code == 400


def test_ctrader_callback_exchange_failure_redirects_with_error_not_500(client, monkeypatch):
    from unittest.mock import patch

    from integrations.ctrader.oauth import CTraderOAuthError

    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("CTRADER_REDIRECT_URI", "https://iatis.example/ctrader/callback")
    _login(client)

    r1 = client.get("/ctrader/authorize", headers=HDR)
    state = r1.headers["location"].split("state=")[1].split("&")[0]

    with patch("integrations.ctrader.oauth.exchange_code", side_effect=CTraderOAuthError("400: invalid_grant")):
        r2 = client.get("/ctrader/callback", params={"code": "bad-code", "state": state})

    assert r2.status_code == 302
    assert "ctrader_error=exchange_failed" in r2.headers["location"]


def test_ctrader_callback_account_discovery_failure_is_non_fatal(client, monkeypatch):
    from unittest.mock import patch

    monkeypatch.setenv("CTRADER_CLIENT_ID", "cid")
    monkeypatch.setenv("CTRADER_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("CTRADER_REDIRECT_URI", "https://iatis.example/ctrader/callback")
    _login(client)

    r1 = client.get("/ctrader/authorize", headers=HDR)
    state = r1.headers["location"].split("state=")[1].split("&")[0]

    with patch(
        "integrations.ctrader.oauth.exchange_code",
        return_value={"accessToken": "tok", "refreshToken": "rtok", "expiresIn": 100},
    ), patch(
        "integrations.ctrader.account.discover_accounts", side_effect=RuntimeError("discovery unreachable"),
    ):
        r2 = client.get("/ctrader/callback", params={"code": "code", "state": state})

    assert r2.status_code == 302
    assert "ctrader_connected=1" in r2.headers["location"]


def test_ctrader_auth_module_never_touches_the_live_trading_session():
    """Hard-block: execution/routes/ctrader_auth.py and every module in
    integrations/ctrader/ must never CALL/REFERENCE (as a real identifier,
    not merely mention in a docstring/comment) get_shared_ctrader_client,
    _acquire_process_lock, or TradeExecutor — this feature only ever
    supplies credentials, it never opens or competes for the scheduler's
    exclusive live cTrader session. AST-based (Name/Attribute nodes only)
    so the module's own explanatory docstrings, which legitimately name
    these symbols to describe what NOT to do, don't trip a false positive."""
    import ast

    import execution.routes.ctrader_auth as route_mod
    from integrations.ctrader import account, oauth, token_manager

    forbidden = ("get_shared_ctrader_client", "_acquire_process_lock", "TradeExecutor")
    for mod in (route_mod, account, oauth, token_manager):
        tree = ast.parse(inspect.getsource(mod))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        } | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names
        }
        for name in forbidden:
            assert name not in identifiers, f"{mod.__name__} must never reference {name}"
