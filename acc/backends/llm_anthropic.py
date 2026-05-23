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
        cache_prefix: bool = False,
    ) -> dict:
        """Send a chat completion request to Anthropic.

        When *response_schema* is provided, the user message is augmented with
        an instruction to respond with valid JSON only.

        PR-CA2 — when *cache_prefix* is set, the (stable per-role) system
        prompt is sent as a single text block marked
        ``cache_control={"type":"ephemeral"}`` so Anthropic caches it and
        subsequent identical-prefix calls read from cache.  Cache token
        counts are surfaced in ``usage``.  Only worthwhile above
        Anthropic's minimum-cacheable-prefix size; below it the API
        silently treats it as a normal request.
        """
        user_content = user
        if response_schema is not None:
            user_content = (
                f"{user}\n\nRespond with a valid JSON object only. "
                f"Schema: {json.dumps(response_schema)}"
            )

        # When caching is hinted, pass the system prompt as a block list so
        # we can attach cache_control; otherwise the plain string form.
        system_arg = system
        if cache_prefix and system:
            system_arg = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]

        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system_arg,
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

        # PR-R — return token usage so the agent's Cat-B token-budget
        # tracking is accurate.  The Anthropic Messages API reports
        # ``usage.input_tokens`` + ``usage.output_tokens`` separately;
        # ``acc.cognitive_core`` reads ``usage.total_tokens`` (line ~1081
        # for token_count, ~1104 for the over-budget deviation check),
        # so we compute the total and expose all three.  Pre-PR-R this
        # backend discarded usage entirely → token_count was always 0
        # and every token_budget config silently read as 0% utilised.
        usage_obj = getattr(message, "usage", None)
        input_tokens = int(getattr(usage_obj, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage_obj, "output_tokens", 0) or 0)
        # PR-CA2 — prompt-cache token accounting.  Anthropic reports
        # cache_creation_input_tokens (written to cache this call) +
        # cache_read_input_tokens (served from cache).  total_tokens
        # keeps counting input+output for the existing Cat-B budget; the
        # cache fields are additive metrics for the Performance pane.
        cache_creation = int(getattr(usage_obj, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(usage_obj, "cache_read_input_tokens", 0) or 0)
        usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        }

        # Canonical shape — matches ``acc.backends.llm_openai_compat`` and
        # the PR-5 vLLM rewrite: always carry ``content`` + ``usage``;
        # fold parsed-JSON keys in when the model emitted valid JSON;
        # keep ``text`` populated for legacy readers.
        result: dict = {"content": content, "usage": usage}
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    result.setdefault(k, v)
                result["text"] = content
            else:
                result["text"] = content
        except json.JSONDecodeError:
            result["text"] = content
        return result

    async def embed(self, text: str) -> list[float]:
        """Generate embedding using local sentence-transformers model."""
        model = self._get_st_model()
        vector = model.encode(text, normalize_embeddings=True)
        return vector.tolist()
