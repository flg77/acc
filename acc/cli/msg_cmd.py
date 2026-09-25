"""``acc-cli msg`` — say something to one running agent.

    acc-cli msg send <agent_id> --text "…" [--steer | --follow-up | --auto] [--task T]
    acc-cli msg tail <agent_id> [--limit N]
    acc-cli msg show <message_id>

OpenSpec ``20260923-lessons-that-travel`` Phase 6 (PA-02).  ``send`` publishes
one :class:`acc.agent_messages.AgentMessage` on the agent's inbox subject
with the operator's attribution stamped (the follow-up task runs as you, at
your ceiling).  ``tail`` reads the receipts the agent wrote to Redis:
``delivered`` (rendered into the task in flight), ``follow_up`` (became a
task, which id), or ``dropped`` (why).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from acc.cli._common import connect_nats, default_collective, encode_payload
from acc.signals import (
    redis_agent_messages_key,
    redis_message_key,
    subject_agent_inbox,
)

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_UNSUPPORTED = 2


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("msg", help="Direct messages to one agent (steer or follow-up).")
    sp = p.add_subparsers(dest="msg_command", required=True, metavar="ACTION")

    se = sp.add_parser("send", help="Send one message to one agent.")
    se.add_argument("agent_id")
    se.add_argument("--text", required=True)
    mode = se.add_mutually_exclusive_group()
    mode.add_argument("--steer", action="store_const", dest="delivery", const="steer",
                      help="Into the task in flight (a follow-up when nothing is).")
    mode.add_argument("--follow-up", action="store_const", dest="delivery", const="follow_up",
                      help="A new task for the agent, after its current one.")
    mode.add_argument("--auto", action="store_const", dest="delivery", const="auto",
                      help="Steer if busy, follow-up if idle (default).")
    se.add_argument("--task", default="", help="The task this is about, if known.")
    se.add_argument("--collective", default="")
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=_cmd_send, delivery="auto")

    ta = sp.add_parser("tail", help="Receipts for the messages an agent received.")
    ta.add_argument("agent_id")
    ta.add_argument("--limit", type=int, default=20)
    ta.add_argument("--collective", default="")
    ta.add_argument("--json", action="store_true")
    ta.set_defaults(func=_cmd_tail)

    sh = sp.add_parser("show", help="One message and its receipt.")
    sh.add_argument("message_id")
    sh.add_argument("--collective", default="")
    sh.set_defaults(func=_cmd_show)


def _redis():
    try:
        from acc.agent import _build_redis_client  # noqa: PLC0415
        from acc.config import load_config  # noqa: PLC0415
        return _build_redis_client(load_config())
    except Exception:  # noqa: BLE001
        return None


def _decode(v) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, (bytes, bytearray)) else str(v)


def _cid(args: argparse.Namespace) -> str:
    return args.collective or default_collective()


def _attribution() -> dict:
    try:
        from acc.identity import current  # noqa: PLC0415
        p = current()
        return {
            "requested_by": p.attribution(), "requester_subject": p.subject,
            "requester_source": "cli", "requester_tier": p.tier,
            "requester_ceiling": p.effective_ceiling, "requester_channel": "cli",
            "requester_scope": "direct",
        }
    except Exception:  # noqa: BLE001
        return {}


def _cmd_send(args: argparse.Namespace) -> int:
    from acc.agent_messages import AgentMessage  # noqa: PLC0415

    cid = _cid(args)
    msg = AgentMessage(
        collective_id=cid, from_agent="acc-cli", to_agent=args.agent_id,
        delivery=args.delivery, body=args.text.strip(), task_id=args.task,
        attribution=_attribution(),
    )
    body = msg.model_dump()

    # The "sent" record goes in BEFORE the publish: the receiver writes its
    # receipt within milliseconds of delivery, and a record written after the
    # publish overwrote it (seen on the lighthouse smoke, 2026-09-23 -- every
    # receipt read back as "sent").  Written first, the receipt wins.
    redis_client = _redis()
    if redis_client is not None:
        try:
            redis_client.set(redis_message_key(cid, msg.message_id),
                             json.dumps({**body, "status": "sent"}))
            redis_client.expire(redis_message_key(cid, msg.message_id), 7 * 24 * 3600)
        except Exception:  # noqa: BLE001
            pass

    async def _go() -> None:
        nc = await connect_nats()
        try:
            await nc.publish(subject_agent_inbox(cid, args.agent_id), encode_payload(body))
            await nc.flush(timeout=2.0)
        finally:
            await nc.close()

    try:
        asyncio.run(_go())
    except ConnectionError as exc:
        print(f"msg: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    if args.json:
        print(json.dumps(body, indent=2))
    else:
        print(f"msg: sent {msg.message_id[:8]} → {args.agent_id} ({args.delivery}); "
              f"receipt: acc-cli msg show {msg.message_id[:8]}")
    return EXIT_OK


def _load(redis_client, cid: str, message_id: str) -> dict | None:
    raw = redis_client.get(redis_message_key(cid, message_id))
    if not raw:
        return None
    try:
        return json.loads(_decode(raw))
    except json.JSONDecodeError:
        return None


def _cmd_tail(args: argparse.Namespace) -> int:
    redis_client = _redis()
    if redis_client is None:
        print("msg: no Redis backend configured", file=sys.stderr)
        return EXIT_UNSUPPORTED
    cid = _cid(args)
    try:
        ids = [_decode(i) for i in redis_client.lrange(redis_agent_messages_key(cid, args.agent_id), 0, max(0, args.limit - 1))]
    except Exception as exc:  # noqa: BLE001
        print(f"msg: read failed: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    msgs = [m for m in (_load(redis_client, cid, i) for i in ids) if m]
    if args.json:
        print(json.dumps({"agent": args.agent_id, "messages": msgs}, indent=2))
        return EXIT_OK
    if not msgs:
        print(f"No messages received by {args.agent_id} (last 7 days).")
        return EXIT_OK
    print(f"Messages to {args.agent_id} (newest first, {len(msgs)}):")
    for m in msgs:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(m.get("ts", 0) or 0)))
        print(f"  {str(m.get('message_id', ''))[:8]}  {ts}  {m.get('status', '?'):<10} "
              f"{m.get('delivery', '')}  from {m.get('from_agent') or 'operator'}: "
              f"{str(m.get('body', '')).strip()[:90]}"
              + (f"  → task {m['task_ref']}" if m.get("task_ref") else ""))
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    redis_client = _redis()
    if redis_client is None:
        print("msg: no Redis backend configured", file=sys.stderr)
        return EXIT_UNSUPPORTED
    cid = _cid(args)
    m = _load(redis_client, cid, args.message_id)
    if m is None:
        # prefix search over the receipt lists is not indexed; try the key scan
        try:
            keys = [_decode(k) for k in redis_client.keys(redis_message_key(cid, args.message_id + "*"))]
            if len(keys) == 1:
                m = _load(redis_client, cid, keys[0].rsplit(":", 1)[-1])
        except Exception:  # noqa: BLE001
            m = None
    if m is None:
        print(f"msg: no message matches {args.message_id!r}", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(json.dumps(m, indent=2))
    return EXIT_OK
