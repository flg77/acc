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
    ) -> dict:
        """Request a chat completion.

        Args:
            system: System prompt.
            user: User turn content.
            response_schema: Optional JSON Schema dict.  When provided, the
                backend SHOULD request structured JSON output.

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
