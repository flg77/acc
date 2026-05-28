"""A2A outbound client + cross-collective transport resolver.

OpenSpec: ``openspec/changes/20260527-a2a-agent-interop/`` (Phases 3 + 4).
Docs: ``docs/a2a-interop.md``.

The *outbound* side of A2A.  Three helpers — two pure decision/utility
functions and one async network call:

- :func:`select_transport` — pure decision matrix from the bridge-deprecation
  policy (``rhoai`` + reachable peer → ``"a2a"``; else → ``"nats"``).
- :func:`call_peer` — async JSON-RPC ``message/send`` over HTTPS; raises
  :class:`A2AClientError` on any failure with ``.is_governance_blocked`` so
  the caller knows whether a NATS fallback would be valid.
- :func:`try_a2a_delegation` (Phase 4) — composition of the above into the
  hub-as-gateway helper :meth:`acc.agent.Agent._maybe_delegate_via_a2a` calls
  inside ``_delegate_task``.  Returns a bridge-result-shaped dict on success
  or governance denial, ``None`` on transport failure (caller falls back to
  NATS bridge — the resilience path edge/standalone rely on).

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


# --------------------------------------------------------------------------
# Hub-as-gateway helper (Phase 4)
# --------------------------------------------------------------------------


async def try_a2a_delegation(
    *,
    target_cid: str,
    content: str,
    task_id: str,
    deploy_mode: str,
    peer_urls: dict[str, str] | None,
    timeout: float = 30.0,
    prefer_a2a: bool = True,
) -> dict[str, Any] | None:
    """Try to delegate ``content`` to ``target_cid`` via A2A; return a
    bridge-result-shaped dict or ``None`` if the caller should fall back to
    the NATS bridge.

    Composition of :func:`select_transport` + :func:`call_peer` that
    encapsulates the **hub-as-gateway** behaviour:

    - Transport not A2A (mode-aware resolver said NATS, or no peer URL):
      return ``None`` immediately — caller uses the NATS bridge.
    - A2A call succeeds: return ``{output, blocked: False, block_reason: "",
      episode_id: "", latency_ms}`` — caller skips the NATS path and forwards
      this dict as the bridge result.
    - Peer denied via governance (``A2AClientError.is_governance_blocked``):
      return ``{output: "", blocked: True, block_reason: <peer reason>}``.
      **Do not** fall back to NATS — a denial is a denial.
    - Any other A2A transport failure (HTTP error, timeout, connection
      refused): return ``None``.  Caller falls back to NATS bridge
      (reachability fallback — the bridge is also the resilience path).

    Keeping this as a pure-ish helper (no agent state, no signaling) makes
    the policy easy to unit-test; the agent's ``_delegate_task`` becomes a
    single ``if`` against the return value.
    """
    import time  # noqa: PLC0415

    transport = select_transport(
        deploy_mode=deploy_mode, target_cid=target_cid,
        peer_urls=peer_urls, prefer_a2a=prefer_a2a,
    )
    if transport != "a2a":
        return None

    peer_url = (peer_urls or {}).get(target_cid, "")
    assert peer_url, "select_transport returned 'a2a' but peer URL is empty"

    t0 = time.monotonic()
    try:
        result = await call_peer(peer_url, content, task_id=task_id, timeout=timeout)
    except A2AClientError as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if exc.is_governance_blocked:
            block_reason = "peer governance denial"
            if isinstance(exc.data, dict):
                block_reason = exc.data.get("blockReason") or block_reason
            logger.info(
                "a2a: delegation denied by peer governance "
                "(target=%s task_id=%s reason=%r)",
                target_cid, task_id, block_reason,
            )
            return {
                "output": "",
                "blocked": True,
                "block_reason": f"a2a_peer_denied: {block_reason}",
                "episode_id": "",
                "latency_ms": elapsed_ms,
            }
        # Transport failure → tell the caller to fall back to NATS.
        logger.warning(
            "a2a: delegation transport failure (target=%s task_id=%s err=%s); "
            "caller will fall back to NATS bridge",
            target_cid, task_id, exc,
        )
        return None

    elapsed_ms = (time.monotonic() - t0) * 1000.0
    logger.info(
        "a2a: delegation succeeded (target=%s task_id=%s latency_ms=%.1f)",
        target_cid, task_id, elapsed_ms,
    )
    return {
        "output": result.get("output", "") or "",
        "blocked": False,
        "block_reason": "",
        "episode_id": "",
        "latency_ms": elapsed_ms,
    }
