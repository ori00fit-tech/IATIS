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

            # A safety block / empty result carries promptFeedback and no
            # candidates — surface the block reason instead of a bare
            # KeyError("candidates") masquerading as a shape problem.
            candidates = data.get("candidates") or []
            if not candidates:
                reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
                raise AIProviderError(f"Gemini returned no candidates ({reason})")

            candidate = candidates[0]
            # Thinking models emit "thought" parts before the answer;
            # concatenating them used to corrupt the JSON payload.
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(
                part.get("text", "") for part in parts if not part.get("thought")
            )
            if not text:
                finish = candidate.get("finishReason", "?")
                raise AIProviderError(
                    f"Gemini response had no text content (finishReason={finish})"
                )
            if candidate.get("finishReason") == "MAX_TOKENS":
                # Truncated JSON parses as garbage downstream — name the
                # real cause (raise ai.max_tokens) instead of BAD_FORMAT.
                logger.warning(
                    "Gemini response hit maxOutputTokens — output may be truncated"
                )
            return text
        except requests.RequestException as exc:
            raise AIProviderError(f"Gemini request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AIProviderError(f"Unexpected Gemini response shape: {exc}") from exc
