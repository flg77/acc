"""``acc-cli logs`` — read across the collective, not one container at a time.

    acc-cli logs [--role ROLE] [--task ID] [--session ID]
                 [--since 30m] [--level INFO] [--source container|tracelog]
                 [--follow] [--json]

``--task`` is the one that earns the command: it answers *what happened to this
piece of work* across every agent that touched it, which otherwise means one
terminal per container and reading by eye.

Sources are labelled rather than blended. Container logs carry stack traces;
the tracelog carries governance verdicts. Presenting them as one stream would
make a Category evaluation look like a log line.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import logs as logs_mod


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("logs", help="Read logs across the whole collective.")
    p.add_argument("--role", default="", help="Only agents whose name mentions this role.")
    p.add_argument("--task", default="", help="Follow one task across every agent.")
    p.add_argument("--session", default="", help="One tracelog session id.")
    p.add_argument("--since", default="", help="Window, e.g. 30m, 2h, 90s, 1d.")
    p.add_argument(
        "--level", default="DEBUG", choices=list(logs_mod.LEVELS),
        help="Minimum severity to show.",
    )
    p.add_argument(
        "--source", action="append", choices=["container", "tracelog"], default=None,
        help="Limit to a source (repeatable). Default: both.",
    )
    p.add_argument("--limit", type=int, default=2000, help="Most recent N lines.")
    p.add_argument("--follow", action="store_true", help="Stream new lines.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--runtime", default="podman", help="Container runtime (default: podman).")
    p.set_defaults(func=_cmd_logs)


def _query(args: argparse.Namespace) -> logs_mod.Query:
    return logs_mod.Query(
        role=args.role,
        task=args.task,
        session=args.session,
        since_s=logs_mod.parse_since(args.since),
        level=args.level,
        sources=tuple(args.source) if args.source else ("container", "tracelog"),
        limit=args.limit,
    )


def _render(line: logs_mod.LogLine) -> str:
    when = ""
    if line.ts:
        from datetime import datetime, timezone

        when = datetime.fromtimestamp(line.ts, tz=timezone.utc).strftime("%H:%M:%S")
    return f"  {when:<8} {line.source[:4]:<4} {line.origin:<22} {line.text}"


def _cmd_logs(args: argparse.Namespace) -> int:
    query = _query(args)

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

    if args.follow:
        try:
            for line in logs_mod.follow(query, runtime=args.runtime):
                print(_render(line), flush=True)
        except KeyboardInterrupt:
            return 0
        return 0

    report = logs_mod.gather(query, runtime=args.runtime)

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return 0

    for line in report.lines:
        print(_render(line))

    if report.unavailable:
        print()
        print("  sources unavailable:")
        for name, reason in sorted(report.unavailable.items()):
            print(f"    {name}: {reason}")

    if not report.lines:
        print()
        filters = [
            f"{k}={v}"
            for k, v in (
                ("role", args.role), ("task", args.task),
                ("session", args.session), ("since", args.since),
            )
            if v
        ]
        suffix = f" matching {', '.join(filters)}" if filters else ""
        print(f"  no lines{suffix}")
    else:
        print()
        print(f"  {len(report.lines)} line(s)")
    return 0
