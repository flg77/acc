"""``acc-cli objective`` — work that persists across turns, under a ceiling.

    acc-cli objective list [--all]
    acc-cli objective new "<statement>" --max-turns N | --max-tokens N | --max-seconds N
    acc-cli objective show <id>
    acc-cli objective pause|resume|cancel <id>

A ceiling is **mandatory**. An objective the agent decides when to stop pursuing
is an unbounded spend nobody authorised, so `new` refuses without one.

An objective does not raise the autonomy level: a gated action inside one is
still gated, and the objective waits rather than escalating.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from acc import objectives


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("objective", help="Persistent objectives with an operator ceiling.")
    sp = p.add_subparsers(dest="objective_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Show objectives.")
    ls.add_argument("--all", action="store_true", help="Include stopped and completed.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    new = sp.add_parser("new", help="Create an objective (a ceiling is required).")
    new.add_argument("statement")
    new.add_argument("--max-turns", type=int, default=0)
    new.add_argument("--max-tokens", type=int, default=0)
    new.add_argument("--max-seconds", type=float, default=0.0)
    new.add_argument("--role", default="", help="Role that pursues it.")
    new.add_argument("--owner", default="", help="Who asked for it.")
    new.set_defaults(func=_cmd_new)

    show = sp.add_parser("show", help="Inspect one objective.")
    show.add_argument("id")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_show)

    for name, helptext in (
        ("pause", "Pause an objective."),
        ("resume", "Resume a paused objective."),
        ("cancel", "Stop an objective."),
    ):
        cmd = sp.add_parser(name, help=helptext)
        cmd.add_argument("id")
        if name == "cancel":
            cmd.add_argument("--reason", default="")
        cmd.set_defaults(func=_make_action(name))


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _make_action(name: str):
    def _run(args: argparse.Namespace) -> int:
        fn = getattr(objectives, name)
        try:
            objective = (
                fn(args.id, args.reason) if name == "cancel" else fn(args.id)
            )
        except objectives.ObjectiveError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        _safe()
        print(f"  {objective.id}: {objective.state}")
        if objective.stop_reason:
            print(f"  {objective.stop_reason}")
        return 0

    return _run


def _describe_ceiling(objective) -> str:
    c, u = objective.ceiling, objective.consumption
    parts = []
    if c.max_turns:
        parts.append(f"{u.turns}/{c.max_turns} turns")
    if c.max_tokens:
        parts.append(f"{u.tokens}/{c.max_tokens} tokens")
    if c.max_seconds:
        parts.append(f"{u.elapsed():.0f}/{c.max_seconds:.0f}s")
    return ", ".join(parts)


def _cmd_list(args: argparse.Namespace) -> int:
    all_objectives = objectives.load()
    shown = [
        o for o in all_objectives.values()
        if args.all or o.state in (objectives.State.ACTIVE, objectives.State.PAUSED)
    ]
    if args.json:
        print(json.dumps([o.as_dict() for o in shown], indent=2))
        return 0

    _safe()
    if not shown:
        print("  no objectives" + ("" if args.all else " (use --all for stopped ones)"))
        return 0
    for o in sorted(shown, key=lambda x: x.created_at):
        wait = f"  waiting on {o.waiting_on}" if o.waiting_on else ""
        print(f"  {o.id}  {o.state:<8} {_describe_ceiling(o)}{wait}")
        print(f"      {o.statement}")
        if o.stop_reason:
            print(f"      stopped: {o.stop_reason}")
    return 0


def _cmd_new(args: argparse.Namespace) -> int:
    try:
        objective = objectives.create(
            args.statement,
            max_turns=args.max_turns,
            max_tokens=args.max_tokens,
            max_seconds=args.max_seconds,
            owner=args.owner,
            role=args.role,
        )
    except objectives.ObjectiveError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _safe()
    print(f"  created {objective.id}")
    print(f"      {objective.statement}")
    print(f"      ceiling: {_describe_ceiling(objective)}")
    print()
    print("  gated actions inside this objective still require approval;")
    print("  it waits rather than escalating.")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    objective = objectives.load().get(args.id)
    if objective is None:
        print(f"no objective {args.id!r}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(objective.as_dict(), indent=2))
        return 0
    _safe()
    print(f"  {objective.id}  {objective.state}")
    print(f"      {objective.statement}")
    print(f"      ceiling:  {_describe_ceiling(objective)}")
    print(f"      created:  {time.strftime('%Y-%m-%d %H:%M', time.localtime(objective.created_at))}")
    if objective.owner or objective.role:
        print(f"      owner:    {objective.owner or '-'}   role: {objective.role or '-'}")
    if objective.waiting_on:
        print(f"      waiting on oversight {objective.waiting_on}")
    if objective.stop_reason:
        print(f"      stopped:  {objective.stop_reason}")
    return 0
