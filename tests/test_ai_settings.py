"""
tests/test_ai_settings.py
-----------------------------
AI Settings (2026-07-28) — contract tests for GET/POST /ai/settings and
the per-request provider/model override on POST /ai/suggest-hypothesis.
Matches tests/test_api_server.py's client/HDR fixture convention.
"""
from __future__ import annotations

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

_VALID_SAVE_BODY = {
    "enabled": False,
    "providers": {"gemini": {"enabled": True}, "openai": {"enabled": False}, "anthropic": {"enabled": False}},
    "fallback_order": ["gemini", "openai", "anthropic"],
    "model": "gemini-flash-latest",
    "temperature": 0.1,
    "max_tokens": 1200,
    "timeout": 20,
}


@pytest.fixture
def client(monkeypatch):
    import execution.api_server as m
    m._ENV = "development"
    monkeypatch.setenv("API_SERVER_KEY", "test-key-123")
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_real_ai_keys(monkeypatch):
    """Every test in this file gets a clean slate — no accidental
    dependence on whatever API keys happen to be exported in the real
    shell this suite runs in."""
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


# ── GET /ai/settings ──────────────────────────────────────────────────────

def test_ai_get_settings_requires_auth(client):
    assert client.get("/ai/settings").status_code == 401


def test_ai_get_settings_returns_real_config_shape(client):
    r = client.get("/ai/settings", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    for key in ("enabled", "providers", "fallback_order", "model", "temperature",
                "max_tokens", "timeout", "default_models", "has_api_key", "active_provider"):
        assert key in body
    assert set(body["has_api_key"]) == {"gemini", "openai", "anthropic"}
    assert all(v is False for v in body["has_api_key"].values())  # no keys in this sandbox


def test_ai_get_settings_has_api_key_reflects_env(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-test")
    r = client.get("/ai/settings", headers=HDR)
    assert r.json()["has_api_key"]["openai"] is True
    assert r.json()["has_api_key"]["gemini"] is False


# ── POST /ai/settings — auth + validation ─────────────────────────────────

def test_ai_save_settings_requires_auth(client):
    assert client.post("/ai/settings", json=_VALID_SAVE_BODY).status_code == 401


def test_ai_save_settings_rejects_unknown_provider(client):
    body = {**_VALID_SAVE_BODY, "providers": {"deepseek": {"enabled": True}}}
    r = client.post("/ai/settings", json=body, headers=HDR)
    assert r.status_code == 400
    assert "Unknown provider" in r.json()["detail"]


def test_ai_save_settings_rejects_unknown_provider_in_fallback_order(client):
    body = {**_VALID_SAVE_BODY, "fallback_order": ["gemini", "deepseek"]}
    r = client.post("/ai/settings", json=body, headers=HDR)
    assert r.status_code == 400


def test_ai_save_settings_rejects_duplicate_fallback_order(client):
    body = {**_VALID_SAVE_BODY, "fallback_order": ["gemini", "gemini"]}
    r = client.post("/ai/settings", json=body, headers=HDR)
    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"].lower()


def test_ai_save_settings_rejects_empty_model(client):
    body = {**_VALID_SAVE_BODY, "model": "   "}
    r = client.post("/ai/settings", json=body, headers=HDR)
    assert r.status_code == 400


@pytest.mark.parametrize("field,value", [
    ("temperature", -0.1), ("temperature", 2.1),
    ("max_tokens", 0), ("max_tokens", 100_000),
    ("timeout", 0), ("timeout", 500),
])
def test_ai_save_settings_rejects_out_of_bounds_values(client, field, value):
    body = {**_VALID_SAVE_BODY, field: value}
    r = client.post("/ai/settings", json=body, headers=HDR)
    assert r.status_code == 400


def test_ai_save_settings_rejects_enabling_without_api_key(client):
    body = {**_VALID_SAVE_BODY, "enabled": True}
    r = client.post("/ai/settings", json=body, headers=HDR)
    assert r.status_code == 400
    assert "no API key configured" in r.json()["detail"]


# ── POST /ai/settings — real write ─────────────────────────────────────────

def test_ai_save_settings_writes_real_file_and_resets_cache(client, tmp_path, monkeypatch):
    import execution.routes.ai as m

    fake_path = tmp_path / "ai.yaml"
    monkeypatch.setattr(m, "_AI_CONFIG_PATH", fake_path)
    monkeypatch.setenv("GEMINI_API_KEY", "sk-fake-for-test")

    reset_calls = {"n": 0}
    real_reset = m._reset_config_cache

    def _counting_reset():
        reset_calls["n"] += 1
        real_reset()

    monkeypatch.setattr(m, "_reset_config_cache", _counting_reset)

    body = {
        "enabled": True,
        "providers": {"gemini": {"enabled": True}, "openai": {"enabled": False}, "anthropic": {"enabled": False}},
        "fallback_order": ["gemini", "openai", "anthropic"],
        "model": "gemini-2.5-flash-test",
        "temperature": 0.4,
        "max_tokens": 900,
        "timeout": 15,
    }
    r = client.post("/ai/settings", json=body, headers=HDR)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "saved"
    assert r.json()["active_provider"] == "gemini"

    assert fake_path.exists()
    content = fake_path.read_text()
    assert "config/ai.yaml" in content  # header preserved
    import yaml
    written = yaml.safe_load(content)
    assert written["enabled"] is True
    assert written["model"] == "gemini-2.5-flash-test"
    assert written["temperature"] == 0.4
    assert written["fallback_order"] == ["gemini", "openai", "anthropic"]
    assert reset_calls["n"] == 1


# ── Per-request override on /ai/suggest-hypothesis ─────────────────────────

def test_ai_suggest_hypothesis_override_rejects_unknown_provider(client):
    r = client.post("/ai/suggest-hypothesis", json={"provider": "deepseek"}, headers=HDR)
    assert r.status_code == 400
    assert "Unknown provider" in r.json()["detail"]


def test_ai_suggest_hypothesis_override_with_no_key_returns_disabled_not_crash(client):
    r = client.post("/ai/suggest-hypothesis", json={"provider": "openai", "model": "gpt-4o"}, headers=HDR)
    assert r.status_code == 200
    assert r.json()["status"] in ("disabled", "error")


def test_ai_suggest_hypothesis_override_does_not_mutate_shared_config_cache(client):
    from execution.api_core import _get_config

    before = dict(_get_config().get("ai", {}) or {})
    client.post("/ai/suggest-hypothesis", json={"provider": "anthropic"}, headers=HDR)
    after = dict(_get_config().get("ai", {}) or {})
    assert before == after
