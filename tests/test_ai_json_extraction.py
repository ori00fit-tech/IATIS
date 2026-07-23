"""
tests/test_ai_json_extraction.py — extract_json robustness.

The dashboard's AI briefing surfaced BAD_FORMAT whenever a provider
wrapped its JSON in prose or fences (models routinely ignore "return only
JSON"). extract_json now recovers the first balanced {...} from anywhere
in the response; these tests pin that behavior and the failure mode.
"""
from __future__ import annotations

import pytest

from ai.providers.base import AIProviderError, extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_wrapped_json():
    text = 'Here is the analysis you asked for:\n\n{"sentiment": "NEUTRAL", "impact": "LOW"}\n\nLet me know if you need more.'
    assert extract_json(text) == {"sentiment": "NEUTRAL", "impact": "LOW"}


def test_prose_wrapped_fenced_json():
    text = 'Sure! ```json\n{"a": {"nested": true}}\n``` hope that helps'
    assert extract_json(text) == {"a": {"nested": True}}


def test_braces_inside_strings_do_not_break_scan():
    text = 'prefix {"summary": "risk {elevated} today", "n": 2} suffix'
    assert extract_json(text) == {"summary": "risk {elevated} today", "n": 2}


def test_escaped_quotes_inside_strings():
    text = 'x {"s": "he said \\"go\\" {now}"} y'
    assert extract_json(text) == {"s": 'he said "go" {now}'}


def test_no_json_raises():
    with pytest.raises(AIProviderError):
        extract_json("The market looks neutral today.")


def test_truncated_json_raises():
    with pytest.raises(AIProviderError):
        extract_json('{"a": 1, "b": ')


def test_earlier_unrelated_brace_pair_does_not_shadow_the_real_object():
    """Regression for docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-6: the
    old implementation grabbed the FIRST `{` in the text, so a response
    like "The deal is ${100} give or take. Here: {...real json...}" would
    try to parse "{100}" (balanced, but not valid JSON — no key), fail,
    and raise — never reaching the real object right after it. This is
    the exact BAD_FORMAT failure the module exists to prevent."""
    text = 'The deal is ${100} give or take. Here: {"sentiment": "NEUTRAL"}'
    assert extract_json(text) == {"sentiment": "NEUTRAL"}


def test_multiple_false_starts_before_the_real_object():
    text = 'Notes: {not json}, also {"broken": , still not it. Actually: {"ok": true}'
    assert extract_json(text) == {"ok": True}
