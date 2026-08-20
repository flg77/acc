"""``acc-cli profile`` — switch a whole deployment posture, deliberately.

    acc-cli profile list
    acc-cli profile show <name>
    acc-cli profile diff <name>
    acc-cli profile validate <name>
    acc-cli profile apply <name> [--dry-run]
    acc-cli profile revert
    acc-cli profile export <name> [--out FILE]
    acc-cli profile import <file> [--overwrite]

Validation always runs before application: a profile that half-applies leaves
the deployment in a state no profile describes, which is worse than either the
old one or the new one.

Posture changes — governance floor, deploy mode, compliance enforcement — are
called out separately, because those are the ones nobody should make by
accident while thinking they are switching a model.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import profiles


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("profile", help="Named deployment profiles.")
    sp = p.add_subparsers(dest="profile_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Show available profiles and the active one.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    show = sp.add_parser("show", help="Print a profile.")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=_cmd_show)

    df = sp.add_parser("diff", help="What applying it would change.")
    df.add_argument("name")
    df.add_argument("--json", action="store_true")
    df.set_defaults(func=_cmd_diff)

    va = sp.add_parser("validate", help="Check it without applying it.")
    va.add_argument("name")
    va.add_argument("--json", action="store_true")
    va.set_defaults(func=_cmd_validate)

    ap = sp.add_parser("apply", help="Validate, then apply.")
    ap.add_argument("name")
    ap.add_argument("--dry-run", action="store_true", help="Show changes, write nothing.")
    ap.set_defaults(func=_cmd_apply)

    rv = sp.add_parser("revert", help="Undo the last apply.")
    rv.set_defaults(func=_cmd_revert)

    ex = sp.add_parser("export", help="Emit a portable profile document.")
    ex.add_argument("name")
    ex.add_argument("--out", default=None, help="Write to a file instead of stdout.")
    ex.set_defaults(func=_cmd_export)

    im = sp.add_parser("import", help="Install an exported profile (does not apply it).")
    im.add_argument("path")
    im.add_argument("--overwrite", action="store_true")
    im.set_defaults(func=_cmd_import)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_list(args: argparse.Namespace) -> int:
    names = profiles.list_profiles()
    active = profiles.active_profile()
    if args.json:
        print(json.dumps({"profiles": names, "active": active}, indent=2))
        return 0
    _safe()
    if not names:
        print(f"  no profiles in {profiles.profiles_dir()}")
        return 0
    for name in names:
        marker = "  <- active" if active and active.get("name") == name else ""
        print(f"  {name}{marker}")
    if active is None:
        print()
        print("  no profile recorded as applied on this deployment")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        profile = profiles.load_profile(args.name)
    except profiles.ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(profile.as_dict(), indent=2))
        return 0
    _safe()
    print(f"  {profile.name}: {profile.description or '(no description)'}")
    for key, value in sorted(profile.settings.items()):
        flag = "  [posture]" if key in profiles.POSTURE_KEYS else ""
        print(f"      {key} = {value!r}{flag}")
    for role, value in sorted(profile.role_models.items()):
        print(f"      role_models.{role} = {value!r}")
    if profile.requires_env:
        print(f"      requires env: {', '.join(profile.requires_env)}")
    return 0


def _print_changes(changes) -> None:
    if not changes:
        print("  no changes — the deployment already matches this profile")
        return
    for change in changes:
        flag = "  [POSTURE]" if change.posture else ""
        print(f"  {change.key}{flag}")
        print(f"      {change.before!r}  ->  {change.after!r}")


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        changes = profiles.diff(profiles.load_profile(args.name))
    except profiles.ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([c.as_dict() for c in changes], indent=2))
        return 0
    _safe()
    _print_changes(changes)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        result = profiles.validate(profiles.load_profile(args.name))
    except profiles.ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return 0 if result.ok else 1
    _safe()
    if result.ok:
        print(f"  {args.name}: valid")
    else:
        for problem in result.problems:
            print(f"  PROBLEM  {problem}")
    if result.missing_env:
        print(f"  missing environment: {', '.join(result.missing_env)}")
        print("  (the profile declares these; agents will fail without them)")
    return 0 if result.ok else 1


def _cmd_apply(args: argparse.Namespace) -> int:
    try:
        profile = profiles.load_profile(args.name)
        changes = profiles.apply(profile, dry_run=args.dry_run)
    except profiles.ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _safe()
    verb = "would change" if args.dry_run else "changed"
    print(f"  {profile.name}: {verb} {len(changes)} setting(s)")
    _print_changes(changes)

    posture = [c for c in changes if c.posture]
    if posture:
        print()
        print("  POSTURE CHANGES — these alter the governance or security floor:")
        for change in posture:
            print(f"      {change.key}: {change.before!r} -> {change.after!r}")

    if not args.dry_run and changes:
        print()
        print("  recorded; `acc-cli profile revert` undoes this")
        print("  agents resolve configuration at boot — restart to pick it up")
    return 0


def _cmd_revert(args: argparse.Namespace) -> int:
    try:
        changes = profiles.revert()
    except profiles.ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _safe()
    print(f"  reverted {len(changes)} setting(s)")
    _print_changes(changes)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        document = profiles.export_profile(args.name)
    except profiles.ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = json.dumps(document, indent=2, sort_keys=True)
    if args.out:
        from pathlib import Path

        Path(args.out).write_text(text, encoding="utf-8")
        _safe()
        print(f"  wrote {args.out}")
        required = document["requires"]["environment"]
        if required:
            print(f"  the receiving site must provide: {', '.join(required)}")
        print("  no credentials are included in this file")
        return 0
    print(text)
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from pathlib import Path

    try:
        document = json.loads(Path(args.path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.path}: {exc}", file=sys.stderr)
        return 2
    try:
        profile = profiles.import_profile(document, overwrite=args.overwrite)
    except profiles.ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _safe()
    print(f"  installed profile {profile.name!r} (not applied)")
    if profile.requires_env:
        print(f"  provide these before applying: {', '.join(profile.requires_env)}")
    print(f"  review with `acc-cli profile diff {profile.name}`")
    return 0
