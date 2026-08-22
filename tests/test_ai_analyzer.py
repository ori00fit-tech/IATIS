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
from ai.providers.gemini import GeminiProvider
from ai.providers.openai import OpenAIProvider
from ai.providers.anthropic import AnthropicProvider
from ai.ai_analyzer import AIAnalyzer, _resolve_provider_name


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


def test_gemini_provider_chat_parses_response():
    provider = GeminiProvider(api_key="test-key", model="gemini-flash-latest")
    fake_resp = _mock_response(
        {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}
    )
    with patch("ai.providers.gemini.requests.post", return_value=fake_resp) as post:
        result = provider._chat("say hello")
    assert result == "hello"
    assert post.call_args.kwargs["headers"]["X-goog-api-key"] == "test-key"


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
    provider = GeminiProvider(api_key="test-key", model="gemini-flash-latest")
    with patch(
        "ai.providers.gemini.requests.post",
        side_effect=_requests.RequestException("boom"),
    ):
        with pytest.raises(AIProviderError):
            provider._chat("hello")


# ---------------------------------------------------------------------------
# AIAnalyzer orchestrator
# ---------------------------------------------------------------------------

def _config(enabled: bool = True, provider: str = "gemini") -> dict:
    return {"ai": {"enabled": enabled, "provider": provider, "model": "gemini-flash-latest",
                    "cache": {"news_ttl_min": 20, "macro_ttl_min": 60}}}


def test_ai_analyzer_disabled_by_default_returns_status_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    assert analyzer.available is False
    result = analyzer.explain_trade({"symbol": "EURUSD"})
    assert result["status"] == "disabled"


def test_ai_analyzer_without_api_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    analyzer = AIAnalyzer(_config(enabled=True))
    assert analyzer.available is False
    result = analyzer.explain_trade({"symbol": "EURUSD"})
    assert result["status"] == "disabled"


def test_ai_analyzer_explain_trade_never_touches_final_verdict(monkeypatch):
    """Explicit regression guard for the design constraint: AIAnalyzer must
    not be able to influence or overwrite final_verdict on the report it's
    given — it only reads from it and returns a separate explanation dict."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
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
    with patch.object(GeminiProvider, "_chat", return_value=fake_reply):
        result = analyzer.explain_trade(report)

    assert result["status"] == "ok"
    assert result["summary"] == "s"
    assert "final_verdict" not in result
    assert report["final_verdict"] == "EXECUTE"  # untouched


def test_ai_analyzer_explain_trade_handles_provider_error_gracefully(monkeypatch):
    # The raw exception ("boom") must be logged (see caplog test below) but
    # never reach the API response — end users get a plain-language
    # message, not a provider internals dump. See _user_safe_error.
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", side_effect=AIProviderError("boom")):
        result = analyzer.explain_trade({"symbol": "EURUSD"})
    assert result["status"] == "error"
    assert "boom" not in result["error"]
    assert result["error"] == "The AI provider request failed. See server logs for details."


def test_ai_analyzer_provider_error_full_detail_still_reaches_the_logs(monkeypatch, caplog):
    # The sanitized message is for the API response only — an operator
    # debugging via logs must still see the real exception.
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", side_effect=AIProviderError("HTTPSConnectionPool(host='x', port=443): Read timed out.")):
        with caplog.at_level("WARNING"):
            analyzer.explain_trade({"symbol": "EURUSD"})
    assert "Read timed out" in caplog.text


@pytest.mark.parametrize("raw,expected_fragment", [
    ("Gemini request failed: HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): Read timed out. (read timeout=20.0)", "took too long"),
    ("Connection failed: Name or service not known", "Could not reach"),
    ("OpenAI request failed: 401 Unauthorized", "rejected the request"),
    ("Anthropic request failed: 429 Too Many Requests — rate limit exceeded", "rate-limiting"),
    ("Unexpected Gemini response shape: no text content", "unexpected response"),
])
def test_user_safe_error_never_leaks_connection_internals(raw, expected_fragment):
    from ai.ai_analyzer import _user_safe_error

    message = _user_safe_error(AIProviderError(raw))
    assert expected_fragment.lower() in message.lower()
    # The whole point: no hostnames, ports, or library exception names.
    assert "generativelanguage.googleapis.com" not in message
    assert "443" not in message
    assert "HTTPSConnectionPool" not in message


def test_ai_analyzer_analyze_news_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    result = analyzer.analyze_news([{"name": "NFP", "impact": "High"}], ["USD"])
    assert result["status"] == "disabled"


def test_ai_analyzer_unknown_provider_is_unavailable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True, provider="not_a_real_provider"))
    assert analyzer.available is False


def test_ai_analyzer_generate_research_summary_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    result = analyzer.generate_research_summary({"total": 13, "passed": 1})
    assert result["status"] == "disabled"


def test_ai_analyzer_generate_research_summary_ok(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", return_value="13 hypotheses, 1 passed."):
        result = analyzer.generate_research_summary(
            {"total": 13, "passed": 1, "failed": 3, "research": 9, "avg_wr": 60.5, "avg_pf": 2.72}
        )
    assert result["status"] == "ok"
    assert "13 hypotheses" in result["text"]


def test_ai_analyzer_answer_research_question_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    result = analyzer.answer_research_question("{}", "Why did PF drop?")
    assert result["status"] == "disabled"


def test_ai_analyzer_answer_research_question_rejects_empty_question(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    result = analyzer.answer_research_question("{}", "   ")
    assert result["status"] == "error"
    assert "empty" in result["error"].lower()


def test_ai_analyzer_answer_research_question_ok(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", return_value="PF dropped because of a losing streak in H1."):
        result = analyzer.answer_research_question(
            '{"pf": 1.1, "trades": 120}', "Why did PF drop?"
        )
    assert result["status"] == "ok"
    assert "PF dropped" in result["text"]


def test_ai_analyzer_generate_daily_report_still_works_after_refactor(monkeypatch):
    # Regression guard: generate_daily_report and generate_research_summary
    # now share _summarize_text() — make sure the refactor didn't change
    # generate_daily_report's own behavior.
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", return_value="Quiet day, 3 EXECUTE signals."):
        result = analyzer.generate_daily_report({"total": 10, "execute": 3, "no_trade": 7})
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# _resolve_provider_name (config/ai.yaml governance restructure, 2026-07-12)
# ---------------------------------------------------------------------------

def test_resolve_provider_name_uses_legacy_provider_key_when_no_providers_dict():
    assert _resolve_provider_name({"provider": "anthropic"}) == "anthropic"


def test_resolve_provider_name_defaults_to_gemini_when_nothing_set():
    assert _resolve_provider_name({}) == "gemini"


def test_resolve_provider_name_picks_first_enabled_in_fallback_order():
    ai_cfg = {
        "providers": {
            "gemini": {"enabled": False},
            "openai": {"enabled": True},
            "anthropic": {"enabled": True},
        },
        "fallback_order": ["gemini", "openai", "anthropic"],
    }
    assert _resolve_provider_name(ai_cfg) == "openai"


def test_resolve_provider_name_respects_fallback_order_not_dict_order():
    ai_cfg = {
        "providers": {
            "gemini": {"enabled": True},
            "anthropic": {"enabled": True},
        },
        "fallback_order": ["anthropic", "gemini"],
    }
    assert _resolve_provider_name(ai_cfg) == "anthropic"


def test_resolve_provider_name_falls_back_to_legacy_key_when_none_enabled():
    ai_cfg = {
        "provider": "openai",
        "providers": {"gemini": {"enabled": False}},
        "fallback_order": ["gemini"],
    }
    assert _resolve_provider_name(ai_cfg) == "openai"


def test_ai_analyzer_reads_new_providers_structure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    config = {
        "ai": {
            "enabled": True,
            "providers": {"gemini": {"enabled": False}, "anthropic": {"enabled": True}},
            "fallback_order": ["gemini", "anthropic"],
            "model": "claude-sonnet-4-6",
            "cache": {"news_ttl_min": 20, "macro_ttl_min": 60},
        }
    }
    analyzer = AIAnalyzer(config)
    assert analyzer.provider_name == "anthropic"
    assert analyzer.available is True


# ---------------------------------------------------------------------------
# suggest_next_hypothesis (AI Copilot, Phase 4d)
# ---------------------------------------------------------------------------

def test_ai_analyzer_suggest_next_hypothesis_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    result = analyzer.suggest_next_hypothesis({"registry_summary": []})
    assert result["status"] == "disabled"


def test_ai_analyzer_suggest_next_hypothesis_ok(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    fake_json = (
        '{"title": "Order-flow imbalance on crypto", '
        '"statement": "A cumulative delta divergence predicts short-term reversals.", '
        '"why_this_might_be_true": "Retail order flow on perpetuals is public and lagged.", '
        '"data_required": {"symbols": ["BTCUSD"], "timeframes": ["H4"], "date_range": "2022-2026", "min_sample_size": "300"}, '
        '"falsification_criteria": "PASS if OOS PF >= 1.2 at n >= 300; FAIL otherwise.", '
        '"distinct_from_prior_kill": "Not the crypto_volume experiment (traded volume) — this is order-flow imbalance, a different signal.", '
        '"notes": "n/a", '
        '"observation": "BUY trades outperformed SELL in recent missions.", '
        '"effect_size": "Very large", '
        '"confidence": "Medium", '
        '"possible_explanation": "Current engines may identify bullish continuation better than bearish reversals.", '
        '"suggested_experiments": ["BUY ONLY", "Disable SELL", "BUY during London only"], '
        '"priority": "HIGH"}'
    )
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.suggest_next_hypothesis({"registry_summary": []}, focus_hint="crypto order flow")
    assert result["status"] == "ok"
    assert result["title"] == "Order-flow imbalance on crypto"
    assert result["data_required"]["symbols"] == ["BTCUSD"]
    assert "crypto_volume" in result["distinct_from_prior_kill"]
    assert result["effect_size"] == "Very large"
    assert result["confidence"] == "Medium"
    assert result["suggested_experiments"] == ["BUY ONLY", "Disable SELL", "BUY during London only"]
    assert result["priority"] == "HIGH"


def test_ai_analyzer_suggest_next_hypothesis_rejects_missing_required_fields(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    # Missing distinct_from_prior_kill — a malformed suggestion must never
    # reach status="ok" (that's what gates the frontend's Save button).
    fake_json = '{"title": "t", "statement": "s", "falsification_criteria": "f"}'
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.suggest_next_hypothesis({})
    assert result["status"] == "error"
    assert "required fields" in result["error"].lower()


_FULL_VALID_SUGGESTION_JSON = (
    '{"title": "t", "statement": "s", "why_this_might_be_true": "w", '
    '"data_required": {}, "falsification_criteria": "f", "distinct_from_prior_kill": "d", "notes": "", '
    '"observation": "o", "effect_size": "Medium", "confidence": "Medium", "possible_explanation": "p", '
    '"suggested_experiments": ["BUY ONLY"], "priority": "MEDIUM"}'
)


def test_ai_analyzer_suggest_next_hypothesis_rejects_missing_hypothesis_candidate_fields(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    # Has the original 4 required fields but is missing effect_size.
    fake_json = (
        '{"title": "t", "statement": "s", "falsification_criteria": "f", "distinct_from_prior_kill": "d", '
        '"observation": "o", "confidence": "Medium", "possible_explanation": "p", '
        '"suggested_experiments": ["BUY ONLY"], "priority": "MEDIUM"}'
    )
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.suggest_next_hypothesis({})
    assert result["status"] == "error"


def test_ai_analyzer_suggest_next_hypothesis_rejects_bad_priority_enum(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    fake_json = _FULL_VALID_SUGGESTION_JSON.replace('"priority": "MEDIUM"', '"priority": "URGENT"')
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.suggest_next_hypothesis({})
    assert result["status"] == "error"


def test_ai_analyzer_suggest_next_hypothesis_rejects_empty_suggested_experiments_list(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    fake_json = _FULL_VALID_SUGGESTION_JSON.replace('"suggested_experiments": ["BUY ONLY"]', '"suggested_experiments": []')
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.suggest_next_hypothesis({})
    assert result["status"] == "error"


def test_ai_analyzer_suggest_next_hypothesis_handles_provider_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", return_value="not json at all, sorry"):
        result = analyzer.suggest_next_hypothesis({})
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# propose_matrix_research_plan (Hypothesis Discovery Engine, Phase 3B)
# ---------------------------------------------------------------------------

_FULL_VALID_PLAN_JSON = (
    '{"reasoning_summary": "XAUUSD has no NNFX-bundle cells tested yet.", '
    '"coverage_gaps": ["XAUUSD x NNFX trend x balanced untested"], '
    '"proposed_next_cells": [{"symbol": "XAUUSD", "bundle_name": "NNFX trend", '
    '"timeframes": ["H4"], "engines": ["nnfx"], "risk_preset": "balanced", '
    '"rationale": "coverage gap, not adjacent to any dead-list idea"}], '
    '"distinct_from_dead_list": "not a liquidity sweep or SMC idea", '
    '"priority": "MEDIUM"}'
)


def test_ai_analyzer_propose_matrix_research_plan_disabled():
    analyzer = AIAnalyzer(_config(enabled=False))
    result = analyzer.propose_matrix_research_plan({"families": []})
    assert result["status"] == "disabled"


def test_ai_analyzer_propose_matrix_research_plan_ok(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", return_value=_FULL_VALID_PLAN_JSON):
        result = analyzer.propose_matrix_research_plan({"families": []}, focus_hint="metals")
    assert result["status"] == "ok"
    assert result["reasoning_summary"] == "XAUUSD has no NNFX-bundle cells tested yet."
    assert result["proposed_next_cells"][0]["symbol"] == "XAUUSD"
    assert result["priority"] == "MEDIUM"
    assert result["coverage_gaps"] == ["XAUUSD x NNFX trend x balanced untested"]


def test_ai_analyzer_propose_matrix_research_plan_rejects_missing_required_fields(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    fake_json = '{"reasoning_summary": "r"}'  # missing distinct_from_dead_list, proposed_next_cells, priority
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.propose_matrix_research_plan({})
    assert result["status"] == "error"
    assert "required fields" in result["error"].lower()


def test_ai_analyzer_propose_matrix_research_plan_rejects_empty_proposed_cells(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    fake_json = _FULL_VALID_PLAN_JSON.replace(
        '"proposed_next_cells": [{"symbol": "XAUUSD", "bundle_name": "NNFX trend", '
        '"timeframes": ["H4"], "engines": ["nnfx"], "risk_preset": "balanced", '
        '"rationale": "coverage gap, not adjacent to any dead-list idea"}]',
        '"proposed_next_cells": []',
    )
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.propose_matrix_research_plan({})
    assert result["status"] == "error"


def test_ai_analyzer_propose_matrix_research_plan_rejects_a_cell_missing_a_required_field(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    fake_json = _FULL_VALID_PLAN_JSON.replace('"risk_preset": "balanced", ', "")  # drop risk_preset from the cell
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.propose_matrix_research_plan({})
    assert result["status"] == "error"


def test_ai_analyzer_propose_matrix_research_plan_rejects_bad_priority_enum(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    fake_json = _FULL_VALID_PLAN_JSON.replace('"priority": "MEDIUM"', '"priority": "URGENT"')
    with patch.object(GeminiProvider, "_chat", return_value=fake_json):
        result = analyzer.propose_matrix_research_plan({})
    assert result["status"] == "error"


def test_ai_analyzer_propose_matrix_research_plan_handles_provider_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    with patch.object(GeminiProvider, "_chat", return_value="not json at all"):
        result = analyzer.propose_matrix_research_plan({})
    assert result["status"] == "error"


# --- Phase 3B-H hardening pass 2: no silent context truncation -------------


def test_ai_analyzer_propose_matrix_research_plan_never_truncates_large_context(monkeypatch):
    """P0 regression: a context whose serialization exceeds the OLD 16000-
    char silent-truncation threshold must still reach the provider in
    FULL -- the persisted evidence_snapshot must never be able to
    overstate what the AI actually read."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    big_context = {"families": [{"padding": "x" * 500} for _ in range(50)]}  # well over 16000 chars serialized
    captured_prompt: dict[str, str] = {}

    def _capture_chat(self, prompt: str) -> str:
        captured_prompt["prompt"] = prompt
        return _FULL_VALID_PLAN_JSON

    with patch.object(GeminiProvider, "_chat", _capture_chat):
        result = analyzer.propose_matrix_research_plan(big_context, focus_hint="metals")
    assert result["status"] == "ok"
    full_context_text = __import__("json").dumps(big_context, indent=2, default=str)
    assert len(full_context_text) > 16_000
    assert full_context_text in captured_prompt["prompt"]  # the WHOLE context reached the prompt, not a prefix


# --- Phase 3B-H hardening pass 2: model provenance (resolved_model) --------


def test_ai_analyzer_resolved_model_reflects_the_actual_provider_instance(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    analyzer = AIAnalyzer(_config(enabled=True))
    assert analyzer.resolved_model == "gemini-flash-latest"


def test_ai_analyzer_resolved_model_falls_back_to_the_per_provider_default_when_config_model_unset():
    """The exact gap the audit found: config.get('ai',{}).get('model','')
    would have returned '' here, while the REAL provider instance
    resolved and used the per-provider default."""
    import os
    os.environ["GEMINI_API_KEY"] = "test-key"
    try:
        config = {"ai": {"enabled": True, "provider": "gemini", "model": None, "cache": {"news_ttl_min": 20, "macro_ttl_min": 60}}}
        analyzer = AIAnalyzer(config)
        assert config["ai"].get("model") is None  # what a naive config read would (wrongly) report
        assert analyzer.resolved_model == "gemini-flash-latest"  # what actually executes
    finally:
        os.environ.pop("GEMINI_API_KEY", None)


def test_ai_analyzer_resolved_model_is_none_when_unavailable():
    analyzer = AIAnalyzer(_config(enabled=False))
    assert analyzer.resolved_model is None
