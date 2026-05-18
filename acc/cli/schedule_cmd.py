"""ACC scheduler CLI — register + list + remove + run-once.

Proposal 005.  Operator-facing surface for recurring task
dispatch.  The "daemon" is a `run-once` invocation the operator
wires into their host's normal scheduling (cron, systemd-timer,
Windows Task Scheduler) — no acc-side long-running process,
which sidesteps the "what restarts the scheduler when acc1
reboots" question.

Subcommands:

* ``acc-cli schedule add`` — register a schedule
* ``acc-cli schedule list`` — show registered schedules
* ``acc-cli schedule remove <name>`` — drop a schedule
* ``acc-cli schedule run-once`` — fire any due schedules + exit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from acc.cli._common import connect_nats, encode_payload, roles_root
from acc.scheduler import (
    Schedule,
    ScheduleStore,
    due_schedules,
    next_fire_time,
)


logger = logging.getLogger("acc.cli.schedule")


def _schedules_root() -> Path:
    """Default schedule directory — sibling of ``roles/``."""
    return Path(roles_root()).parent / "schedules"


def register_schedule_subparser(subparsers: Any) -> None:
    """Wire ``acc-cli schedule …`` into the main argparse tree."""
    sched_p = subparsers.add_parser(
        "schedule",
        help="Manage recurring task schedules (proposal 005).",
    )
    sched_subs = sched_p.add_subparsers(dest="schedule_cmd", required=True)

    add_p = sched_subs.add_parser("add", help="Register a new schedule.")
    add_p.add_argument("--name", required=True)
    add_p.add_argument("--role", required=True)
    add_p.add_argument("--task", required=True, help="Task description.")
    add_p.add_argument(
        "--cron", required=True,
        help='Cron expression — supports `* * * * *`, `*/N * * * *`, `M * * * *`, `0 H * * *`, `M H * * *`.',
    )
    add_p.add_argument("--collective-id", default="sol-01")
    add_p.add_argument("--target-agent-id", default="")
    add_p.add_argument("--root", default="", help="Schedules dir override.")
    add_p.set_defaults(func=_cmd_add)

    list_p = sched_subs.add_parser("list", help="List schedules.")
    list_p.add_argument("--root", default="")
    list_p.set_defaults(func=_cmd_list)

    rm_p = sched_subs.add_parser("remove", help="Remove a schedule.")
    rm_p.add_argument("name")
    rm_p.add_argument("--root", default="")
    rm_p.set_defaults(func=_cmd_remove)

    run_p = sched_subs.add_parser(
        "run-once", help="Fire any due schedules and exit.",
    )
    run_p.add_argument("--root", default="")
    run_p.add_argument(
        "--dry-run", action="store_true",
        help="Don't publish; just print what would fire.",
    )
    run_p.set_defaults(func=_cmd_run_once)


def _resolve_root(args: argparse.Namespace) -> Path:
    root = (args.root or "").strip()
    if root:
        return Path(root)
    return _schedules_root()


def _cmd_add(args: argparse.Namespace) -> int:
    # Validate cron early so we don't persist a bad expression.
    try:
        next_fire_time(args.cron, time.time())
    except ValueError as exc:
        print(f"schedule: bad cron expression: {exc}", file=sys.stderr)
        return 2

    store = ScheduleStore(root=_resolve_root(args))
    if store.get(args.name) is not None:
        print(
            f"schedule: {args.name!r} already exists — remove first",
            file=sys.stderr,
        )
        return 1

    sched = Schedule(
        name=args.name,
        role=args.role,
        task=args.task,
        cron=args.cron,
        collective_id=args.collective_id,
        target_agent_id=args.target_agent_id,
        created_ts=time.time(),
    )
    path = store.save(sched)
    print(f"schedule: wrote {path}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    store = ScheduleStore(root=_resolve_root(args))
    schedules = store.list()
    if not schedules:
        print("schedule: no schedules registered")
        return 0
    now = time.time()
    for s in schedules:
        try:
            nft = next_fire_time(s.cron, s.last_fired_ts or s.created_ts or now)
            nft_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(nft))
        except Exception as exc:
            nft_str = f"(bad cron: {exc})"
        enabled = "" if s.enabled else " [DISABLED]"
        print(
            f"  {s.name:<24}  role={s.role:<24}  cron={s.cron:<12}  "
            f"next={nft_str}{enabled}"
        )
        print(f"    task: {s.task}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    store = ScheduleStore(root=_resolve_root(args))
    if store.remove(args.name):
        print(f"schedule: removed {args.name!r}")
        return 0
    print(f"schedule: {args.name!r} not found", file=sys.stderr)
    return 1


def _cmd_run_once(args: argparse.Namespace) -> int:
    return asyncio.run(_run_once_async(args))


async def _run_once_async(args: argparse.Namespace) -> int:
    store = ScheduleStore(root=_resolve_root(args))
    schedules = store.list()
    now = time.time()
    due = due_schedules(schedules, now)
    if not due:
        print("schedule: nothing due")
        return 0

    if args.dry_run:
        for sched in due:
            print(f"schedule: would fire {sched.name!r} (role={sched.role})")
        return 0

    nc = await connect_nats()
    try:
        from acc.signals import subject_task_assign  # noqa: PLC0415
        for sched in due:
            task_id = uuid.uuid4().hex
            payload: dict[str, Any] = {
                "signal_type": "TASK_ASSIGN",
                "task_id": task_id,
                "plan_id": f"schedule-{sched.name}",
                "step_id": "scheduled-1",
                "collective_id": sched.collective_id,
                "from_agent": "acc-scheduler",
                "target_role": sched.role,
                "task_type": "SCHEDULED",
                "task_description": sched.task,
                "priority": "NORMAL",
                "iteration_n": 0,
                "max_iterations": 1,
                "ts": now,
            }
            if sched.target_agent_id:
                payload["target_agent_id"] = sched.target_agent_id

            await nc.publish(
                subject_task_assign(sched.collective_id),
                encode_payload(payload),
            )
            sched.last_fired_ts = now
            store.save(sched)
            print(
                f"schedule: fired {sched.name!r} task_id={task_id[:12]}"
            )
        await nc.flush(timeout=2.0)
    finally:
        await nc.drain()

    return 0
