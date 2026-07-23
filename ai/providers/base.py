"""
ai/providers/base.py
----------------------
Common interface every AI provider (Gemini, OpenAI, Anthropic, ...)
implements, plus the shared plumbing (prompt loading, JSON extraction)
so each concrete provider only has to know how to call its own HTTP API.

AIAnalyzer (ai/ai_analyzer.py) talks to providers only through this
interface — swapping `ai.provider` in config.yaml never requires
touching the orchestrator or the rest of IATIS.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class AIProviderError(Exception):
    """Raised for a failed/unparseable provider call. Always caught by
    AIAnalyzer — this exists so provider code can fail loudly to its
    direct caller (useful in tests) without the orchestrator crashing."""


def load_prompt(name: str, **kwargs: Any) -> str:
    """Load ai/prompts/{name}.txt and fill in {placeholders}.

    Raises AIProviderError if a template is missing a value the caller
    didn't supply — better to fail the call than send a half-filled
    prompt to a paid API.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    try:
        template = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AIProviderError(f"Prompt template not found: {path}") from exc
    try:
        return template.format(**kwargs)
    except KeyError as exc:
        raise AIProviderError(f"Prompt '{name}' missing placeholder value: {exc}") from exc


def _iter_json_object_candidates(text: str):
    """Yield every balanced top-level ``{...}`` substring in ``text``, in
    order of appearance, one per ``{`` found outside a string literal.

    Brace-scans with string/escape awareness so braces inside JSON string
    values don't end an object early. Previously only the FIRST ``{`` in
    the text was tried — which fails whenever a response contains an
    earlier, unrelated brace pair ahead of the real JSON object (e.g. "The
    deal is ${100} give or take. Here: {"sentiment": "NEUTRAL"}" — `${100}`
    balances but never parses, so the real object right after it was never
    reached). extract_json tries each yielded candidate in turn until one
    actually parses (audit docs/FULL_INSTITUTIONAL_AUDIT_2026-07-23.md P2-6).
    """
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        start = i
        depth = 0
        in_string = False
        escaped = False
        j = start
        closed_at = None
        while j < n:
            ch = text[j]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = in_string
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        closed_at = j
                        break
            j += 1
        if closed_at is not None:
            yield text[start:closed_at + 1]
        # Continue scanning right after this start (not past its close) so
        # a `{` nested inside a failed candidate is still tried on its own.
        i = start + 1


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model response.

    Models routinely violate "return only JSON": they wrap it in ```json
    fences, prepend prose ("Here is the analysis:"), or append a closing
    remark. Each of those used to fail the whole call and surface as
    BAD_FORMAT on the dashboard — so after the fast path, fall back to
    extracting the first balanced {...} from anywhere in the response.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    for candidate in _iter_json_object_candidates(cleaned):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                logger.debug("extract_json: recovered embedded JSON object from prose-wrapped response")
                return parsed
        except json.JSONDecodeError:
            continue

    raise AIProviderError(
        f"Provider returned non-JSON response (first 120 chars: {text.strip()[:120]!r})"
    )


class AIProvider(ABC):
    """Common contract for all AI providers.

    Every method returns a plain dict matching the shapes in
    ai/models.py (the orchestrator wraps these into the dataclasses).
    Implementations should raise AIProviderError on failure — never
    return a fabricated/default result silently.
    """

    name: str = "base"

    def __init__(self, api_key: str, model: str, temperature: float = 0.1,
                 max_tokens: int = 1200, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    @abstractmethod
    def _chat(self, prompt: str) -> str:
        """Send `prompt` as a single user message, return the raw text
        response. The only method each provider truly needs to implement
        differently — everything else composes this."""
        raise NotImplementedError

    def explain_trade(self, context: dict) -> dict:
        prompt = load_prompt("explain_trade", **context)
        return extract_json(self._chat(prompt))

    def analyze_news(self, news_items: str, symbols: str) -> dict:
        prompt = load_prompt("news_analysis", news_items=news_items, symbols=symbols)
        return extract_json(self._chat(prompt))

    def macro_analysis(self, context: dict) -> dict:
        prompt = load_prompt("macro_analysis", **context)
        return extract_json(self._chat(prompt))

    def summarize(self, text: str) -> str:
        prompt = load_prompt("summarize", text=text)
        return self._chat(prompt).strip()
