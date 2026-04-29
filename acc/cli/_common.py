"""Shared helpers for ``acc-cli`` subcommands.

* ``nats_url()`` / ``default_collective()`` / ``roles_root()`` — env-var resolution.
* ``connect_nats()`` — one-line async connection.
* ``encode_payload()`` / ``decode_payload()`` — wire format that mirrors
  :class:`acc.backends.signaling_nats.NATSBackend` (``msgpack(json_bytes)``).

All CLI commands go through these helpers so the wire format stays in
lockstep with the agent / TUI without re-implementing the codec.
"""

from __future__ import annotations

import json
import os
from typing import Any

import msgpack

_DEFAULT_NATS_URL = "nats://localhost:4222"
_DEFAULT_COLLECTIVE = "sol-01"
_DEFAULT_ROLES_ROOT = "roles"


def nats_url() -> str:
    """Resolve the NATS endpoint URL from ``ACC_NATS_URL`` or the default."""
    return os.environ.get("ACC_NATS_URL", _DEFAULT_NATS_URL)


def default_collective() -> str:
    """Resolve the default collective from ``ACC_COLLECTIVE_ID`` or fallback."""
    return os.environ.get("ACC_COLLECTIVE_ID", _DEFAULT_COLLECTIVE)


def roles_root() -> str:
    """Resolve the roles/ directory from ``ACC_ROLES_ROOT`` or fallback."""
    return os.environ.get("ACC_ROLES_ROOT", _DEFAULT_ROLES_ROOT)


async def connect_nats() -> Any:
    """Connect to NATS using ``nats-py`` and return the live client.

    Raises:
        ConnectionError: On any connect failure (re-wraps nats exceptions).
    """
    import nats  # local import keeps `acc-cli --help` fast
    try:
        return await nats.connect(nats_url())
    except Exception as exc:  # pragma: no cover — pass-through error path
        raise ConnectionError(
            f"NATS connect failed for {nats_url()!r}: {exc}"
        ) from exc


def encode_payload(payload: dict[str, Any]) -> bytes:
    """Encode a Python dict into the canonical ACC wire format.

    Wire format = ``msgpack.packb(json.dumps(payload).encode())`` — exact
    match for :meth:`acc.backends.signaling_nats.NATSBackend.publish`.
    Going through JSON guarantees the receiving end can decode with the
    existing two-step (msgpack → json.loads) used by every agent and the
    TUI.
    """
    json_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
    return msgpack.packb(json_bytes, use_bin_type=True)


def decode_payload(raw: bytes) -> Any:
    """Decode a raw NATS message body into a Python value.

    Tries the canonical ``msgpack(json)`` envelope first.  Falls back to
    raw JSON, then raw bytes — useful for one-off inspection of legacy
    publishers that did not go through ``NATSBackend``.
    """
    # Step 1: msgpack outer
    try:
        outer = msgpack.unpackb(raw, raw=False)
    except Exception:
        outer = raw

    # Step 2: inner JSON if outer is bytes/str
    if isinstance(outer, (bytes, bytearray)):
        try:
            return json.loads(outer.decode("utf-8"))
        except Exception:
            return outer.decode("utf-8", errors="replace")
    if isinstance(outer, str):
        try:
            return json.loads(outer)
        except Exception:
            return outer
    return outer
