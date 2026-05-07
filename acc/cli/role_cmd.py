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

    # PR-3 — markdown role authoring
    compile_p = role_sub.add_parser(
        "compile",
        help="Compile a role.md source into role.yaml + system_prompt.md.",
    )
    compile_p.add_argument("path", help="Path to a role.md file.")
    compile_p.add_argument(
        "--dest", default=None,
        help=(
            "Destination directory (default: sibling directory named after "
            "the role's '# Role: <name>' header)."
        ),
    )
    compile_p.set_defaults(func=_cmd_compile)

    decompile_p = role_sub.add_parser(
        "decompile",
        help="Render an existing roles/<name>/ directory back to markdown.",
    )
    decompile_p.add_argument(
        "name",
        help="Role directory name under roles/ (e.g. coding_agent).",
    )
    decompile_p.set_defaults(func=_cmd_decompile)

    lint_p = role_sub.add_parser(
        "lint",
        help="Validate a role.md without writing.  Exits 0 clean, 1 dirty.",
    )
    lint_p.add_argument("path", help="Path to a role.md file.")
    lint_p.set_defaults(func=_cmd_lint)


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
# PR-3 — markdown role authoring handlers
# ---------------------------------------------------------------------------


def _cmd_compile(args: argparse.Namespace) -> int:
    from acc.role_md import RoleMarkdownError, compile_file  # noqa: PLC0415

    md_path = Path(args.path)
    if not md_path.is_file():
        print(f"role.md not found: {md_path}", file=sys.stderr)
        return 1
    dest = Path(args.dest) if args.dest else None
    try:
        yaml_path, sp_path = compile_file(md_path, dest_dir=dest)
    except RoleMarkdownError as exc:
        print(f"role compile failed (L{exc.line}): {exc}", file=sys.stderr)
        return 1
    print(f"wrote {yaml_path}")
    if sp_path.exists():
        print(f"wrote {sp_path}")
    return 0


def _cmd_decompile(args: argparse.Namespace) -> int:
    from acc.role_md import RoleMarkdownError, decompile_dir  # noqa: PLC0415

    role_dir = Path(roles_root()) / args.name
    if not role_dir.is_dir():
        print(f"role directory not found: {role_dir}", file=sys.stderr)
        return 1
    try:
        markdown = decompile_dir(role_dir)
    except RoleMarkdownError as exc:
        print(f"decompile failed: {exc}", file=sys.stderr)
        return 1
    print(markdown, end="")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    from acc.role_md import lint_markdown  # noqa: PLC0415

    md_path = Path(args.path)
    if not md_path.is_file():
        print(f"role.md not found: {md_path}", file=sys.stderr)
        return 1
    issues = lint_markdown(md_path.read_text(encoding="utf-8"))
    if not issues:
        print(f"{md_path}: clean")
        return 0
    for issue in issues:
        print(f"{md_path}: {issue}", file=sys.stderr)
    return 1


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
