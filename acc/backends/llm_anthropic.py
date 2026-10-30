"""Anthropic Claude LLM backend."""

from __future__ import annotations

import json
import logging
import os

import anthropic

from acc.backends import LLMCallError

logger = logging.getLogger(__name__)
_RETRYABLE_TYPES = {"overloaded_error", "api_error"}



#: Output-token ceiling per call.  Was a hard-coded 4096, which cut long
#: answers (audit reports, generated code) short without a word.
DEFAULT_MAX_OUTPUT_TOKENS = 8192


def _max_output_tokens() -> int:
    try:
        n = int(os.environ.get("ACC_LLM_MAX_OUTPUT_TOKENS", "") or DEFAULT_MAX_OUTPUT_TOKENS)
    except ValueError:
        n = DEFAULT_MAX_OUTPUT_TOKENS
    return max(256, n)

def _vertex_client(project_id: str, region: str) -> "anthropic.AsyncAnthropicVertex":
    """Claude through Google Vertex AI (CS-07).

    Authenticates with application default credentials -- the file named by
    ``GOOGLE_APPLICATION_CREDENTIALS``, or gcloud's own -- which the SDK reads
    through ``google-auth`` (the ``vertex`` extra).  ACC never opens the file.
    An empty project or region defers to the SDK's ``ANTHROPIC_VERTEX_PROJECT_ID``
    and ``CLOUD_ML_REGION``.  No API key is sent: a host that also holds
    ``ANTHROPIC_API_KEY`` for another role cannot have this call billed to it.
    """
    try:
        import google.auth  # noqa: F401, PLC0415 -- what AnthropicVertex needs at the first call
    except ImportError as exc:
        raise ImportError(
            "anthropic backend with provider 'vertex' needs google-auth: "
            "pip install 'acc[vertex]' (or the 'vertex' feature in the image)",
            name="google.auth",
        ) from exc
    given = {k: v for k, v in (("project_id", project_id), ("region", region)) if v}
    return anthropic.AsyncAnthropicVertex(**given)


class AnthropicBackend:
    """Anthropic Claude SDK backend.

    The system prompt is passed separately from the user message as required
    by the Messages API.  Structured JSON output is requested via a text
    block with explicit JSON instruction when *response_schema* is provided.

    Embeddings use a local ``sentence-transformers`` fallback
    (``all-MiniLM-L6-v2``) since Anthropic does not expose an embedding API.
    """

    def __init__(
        self, model: str, embedding_model_path: str, *, accepts_images: bool | None = None,
        provider: str = "anthropic", vertex_project_id: str = "", vertex_region: str = "",
    ) -> None:
        self._model = model
        # F2b -- every current Claude model takes images, so undeclared sends
        # them; a model declared `accepts_images: false` is refused.
        self._accepts_images = accepts_images
        self._embedding_model_path = embedding_model_path
        self._provider = provider or "anthropic"
        if self._provider == "vertex":
            self._client = _vertex_client(vertex_project_id, vertex_region)
        elif self._provider == "anthropic":
            # Accept the ACC-namespaced key OR the standard Anthropic SDK env name.
            # Operators (and the SDK itself) default to ANTHROPIC_API_KEY; honouring
            # it means "I set ANTHROPIC_API_KEY" actually authenticates instead of
            # silently sending an empty key (2026-06-22 debug report).
            api_key = (os.environ.get("ACC_ANTHROPIC_API_KEY")
                       or os.environ.get("ANTHROPIC_API_KEY") or "")
            if not api_key:
                logger.warning(
                    "anthropic backend: no API key in ACC_ANTHROPIC_API_KEY or "
                    "ANTHROPIC_API_KEY — requests will 401")
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            raise ValueError(f"anthropic backend: unknown provider {provider!r} (anthropic | vertex)")
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
        *,
        content: list[dict] | None = None,
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

        *content* -- image blocks from :func:`acc.attachments.content_blocks`,
        already in the Messages API shape -- follow the text in the user turn.
        """
        if content and self._accepts_images is False:
            from acc.backends import ContentNotSupported  # noqa: PLC0415

            raise ContentNotSupported("anthropic", model=self._model, declared=False)
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
                max_tokens=_max_output_tokens(),
                system=system_arg,
                messages=[{
                    "role": "user",
                    "content": (
                        [{"type": "text", "text": user_content}, *content]
                        if content else user_content
                    ),
                }],
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
        # A reply cut off at the output limit used to pass as a complete one
        # (2026-10-05: a specialist's audit report ended mid-section and the
        # reviewing assistant filled the gap with findings of its own).  Say so.
        stop_reason = str(getattr(message, "stop_reason", "") or "")
        result["stop_reason"] = stop_reason
        if stop_reason == "max_tokens":
            result["truncated"] = True
            logger.warning(
                "anthropic backend: reply hit max_tokens=%d (model=%s); raise "
                "ACC_LLM_MAX_OUTPUT_TOKENS for longer answers",
                _max_output_tokens(), self._model,
            )
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
