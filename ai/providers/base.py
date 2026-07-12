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


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model response.

    Models occasionally wrap JSON in ```json fences despite instructions
    not to — strip those before parsing rather than failing the call.
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
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"Provider returned non-JSON response: {exc}") from exc


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
