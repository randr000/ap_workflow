"""Ollama provider — a local or LAN-hosted model via Ollama's native API.

Uses POST /api/chat with `format` set to a JSON schema (Ollama structured
outputs, v0.5+), `stream: false` and temperature 0. Prompts and validation are
shared with the OpenAI-compatible provider (see json_chat.py), so a local model
is held to exactly the same contracts and deterministic controls.

Ollama on another machine: start it there with OLLAMA_HOST=0.0.0.0 so it listens
on the network, then set OLLAMA_BASE_URL=http://<that-machine-ip>:11434 here.
"""

from __future__ import annotations

import json

import httpx

from app.ai.json_chat import SYSTEM_PROMPT, JSONChatProvider
from app.ai.provider import AIProviderError


class OllamaProvider(JSONChatProvider):
    def __init__(self, base_url: str, model: str, timeout: float = 180.0,
                 transport: httpx.BaseTransport | None = None):
        self.name = f"Ollama ({model})"
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Generation can be slow on a local model, but an unreachable host should fail fast.
        self._client = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=10.0),
                                    transport=transport)

    def _chat_json(self, instruction: str, payload: str, schema: dict) -> dict:
        try:
            resp = self._client.post("/api/chat", json={
                "model": self.model,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"{instruction}\n\n---\n{payload}"},
                ],
            })
            if resp.status_code == 404:
                raise AIProviderError(
                    f"Ollama at {self.base_url} does not have model '{self.model}'. "
                    f"Run `ollama pull {self.model}` on that machine.")
            resp.raise_for_status()
            # Only the final answer is read. Any separate "thinking" output from reasoning
            # models is ignored and never stored.
            return json.loads(resp.json()["message"]["content"])
        except httpx.ConnectError as e:
            raise AIProviderError(f"Cannot reach Ollama at {self.base_url}: {e}") from e
        except httpx.TimeoutException as e:
            raise AIProviderError(f"Ollama at {self.base_url} timed out: {e}") from e
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError) as e:
            raise AIProviderError(f"Ollama request failed: {e}") from e

    def list_models(self) -> list[str]:
        """Models installed on the Ollama host (GET /api/tags)."""
        try:
            resp = self._client.get("/api/tags", timeout=5.0)
            resp.raise_for_status()
            return sorted(m["name"] for m in resp.json().get("models", []))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
            raise AIProviderError(f"Cannot reach Ollama at {self.base_url}: {e}") from e

    def check_connection(self) -> tuple[bool, str, list[str]]:
        """(ok, message, installed models) — ok means the host is reachable AND the model is installed."""
        try:
            models = self.list_models()
        except AIProviderError as e:
            return False, str(e), []
        if self.model in models or f"{self.model}:latest" in models:
            return True, f"Connected to {self.base_url} · model {self.model}", models
        return False, (f"Connected to {self.base_url}, but model '{self.model}' is not installed. "
                       f"Run `ollama pull {self.model}` on that machine."), models
