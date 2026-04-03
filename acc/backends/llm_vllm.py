"""vLLM / KServe InferenceService LLM backend (RHOAI)."""

from __future__ import annotations

import json

import httpx

from acc.backends import LLMCallError

_RETRYABLE = {429, 503}


class VLLMBackend:
    """vLLM backend using the OpenAI-compatible ``/v1/chat/completions`` endpoint.

    Targets a KServe InferenceService running vLLM on RHOAI.
    """

    def __init__(self, inference_url: str, model: str) -> None:
        self._base_url = inference_url.rstrip("/")
        self._model = model

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 200 or response.status_code >= 300:
            retryable = response.status_code in _RETRYABLE
            raise LLMCallError(
                f"vLLM returned HTTP {response.status_code}: {response.text}",
                retryable=retryable,
                status_code=response.status_code,
            )

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
    ) -> dict:
        """POST to ``/v1/chat/completions`` (OpenAI-compatible format)."""
        body: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema is not None:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=body,
                timeout=120.0,
            )
        self._raise_for_status(response)
        content = response.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}

    async def embed(self, text: str) -> list[float]:
        """POST to ``/v1/embeddings`` (OpenAI-compatible format)."""
        body = {"model": self._model, "input": text}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self._base_url}/v1/embeddings",
                json=body,
                timeout=60.0,
            )
        self._raise_for_status(response)
        return response.json()["data"][0]["embedding"]
