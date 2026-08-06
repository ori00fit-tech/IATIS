"""tests/test_ctrader_oauth.py

Regression coverage for integrations/ctrader/oauth.py — the cTrader OAuth
2.0 web-flow rebuild. refresh_tokens()/write_env_var()'s own HTTP-call-shape
and anchor-collision tests already live in
tests/test_ctrader_refresh_access_token.py (which imports through the
script's re-exported names, pinning the CLI's own public contract). This
file covers what's genuinely new here: build_authorize_url() and
exchange_code() (the authorization_code grant, not previously implemented
anywhere in this repo). No live network call is ever made — requests.get
is mocked.
"""
from __future__ import annotations

from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from integrations.ctrader.oauth import (
    AUTHORIZE_URL,
    TOKEN_URL,
    CTraderOAuthError,
    build_authorize_url,
    exchange_code,
)


def _fake_response(status_code=200, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def test_build_authorize_url_has_the_documented_params():
    url = build_authorize_url("cid", "https://iatis.example/ctrader/callback", "state123", scope="trading")
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZE_URL
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["cid"]
    assert params["redirect_uri"] == ["https://iatis.example/ctrader/callback"]
    assert params["scope"] == ["trading"]
    assert params["product"] == ["web"]
    assert params["state"] == ["state123"]


def test_build_authorize_url_defaults_scope_to_trading():
    url = build_authorize_url("cid", "https://iatis.example/ctrader/callback", "state123")
    assert parse_qs(urlparse(url).query)["scope"] == ["trading"]


def test_exchange_code_calls_the_documented_endpoint_with_correct_params():
    with patch("integrations.ctrader.oauth.requests.get") as mock_get:
        mock_get.return_value = _fake_response(
            json_data={"accessToken": "new-access", "refreshToken": "new-refresh", "expiresIn": 2628000},
        )
        data = exchange_code("cid", "csecret", "authcode", "https://iatis.example/ctrader/callback")
    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert args[0] == TOKEN_URL
    assert kwargs["params"] == {
        "grant_type": "authorization_code",
        "code": "authcode",
        "redirect_uri": "https://iatis.example/ctrader/callback",
        "client_id": "cid",
        "client_secret": "csecret",
    }
    assert data["accessToken"] == "new-access"


def test_exchange_code_raises_readable_error_on_non_200():
    with patch("integrations.ctrader.oauth.requests.get") as mock_get:
        mock_get.return_value = _fake_response(status_code=400, text="invalid_grant")
        with pytest.raises(CTraderOAuthError, match="400"):
            exchange_code("cid", "csecret", "bad-code", "https://iatis.example/ctrader/callback")


def test_exchange_code_raises_readable_error_on_missing_access_token():
    with patch("integrations.ctrader.oauth.requests.get") as mock_get:
        mock_get.return_value = _fake_response(json_data={"error": "invalid_grant"})
        with pytest.raises(CTraderOAuthError, match="missing accessToken"):
            exchange_code("cid", "csecret", "code", "https://iatis.example/ctrader/callback")


def test_exchange_code_raises_readable_error_on_request_exception():
    with patch("integrations.ctrader.oauth.requests.get", side_effect=requests.ConnectionError("network down")):
        with pytest.raises(CTraderOAuthError, match="Request failed"):
            exchange_code("cid", "csecret", "code", "https://iatis.example/ctrader/callback")


def test_ctrader_oauth_error_is_a_runtime_error():
    """Existing callers (scripts/ctrader_refresh_access_token.py's prior
    `except RuntimeError` contract) must keep working unchanged."""
    assert issubclass(CTraderOAuthError, RuntimeError)
