"""``acc-cli up|down|restart|stack`` — run the container stack from acc-cli.

    acc-cli up [--webgui] [acc-deploy args...]
    acc-cli down [-v]
    acc-cli restart [--webgui]
    acc-cli stack <any acc-deploy verb> [args...]   # build, rebuild, ps, logs, ...

Every verb hands its arguments, untouched and in order, to the ``acc-deploy.sh``
this host's layout resolves (the same script ``acc stack`` runs).  The
container logic stays in that one script; this module only finds it.

``acc-cli status`` and ``acc-cli logs`` read the collective, not the
containers; the container views are ``acc-cli stack status`` and
``acc-cli stack logs``.
"""

from __future__ import annotations

import argparse

_SHORTCUTS = {
    "up": "Start the container stack (acc-deploy.sh up).",
    "down": "Stop the container stack (acc-deploy.sh down).",
    "restart": "Re-create the stack on the current images (acc-deploy.sh restart).",
}


def register(sub: argparse._SubParsersAction) -> None:
    for verb, help_text in _SHORTCUTS.items():
        # No options of its own: every flag (``--webgui``, ``-v``, ``-h``)
        # belongs to acc-deploy.sh.
        p = sub.add_parser(verb, help=help_text, add_help=False, prefix_chars="\0")
        p.set_defaults(func=_cmd_stack, stack_verb=[verb], stack_passthrough=True)
    p = sub.add_parser(
        "stack", help="Any acc-deploy.sh verb (build, rebuild, ps, logs, ...).",
        add_help=False, prefix_chars="\0",
    )
    p.set_defaults(func=_cmd_stack, stack_verb=[], stack_passthrough=True)


def _cmd_stack(args: argparse.Namespace) -> int:
    from acc.launcher import run_stack  # noqa: PLC0415
    return run_stack([*args.stack_verb, *args.stack_args])
