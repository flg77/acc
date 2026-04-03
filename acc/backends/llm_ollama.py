"""Ollama REST LLM backend."""

from __future__ import annotations

import json

import httpx

from acc.backends import LLMCallError

_RETRYABLE = {429, 503}
_NON_RETRYABLE = {400, 401, 422}


class OllamaBackend:
    """Ollama REST API backend (OpenAI-compatible).

    Sends requests to ``{base_url}/api/chat`` and ``{base_url}/api/embeddings``.
    """

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 200 or response.status_code >= 300:
            retryable = response.status_code in _RETRYABLE
            raise LLMCallError(
                f"Ollama returned HTTP {response.status_code}: {response.text}",
                retryable=retryable,
                status_code=response.status_code,
            )

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
    ) -> dict:
        """POST to ``/api/chat``.

        When *response_schema* is provided, ``format: "json"`` is added to the
        request body so Ollama constrains output to JSON.
        """
        body: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if response_schema is not None:
            body["format"] = "json"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json=body,
                timeout=120.0,
            )
        self._raise_for_status(response)
        data = response.json()
        content = data["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}

    async def embed(self, text: str) -> list[float]:
        """POST to ``/api/embeddings`` and return the embedding vector."""
        body = {"model": self._model, "prompt": text}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/api/embeddings",
                json=body,
                timeout=60.0,
            )
        self._raise_for_status(response)
        return response.json()["embedding"]
