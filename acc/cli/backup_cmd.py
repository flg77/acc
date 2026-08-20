"""``acc-cli backup`` / ``restore`` — capture a deployment, and put it back.

    acc-cli backup [-o PATH] [--label NAME] [--include config,packages,tracelog]
    acc-cli restore <archive> [--dry-run] [--force] [--allow-missing-secrets]

An archive contains **no secret values**. It records which secret *names* the
deployment needs, and a restore refuses rather than handing back a deployment
that looks restored and cannot authenticate.

``restore`` will not overwrite existing files without ``--force``: the moment
someone restores the wrong archive is the moment they most need it to have
asked.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from acc import backup as backup_mod


def register(sub: argparse._SubParsersAction) -> None:
    b = sub.add_parser("backup", help="Capture configuration, packages and tracelog.")
    b.add_argument("-o", "--output", default=None, help="Archive path.")
    b.add_argument("--label", default="", help="A name recorded in the manifest.")
    b.add_argument(
        "--include", default=",".join(backup_mod.DEFAULT_TIERS),
        help=f"Comma-separated tiers ({', '.join(backup_mod.TIERS)}).",
    )
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=_cmd_backup)

    r = sub.add_parser("restore", help="Restore a deployment archive.")
    r.add_argument("archive")
    r.add_argument("--dry-run", action="store_true", help="Report, write nothing.")
    r.add_argument("--force", action="store_true", help="Acknowledge overwriting files.")
    r.add_argument(
        "--allow-missing-secrets", action="store_true",
        help="Restore even though required credentials are absent.",
    )
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=_cmd_restore)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_backup(args: argparse.Namespace) -> int:
    tiers = [t.strip() for t in args.include.split(",") if t.strip()]
    output = args.output or f"acc-backup-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    try:
        manifest = backup_mod.create(output, tiers=tiers, label=args.label)
    except backup_mod.BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"archive": str(output), **manifest.as_dict()}, indent=2))
        return 0

    _safe()
    print(f"  wrote {output}")
    print(f"  {len(manifest.files)} file(s), tiers: {', '.join(manifest.tiers)}")
    print()
    print("  no secret values are in this archive")
    if manifest.required_secrets:
        print("  the restoring host must provide:")
        for name in manifest.required_secrets:
            print(f"      {name}")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    _safe()
    try:
        result = backup_mod.plan(args.archive)
    except backup_mod.BackupError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json and args.dry_run:
        print(json.dumps(result.as_dict(), indent=2))
        return 0 if result.ok else 1

    manifest = result.manifest
    print(f"  archive: {Path(args.archive).name}")
    print(f"  taken:   {time.strftime('%Y-%m-%d %H:%M', time.localtime(manifest.created_at))}"
          f"{'  label=' + manifest.label if manifest.label else ''}")
    print(f"  acc:     {manifest.acc_version}")
    print()

    if result.would_replace:
        print("  would REPLACE:")
        for path in result.would_replace:
            print(f"      {path}")
    if result.would_create:
        print("  would create:")
        for path in result.would_create:
            print(f"      {path}")
    if result.missing_secrets:
        print()
        print("  required secrets not present here:")
        for name in result.missing_secrets:
            print(f"      {name}")

    if args.dry_run:
        print()
        print("  dry run — nothing was written")
        return 0 if result.ok else 1

    try:
        backup_mod.restore(
            args.archive,
            force=args.force,
            allow_missing_secrets=args.allow_missing_secrets,
        )
    except backup_mod.BackupError as exc:
        print()
        print(str(exc), file=sys.stderr)
        return 1

    print()
    print(f"  restored {len(result.manifest.files)} file(s)")
    print("  agents resolve configuration at boot — restart to pick it up")
    return 0
