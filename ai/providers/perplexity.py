"""
ai/providers/perplexity.py
-----------------------------
Perplexity's chat completions API is OpenAI-compatible:
https://api.perplexity.ai/chat/completions

Default model: sonar-pro (websearch-grounded, useful for news/macro
analysis where the model benefits from live retrieval rather than
training-data recall alone).
"""
from __future__ import annotations

import requests

from ai.providers.base import AIProvider, AIProviderError
from utils.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://api.perplexity.ai/chat/completions"


class PerplexityProvider(AIProvider):
    name = "perplexity"

    def _chat(self, prompt: str) -> str:
        try:
            response = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
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
            return data["choices"][0]["message"]["content"]
        except requests.RequestException as exc:
            raise AIProviderError(f"Perplexity request failed: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected Perplexity response shape: {exc}") from exc
