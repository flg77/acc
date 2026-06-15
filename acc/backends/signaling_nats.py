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

    def __init__(self, url: str, nkey_seed_path: str | None = None) -> None:
        """Args:
            url: NATS server URL.
            nkey_seed_path: Optional path to an NKey *seed* file
                (proposal 013).  When set, the connection authenticates
                with that NKey identity.  ``None`` (the default)
                preserves the legacy credential-less connection — so
                deployments with ``security.nkey.enabled: false`` are
                byte-for-byte unchanged on the wire.
        """
        self._url = url
        self._nkey_seed_path = nkey_seed_path or None
        self._nc: NATSClient | None = None

    async def connect(self) -> None:
        """Connect to the NATS server at the configured URL.

        When an NKey seed path was supplied, the connection is
        authenticated with that identity; otherwise it connects
        without credentials (legacy behaviour).

        Raises:
            BackendConnectionError: If the connection cannot be
                established — including a missing seed file when NKey
                auth was requested (fail closed, never silently
                anonymous).
        """
        import os  # noqa: PLC0415

        opts: dict[str, Any] = {}
        if self._nkey_seed_path is not None:
            if not os.path.isfile(self._nkey_seed_path):
                raise BackendConnectionError(
                    f"NKey auth requested but seed file not found at "
                    f"{self._nkey_seed_path!r} — generate it with "
                    f"`scripts/acc-nkeys generate` (standalone) or check "
                    f"the operator-projected Secret (rhoai/edge)"
                )
            opts["nkeys_seed"] = self._nkey_seed_path
        # Resilient initial connect (proposal 031 §11 #1).  nats.py's initial
        # connect attempts the servers once and raises NoServersError if NATS is
        # momentarily unready — common when the agent (re)starts before its NATS
        # pod's headless-Service endpoints exist.  Without a retry the agent
        # hard-crashes -> CrashLoopBackOff -> races NATS again on restart.
        # (allow_reconnect only covers drops AFTER a successful connect, not the
        # first one.)  Bound an explicit backoff loop here; env-tunable, with
        # defaults giving ~60s of patience for NATS to come up.
        attempts = max(1, int(os.environ.get("ACC_NATS_CONNECT_ATTEMPTS", "30")))
        wait_s = float(os.environ.get("ACC_NATS_CONNECT_WAIT_S", "2"))
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._nc = await nats.connect(self._url, **opts)
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < attempts:
                    await asyncio.sleep(wait_s)
        raise BackendConnectionError(
            f"Failed to connect to NATS at {self._url} after {attempts} "
            f"attempt(s): {last_exc}"
        ) from last_exc

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
