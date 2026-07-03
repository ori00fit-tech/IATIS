"""
ai/providers/anthropic.py
----------------------------
Anthropic Messages API: https://api.anthropic.com/v1/messages

Note: this is a separate, correct implementation from the ad-hoc call in
ai/dynamic_weights.py (which sends no x-api-key/anthropic-version headers
and would 401) — that module is a distinct, pre-existing feature and out
of scope here; this provider is the one AIAnalyzer actually uses.
"""
from __future__ import annotations

import requests

from ai.providers.base import AIProvider, AIProviderError
from utils.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def _chat(self, prompt: str) -> str:
        try:
            response = requests.post(
                API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            text = "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
            if not text:
                raise AIProviderError("Anthropic response had no text content block")
            return text
        except requests.RequestException as exc:
            raise AIProviderError(f"Anthropic request failed: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected Anthropic response shape: {exc}") from exc
