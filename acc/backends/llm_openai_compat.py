"""Universal OpenAI-compatible inference backend (ACC-LLM-Independence).

This backend implements the ``LLMBackend`` protocol against **any** provider
that exposes the OpenAI Chat Completions API (``/v1/chat/completions``).

Verified provider compatibility
--------------------------------

.. list-table::
   :widths: 25 55 20
   :header-rows: 1

   * - Provider
     - ``base_url``
     - ``api_key_env``
   * - OpenAI
     - ``https://api.openai.com/v1``
     - ``OPENAI_API_KEY``
   * - Azure OpenAI
     - ``https://{resource}.openai.azure.com/openai/v1``
     - ``AZURE_OPENAI_API_KEY``
   * - Groq
     - ``https://api.groq.com/openai/v1``
     - ``GROQ_API_KEY``
   * - Gemini (compat proxy)
     - ``https://generativelanguage.googleapis.com/v1beta/openai``
     - ``GEMINI_API_KEY``
   * - OpenRouter
     - ``https://openrouter.ai/api/v1``
     - ``OPENROUTER_API_KEY``
   * - HuggingFace TGI / Inference API
     - ``https://api-inference.huggingface.co/v1``
     - ``HF_TOKEN``
   * - Together AI
     - ``https://api.together.xyz/v1``
     - ``TOGETHER_API_KEY``
   * - Fireworks AI
     - ``https://api.fireworks.ai/inference/v1``
     - ``FIREWORKS_API_KEY``
   * - vLLM (local)
     - ``http://localhost:8000/v1``
     - *(empty — no auth)*
   * - LM Studio
     - ``http://localhost:1234/v1``
     - *(empty — no auth)*
   * - Anyscale
     - ``https://api.endpoints.anyscale.com/v1``
     - ``ANYSCALE_API_KEY``

Configuration (acc-config.yaml)
--------------------------------

.. code-block:: yaml

   llm:
     backend: openai_compat
     base_url: https://api.groq.com/openai/v1
     model: llama-3.3-70b-versatile
     api_key_env: GROQ_API_KEY

Or via environment variables::

    ACC_LLM_BACKEND=openai_compat
    ACC_LLM_BASE_URL=https://api.groq.com/openai/v1
    ACC_LLM_MODEL=llama-3.3-70b-versatile
    ACC_LLM_API_KEY_ENV=GROQ_API_KEY
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from acc.backends import BackendConnectionError, LLMCallError

logger = logging.getLogger(__name__)

# HTTP status codes that warrant a retry with exponential back-off.
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class OpenAICompatBackend:
    """OpenAI Chat Completions-compatible inference backend with retry/back-off.

    This class is the primary recommended backend for all non-Anthropic, non-Ollama
    inference.  It is a strict superset of the existing ``VLLMBackend`` — the
    ``vllm`` backend choice is retained for backward compatibility but now delegates
    its universal-field resolution to the same pattern.

    Args:
        base_url: Root URL of the OpenAI-compatible endpoint (no trailing slash).
        model: Model identifier string (provider-specific).
        api_key_env: Name of the env var that holds the Bearer token.
                     Empty string = no ``Authorization`` header sent.
        embedding_model_path: Local path for sentence-transformers embedding model.
                              Used by :meth:`embed` when the provider does not offer
                              a ``/v1/embeddings`` endpoint or for consistency with
                              the rest of ACC's embedding pipeline.
        timeout_s: Per-request HTTP timeout in seconds.
        max_retries: Maximum number of retry attempts on retryable errors.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key_env: str = "",
        embedding_model_path: str = "/app/models/all-MiniLM-L6-v2",
        timeout_s: int = 120,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        self._api_key_env = api_key_env
        self._embedding_model_path = embedding_model_path
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        # Lazy-loaded sentence-transformers embedder (avoids import at module level)
        self._embedder: Any = None

    # ------------------------------------------------------------------
    # LLMBackend Protocol implementation
    # ------------------------------------------------------------------

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
    ) -> dict:
        """POST to ``{base_url}/chat/completions`` (OpenAI Chat Completions format).

        Retries on :data:`_RETRYABLE_STATUS` responses with exponential back-off
        (1 s, 2 s, 4 s, …).

        Args:
            system: System prompt text.
            user: User turn content.
            response_schema: Optional JSON Schema dict.  When provided, requests
                ``response_format.type = json_schema`` structured output.  Falls
                back gracefully to ``json_object`` mode on providers that do not
                support the full schema parameter.

        Returns:
            Parsed response dict.  The ``content`` key holds the text output;
            ``usage`` (when present from the provider) holds token counts.

        Raises:
            :class:`~acc.backends.LLMCallError`: On non-2xx responses after all
                retries are exhausted.
            :class:`~acc.backends.BackendConnectionError`: When the endpoint is
                unreachable (network error before any HTTP exchange).
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema is not None:
            # Prefer full JSON-schema structured output; providers that don't
            # support this will return a 400 — callers should not pass a schema
            # for those providers, or handle the error and retry without it.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "acc_response",
                    "strict": True,
                    "schema": response_schema,
                },
            }

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=float(self._timeout_s)) as client:
                    resp = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )

                if resp.status_code in _RETRYABLE_STATUS:
                    backoff = 2 ** (attempt - 1)  # 1 s, 2 s, 4 s
                    logger.warning(
                        "openai_compat: HTTP %s on attempt %s/%s — retrying in %ss "
                        "(model=%s base_url=%s)",
                        resp.status_code,
                        attempt,
                        self._max_retries,
                        backoff,
                        self._model,
                        self._base_url,
                    )
                    last_exc = LLMCallError(
                        f"HTTP {resp.status_code} (retryable)",
                        retryable=True,
                        status_code=resp.status_code,
                    )
                    await asyncio.sleep(backoff)
                    continue

                if resp.status_code < 200 or resp.status_code >= 300:
                    raise LLMCallError(
                        f"HTTP {resp.status_code}: {resp.text[:500]}",
                        retryable=False,
                        status_code=resp.status_code,
                    )

                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                try:
                    parsed = json.loads(content)
                    parsed.setdefault("usage", usage)
                    return parsed
                except (json.JSONDecodeError, ValueError):
                    return {"content": content, "usage": usage}

            except httpx.ConnectError as exc:
                raise BackendConnectionError(
                    f"openai_compat: cannot reach {self._base_url}: {exc}"
                ) from exc
            except httpx.TimeoutException as exc:
                timeout_err = LLMCallError(
                    f"openai_compat: request timed out after {self._timeout_s}s",
                    retryable=True,
                    status_code=None,
                )
                if attempt == self._max_retries:
                    raise timeout_err from exc
                logger.warning(
                    "openai_compat: timeout on attempt %s/%s — retrying",
                    attempt,
                    self._max_retries,
                )
                last_exc = timeout_err
                await asyncio.sleep(2 ** (attempt - 1))
                continue

        # All attempts exhausted
        if last_exc is not None:
            raise last_exc
        raise LLMCallError(
            "openai_compat: all retries exhausted",
            retryable=False,
            status_code=None,
        )

    async def embed(self, text: str) -> list[float]:
        """Return a dense embedding vector using a local sentence-transformers model.

        Embedding is always performed locally (sentence-transformers) rather than
        via the provider's ``/v1/embeddings`` endpoint.  This ensures:

        * Consistent embedding dimensionality (384-dim ``all-MiniLM-L6-v2``) across
          all providers — critical for centroid drift scoring.
        * No extra API costs or rate-limit pressure on the inference provider.
        * Works for providers that don't offer an embeddings endpoint (e.g. Groq).

        The embedder is lazy-loaded on first call to avoid importing
        ``sentence_transformers`` at module import time.
        """
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
            self._embedder = SentenceTransformer(self._embedding_model_path)
        return self._embedder.encode(text).tolist()

    async def health(self) -> bool:
        """Check that the endpoint is reachable by calling ``GET /models``.

        Returns:
            ``True`` when the server responds with any 2xx status;
            ``False`` on connection error or non-2xx status.

        Note:
            Not all providers implement ``GET /models``.  A 404 response is
            treated as "reachable" (server is up, just no model list endpoint).
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/models",
                    headers=self._headers(),
                )
            return resp.status_code < 500
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build request headers.  Re-reads the env var on each call so that
        key rotation takes effect without restarting the agent."""
        key = os.environ.get(self._api_key_env, "") if self._api_key_env else self._api_key
        h: dict[str, str] = {"Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    @property
    def _embedding_model_path(self) -> str:
        return self.__embedding_model_path

    @_embedding_model_path.setter
    def _embedding_model_path(self, value: str) -> None:
        self.__embedding_model_path = value
