"""``acc-cli config`` — inspect and edit ACC's configuration files.

    acc-cli config show [--file ID] [--json]
    acc-cli config get <dotted.key> [--json]
    acc-cli config set <dotted.key> <value> [--dry-run]
    acc-cli config unset <dotted.key> [--dry-run]
    acc-cli config path [<file>]
    acc-cli config check [--all] [--json]
    acc-cli config migrate [--file ID] [--dry-run]

Every command reports **which file** a key lives in.  That is not decoration:
only some of the five surfaces are operator-owned, only some are gitignored,
and ``.env`` is never written by this command at all.

Values of secret-bearing keys are never printed.  ``show`` and ``get`` render
them as ``<set>`` / ``<unset>`` so an operator can confirm a credential is
present without the value reaching a terminal, a screenshot or a log.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import configschema as cs
from acc import configstore as st

_REDACTED = "<set>"
_ABSENT = "<unset>"


def register(sub: argparse._SubParsersAction) -> None:
    cfg = sub.add_parser("config", help="Inspect and edit ACC configuration files.")
    cfg_sub = cfg.add_subparsers(dest="config_command", required=True, metavar="ACTION")

    show_p = cfg_sub.add_parser("show", help="Print the merged configuration.")
    show_p.add_argument("--file", default=None, help="Limit to one file (see `config path`).")
    show_p.add_argument("--json", action="store_true", help="Emit JSON.")
    show_p.set_defaults(func=_cmd_show)

    get_p = cfg_sub.add_parser("get", help="Print one key and the file that owns it.")
    get_p.add_argument("key", help="Dotted key, e.g. llm.backend")
    get_p.add_argument("--json", action="store_true", help="Emit JSON.")
    get_p.set_defaults(func=_cmd_get)

    set_p = cfg_sub.add_parser("set", help="Set one key, preserving comments.")
    set_p.add_argument("key", help="Dotted key, e.g. llm.backend")
    set_p.add_argument("value", help="New value; parsed according to the schema.")
    set_p.add_argument("--dry-run", action="store_true", help="Show the diff, write nothing.")
    set_p.set_defaults(func=_cmd_set)

    unset_p = cfg_sub.add_parser("unset", help="Remove one key.")
    unset_p.add_argument("key", help="Dotted key.")
    unset_p.add_argument("--dry-run", action="store_true", help="Show the diff, write nothing.")
    unset_p.set_defaults(func=_cmd_unset)

    path_p = cfg_sub.add_parser("path", help="Where a configuration file resolves to.")
    path_p.add_argument("file", nargs="?", default=None, help="File id; omit for all.")
    path_p.set_defaults(func=_cmd_path)

    check_p = cfg_sub.add_parser("check", help="Report configuration faults.")
    check_p.add_argument(
        "--all", action="store_true", help="Include notes for keys left at their default."
    )
    check_p.add_argument("--json", action="store_true", help="Emit JSON.")
    check_p.set_defaults(func=_cmd_check)

    mig_p = cfg_sub.add_parser(
        "migrate", help="Write options that are absent, using their defaults."
    )
    mig_p.add_argument("--file", default="acc-config", help="File id (default: acc-config).")
    mig_p.add_argument("--dry-run", action="store_true", help="List additions, write nothing.")
    mig_p.set_defaults(func=_cmd_migrate)


# --------------------------------------------------------------------------


def _render(value: object, secret: bool, present: bool = True) -> str:
    if secret:
        return _REDACTED if present and value not in (None, "", False) else _ABSENT
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def _redact_tree(data: object, prefix: str = "") -> object:
    """Recursively replace secret-bearing values before display."""
    if not isinstance(data, dict):
        return data
    out: dict[str, object] = {}
    for k, v in data.items():
        dotted = f"{prefix}{k}"
        key = cs.find(dotted)
        if key is not None and key.secret:
            out[k] = _REDACTED if v not in (None, "", False) else _ABSENT
        elif isinstance(v, dict):
            out[k] = _redact_tree(v, f"{dotted}.")
        else:
            out[k] = v
    return out


def _cmd_show(args: argparse.Namespace) -> int:
    if args.file:
        try:
            spec = cs.file_by_id(args.file)
        except KeyError as exc:
            print(exc, file=sys.stderr)
            return 2
        data: object = _redact_tree(
            st.read(spec.id), "env." if spec.id == "env" else ""
        )
    else:
        data = _redact_tree(st.merged())

    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0
    print(_yaml_ish(data))
    return 0


def _yaml_ish(data: object, indent: int = 0) -> str:
    """A compact block rendering — enough to read, not a config file."""
    lines: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            pad = " " * indent
            if isinstance(v, dict) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_yaml_ish(v, indent + 2))
            else:
                rendered = json.dumps(v) if isinstance(v, (list, dict)) else v
                lines.append(f"{pad}{k}: {rendered}")
    else:
        lines.append(f"{' ' * indent}{data}")
    return "\n".join(x for x in lines if x)


def _cmd_get(args: argparse.Namespace) -> int:
    r = st.get(args.key)
    if r.key is None and not r.present:
        print(
            f"{args.key}: not a known key and not set in any file.\n"
            f"`acc-cli config check` lists what this release knows about.",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(
            json.dumps(
                {
                    "key": r.path,
                    "value": _REDACTED if r.secret else r.value,
                    "file": r.file,
                    "path": str(r.file_path),
                    "set": r.present,
                },
                indent=2,
                default=str,
            )
        )
        return 0
    print(f"{r.path} = {_render(r.value, r.secret, r.present)}")
    print(f"  file:  {r.file or '(unknown)'} ({r.file_path})")
    print(f"  set:   {'yes' if r.present else 'no — showing the default'}")
    if r.key is not None:
        print(f"  type:  {r.key.type}{'  choices: ' + ', '.join(r.key.choices) if r.key.choices else ''}")
        if r.key.description:
            print(f"  about: {r.key.description}")
    return 0



def _cmd_set(args: argparse.Namespace) -> int:
    try:
        change = st.set_key(args.key, args.value, dry_run=args.dry_run)
    except st.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    verb = "would set" if args.dry_run else ("added" if change.action == "add" else "set")
    print(f"{verb} {change.path} in {change.file_path}")
    for line in change.diff:
        print(f"  {line}")
    return 0


def _cmd_unset(args: argparse.Namespace) -> int:
    try:
        change = st.unset_key(args.key, dry_run=args.dry_run)
    except st.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{'would remove' if args.dry_run else 'removed'} {change.path} from {change.file_path}")
    for line in change.diff:
        print(f"  {line}")
    return 0


def _cmd_path(args: argparse.Namespace) -> int:
    ids = [args.file] if args.file else [f.id for f in cs.FILES]
    for file_id in ids:
        try:
            spec = cs.file_by_id(file_id)
        except KeyError as exc:
            print(exc, file=sys.stderr)
            return 2
        path = cs.resolve_path(spec.id)
        flags = []
        if not spec.writable:
            flags.append("read-only here")
        if spec.secret_bearing:
            flags.append("secret-bearing")
        if not path.is_file():
            flags.append("MISSING")
        elif path.name.endswith(".example"):
            flags.append("template fallback")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{spec.id:<12} {path}{suffix}")
    return 0


_LEVEL_ORDER = {"error": 0, "warning": 1, "note": 2}


def _cmd_check(args: argparse.Namespace) -> int:
    findings = st.check()
    if not args.all:
        findings = [f for f in findings if f.level != "note"]
    findings.sort(key=lambda f: (_LEVEL_ORDER.get(f.level, 9), f.file, f.path))

    if args.json:
        print(
            json.dumps(
                [
                    {"level": f.level, "file": f.file, "key": f.path, "message": f.message}
                    for f in findings
                ],
                indent=2,
            )
        )
    else:
        for f in findings:
            where = f"{f.file}:{f.path}" if f.path else f.file
            print(f"{f.level.upper():<8} {where}\n         {f.message}")
        errors = sum(1 for f in findings if f.level == "error")
        warnings = sum(1 for f in findings if f.level == "warning")
        if not findings:
            print("no configuration faults found")
        else:
            print(f"\n{errors} error(s), {warnings} warning(s)")
        if not args.all:
            hidden = sum(1 for f in st.check() if f.level == "note")
            if hidden:
                print(
                    f"{hidden} key(s) left at their default — `config check --all` "
                    f"lists them, `config migrate` writes them out"
                )
    return 1 if any(f.level == "error" for f in findings) else 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    try:
        changes = st.migrate(args.file, dry_run=args.dry_run)
    except (KeyError, st.ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not changes:
        print(f"{args.file}: nothing to add — every known option is already present")
        return 0
    verb = "would add" if args.dry_run else "added"
    print(f"{verb} {len(changes)} option(s) to {changes[0].file_path}:")
    for c in changes:
        print(f"  {c.path} = {c.after!r}")
    if not args.dry_run:
        print("\nExisting values were not touched.")
    return 0
