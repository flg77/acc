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

    **num_ctx is the one endpoint knob ACC owns.** Ollama does not serve a
    model at the model's own context length; it serves at ``num_ctx``, a
    per-request option defaulting to roughly 4096. Until this parameter
    existed ACC never sent it, so every Ollama deployment ran a ~4k effective
    window no matter what ``models.yaml`` advertised -- and a prompt past it
    was trimmed **by the server, silently**, with no event on either side.

    That is an evidence-integrity defect before it is an efficiency one.
    ``20260825-conversational-turn-continuity`` established *model-visible
    means logged*; server-side truncation breaks the converse, because
    ``acc/prompt_record.py`` hashes an assembled prompt the model only
    partially received. Worse, the bytes at risk are the operator's request:
    ``_compose_user_content`` puts the task last, which is right for attention
    and for the PR-CA1 prefix cache and exactly wrong under a head/tail trim.

    ``num_ctx=0`` means **undeclared** and sends no ``options`` block at all,
    leaving the request byte-identical to the pre-change one. That default is
    deliberate and conservative: raising the served window raises the KV cache
    allocated per request, and a box sized for the accidental 4k is where an
    OOM would land. The figure should be rolled out per deployment as a
    measured one, never as a maximal one.

    Change: ``openspec/changes/20260826-context-budget`` Phase 1.6.
    """

    def __init__(self, base_url: str, model: str, num_ctx: int = 0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_ctx = int(num_ctx or 0)

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
        cache_prefix: bool = False,  # PR-CA2: ignored — Ollama auto-caches prefixes
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
        if self._num_ctx > 0:
            body["options"] = {"num_ctx": self._num_ctx}

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
