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
    inf.add_argument("name", help="Role to infuse — in-tree (roles/) OR served "
                                  "by an installed family pack.")
    inf.add_argument(
        "--from-pkg", "--install", dest="from_pkg", default=None, metavar="@scope/name[@constraint]",
        help="Fetch+verify+install this family pack from the catalog FIRST, "
             "then infuse <name> from it (one-step load).  Aligns with "
             "`acc-pkg install` / `acc-deploy.sh pkg add`.",
    )
    inf.add_argument(
        "--allow-unsigned", action="store_true",
        help="With --from-pkg: bypass the signing floor for unsigned dev packs "
             "(audit-logged).",
    )
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

    # Proposal 006 — content-drift audit across role.yaml + role.md.
    # Separate subcommand so the narrower `role lint <path>` doesn't
    # change shape.
    audit_p = role_sub.add_parser(
        "audit",
        help=(
            "Cross-check role.yaml + role.md for boundary-doc drift "
            "(proposal 006).  Warnings only by default; --strict "
            "exits 1 on any warning."
        ),
    )
    audit_p.add_argument(
        "name", help="Role directory name under roles/ (e.g. coding_agent).",
    )
    audit_p.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if any LINT* warning fires (default: exit 0).",
    )
    audit_p.set_defaults(func=_cmd_audit)


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

    # Optional one-step load: install the family pack from the catalog, then
    # infuse a role it provides.  The dual-source RoleLoader below resolves the
    # freshly-installed packaged role transparently.
    if getattr(args, "from_pkg", None):
        try:
            from acc.collective import parse_required_package  # noqa: PLC0415
            from acc.pkg.fetch import (  # noqa: PLC0415
                FetchError,
                fetch_and_install_closure,
            )
        except ImportError as exc:
            print(f"role infuse --from-pkg: acc.pkg unavailable ({exc})", file=sys.stderr)
            return 1
        try:
            pkg_name, constraint = parse_required_package(args.from_pkg)
            res = fetch_and_install_closure(
                pkg_name, constraint, allow_unsigned=args.allow_unsigned,
            )
        except (ValueError, FetchError) as exc:
            print(f"role infuse --from-pkg {args.from_pkg!r}: {exc}", file=sys.stderr)
            return 1
        print(f"installed {res.install.entry.name}@{res.install.entry.version} "
              f"-> {res.install.install_path}")

    role_def = RoleLoader(roles_root(), args.name).load()
    if role_def is None:
        hint = "" if not getattr(args, "from_pkg", None) else (
            f" (pack {args.from_pkg} installed, but it does not provide a role "
            f"named {args.name!r})"
        )
        print(f"role {args.name!r} not found{hint}", file=sys.stderr)
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


# Proposal 006 — content-drift audit codes.
LINT_CODES = {
    "LINT001": "role.yaml missing or unreadable",
    "LINT002": "role.yaml `purpose` is empty",
    "LINT003": "role.yaml `purpose` longer than 200 chars (boundary doc says one-liner)",
    "LINT004": "role.md missing for a role with declared task_types",
    "LINT005": "role.md H1 heading appears unrelated to role.yaml `purpose`",
}


def audit_role(
    roles_root_path: Path, role_name: str,
) -> list[tuple[str, str]]:
    """Run the proposal-006 boundary-doc checks on a single role.

    Returns a list of ``(code, detail)`` tuples.  Empty list = clean.
    Pure-fn: no I/O outside the filesystem reads it explicitly does.

    Codes (see ``LINT_CODES`` for the table):

    * LINT001 — role.yaml missing or unreadable
    * LINT002 — role.yaml ``purpose`` empty
    * LINT003 — role.yaml ``purpose`` > 200 chars
    * LINT004 — role.md missing but role declares task_types
    * LINT005 — role.md H1 doesn't share a meaningful word with
      role.yaml ``purpose`` (cheap drift heuristic)
    """
    findings: list[tuple[str, str]] = []
    role_dir = Path(roles_root_path) / role_name
    yaml_path = role_dir / "role.yaml"
    md_path = role_dir / "role.md"

    if not yaml_path.is_file():
        findings.append(("LINT001", f"{yaml_path} not found"))
        return findings

    try:
        import yaml as _yaml  # noqa: PLC0415
        with yaml_path.open("r", encoding="utf-8") as fh:
            doc = _yaml.safe_load(fh) or {}
    except Exception as exc:
        findings.append(("LINT001", f"{yaml_path}: {exc}"))
        return findings

    role_def = (doc.get("role_definition") or {}) if isinstance(doc, dict) else {}
    purpose = str(role_def.get("purpose", "")).strip()
    task_types = role_def.get("task_types") or []

    if not purpose:
        findings.append(("LINT002", str(yaml_path)))
    elif len(purpose) > 200:
        findings.append(("LINT003", f"{yaml_path}: {len(purpose)} chars"))

    md_exists = md_path.is_file()
    if not md_exists and task_types:
        findings.append((
            "LINT004",
            f"{md_path} missing (task_types declared: {task_types!r})",
        ))

    if md_exists and purpose:
        try:
            md_body = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(("LINT001", f"{md_path}: {exc}"))
            return findings
        h1 = _first_h1(md_body)
        if h1 and not _heading_purpose_overlap(h1, purpose):
            findings.append((
                "LINT005",
                f"{md_path}: H1 {h1!r} vs role.yaml purpose {purpose[:60]!r}",
            ))

    return findings


def _first_h1(md: str) -> str:
    """Return the first H1 heading in a markdown body, or empty."""
    for line in md.splitlines():
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    return ""


def _heading_purpose_overlap(h1: str, purpose: str) -> bool:
    """Heuristic drift check.

    Returns True iff ``h1`` and ``purpose`` share a meaningful
    word *or* one is a substring/stem of the other (after
    stripping stopwords + role boilerplate).  The substring pass
    catches common morphology — "coding" matches "code",
    "researcher" matches "research", etc. — without requiring a
    stemmer dependency.
    """
    stopwords = {
        "a", "an", "and", "are", "for", "from", "in", "is", "of",
        "or", "that", "the", "this", "to", "with",
        "role", "agent", "subagent", "task", "tasks",
    }

    def tokens(s: str) -> set[str]:
        out: set[str] = set()
        for word in s.lower().replace("_", " ").split():
            cleaned = "".join(c for c in word if c.isalnum())
            if cleaned and cleaned not in stopwords and len(cleaned) > 2:
                out.add(cleaned)
        return out

    h1_tokens = tokens(h1)
    p_tokens = tokens(purpose)
    if h1_tokens & p_tokens:
        return True
    # Substring pass: any h1 token that contains or is contained
    # by any purpose token (≥ 4 chars on the shorter side to
    # avoid spurious matches like "be" ⊂ "becomes").
    for h in h1_tokens:
        for p in p_tokens:
            shorter, longer = sorted([h, p], key=len)
            if len(shorter) >= 4 and shorter in longer:
                return True
    return False


def _cmd_audit(args: argparse.Namespace) -> int:
    roots = Path(roles_root())
    findings = audit_role(roots, args.name)
    if not findings:
        print(f"role {args.name}: clean")
        return 0
    for code, detail in findings:
        print(
            f"role {args.name}: [{code}] {LINT_CODES[code]} — {detail}",
            file=sys.stderr,
        )
    return 1 if args.strict else 0


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
