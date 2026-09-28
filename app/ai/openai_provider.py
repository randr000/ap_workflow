"""OpenAI-compatible provider (any /chat/completions endpoint that supports JSON mode).

Prompts and validation are shared with the Ollama provider (see json_chat.py).
"""

from __future__ import annotations

import json

import httpx

from app.ai.json_chat import SYSTEM_PROMPT, JSONChatProvider
from app.ai.provider import AIProviderError


class OpenAICompatibleProvider(JSONChatProvider):
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.name = f"OpenAI-compatible ({model})"
        self._model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout, connect=10.0), transport=transport,
            headers={"Authorization": f"Bearer {api_key}"})

    def _chat_json(self, instruction: str, payload: str, schema: dict) -> dict:
        try:
            resp = self._client.post("/chat/completions", json={
                "model": self._model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"{instruction}\n\n---\n{payload}"},
                ],
            })
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise AIProviderError(f"LLM request failed: {e}") from e
