"""``acc-cli oversight …`` — drive the human oversight queue from the CLI.

* ``pending`` — list pending items (read directly from Redis when configured,
  otherwise tail the arbiter's HEARTBEAT).
* ``submit``  — publish a synthetic ``OVERSIGHT_SUBMIT`` request.  Useful
  for demos: the arbiter receives the request and enqueues an item so
  the TUI Compliance screen has something to render and approve.
* ``approve`` / ``reject`` — publish ``OVERSIGHT_DECISION`` directly.
  This is the same path the TUI buttons take, exposed here for ops.

The submit path uses a new lightweight signal type ``OVERSIGHT_SUBMIT``
(subject ``acc.{cid}.oversight.submit``) which the arbiter handles by
calling its own :class:`acc.oversight.HumanOversightQueue.submit`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

from acc.cli._common import (
    connect_nats,
    decode_payload,
    default_collective,
    encode_payload,
)


def register(sub: argparse._SubParsersAction) -> None:
    ov = sub.add_parser("oversight", help="Inspect and drive the oversight queue.")
    ov_sub = ov.add_subparsers(dest="oversight_command", required=True, metavar="ACTION")

    pend = ov_sub.add_parser("pending", help="List pending oversight items.")
    pend.add_argument("--collective", "-c", default=None)
    pend.add_argument(
        "--watch",
        action="store_true",
        help="Tail the arbiter HEARTBEAT and re-print on each update.",
    )
    pend.set_defaults(func=_cmd_pending)

    sub_p = ov_sub.add_parser("submit", help="Submit a synthetic oversight item.")
    sub_p.add_argument("--collective", "-c", default=None)
    sub_p.add_argument("--task-id", required=True)
    sub_p.add_argument("--agent-id", required=True)
    sub_p.add_argument("--risk", default="HIGH", choices=("HIGH", "UNACCEPTABLE"))
    sub_p.add_argument("summary", nargs="+",
                       help="Human-readable summary (positional, may include spaces).")
    sub_p.set_defaults(func=_cmd_submit)

    appr = ov_sub.add_parser("approve", help="Publish OVERSIGHT_DECISION APPROVE.")
    appr.add_argument("oversight_id")
    appr.add_argument("--collective", "-c", default=None)
    appr.add_argument("--approver-id", default="cli:operator")
    appr.set_defaults(func=lambda a: _cmd_decide(a, "APPROVE"))

    rej = ov_sub.add_parser("reject", help="Publish OVERSIGHT_DECISION REJECT.")
    rej.add_argument("oversight_id")
    rej.add_argument("--collective", "-c", default=None)
    rej.add_argument("--approver-id", default="cli:operator")
    rej.add_argument("--reason", default="")
    rej.set_defaults(func=lambda a: _cmd_decide(a, "REJECT"))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _cmd_pending(args: argparse.Namespace) -> int:
    cid = args.collective or default_collective()
    nc = await connect_nats()
    seen_once = asyncio.Event()

    def _print(items: list[dict[str, Any]]) -> None:
        if not items:
            print("(no pending items)")
            return
        print(f"{'ID':<18} {'AGENT':<20} {'RISK':<14} {'SUBMITTED':<10} STATUS")
        for it in items:
            oid = str(it.get("oversight_id", ""))[:18]
            agent = str(it.get("agent_id", ""))[:20]
            risk = str(it.get("risk_level", ""))[:14]
            ms = int(it.get("submitted_at_ms") or 0)
            ts_str = time.strftime("%H:%M:%S", time.localtime(ms / 1000.0)) if ms else "—"
            status = str(it.get("status", "PENDING"))
            print(f"{oid:<18} {agent:<20} {risk:<14} {ts_str:<10} {status}")

    async def _on_heartbeat(msg: Any) -> None:
        decoded = decode_payload(msg.data)
        if not isinstance(decoded, dict):
            return
        if decoded.get("role") != "arbiter":
            return
        items = decoded.get("oversight_pending_items") or []
        _print(items if isinstance(items, list) else [])
        seen_once.set()

    try:
        await nc.subscribe(f"acc.{cid}.heartbeat", cb=_on_heartbeat)
        if args.watch:
            print(f"watching arbiter heartbeats on acc.{cid}.heartbeat (Ctrl-C to stop)…",
                  file=sys.stderr)
            await asyncio.Event().wait()
        else:
            try:
                # Wait up to one heartbeat interval (default 30 s) for an
                # arbiter heartbeat to arrive.
                await asyncio.wait_for(seen_once.wait(), timeout=35.0)
            except asyncio.TimeoutError:
                print("no arbiter heartbeat seen within 35s", file=sys.stderr)
                return 1
    finally:
        await nc.drain()
    return 0


async def _cmd_submit(args: argparse.Namespace) -> int:
    cid = args.collective or default_collective()
    summary = " ".join(args.summary)
    payload = {
        "signal_type": "OVERSIGHT_SUBMIT",
        "task_id": args.task_id,
        "agent_id": args.agent_id,
        "risk_level": args.risk.upper(),
        "summary": summary,
        "ts": time.time(),
        "collective_id": cid,
    }
    subject = f"acc.{cid}.oversight.submit"

    nc = await connect_nats()
    try:
        await nc.publish(subject, encode_payload(payload))
        await nc.flush(timeout=2.0)
    finally:
        await nc.drain()

    print(f"submitted to {subject}: task={args.task_id} agent={args.agent_id} risk={args.risk}")
    print("note: the arbiter must subscribe to acc.{cid}.oversight.submit to enqueue")
    return 0


async def _cmd_decide(args: argparse.Namespace, decision: str) -> int:
    cid = args.collective or default_collective()
    from acc.signals import subject_oversight_decision  # noqa: PLC0415
    payload: dict[str, Any] = {
        "signal_type": "OVERSIGHT_DECISION",
        "oversight_id": args.oversight_id,
        "decision": decision,
        "approver_id": args.approver_id,
        "reason": getattr(args, "reason", ""),
        "ts": time.time(),
        "collective_id": cid,
    }

    nc = await connect_nats()
    try:
        await nc.publish(
            subject_oversight_decision(cid, args.oversight_id),
            encode_payload(payload),
        )
        await nc.flush(timeout=2.0)
    finally:
        await nc.drain()

    print(f"published OVERSIGHT_DECISION {decision} for {args.oversight_id}")
    return 0
