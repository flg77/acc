"""
ACC Backend Protocols and custom exceptions.

Four structural interfaces (PEP 544) define the contracts that all concrete
backend implementations must satisfy.  No business logic lives here — only
interface definitions and the two exception types used across all backends.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

__all__ = [
    "SignalingBackend",
    "VectorBackend",
    "LLMBackend",
    "MetricsBackend",
    "BackendConnectionError",
    "LLMCallError",
]


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class BackendConnectionError(Exception):
    """Raised when a backend fails to establish its initial connection.

    The original infrastructure exception is always chained via ``raise ... from``.
    """


class LLMCallError(Exception):
    """Raised when an LLM backend returns a non-2xx response.

    Attributes:
        retryable: True for transient errors (429, 503); False for client
                   errors (400, 401, 422).
        status_code: The HTTP status code returned by the upstream service.
    """

    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class ContentNotSupported(LLMCallError):
    """A backend was handed image blocks it cannot send.

    `20260830-attachment-delivery-path` -- a text-only backend **raises**; it
    never ignores the blocks.  Ignoring them would produce a confident answer
    about an image the model never received, and nothing would look wrong.
    Never retryable: a failover chain that walks onto a text-only model must
    stop there, not answer without the image.
    """

    def __init__(self, backend: str, *, model: str = "", declared: bool | None = None) -> None:
        if model:
            why = (
                f"model {model!r} is declared as not taking images"
                if declared is False else
                f"model {model!r} is not declared to take images "
                f"(accepts_images in its models.yaml entry)"
            )
            message = (
                f"the {backend!r} backend would pass the image on, but {why}. "
                f"Declare `accepts_images: true` for a model that takes images, bind "
                f"this role to one, or send the prompt without the attachment -- it "
                f"will not be silently dropped."
            )
        else:
            message = (
                f"the {backend!r} backend cannot accept images. Bind this role to a "
                f"multimodal model, or send the prompt without the attachment -- it "
                f"will not be silently dropped."
            )
        super().__init__(message, retryable=False)
        self.backend = backend
        self.model = model


def refuse_content(backend: str, content: list[dict] | None) -> None:
    """A text-only backend's whole answer to *content*: refuse, never drop."""
    if content:
        raise ContentNotSupported(backend)


# ---------------------------------------------------------------------------
# Signaling
# ---------------------------------------------------------------------------


@runtime_checkable
class SignalingBackend(Protocol):
    """Async publish/subscribe transport for inter-agent signals."""

    async def connect(self) -> None:
        """Establish connection to the messaging backend.

        Raises:
            BackendConnectionError: If the connection cannot be established.
        """
        ...

    async def close(self) -> None:
        """Gracefully close the connection and release resources."""
        ...

    async def publish(self, subject: str, payload: bytes) -> None:
        """Publish *payload* bytes to *subject*."""
        ...

    async def subscribe(self, subject: str, handler: Callable[[bytes], Any]) -> None:
        """Register *handler* to be called for each message on *subject*."""
        ...


# ---------------------------------------------------------------------------
# Vector DB
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorBackend(Protocol):
    """Synchronous vector database interface for episodic and pattern memory."""

    def create_table_if_absent(self, table: str, schema: Any) -> None:
        """Ensure *table* exists with *schema*; create it if absent."""
        ...

    def insert(self, table: str, records: list[dict]) -> int:
        """Insert *records* into *table*.  Returns the number of rows inserted."""
        ...

    def search(self, table: str, embedding: list[float], top_k: int) -> list[dict]:
        """Return up to *top_k* rows from *table* ordered by cosine similarity descending."""
        ...


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMBackend(Protocol):
    """Async language-model interface for reasoning and embedding."""

    async def complete(
        self,
        system: str,
        user: str,
        response_schema: dict | None = None,
        cache_prefix: bool = False,
        *,
        content: list[dict] | None = None,
    ) -> dict:
        """Request a chat completion.

        Args:
            system: System prompt.
            user: User turn content.
            response_schema: Optional JSON Schema dict.  When provided, the
                backend SHOULD request structured JSON output.
            cache_prefix: PR-CA2 — hint that the (stable) *system* prompt
                is a cacheable prefix.  Backends with an explicit
                prompt-cache API (Anthropic ``cache_control``) act on it;
                backends whose server caches prefixes automatically
                (vLLM ``--enable-prefix-caching``, Ollama/llama.cpp) and
                those without any cache API simply ignore it — the win
                there comes from sending a stable prefix (PR-CA1), not a
                client hint.
            content: image blocks to send with the user turn, in the shape
                :func:`acc.attachments.content_blocks` builds.  A backend
                that cannot carry them MUST raise
                :class:`ContentNotSupported` (:func:`refuse_content`) and
                must never ignore them.  ``None`` -- every existing call --
                changes nothing.

        Returns:
            Parsed response as a plain dict.
        """
        ...

    async def embed(self, text: str) -> list[float]:
        """Return a dense embedding vector for *text*."""
        ...


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@runtime_checkable
class MetricsBackend(Protocol):
    """Synchronous telemetry emission interface."""

    def emit_span(self, name: str, attributes: dict[str, str | float | int]) -> None:
        """Record a trace span with the given *name* and *attributes*."""
        ...

    def emit_metric(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Emit a numeric metric observation."""
        ...
