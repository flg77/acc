"""ACC operator CLI — `acc-cli` entry point.

Surface every internal channel (NATS, LLM, role-loader, oversight queue)
through one zero-dep CLI for ops, debugging, and demo scripting.

Usage::

    acc-cli                         # print help
    acc-cli role list
    acc-cli role show <name>
    acc-cli role infuse <cid> <role>
    acc-cli nats sub '<pattern>'
    acc-cli nats pub <subject> '<json>'
    acc-cli llm test [--backend openai_compat]
    acc-cli trace <task_id> [--limit 100]
    acc-cli oversight pending
    acc-cli oversight submit <task_id> <agent_id> <risk> <summary...>
    acc-cli oversight approve <oversight_id>
    acc-cli oversight reject  <oversight_id> [--reason TEXT]
    acc-cli plan submit <plan.json> [--watch]
    acc-cli plan watch <plan_id>

Common environment variables (read on every invocation):

    ACC_NATS_URL          NATS endpoint (default: nats://localhost:4222)
    ACC_COLLECTIVE_ID     Default collective for nats/oversight commands
    ACC_ROLES_ROOT        roles/ directory (default: roles)
    ACC_CONFIG_PATH       acc-config.yaml location (default: acc-config.yaml)

The CLI is dependency-light — only ``argparse`` (stdlib) for parsing.
Each subcommand lives in its own ``acc.cli.<verb>_cmd`` module so new
commands can be added without touching the dispatcher.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Awaitable, Callable

logger = logging.getLogger("acc.cli")

# Each registrar function takes a sub-parsers action and adds its commands.
# Keeping the registry explicit avoids magic auto-discovery and makes the
# CLI surface easy to grep.
_REGISTRARS: list[Callable[[argparse._SubParsersAction], None]] = []


def _register(reg: Callable[[argparse._SubParsersAction], None]) -> None:
    _REGISTRARS.append(reg)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acc-cli",
        description="ACC operator CLI — inspect roles, NATS, LLM, oversight queue.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity; -v for INFO, -vv for DEBUG.",
    )
    sub = parser.add_subparsers(dest="command", required=False, metavar="COMMAND")

    # Lazy-import each command module so pip-install is fast and unused
    # dependencies (e.g. nats-py for `role list`) do not block startup.
    from acc.cli import (  # noqa: PLC0415
        role_cmd,
        nats_cmd,
        llm_cmd,
        trace_cmd,
        oversight_cmd,
        plan_cmd,
    )
    role_cmd.register(sub)
    nats_cmd.register(sub)
    llm_cmd.register(sub)
    trace_cmd.register(sub)
    oversight_cmd.register(sub)
    plan_cmd.register(sub)

    return parser


def _configure_logging(verbosity: int) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    handler: Callable[[argparse.Namespace], Awaitable[int] | int] = args.func
    try:
        result = handler(args)
        if asyncio.iscoroutine(result):
            return int(asyncio.run(result) or 0)
        return int(result or 0)
    except KeyboardInterrupt:
        # Common when the user presses Ctrl-C in `nats sub` — exit cleanly.
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
