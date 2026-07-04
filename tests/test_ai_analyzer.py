"""
tests/test_ai_analyzer.py
----------------------------
Tests for ai/ — providers, cache, and the AIAnalyzer orchestrator. All
HTTP calls are mocked (same convention as tests/test_twelve_data.py) —
no real API keys or network access required.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from ai.cache import TTLCache
from ai.providers.base import AIProviderError, extract_json, load_prompt
from ai.providers.perplexity import PerplexityProvider
from ai.providers.openai import OpenAIProvider
from ai.providers.anthropic import AnthropicProvider
from ai.ai_analyzer import AIAnalyzer


# ---------------------------------------------------------------------------
# base.py helpers
# ---------------------------------------------------------------------------

def test_extract_json_parses_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_markdown_fences():
    text = '```json\n{"a": 1}\n```'
    assert extract_json(text) == {"a": 1}


def test_extract_json_raises_on_garbage():
    with pytest.raises(AIProviderError):
        extract_json("not json at all")


def test_load_prompt_fills_placeholders():
    text = load_prompt("summarize", text="hello world")
    assert "hello world" in text


def test_load_prompt_raises_on_missing_placeholder():
    with pytest.raises(AIProviderError):
        load_prompt("summarize")  # missing `text=`


# ---------------------------------------------------------------------------
# ai/cache.py
# ---------------------------------------------------------------------------

def test_ttl_cache_returns_cached_value_within_ttl():
    cache = TTLCache()
    calls = []

    def compute():
        calls.append(1)
        return "value"

    assert cache.get_or_compute("k", 60, compute) == "value"
    assert cache.get_or_compute("k", 60, compute) == "value"
    assert len(calls) == 1  # second call served from cache


def test_ttl_cache_recomputes_after_expiry():
    cache = TTLCache()
    calls = []

    def compute():
        calls.append(1)
        return len(calls)

    assert cache.get_or_compute("k", 0.01, compute) == 1
    time.sleep(0.02)
    assert cache.get_or_compute("k", 0.01, compute) == 2


# ---------------------------------------------------------------------------
# Providers — each hits a different API shape, mocked at the requests layer
# ---------------------------------------------------------------------------

def _mock_response(json_body: dict, status_ok: bool = True) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock() if status_ok else MagicMock(
        side_effect=Exception("HTTP error")
    )
    return resp


def test_perplexity_provider_chat_parses_openai_style_response():
    provider = PerplexityProvider(api_key="test-key", model="sonar-pro")
    fake_resp = _mock_response({"choices": [{"message": {"content": "hello"}}]})
    with patch("ai.providers.perplexity.requests.post", return_value=fake_resp) as post:
        result = provider._chat("say hello")
    assert result == "hello"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


def test_openai_provider_chat_parses_response():
    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini")
    fake_resp = _mock_response({"choices": [{"message": {"content": "hi there"}}]})
    with patch("ai.providers.openai.requests.post", return_value=fake_resp):
        result = provider._chat("say hi")
    assert result == "hi there"


def test_anthropic_provider_chat_parses_content_blocks_and_sends_auth_headers():
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")
    fake_resp = _mock_response({"content": [{"type": "text", "text": "hi from claude"}]})
    with patch("ai.providers.anthropic.requests.post", return_value=fake_resp) as post:
        result = provider._chat("say hi")
    assert result == "hi from claude"
    headers = post.call_args.kwargs["headers"]
    assert headers["x-api-key"] == "test-key"
    assert "anthropic-version" in headers


def test_provider_raises_ai_provider_error_on_request_exception():
    import requests as _requests
    provider = PerplexityProvider(api_key="test-key", model="sonar-pro")
    with patch(
        "ai.providers.perplexity.requests.post",
        side_effect=_requests.RequestException("boom"),
    ):
        with pytest.raises(AIProviderError):
            provider._chat("hello")


# ---------------------------------------------------------------------------
# AIAnalyzer orchestrator
# ---------------------------------------------------------------------------

def _config(enabled: bool = True, provider: str = "perplexity") -> dict:
    return {"ai": {"enabled": enabled, "provider": provider, "model": "sonar-pro",
                    "cache": {"news_ttl_min": 20, "macro_ttl_min": 60}}}


def test_ai_analyzer_disabled_by_default_returns_status_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    assert analyzer.available is False
    result = analyzer.explain_trade({"symbol": "EURUSD"})
    assert result["status"] == "disabled"


def test_ai_analyzer_without_api_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    analyzer = AIAnalyzer(_config(enabled=True))
    assert analyzer.available is False
    result = analyzer.explain_trade({"symbol": "EURUSD"})
    assert result["status"] == "disabled"


def test_ai_analyzer_explain_trade_never_touches_final_verdict(monkeypatch):
    """Explicit regression guard for the design constraint: AIAnalyzer must
    not be able to influence or overwrite final_verdict on the report it's
    given — it only reads from it and returns a separate explanation dict."""
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    assert analyzer.available is True

    report = {
        "symbol": "EURUSD",
        "final_verdict": "EXECUTE",
        "confluence": {"score": 72, "vote": {"winning_bias": "BULLISH"}},
        "regime": {"state": "TRENDING", "trend_strength": 0.6},
        "risk": {"passed": True, "recommended_risk_pct": 0.01},
        "news": {"risk_level": "LOW", "blackout_active": False},
        "engine_outputs": [
            {"engine": "SMC", "bias": "BULLISH", "score": 65, "reasons": ["HH/HL"]}
        ],
    }
    fake_reply = (
        '{"summary": "s", "pros": ["p1"], "cons": [], "risk_level": "LOW", '
        '"confidence": 80, "recommendation": "r", "market_sentiment": "Bullish", '
        '"news_risk": "Low", "explanation": "e", "warnings": []}'
    )
    with patch.object(PerplexityProvider, "_chat", return_value=fake_reply):
        result = analyzer.explain_trade(report)

    assert result["status"] == "ok"
    assert result["summary"] == "s"
    assert "final_verdict" not in result
    assert report["final_verdict"] == "EXECUTE"  # untouched


def test_ai_analyzer_explain_trade_handles_provider_error_gracefully(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(PerplexityProvider, "_chat", side_effect=AIProviderError("boom")):
        result = analyzer.explain_trade({"symbol": "EURUSD"})
    assert result["status"] == "error"
    assert "boom" in result["error"]


def test_ai_analyzer_analyze_news_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    result = analyzer.analyze_news([{"name": "NFP", "impact": "High"}], ["USD"])
    assert result["status"] == "disabled"


def test_ai_analyzer_unknown_provider_is_unavailable(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True, provider="not_a_real_provider"))
    assert analyzer.available is False


def test_ai_analyzer_generate_research_summary_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    result = analyzer.generate_research_summary({"total": 13, "passed": 1})
    assert result["status"] == "disabled"


def test_ai_analyzer_generate_research_summary_ok(monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(PerplexityProvider, "_chat", return_value="13 hypotheses, 1 passed."):
        result = analyzer.generate_research_summary(
            {"total": 13, "passed": 1, "failed": 3, "research": 9, "avg_wr": 60.5, "avg_pf": 2.72}
        )
    assert result["status"] == "ok"
    assert "13 hypotheses" in result["text"]


def test_ai_analyzer_generate_daily_report_still_works_after_refactor(monkeypatch):
    # Regression guard: generate_daily_report and generate_research_summary
    # now share _summarize_text() — make sure the refactor didn't change
    # generate_daily_report's own behavior.
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(PerplexityProvider, "_chat", return_value="Quiet day, 3 EXECUTE signals."):
        result = analyzer.generate_daily_report({"total": 10, "execute": 3, "no_trade": 7})
    assert result["status"] == "ok"
    assert "Quiet day" in result["text"]
