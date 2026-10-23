"""``acc-cli ask "<prompt>"`` -- give ACC a task from a plain command line and
answer its questions inline (vault proposal 058, Phase 2, step 12).

The operator asked (2026-10-15) to "respond from within to cmdline / prompt
to permission requests".  ``ask`` submits the task through the same channel
the TUI Prompt pane uses, streams its progress, and when the task asks a
question -- the mission card, a destructive confirm, a plain gate -- prints it
with its options and reads the answer from the terminal.  The answer travels
as the same ``OVERSIGHT_DECISION`` the TUI publishes, under the same operator
identity (:mod:`acc.tui.actor`).  At the end it prints the answer, where the
work is heading (:mod:`acc.heading`) and how often it asked.

    acc-cli ask "fix the failing test in calc/" --role coding_agent
    acc-cli ask --mode ASK_PERMISSIONS "…"     # every step is a question
    acc-cli ask --no-answer "…"                 # never answer: questions wait
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ask", help="Give ACC a task and answer its questions inline.")
    p.add_argument("prompt", nargs="+", help="The task, in your words.")
    p.add_argument("--role", default="assistant", help="Target role (default: assistant).")
    p.add_argument("--mode", default="ASK_ONCE",
                   help="Operating mode (default ASK_ONCE, 'Ask once').")
    p.add_argument("--session", default=None, help="Continue this session (thread).")
    p.add_argument("--workspace", default=None, help="Workspace project under /workspace.")
    p.add_argument("--timeout", type=float, default=1800.0, help="Seconds to wait for the answer.")
    p.add_argument("--collective-id", default=None)
    p.add_argument("--nats-url", default=None)
    p.add_argument("--no-answer", action="store_true",
                   help="Print questions but do not answer them (they wait in the queue).")
    p.add_argument("--json", action="store_true", help="Print the final reply as JSON.")
    p.set_defaults(func=_cmd_ask)


def _render_question(row: dict) -> tuple[str, list[dict]]:
    q = row.get("question") or {}
    options = [o for o in (q.get("options") or []) if isinstance(o, dict) and o.get("key")]
    head = q.get("text") or row.get("summary") or "a gated call"
    lines = [f"\n? [{row.get('risk_level', '')}] {head}"]
    for ev in (row.get("evidence") or [])[:4]:
        lines.append(f"    {ev}")
    if options:
        for o in options:
            lines.append(f"  {o['key']}) {o.get('label', o['key'])}")
            for d in (o.get("detail") or [])[:5]:
                lines.append(f"       {d}")
    else:
        lines.append("  y) allow   n) refuse")
    return "\n".join(lines), options


async def _answer_loop(observer, task_id: str, *, enabled: bool, seen: set[str]) -> None:
    """Watch the arbiter's pending rows for this task's questions and answer
    each one from stdin."""
    from acc.signals import subject_oversight_decision  # noqa: PLC0415
    from acc.tui.actor import tui_actor, tui_actor_tier  # noqa: PLC0415

    cid = observer.collective_id if hasattr(observer, "collective_id") else ""
    while True:
        await asyncio.sleep(0.5)
        rows = list(getattr(observer._snapshot, "oversight_pending_items", []) or [])
        for row in rows:
            oid = str(row.get("oversight_id") or "")
            if not oid or oid in seen or str(row.get("task_id") or "") != task_id:
                continue
            seen.add(oid)
            text, options = _render_question(row)
            print(text, flush=True)
            if not enabled:
                print("  (not answered: --no-answer)", flush=True)
                continue
            keys = [o["key"] for o in options] or ["y", "n"]
            while True:
                raw = (await asyncio.to_thread(input, f"  answer [{'/'.join(keys)}]: ")).strip()
                if raw in keys:
                    break
                print(f"  please type one of {', '.join(keys)}", flush=True)
            if options:
                opt = next(o for o in options if o["key"] == raw)
                decision, answer = ("APPROVE" if opt.get("proceeds") else "REJECT"), raw
            else:
                decision, answer = ("APPROVE" if raw == "y" else "REJECT"), ""
            payload = {
                "signal_type": "OVERSIGHT_DECISION", "oversight_id": oid,
                "decision": decision, "approver_id": "cli:" + tui_actor().split(":", 1)[-1],
                "approver_tier": tui_actor_tier(), "reason": "acc-cli ask",
                "ts": time.time(), "collective_id": cid,
            }
            if answer:
                payload["answer"] = answer
            await observer.publish(subject_oversight_decision(cid, oid), payload)
            print(f"  -> {decision.lower()}{f' (option {answer})' if answer else ''}", flush=True)


async def _run(args: argparse.Namespace) -> int:
    from acc.channels.tui import TUIPromptChannel  # noqa: PLC0415
    from acc.cli._common import default_collective, nats_url  # noqa: PLC0415
    from acc.heading import render  # noqa: PLC0415
    from acc.operating_modes import label, normalise  # noqa: PLC0415
    from acc.tui.client import NATSObserver  # noqa: PLC0415

    cid = args.collective_id or default_collective()
    updates: asyncio.Queue = asyncio.Queue(maxsize=200)
    observer = NATSObserver(nats_url=args.nats_url or nats_url(), collective_id=cid,
                            update_queue=updates)
    observer.collective_id = cid
    await observer.connect()
    await observer.subscribe()

    async def drain():
        # The observer pushes a snapshot copy per update; nobody renders them here.
        while True:
            await updates.get()

    drain_task = asyncio.create_task(drain())
    channel = TUIPromptChannel(observer, collective_id=cid)
    mode = normalise(args.mode)
    print(f"[{label(mode)}] {args.role}: {' '.join(args.prompt)}", flush=True)

    def progress(p: dict) -> None:
        step = p.get("step_label") or p.get("label") or ""
        if step:
            print(f"  … {step}", flush=True)

    seen: set[str] = set()
    try:
        task_id = await channel.send(
            prompt=" ".join(args.prompt), target_role=args.role, operating_mode=mode,
            session_id=args.session, workspace=args.workspace, on_progress=progress,
        )
        answers = asyncio.create_task(_answer_loop(observer, task_id, enabled=not args.no_answer, seen=seen))
        try:
            reply = await channel.receive(task_id, timeout=args.timeout)
        finally:
            answers.cancel()
    finally:
        drain_task.cancel()
        try:
            await channel.close()
            await observer.close()
        except Exception:  # noqa: BLE001
            pass

    if args.json:
        print(json.dumps({"task_id": task_id, "output": reply.output, "blocked": reply.blocked,
                          "heading": reply.heading, "asks": reply.asks}, indent=2))
        return 1 if reply.blocked else 0
    print("\n" + (f"[BLOCKED] {reply.block_reason}\n" if reply.blocked else "") + (reply.output or "(empty)"))
    for line in render(reply.heading):
        print(f"↳ {line}")
    if reply.asks:
        a = reply.asks
        print(f"↳ asked you {a.get('asks', 0)}×, answered by a mission {a.get('by_mission', 0)}×")
    print(f"↳ session {getattr(reply, 'task_id', task_id)} — continue with --session {args.session or task_id}")
    return 1 if reply.blocked else 0


def _cmd_ask(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_run(args))
    except TimeoutError:
        print("no answer within the timeout; questions may still be waiting (acc-cli oversight pending)",
              file=sys.stderr)
        return 2
