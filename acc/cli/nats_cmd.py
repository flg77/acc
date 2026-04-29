"""``acc-cli nats sub|pub`` — raw NATS introspection and one-shot publish.

The wire format is the canonical ``msgpack(json_bytes)`` envelope used by
every agent and the TUI (see :mod:`acc.cli._common`).  ``sub`` decodes
incoming messages and pretty-prints the inner JSON; ``pub`` encodes a
JSON document the same way.

Subscriptions are foreground — Ctrl-C exits cleanly via the SIGINT
handler in :func:`acc.cli.main`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from acc.cli._common import connect_nats, decode_payload, encode_payload


def register(sub: argparse._SubParsersAction) -> None:
    nats_p = sub.add_parser("nats", help="Subscribe to or publish on NATS subjects.")
    nats_sub = nats_p.add_subparsers(dest="nats_command", required=True, metavar="ACTION")

    # sub
    sub_p = nats_sub.add_parser("sub", help="Stream messages matching <subject>.")
    sub_p.add_argument("subject", help="NATS subject pattern (e.g. 'acc.sol-01.>').")
    sub_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N messages (0 = infinite, default).",
    )
    sub_p.add_argument(
        "--raw",
        action="store_true",
        help="Print the raw msgpack bytes instead of decoded JSON.",
    )
    sub_p.set_defaults(func=_cmd_sub)

    # pub
    pub_p = nats_sub.add_parser("pub", help="Publish one message to <subject>.")
    pub_p.add_argument("subject", help="Target NATS subject.")
    pub_p.add_argument(
        "payload",
        help="JSON document to send.  Use - to read from stdin.",
    )
    pub_p.set_defaults(func=_cmd_pub)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _cmd_sub(args: argparse.Namespace) -> int:
    nc = await connect_nats()
    received = 0
    done = asyncio.Event()
    limit = max(0, args.limit)

    async def _handler(msg: Any) -> None:
        nonlocal received
        received += 1
        ts = time.strftime("%H:%M:%S")
        if args.raw:
            print(f"[{ts}] {msg.subject}\n  bytes={len(msg.data)}")
        else:
            decoded = decode_payload(msg.data)
            try:
                pretty = json.dumps(decoded, indent=2, default=str)
            except Exception:
                pretty = repr(decoded)
            print(f"[{ts}] {msg.subject}")
            for line in pretty.splitlines():
                print(f"  {line}")
        sys.stdout.flush()
        if limit and received >= limit:
            done.set()

    try:
        await nc.subscribe(args.subject, cb=_handler)
        print(
            f"subscribed to {args.subject!r}; "
            + ("waiting for "+str(limit)+" messages" if limit else "Ctrl-C to stop"),
            file=sys.stderr,
        )
        if limit:
            await done.wait()
        else:
            # Block forever until Ctrl-C.  asyncio.Event().wait() is cheaper
            # than a sleep loop and propagates cancellation cleanly.
            await asyncio.Event().wait()
    finally:
        await nc.drain()
    return 0


async def _cmd_pub(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.payload == "-" else args.payload
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"invalid JSON payload: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("payload must be a JSON object (got "
              f"{type(payload).__name__})", file=sys.stderr)
        return 1

    nc = await connect_nats()
    try:
        await nc.publish(args.subject, encode_payload(payload))
        await nc.flush(timeout=2.0)
    finally:
        await nc.drain()

    print(f"published {len(json.dumps(payload))} bytes to {args.subject}")
    return 0
