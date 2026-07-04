"""
tests/test_dynamic_weights.py
--------------------------------
Regression coverage for ai/dynamic_weights.py's Anthropic call.

Previously this module built its own `requests.post` with no
`x-api-key`/`anthropic-version` headers at all — it would 401
unconditionally against the real API. It now reuses
ai/providers/anthropic.py (the same client ai_analyzer.py uses), so
these tests mock at that layer instead of `requests` directly.
"""
from __future__ import annotations

from unittest.mock import patch

from ai.dynamic_weights import MIN_WEIGHT, MAX_WEIGHT, analyze_and_suggest_weights
from ai.providers.anthropic import AnthropicProvider
from ai.providers.base import AIProviderError

CURRENT_WEIGHTS = {"smc": 0.2, "price_action": 0.2, "nnfx": 0.2, "wyckoff": 0.2, "quant": 0.2}

OUTCOME_SUMMARY_ENOUGH_DATA = {"total_closed": 25, "win_rate": 55.0}
OUTCOME_SUMMARY_NOT_ENOUGH = {"total_closed": 5, "win_rate": 50.0}

ENGINE_STATS = [
    {"engine": "smc", "agreement_rate": 60, "avg_score_when_voting": 55, "neutral_pct": 10},
    {"engine": "nnfx", "agreement_rate": 80, "avg_score_when_voting": 60, "neutral_pct": 5},
]


def test_insufficient_data_short_circuits_without_calling_api():
    result = analyze_and_suggest_weights(ENGINE_STATS, OUTCOME_SUMMARY_NOT_ENOUGH, CURRENT_WEIGHTS)
    assert result["status"] == "insufficient_data"
    assert result["requires_more_data"] is True


def test_missing_api_key_returns_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = analyze_and_suggest_weights(ENGINE_STATS, OUTCOME_SUMMARY_ENOUGH_DATA, CURRENT_WEIGHTS)
    assert result["status"] == "not_configured"
    assert result["suggested_weights"] == CURRENT_WEIGHTS


def test_success_path_sends_proper_anthropic_auth_headers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_reply = (
        '{"suggested_weights": {"smc": 0.25, "price_action": 0.15, "nnfx": 0.3, '
        '"wyckoff": 0.15, "quant": 0.15, "macro": 0.0}, '
        '"reasoning": {}, "confidence": "medium", "note": "n", "requires_more_data": false}'
    )
    with patch.object(AnthropicProvider, "_chat", return_value=fake_reply) as mock_chat:
        result = analyze_and_suggest_weights(ENGINE_STATS, OUTCOME_SUMMARY_ENOUGH_DATA, CURRENT_WEIGHTS)

    assert result["status"] == "success"
    assert mock_chat.called
    weights = result["suggested_weights"]
    assert weights["macro"] == 0.0
    for engine, w in weights.items():
        if engine == "macro":
            continue
        assert MIN_WEIGHT <= w <= MAX_WEIGHT
    non_macro_total = sum(v for k, v in weights.items() if k != "macro")
    assert abs(non_macro_total - 1.0) < 0.01


def test_provider_error_is_handled_gracefully(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with patch.object(AnthropicProvider, "_chat", side_effect=AIProviderError("boom")):
        result = analyze_and_suggest_weights(ENGINE_STATS, OUTCOME_SUMMARY_ENOUGH_DATA, CURRENT_WEIGHTS)
    assert result["status"] == "error"
    assert "boom" in result["message"]
    assert result["suggested_weights"] == CURRENT_WEIGHTS
