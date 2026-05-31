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

    # Local embedder — see `embed()`.  Defaults to ``all-MiniLM-L6-v2``
    # (384-dim) to match ``llm_openai_compat`` byte-for-byte.
    _DEFAULT_EMBED_MODEL = "all-MiniLM-L6-v2"

    def __init__(
        self,
        inference_url: str,
        model: str,
        *,
        embedding_model_path: str | None = None,
    ) -> None:
        base = inference_url.rstrip("/")
        # Operators routinely paste a `/v1`-suffixed URL into the
        # config (it's how vLLM serves the OpenAI-compat API, and
        # the openai_compat backend explicitly takes that shape).
        # Strip a trailing /v1 so we always append /v1/chat/completions
        # cleanly and never produce `/v1/v1/chat/completions` → 404.
        if base.endswith("/v1"):
            base = base[:-3]
        self._base_url = base
        self._model = model
        self._embedding_model_path = (
            embedding_model_path or self._DEFAULT_EMBED_MODEL
        )
        self._embedder = None  # lazy — see embed()

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
        cache_prefix: bool = False,  # PR-CA2: ignored — vLLM --enable-prefix-caching
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
        body_json = response.json()
        content = body_json["choices"][0]["message"]["content"]
        usage = body_json.get("usage", {}) or {}
        # Canonical shape — matches ``acc.backends.llm_openai_compat``:
        # always include ``content`` (the raw LLM text) AND ``usage``
        # so ``cognitive_core._call_llm`` can read token counts.
        # When the LLM emits valid JSON the parsed object is folded
        # in under the same key set so callers that DO want structured
        # output can still read it.
        result: dict = {"content": content, "usage": usage}
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                # Preserve structured fields without clobbering content.
                for k, v in parsed.items():
                    result.setdefault(k, v)
                # Legacy callers still read "text" — keep populated.
                result["text"] = content
            else:
                result["text"] = content
        except json.JSONDecodeError:
            result["text"] = content
        return result

    async def embed(self, text: str) -> list[float]:
        """Return a dense embedding via the local sentence-transformers model.

        Embedding is always performed locally — mirrors
        :class:`acc.backends.llm_openai_compat.OpenAICompatBackend.embed`
        byte-for-byte.  Reasons (same as the sibling backend):

        * **Consistent dimensionality (384) across providers** — critical
          for centroid drift scoring.  vLLM deployments routinely host a
          chat-only model with no embeddings endpoint; if we called
          ``/v1/embeddings`` against such a server it would return an
          error JSON without a ``data`` key and crash with
          ``KeyError: 'data'`` — silently breaking PR-MEM2 reflection
          + PR-I retrieval + the dreamer's centroid recompute (see
          followup #44).
        * No extra API costs / rate-limit pressure on the inference
          provider.
        * Works for vLLM servers that haven't loaded an embeddings model.

        Lazy-loads the embedder on first call to avoid pulling
        ``sentence_transformers`` at module import time.
        """
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
            self._embedder = SentenceTransformer(self._embedding_model_path)
        return self._embedder.encode(text).tolist()
