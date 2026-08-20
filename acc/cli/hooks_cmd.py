"""``acc-cli hooks`` — run a command when a lifecycle event happens.

    acc-cli hooks list
    acc-cli hooks add <name> --event EVENT --command CMD [--filter TEXT]
    acc-cli hooks test <name> [--event EVENT]
    acc-cli hooks remove <name>

Hooks **observe**. They cannot block, delay or alter anything — gating belongs
to the oversight queue, which has the approval record and the audit trail. The
help text says so too, because someone will reasonably assume otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import hooks as hooks_mod


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "hooks",
        help="Run a command on lifecycle events (observe-only; gating is oversight).",
    )
    sp = p.add_subparsers(dest="hooks_command", required=True, metavar="ACTION")

    list_p = sp.add_parser("list", help="Show registered hooks.")
    list_p.add_argument("--json", action="store_true", help="Emit JSON.")
    list_p.set_defaults(func=_cmd_list)

    add_p = sp.add_parser("add", help="Register a hook.")
    add_p.add_argument("name")
    add_p.add_argument("--event", default="*", help="Signal type, or * for all.")
    add_p.add_argument("--command", required=True, help="Command to run (must be allowlisted).")
    add_p.add_argument("--filter", default="", help="Only fire when the payload contains this text.")
    add_p.add_argument(
        "--timeout", type=float, default=hooks_mod.DEFAULT_TIMEOUT_S,
        help=f"Seconds before the hook is killed (default {hooks_mod.DEFAULT_TIMEOUT_S:g}).",
    )
    add_p.set_defaults(func=_cmd_add)

    test_p = sp.add_parser("test", help="Run a hook once with a sample payload.")
    test_p.add_argument("name")
    test_p.add_argument("--event", default=None, help="Event to simulate (default: the hook's own).")
    test_p.set_defaults(func=_cmd_test)

    rm_p = sp.add_parser("remove", help="Remove a hook (takes effect without a restart).")
    rm_p.add_argument("name")
    rm_p.set_defaults(func=_cmd_remove)


def _cmd_list(args: argparse.Namespace) -> int:
    hooks = hooks_mod.load()
    if args.json:
        print(
            json.dumps(
                {
                    "allowlist": sorted(hooks_mod.allowlist()),
                    "hooks": [h.as_dict() for h in hooks],
                },
                indent=2,
            )
        )
        return 0

    allowed = sorted(hooks_mod.allowlist())
    print(f"hooks file: {hooks_mod.hooks_path()}")
    print(f"allowlist:  {', '.join(allowed) if allowed else '(empty — nothing will run)'}")
    print()
    if not hooks:
        print("  no hooks registered")
        return 0
    for h in hooks:
        state = "" if h.enabled else "  [disabled]"
        filt = f"  filter={h.filter!r}" if h.filter else ""
        print(f"  {h.name}{state}")
        print(f"      on {h.event}  ->  {h.command}{filt}")
    print()
    print("Hooks observe only; they cannot block an action. Use the oversight")
    print("queue for anything that must gate.")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        hook = hooks_mod.add(
            args.name, args.event, args.command,
            filter=args.filter, timeout_s=args.timeout,
        )
    except hooks_mod.HookError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"registered {hook.name!r}: on {hook.event} -> {hook.command}")
    print(f"  written to {hooks_mod.hooks_path()}")
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    hooks = {h.name: h for h in hooks_mod.load()}
    hook = hooks.get(args.name)
    if hook is None:
        print(f"no hook named {args.name!r}", file=sys.stderr)
        return 2
    event = args.event or (hook.event if hook.event != "*" else "TASK_COMPLETE")
    payload = {"agent_id": "test-agent", "collective_id": "test", "sample": True}
    run = hooks_mod.run_hook(hook, event, payload)
    print(f"{hook.name}: {'ok' if run.ok else 'FAILED'} in {run.duration_s:.2f}s")
    if run.returncode is not None:
        print(f"  exit code: {run.returncode}")
    if run.detail:
        print(f"  {run.detail}")
    return 0 if run.ok else 1


def _cmd_remove(args: argparse.Namespace) -> int:
    if not hooks_mod.remove(args.name):
        print(f"no hook named {args.name!r}", file=sys.stderr)
        return 2
    print(f"removed {args.name!r}")
    return 0
