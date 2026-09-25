"""``acc-cli lessons`` — what the collective's agents have told each other.

    acc-cli lessons list [--limit N] [--json]
    acc-cli lessons show <id>
    acc-cli lessons trace <id>
    acc-cli lessons send --to <agent_id> --text "…" [--role R] [--scope S] [--ceiling C]

OpenSpec ``20260923-lessons-that-travel`` Phase 1.  A lesson is a reflected
memory note on the wire (``acc/lessons.py``); ``list`` / ``show`` read the
7-day Redis copy the publisher kept, ``trace`` joins a lesson to the task ids
whose prompt rendered it, and ``send`` publishes one operator-authored lesson
addressed to a single agent -- the smoke test for the relay, not a memory
write (nothing the receiver stores survives its next prompt).

Ids may be prefixes, as with the oversight queue.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from acc.cli._common import connect_nats, default_collective, encode_payload
from acc.signals import (
    redis_lesson_key,
    redis_lesson_used_key,
    redis_lessons_index_key,
    subject_knowledge_share,
)

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_UNSUPPORTED = 2


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("lessons", help="Lessons agents shared with each other (KNOWLEDGE_SHARE).")
    sp = p.add_subparsers(dest="lessons_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Most recent lessons published in the collective.")
    ls.add_argument("--collective", default="", help="Collective id (default: ACC_COLLECTIVE_ID).")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    sh = sp.add_parser("show", help="One lesson, in full.")
    sh.add_argument("lesson_id")
    sh.add_argument("--collective", default="")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=_cmd_show)

    tr = sp.add_parser("trace", help="A lesson and every task whose prompt rendered it.")
    tr.add_argument("lesson_id")
    tr.add_argument("--collective", default="")
    tr.add_argument("--json", action="store_true")
    tr.set_defaults(func=_cmd_trace)

    se = sp.add_parser("send", help="Publish one lesson addressed to a single agent (relay smoke test).")
    se.add_argument("--to", required=True, help="Target agent id.")
    se.add_argument("--text", required=True, help="The lesson, one or two sentences.")
    se.add_argument("--collective", default="")
    se.add_argument("--role", default="operator", help="role_label the receiver sees (default: operator).")
    se.add_argument("--scope", default="local", help="Memory scope the lesson belongs to (default: local).")
    se.add_argument("--ceiling", default="LOW",
                    help="Reader ceiling needed to see it (LOW < MEDIUM < HIGH < CRITICAL; default LOW).")
    se.add_argument("--tag", default="", help="domain_tag (default: universal).")
    se.add_argument("--kind", default="note", choices=["note", "role_patch"],
                    help="note (rendered once) or role_patch (becomes a role_update proposal; Phase 3).")
    se.add_argument("--patch-role", default="", help="role_patch: the role to change (default: the receiver's).")
    se.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE",
                    help="role_patch: a field to set (repeatable; JSON values accepted).")
    se.add_argument("--json", action="store_true")
    se.set_defaults(func=_cmd_send)


# ---------------------------------------------------------------------------
# Redis access (read side)
# ---------------------------------------------------------------------------

def _redis():
    try:
        from acc.agent import _build_redis_client  # noqa: PLC0415
        from acc.config import load_config  # noqa: PLC0415
        return _build_redis_client(load_config())
    except Exception:  # noqa: BLE001
        return None


def _decode(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _load(redis_client, cid: str, lesson_id: str) -> dict | None:
    raw = redis_client.get(redis_lesson_key(cid, lesson_id))
    if not raw:
        return None
    try:
        return json.loads(_decode(raw))
    except json.JSONDecodeError:
        return None


def _resolve(redis_client, cid: str, prefix: str) -> str | None:
    """Full id for a prefix; None when nothing or more than one matches."""
    if _load(redis_client, cid, prefix) is not None:
        return prefix
    try:
        ids = [_decode(i) for i in redis_client.zrevrange(redis_lessons_index_key(cid), 0, -1)]
    except Exception:  # noqa: BLE001
        return None
    hits = [i for i in ids if i.startswith(prefix)]
    return hits[0] if len(hits) == 1 else None


def _cid(args: argparse.Namespace) -> str:
    return args.collective or default_collective()


def _line(lesson: dict) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(lesson.get("ts", 0) or 0)))
    target = f" → {lesson['target_agent_id']}" if lesson.get("target_agent_id") else ""
    return (f"  {str(lesson.get('lesson_id', ''))[:8]}  {ts}  [{lesson.get('ceiling') or 'CRITICAL'}/"
            f"{lesson.get('scope', 'local')}]  {lesson.get('role_label') or '?'}{target}: "
            f"{str(lesson.get('summary', '')).strip()[:110]}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> int:
    redis_client = _redis()
    if redis_client is None:
        print("lessons: no Redis backend configured", file=sys.stderr)
        return EXIT_UNSUPPORTED
    cid = _cid(args)
    try:
        ids = [_decode(i) for i in redis_client.zrevrange(redis_lessons_index_key(cid), 0, max(0, args.limit - 1))]
    except Exception as exc:  # noqa: BLE001
        print(f"lessons: index read failed: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    lessons = [item for item in (_load(redis_client, cid, i) for i in ids) if item]
    if args.json:
        print(json.dumps({"collective": cid, "lessons": lessons}, indent=2))
        return EXIT_OK
    if not lessons:
        print(f"No lessons published in {cid} (last 7 days).")
        return EXIT_OK
    print(f"Lessons in {cid} (newest first, {len(lessons)}):")
    for lesson in lessons:
        print(_line(lesson))
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    redis_client = _redis()
    if redis_client is None:
        print("lessons: no Redis backend configured", file=sys.stderr)
        return EXIT_UNSUPPORTED
    cid = _cid(args)
    lid = _resolve(redis_client, cid, args.lesson_id)
    lesson = _load(redis_client, cid, lid) if lid else None
    if lesson is None:
        print(f"lessons: no lesson matches {args.lesson_id!r} in {cid}", file=sys.stderr)
        return EXIT_NOT_FOUND
    if args.json:
        print(json.dumps(lesson, indent=2))
        return EXIT_OK
    print(_line(lesson))
    for key in ("from_agent", "trigger", "expected_outcome", "confidence", "target_agent_id"):
        if lesson.get(key) not in (None, "", 0, 0.0):
            print(f"  {key:<16}: {lesson[key]}")
    ev = lesson.get("evidence") or {}
    print(f"  {'evidence':<16}: {len(ev.get('source_episode_ids') or [])} episode(s)"
          + (f"; dissent: {ev.get('dissent')}" if ev.get("dissent") else ""))
    return EXIT_OK


def _cmd_trace(args: argparse.Namespace) -> int:
    redis_client = _redis()
    if redis_client is None:
        print("lessons: no Redis backend configured", file=sys.stderr)
        return EXIT_UNSUPPORTED
    cid = _cid(args)
    lid = _resolve(redis_client, cid, args.lesson_id)
    lesson = _load(redis_client, cid, lid) if lid else None
    if lesson is None:
        print(f"lessons: no lesson matches {args.lesson_id!r} in {cid}", file=sys.stderr)
        return EXIT_NOT_FOUND
    try:
        used = sorted(_decode(t) for t in (redis_client.smembers(redis_lesson_used_key(cid, lid)) or []))
    except Exception:  # noqa: BLE001
        used = []
    if args.json:
        print(json.dumps({"lesson": lesson, "used_by_tasks": used}, indent=2))
        return EXIT_OK
    print(_line(lesson))
    ev = lesson.get("evidence") or {}
    print(f"  from      : {lesson.get('from_agent')} (role {lesson.get('role_label') or '?'}), "
          f"trigger={lesson.get('trigger') or '-'}")
    print(f"  evidence  : {len(ev.get('source_episode_ids') or [])} episode(s)"
          + (f"; dissent: {ev.get('dissent')}" if ev.get("dissent") else ""))
    print(f"  requesters: {', '.join(lesson.get('source_requesters') or []) or '-'}")
    if used:
        print(f"  rendered into {len(used)} task prompt(s):")
        for t in used:
            print(f"    - {t}")
    else:
        print("  rendered into no task prompt yet.")
    return EXIT_OK


def _cmd_send(args: argparse.Namespace) -> int:
    import asyncio  # noqa: PLC0415

    from acc.lessons import Lesson  # noqa: PLC0415

    cid = _cid(args)
    ceiling = args.ceiling.strip().upper()
    if ceiling not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        print(f"lessons: --ceiling must be LOW|MEDIUM|HIGH|CRITICAL, got {args.ceiling!r}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    patch = None
    if args.kind == "role_patch":
        fields: dict = {}
        for item in args.set:
            if "=" not in item:
                print(f"lessons: --set expects FIELD=VALUE, got {item!r}", file=sys.stderr)
                return EXIT_UNSUPPORTED
            k, v = item.split("=", 1)
            try:
                fields[k.strip()] = json.loads(v)
            except json.JSONDecodeError:
                fields[k.strip()] = v
        if not fields:
            print("lessons: a role_patch needs at least one --set FIELD=VALUE", file=sys.stderr)
            return EXIT_UNSUPPORTED
        patch = {"role": args.patch_role, "fields": fields}
    lesson = Lesson(
        collective_id=cid, from_agent="acc-cli", role_label=args.role,
        domain_tag=args.tag, scope=args.scope, ceiling=ceiling, kind=args.kind,
        trigger="operator", summary=args.text.strip(), target_agent_id=args.to,
        patch=patch,
    )
    body = lesson.model_dump()

    async def _go() -> None:
        nc = await connect_nats()
        try:
            await nc.publish(subject_knowledge_share(cid, args.tag or "general"), encode_payload(body))
            await nc.flush(timeout=2.0)
        finally:
            await nc.close()

    try:
        asyncio.run(_go())
    except ConnectionError as exc:
        print(f"lessons: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    redis_client = _redis()
    if redis_client is not None:
        try:
            redis_client.set(redis_lesson_key(cid, lesson.lesson_id), json.dumps(body))
            redis_client.expire(redis_lesson_key(cid, lesson.lesson_id), 7 * 24 * 3600)
            redis_client.zadd(redis_lessons_index_key(cid), {lesson.lesson_id: lesson.ts})
        except Exception:  # noqa: BLE001
            pass
    if args.json:
        print(json.dumps(body, indent=2))
    else:
        print(f"lessons: sent {lesson.lesson_id[:8]} → {args.to} on {subject_knowledge_share(cid, args.tag or 'general')}")
    return EXIT_OK
