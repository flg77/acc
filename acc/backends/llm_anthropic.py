"""Anthropic Claude LLM backend."""

from __future__ import annotations

import json
import os

import anthropic

from acc.backends import LLMCallError

_RETRYABLE_TYPES = {"overloaded_error", "api_error"}


class AnthropicBackend:
    """Anthropic Claude SDK backend.

    The system prompt is passed separately from the user message as required
    by the Messages API.  Structured JSON output is requested via a text
    block with explicit JSON instruction when *response_schema* is provided.

    Embeddings use a local ``sentence-transformers`` fallback
    (``all-MiniLM-L6-v2``) since Anthropic does not expose an embedding API.
    """

    def __init__(self, model: str, embedding_model_path: str) -> None:
        self._model = model
        self._embedding_model_path = embedding_model_path
        self._client = anthropic.AsyncAnthropic(api_key=os.environ.get("ACC_ANTHROPIC_API_KEY", ""))
        self._st_model = None  # lazy-loaded

    def _get_st_model(self):
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self._embedding_model_path)
        return self._st_model

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
    ) -> dict:
        """Send a chat completion request to Anthropic.

        When *response_schema* is provided, the user message is augmented with
        an instruction to respond with valid JSON only.
        """
        user_content = user
        if response_schema is not None:
            user_content = (
                f"{user}\n\nRespond with a valid JSON object only. "
                f"Schema: {json.dumps(response_schema)}"
            )

        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
        except anthropic.APIStatusError as exc:
            retryable = exc.status_code in {429, 503}
            raise LLMCallError(
                f"Anthropic API error {exc.status_code}: {exc.message}",
                retryable=retryable,
                status_code=exc.status_code,
            ) from exc

        content = message.content[0].text
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}

    async def embed(self, text: str) -> list[float]:
        """Generate embedding using local sentence-transformers model."""
        model = self._get_st_model()
        vector = model.encode(text, normalize_embeddings=True)
        return vector.tolist()
