"""``acc-cli checkpoints`` — what an agent changed, and putting it back.

    acc-cli checkpoints list
    acc-cli checkpoints show <id>
    acc-cli checkpoints restore <id> [--dry-run] [--force]
    acc-cli checkpoints prune

A checkpoint records the task that caused the write and, where one exists, the
oversight decision that authorised it — so "what did the agent change, when,
and who approved it" is one question, not three.

Snapshot-before-write is opt-in via ACC_WORKSPACE_CHECKPOINTS: it costs disk on
every agent write, and an edge node is the constrained case.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from acc import checkpoints


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("checkpoints", help="Snapshots taken before agent writes.")
    sp = p.add_subparsers(dest="checkpoints_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Show checkpoints, newest first.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    sh = sp.add_parser("show", help="Inspect one checkpoint.")
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=_cmd_show)

    rs = sp.add_parser("restore", help="Put the files back.")
    rs.add_argument("id")
    rs.add_argument("--dry-run", action="store_true")
    rs.add_argument("--force", action="store_true",
                    help="Acknowledge discarding work done since the checkpoint.")
    rs.set_defaults(func=_cmd_restore)

    pr = sp.add_parser("prune", help="Drop checkpoints past the retention caps.")
    pr.add_argument("--max-days", type=int, default=checkpoints.MAX_AGE_DAYS)
    pr.set_defaults(func=_cmd_prune)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_list(args: argparse.Namespace) -> int:
    entries = checkpoints.index()
    if args.json:
        print(json.dumps([c.as_dict() for c in entries], indent=2))
        return 0
    _safe()
    if not entries:
        print("  no checkpoints")
        print(f"  snapshot-before-write is opt-in: set ACC_WORKSPACE_CHECKPOINTS=1")
        return 0
    for c in entries:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(c.created_at))
        approved = f"  approved by {c.oversight_id}" if c.oversight_id else ""
        task = f"  task={c.task_id}" if c.task_id else ""
        print(f"  {c.id}  {when}  {len(c.files)} file(s)  {c.bytes}B{task}{approved}")
    print()
    print(f"  {checkpoints.total_bytes()} bytes total")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        c = checkpoints.load(args.id)
    except checkpoints.CheckpointError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(c.as_dict(), indent=2))
        return 0
    _safe()
    print(f"  {c.id}")
    print(f"      taken:    {time.strftime('%Y-%m-%d %H:%M', time.localtime(c.created_at))}")
    print(f"      task:     {c.task_id or '-'}")
    print(f"      agent:    {c.agent_id or '-'}  role: {c.role or '-'}")
    print(f"      approved: {c.oversight_id or '(no oversight decision recorded)'}")
    print("      files:")
    for f in c.files:
        state = f"SKIPPED ({f.skipped})" if f.skipped else (
            "created by the write" if not f.existed else f"{f.size}B"
        )
        print(f"          {f.path}  {state}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    try:
        plan = checkpoints.plan_restore(args.id)
    except checkpoints.CheckpointError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    _safe()
    for path in plan.would_revert:
        print(f"  revert  {path}")
    for path in plan.would_delete:
        print(f"  delete  {path}  (created by the write)")
    for path in plan.unchanged:
        print(f"  unchanged  {path}")
    if plan.unrecoverable:
        print()
        print("  NOT recoverable from this checkpoint:")
        for path in plan.unrecoverable:
            print(f"      {path}")
    if plan.modified_since:
        print()
        print("  changed since the checkpoint — restoring discards that work:")
        for path in plan.modified_since:
            print(f"      {path}")

    if args.dry_run:
        print()
        print("  dry run — nothing was changed")
        return 0

    try:
        checkpoints.restore(args.id, force=args.force)
    except checkpoints.CheckpointError as exc:
        print()
        print(str(exc), file=sys.stderr)
        return 1
    print()
    print("  restored")
    return 0


def _cmd_prune(args: argparse.Namespace) -> int:
    removed = checkpoints.prune(max_age_days=args.max_days)
    _safe()
    print(f"  pruned {len(removed)} checkpoint(s)")
    print(f"  {checkpoints.total_bytes()} bytes remain")
    return 0
