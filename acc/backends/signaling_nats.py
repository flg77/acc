"""NATS JetStream signaling backend."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import msgpack
import nats
from nats.aio.client import Client as NATSClient

from acc.backends import BackendConnectionError


class NATSBackend:
    """NATS JetStream async publish/subscribe transport.

    Payloads are serialized as MessagePack bytes on publish and deserialized
    on receipt before the handler is invoked.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._nc: NATSClient | None = None

    async def connect(self) -> None:
        """Connect to the NATS server at the configured URL.

        Raises:
            BackendConnectionError: If the connection cannot be established.
        """
        try:
            self._nc = await nats.connect(self._url)
        except Exception as exc:
            raise BackendConnectionError(
                f"Failed to connect to NATS at {self._url}: {exc}"
            ) from exc

    async def close(self) -> None:
        """Drain and close the NATS connection."""
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None

    async def publish(self, subject: str, payload: bytes) -> None:
        """Serialize *payload* as MessagePack and publish to *subject*.

        Args:
            subject: NATS subject string (e.g. ``acc.sol-01.signal``).
            payload: Raw bytes; callers are responsible for any prior encoding.
                     The bytes are re-packed as MessagePack on the wire.
        """
        if self._nc is None:
            raise RuntimeError("NATSBackend.connect() must be called before publish()")
        packed = msgpack.packb(payload, use_bin_type=True)
        await self._nc.publish(subject, packed)

    async def subscribe(self, subject: str, handler: Callable[[bytes], Any]) -> None:
        """Subscribe to *subject*; deserializes MessagePack before calling *handler*.

        Args:
            subject: NATS subject (wildcards supported).
            handler: Coroutine or callable receiving the deserialized bytes payload.
        """
        if self._nc is None:
            raise RuntimeError("NATSBackend.connect() must be called before subscribe()")

        async def _dispatch(msg: Any) -> None:
            data = msgpack.unpackb(msg.data, raw=False)
            result = handler(data)
            if asyncio.iscoroutine(result):
                await result

        await self._nc.subscribe(subject, cb=_dispatch)
