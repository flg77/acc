"""``acc-cli role …`` — list / show / infuse role definitions.

Reuses :class:`acc.role_loader.RoleLoader` and :func:`acc.role_loader.list_roles`
so the CLI matches the TUI's view of roles exactly.

The ``infuse`` subcommand publishes a ``ROLE_UPDATE`` payload on
``acc.{cid}.role_update``.  The wire format mirrors the TUI's Apply
button (``acc/tui/screens/infuse.py``) so the arbiter validates both
paths through the same code path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from acc.cli._common import (
    connect_nats,
    default_collective,
    encode_payload,
    roles_root,
)


# ---------------------------------------------------------------------------
# Registrar
# ---------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    """Attach the ``role`` command tree."""
    role = sub.add_parser("role", help="Inspect or apply role definitions.")
    role_sub = role.add_subparsers(dest="role_command", required=True, metavar="ACTION")

    # list
    list_p = role_sub.add_parser("list", help="List role names from roles/.")
    list_p.set_defaults(func=_cmd_list)

    # show
    show_p = role_sub.add_parser("show", help="Print the merged role.yaml for one role.")
    show_p.add_argument("name", help="Role directory name (e.g. coding_agent).")
    show_p.add_argument(
        "--format",
        choices=("yaml", "json"),
        default="yaml",
        help="Output format (default: yaml).",
    )
    show_p.set_defaults(func=_cmd_show)

    # infuse
    inf = role_sub.add_parser(
        "infuse",
        help="Publish a ROLE_UPDATE for <role> on collective <cid>.",
    )
    inf.add_argument("collective_id", nargs="?", default=None,
                     help="Collective id (default: $ACC_COLLECTIVE_ID).")
    inf.add_argument("name", help="Role to infuse (must exist under roles/).")
    inf.add_argument(
        "--approver-id",
        default="cli:operator",
        help="approver_id stamped onto the ROLE_UPDATE (default: cli:operator).",
    )
    inf.set_defaults(func=_cmd_infuse)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    from acc.role_loader import list_roles  # noqa: PLC0415
    roots = roles_root()
    names = list_roles(roots)
    if not names:
        print(f"(no roles found under {roots!r})", file=sys.stderr)
        return 1
    for name in names:
        print(name)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    from acc.role_loader import RoleLoader  # noqa: PLC0415
    roots = roles_root()
    role_def = RoleLoader(roots, args.name).load()
    if role_def is None:
        print(f"role {args.name!r} not found under {roots!r}", file=sys.stderr)
        return 1

    data: dict[str, Any] = {"role_definition": _serialise_role_def(role_def)}
    rubric_path = Path(roots) / args.name / "eval_rubric.yaml"
    if rubric_path.is_file():
        try:
            import yaml  # noqa: PLC0415
            data["eval_rubric"] = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
        except Exception as exc:
            data["eval_rubric_error"] = str(exc)

    if args.format == "json":
        print(json.dumps(data, indent=2, default=str))
        return 0

    try:
        import yaml  # noqa: PLC0415
        print(yaml.dump(data, sort_keys=False, default_flow_style=False))
    except ImportError:
        print(json.dumps(data, indent=2, default=str))
    return 0


async def _cmd_infuse(args: argparse.Namespace) -> int:
    from acc.role_loader import RoleLoader  # noqa: PLC0415
    from acc.signals import subject_role_update  # noqa: PLC0415

    cid = args.collective_id or default_collective()
    role_def = RoleLoader(roles_root(), args.name).load()
    if role_def is None:
        print(f"role {args.name!r} not found", file=sys.stderr)
        return 1

    payload: dict[str, Any] = {
        "signal_type": "ROLE_UPDATE",
        "agent_id": "",
        "collective_id": cid,
        "ts": time.time(),
        "approver_id": args.approver_id,
        "signature": "",  # arbiter countersigns; CLI never holds private keys
        "role_definition": _serialise_role_def(role_def),
    }

    nc = await connect_nats()
    try:
        await nc.publish(subject_role_update(cid), encode_payload(payload))
        await nc.flush(timeout=2.0)
    finally:
        await nc.drain()

    print(f"published ROLE_UPDATE for {args.name!r} on {subject_role_update(cid)}")
    print(f"  approver_id: {args.approver_id}")
    print(f"  version:     {role_def.version}")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialise_role_def(role_def: Any) -> dict[str, Any]:
    """Return a plain-dict view of a RoleDefinitionConfig for wire transport.

    Pydantic v2: ``model_dump()`` is the canonical serialiser.  Falls back
    to ``asdict`` for stdlib dataclasses and finally to ``vars`` so we
    never crash on a custom subclass.
    """
    if hasattr(role_def, "model_dump"):
        return role_def.model_dump()
    try:
        return asdict(role_def)
    except TypeError:
        return dict(vars(role_def))
