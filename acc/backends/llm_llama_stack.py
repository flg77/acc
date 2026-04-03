"""Llama Stack inference API backend (RHOAI)."""

from __future__ import annotations

import json

import httpx

from acc.backends import LLMCallError

_RETRYABLE = {429, 503}


class LlamaStackBackend:
    """Llama Stack backend.

    POSTs to ``{base_url}/inference/chat-completion``.
    Embeddings use the local sentence-transformers fallback since Llama Stack
    does not expose a general embedding endpoint.
    """

    def __init__(self, base_url: str, embedding_model_path: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._embedding_model_path = embedding_model_path
        self._st_model = None  # lazy-loaded

    def _get_st_model(self):
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self._embedding_model_path)
        return self._st_model

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 200 or response.status_code >= 300:
            retryable = response.status_code in _RETRYABLE
            raise LLMCallError(
                f"Llama Stack returned HTTP {response.status_code}: {response.text}",
                retryable=retryable,
                status_code=response.status_code,
            )

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
    ) -> dict:
        """POST to ``/inference/chat-completion``."""
        body: dict = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if response_schema is not None:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/inference/chat-completion",
                json=body,
                timeout=120.0,
            )
        self._raise_for_status(response)
        data = response.json()
        content = data.get("completion_message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}

    async def embed(self, text: str) -> list[float]:
        """Generate embedding using local sentence-transformers model."""
        model = self._get_st_model()
        vector = model.encode(text, normalize_embeddings=True)
        return vector.tolist()
