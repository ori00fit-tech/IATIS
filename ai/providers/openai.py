"""
ai/providers/openai.py
-------------------------
OpenAI chat completions API: https://api.openai.com/v1/chat/completions
"""
from __future__ import annotations

import requests

from ai.providers.base import AIProvider, AIProviderError
from utils.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(AIProvider):
    name = "openai"

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
            raise AIProviderError(f"OpenAI request failed: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected OpenAI response shape: {exc}") from exc
