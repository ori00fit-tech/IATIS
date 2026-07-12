"""
ai/providers/gemini.py
-----------------------------
Google Gemini's generateContent API:
https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent

Default model: gemini-flash-latest.
"""
from __future__ import annotations

import requests

from ai.providers.base import AIProvider, AIProviderError
from utils.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider(AIProvider):
    name = "gemini"

    def _chat(self, prompt: str) -> str:
        try:
            response = requests.post(
                API_URL.format(model=self.model),
                headers={
                    "X-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": self.temperature,
                        "maxOutputTokens": self.max_tokens,
                    },
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
            if not text:
                raise AIProviderError("Gemini response had no text content")
            return text
        except requests.RequestException as exc:
            raise AIProviderError(f"Gemini request failed: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise AIProviderError(f"Unexpected Gemini response shape: {exc}") from exc
