"""A2A outbound client + cross-collective transport resolver.

Phase 3 of OpenSpec ``20260527-a2a-agent-interop``: the *outbound* side of
A2A.  Two small async helpers, unit-tested in isolation; the hub-as-gateway
wiring that calls them lives in Phase 4 (``transport.py`` glue).

- :func:`call_peer` — issue a JSON-RPC 2.0 ``message/send`` to a peer's A2A
  endpoint and return the result, or raise :class:`A2AClientError` on any
  failure (HTTP error, timeout, JSON-RPC error).
- :func:`select_transport` — pick ``"a2a"`` vs ``"nats"`` for a cross-collective
  delegation given ``deploy_mode`` + configured peer URLs.  This is the
  ``[DELEGATE:cid:reason]`` resolver from the bridge-deprecation analysis:
  ``rhoai`` + reachable peer → A2A; else → NATS bridge (edge / standalone /
  no-peer-URL).  Fallback on A2A failure is the **caller's** responsibility
  (catch :class:`A2AClientError`, retry on NATS), to keep this resolver pure
  and testable.

Plain HTTP today (Phase 1b/2 ships unsigned); Phase 5 layers TLS + SPIRE x5c
verification on top.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

from .jsonrpc import GOVERNANCE_BLOCKED

logger = logging.getLogger(__name__)


class A2AClientError(Exception):
    """Outbound A2A call failed.

    Wraps three failure shapes uniformly so the caller can react simply:

    - HTTP transport failure (connection refused, 5xx, etc.)  — ``code=None``.
    - JSON-RPC error response from the peer — ``code`` carries the JSON-RPC
      error code; ``data`` carries the structured error payload, including the
      governance ``blockReason`` for ``GOVERNANCE_BLOCKED`` (-32001).
    - Timeout — ``code=None``, ``message`` says "timed out".
    """

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    @property
    def is_governance_blocked(self) -> bool:
        """True when the peer denied the call via Cat-A/B / oversight.  Useful
        for the caller to NOT silently retry on NATS — a governance denial on
        one transport is a governance denial, period."""
        return self.code == GOVERNANCE_BLOCKED


async def call_peer(
    base_url: str,
    content: str,
    *,
    task_id: Optional[str] = None,
    timeout: float = 30.0,
    session: Any = None,
) -> dict[str, Any]:
    """Send a JSON-RPC 2.0 ``message/send`` to an A2A peer.

    Parameters
    ----------
    base_url:
        The peer's JSON-RPC endpoint URL (from its agent card's ``url`` field,
        or — Phase 3 — from a config-supplied ``peer_urls`` mapping).
    content:
        The task content (plain text in Phase 1b/2).
    task_id:
        Optional ACC task id to correlate the call with episode storage on
        either side.  When ``None`` a fresh id is synthesised.
    timeout:
        Per-request budget in seconds (default 30s).
    session:
        Optional ``aiohttp.ClientSession`` for connection reuse.  When ``None``
        a one-shot session is created + closed inside this call.

    Returns
    -------
    The JSON-RPC ``result`` object on success — typically a dict with
    ``output``, ``taskId``, ``reasoning``.

    Raises
    ------
    :class:`A2AClientError` on any failure.  Caller decides whether to fall
    back to the NATS bridge (see ``A2A scope — ACC-9 bridge deprecation
    path``) — *except* on :attr:`A2AClientError.is_governance_blocked`, which
    should not be retried on a different transport.
    """
    import aiohttp  # noqa: PLC0415 — extra-gated

    if task_id is None:
        task_id = f"out-{uuid.uuid4().hex[:12]}"
    payload = {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "message/send",
        "params": {"content": content, "taskId": task_id},
    }

    owns_session = session is None
    if owns_session:
        session = aiohttp.ClientSession()
    try:
        try:
            async with session.post(
                base_url, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                try:
                    body = await resp.json()
                except Exception as exc:  # noqa: BLE001 — wrap as A2AClientError
                    text = await resp.text()
                    raise A2AClientError(
                        f"peer returned non-JSON body (status={resp.status}): "
                        f"{text[:200]}"
                    ) from exc
        except aiohttp.ClientError as exc:
            raise A2AClientError(f"HTTP error: {exc}") from exc
        except asyncio.TimeoutError:
            raise A2AClientError(f"peer call timed out after {timeout}s")

        if "error" in body:
            err = body["error"] or {}
            raise A2AClientError(
                f"JSON-RPC error {err.get('code')}: {err.get('message')}",
                code=err.get("code"),
                data=err.get("data"),
            )
        return body.get("result") or {}
    finally:
        if owns_session:
            await session.close()


# --------------------------------------------------------------------------
# Transport resolver
# --------------------------------------------------------------------------


def select_transport(
    *,
    deploy_mode: str,
    target_cid: str,
    peer_urls: dict[str, str] | None = None,
    prefer_a2a: bool = True,
) -> str:
    """Pick ``"a2a"`` or ``"nats"`` for a ``[DELEGATE:cid:reason]`` request.

    Decision matrix — drives the mode-aware routing described in the
    ``A2A scope — ACC-9 bridge deprecation path`` analysis:

    +----------------+-------------------+---------------+----------+
    | deploy_mode    | peer URL known    | prefer_a2a    | result   |
    +================+===================+===============+==========+
    | rhoai          | yes               | True          | a2a      |
    | rhoai          | no                | True          | nats     |
    | rhoai          | (any)             | False         | nats     |
    | edge           | (any)             | (any)         | nats     |
    | standalone     | (any)             | (any)         | nats     |
    +----------------+-------------------+---------------+----------+

    The caller catches :class:`A2AClientError` from a chosen A2A call and may
    *itself* retry via the NATS bridge (reachability fallback) — except when
    :attr:`A2AClientError.is_governance_blocked`, which is a denial, not a
    transport failure.  This function stays pure: no I/O, no probing.
    """
    if not prefer_a2a:
        return "nats"
    if deploy_mode != "rhoai":
        return "nats"
    if peer_urls and peer_urls.get(target_cid):
        return "a2a"
    return "nats"
