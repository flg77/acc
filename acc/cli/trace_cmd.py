"""``acc-cli trace <task_id>`` — render a timeline of every signal mentioning a task.

The CLI subscribes to the wildcard ``acc.{cid}.>`` for a short window and
prints every message whose JSON body contains the requested ``task_id``.
JetStream replay would be ideal but is currently optional in the deploy
stack; live tail is sufficient for demos and incident triage.

For deeper history, the CLI also supports ``--from-jetstream`` which
attaches an ephemeral consumer with ``DeliverPolicy.ALL`` so prior
messages still in the stream window are replayed first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from acc.cli._common import connect_nats, decode_payload, default_collective


def register(sub: argparse._SubParsersAction) -> None:
    trace = sub.add_parser(
        "trace",
        help="Tail NATS for messages mentioning a specific task_id.",
    )
    trace.add_argument("task_id", help="Task identifier to match in payloads.")
    trace.add_argument(
        "--collective", "-c",
        default=None,
        help="Collective to scope the wildcard (default: $ACC_COLLECTIVE_ID).",
    )
    trace.add_argument(
        "--limit", "-n",
        type=int,
        default=0,
        help="Stop after N matching messages (0 = run until Ctrl-C).",
    )
    trace.add_argument(
        "--from-jetstream",
        action="store_true",
        help="Replay prior messages from JetStream before tailing live.",
    )
    trace.set_defaults(func=_cmd_trace)


async def _cmd_trace(args: argparse.Namespace) -> int:
    cid = args.collective or default_collective()
    pattern = f"acc.{cid}.>"
    needle = args.task_id

    nc = await connect_nats()
    matches = 0
    done = asyncio.Event()
    limit = max(0, args.limit)

    def _maybe_match(payload: Any) -> bool:
        # Cheap textual check first to skip un-related messages without
        # walking the dict structure.  False positives are fine — they get
        # filtered by the structural check below.
        try:
            blob = json.dumps(payload, default=str) if isinstance(payload, (dict, list)) else str(payload)
        except Exception:
            return False
        if needle not in blob:
            return False
        # Confirm via structured access where possible; fall back to the
        # textual hit so we still surface unusual payload shapes.
        if isinstance(payload, dict):
            for key in ("task_id", "id", "task", "oversight_id"):
                if str(payload.get(key, "")) == needle:
                    return True
        return True

    async def _handler(msg: Any) -> None:
        nonlocal matches
        decoded = decode_payload(msg.data)
        if not _maybe_match(decoded):
            return
        matches += 1
        ts = time.strftime("%H:%M:%S")
        signal_type = (
            decoded.get("signal_type", "?") if isinstance(decoded, dict) else "?"
        )
        agent_id = (
            decoded.get("agent_id", "") if isinstance(decoded, dict) else ""
        )
        print(f"[{ts}] {msg.subject}  signal={signal_type}  agent={agent_id}")
        try:
            pretty = json.dumps(decoded, indent=2, default=str)
        except Exception:
            pretty = repr(decoded)
        for line in pretty.splitlines():
            print(f"  {line}")
        sys.stdout.flush()
        if limit and matches >= limit:
            done.set()

    print(
        f"tracing task_id={needle!r} on {pattern!r}; "
        f"{'limit='+str(limit) if limit else 'Ctrl-C to stop'}",
        file=sys.stderr,
    )
    try:
        await nc.subscribe(pattern, cb=_handler)

        if args.from_jetstream:
            # Best-effort JetStream replay: attach an ephemeral pull
            # consumer if a stream covers the wildcard.  Failure is
            # non-fatal — the live subscription above still works.
            try:
                js = nc.jetstream()
                psub = await js.subscribe(
                    pattern,
                    ordered_consumer=True,
                    deliver_policy="all",
                )
                # Drain available messages then close — the live cb above
                # will pick up everything from "now" onward.
                deadline = time.time() + 2.0
                while time.time() < deadline:
                    try:
                        msg = await asyncio.wait_for(psub.next_msg(), timeout=0.5)
                    except asyncio.TimeoutError:
                        break
                    await _handler(msg)
                await psub.unsubscribe()
            except Exception as exc:
                print(f"jetstream replay unavailable: {exc}", file=sys.stderr)

        if limit:
            await done.wait()
        else:
            await asyncio.Event().wait()
    finally:
        await nc.drain()
    return 0
