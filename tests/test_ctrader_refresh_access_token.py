"""tests/test_ctrader_refresh_access_token.py

Regression coverage for scripts/ctrader_refresh_access_token.py — the
operator-run fix for CH_ACCESS_TOKEN_INVALID / "Access token expired"
(execution/ctrader_client.py, BUG-014/15's adjacent finding). No live
network call is ever made in these tests; requests.get is mocked, matching
tests/test_download_deep_history.py's own established convention for this
class of manual, credential-requiring script.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ctrader_refresh_access_token import TOKEN_URL, refresh, write_env_var


def _fake_response(status_code=200, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def test_refresh_calls_the_documented_endpoint_with_correct_params():
    with patch("ctrader_refresh_access_token.requests.get") as mock_get:
        mock_get.return_value = _fake_response(
            json_data={"accessToken": "new-access", "refreshToken": "new-refresh",
                       "expiresIn": 2628000, "tokenType": "bearer"},
        )
        data = refresh("cid", "csecret", "rtoken")
    mock_get.assert_called_once()
    args, kwargs = mock_get.call_args
    assert args[0] == TOKEN_URL == "https://openapi.ctrader.com/apps/token"
    assert kwargs["params"] == {
        "grant_type": "refresh_token",
        "refresh_token": "rtoken",
        "client_id": "cid",
        "client_secret": "csecret",
    }
    assert data["accessToken"] == "new-access"
    assert data["refreshToken"] == "new-refresh"


def test_refresh_raises_readable_error_on_non_200():
    with patch("ctrader_refresh_access_token.requests.get") as mock_get:
        mock_get.return_value = _fake_response(status_code=401, text="invalid_grant")
        with pytest.raises(RuntimeError, match="401"):
            refresh("cid", "csecret", "bad-token")


def test_refresh_raises_readable_error_on_non_json_response():
    with patch("ctrader_refresh_access_token.requests.get") as mock_get:
        mock_get.return_value = _fake_response(status_code=200, json_data=None, text="<html>err</html>")
        with pytest.raises(RuntimeError, match="Non-JSON"):
            refresh("cid", "csecret", "rtoken")


def test_refresh_raises_readable_error_when_access_token_missing():
    with patch("ctrader_refresh_access_token.requests.get") as mock_get:
        mock_get.return_value = _fake_response(json_data={"error": "invalid_grant"})
        with pytest.raises(RuntimeError, match="missing accessToken"):
            refresh("cid", "csecret", "rtoken")


def test_refresh_raises_readable_error_on_request_exception():
    with patch("ctrader_refresh_access_token.requests.get",
              side_effect=requests.ConnectionError("network down")):
        with pytest.raises(RuntimeError, match="Request failed"):
            refresh("cid", "csecret", "rtoken")


def test_write_env_var_replaces_existing_key_preserving_other_lines(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# comment\n"
        "CTRADER_CLIENT_ID=abc\n"
        "CTRADER_ACCESS_TOKEN=old-token\n"
        "OTHER_VAR=unchanged\n"
    )
    write_env_var(env_path, "CTRADER_ACCESS_TOKEN", "new-token")
    content = env_path.read_text()
    assert "CTRADER_ACCESS_TOKEN=new-token\n" in content
    assert "old-token" not in content
    assert "# comment\n" in content
    assert "CTRADER_CLIENT_ID=abc\n" in content
    assert "OTHER_VAR=unchanged\n" in content


def test_write_env_var_appends_key_when_absent(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SOME_OTHER_KEY=value\n")
    write_env_var(env_path, "CTRADER_REFRESH_TOKEN", "fresh-refresh")
    content = env_path.read_text()
    assert "SOME_OTHER_KEY=value\n" in content
    assert "CTRADER_REFRESH_TOKEN=fresh-refresh\n" in content


def test_write_env_var_does_not_touch_a_similarly_prefixed_key(tmp_path):
    """CTRADER_ACCESS_TOKEN must not accidentally match/clobber
    CTRADER_ACCESS_TOKEN_EXPIRY or similar — the regex is anchored to the
    exact key followed by '='."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CTRADER_ACCESS_TOKEN_EXPIRY=2026-09-01\n"
        "CTRADER_ACCESS_TOKEN=old\n"
    )
    write_env_var(env_path, "CTRADER_ACCESS_TOKEN", "new")
    content = env_path.read_text()
    assert "CTRADER_ACCESS_TOKEN_EXPIRY=2026-09-01\n" in content
    assert "CTRADER_ACCESS_TOKEN=new\n" in content
