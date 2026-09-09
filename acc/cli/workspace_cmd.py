"""``acc-cli workspace`` -- the trusted-directory record (IN-03).

    acc-cli workspace list
    acc-cli workspace check [<dir>]          this directory: trusted, denied, or unknown, and why
    acc-cli workspace trust <dir> [--below]  record it (and write the sentinel the cells check)
    acc-cli workspace deny <dir>
    acc-cli workspace revoke <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("workspace", help="Which directories are trusted as workspaces (the record `acc` asks about).")
    sp = p.add_subparsers(dest="workspace_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Every recorded directory.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    ck = sp.add_parser("check", help="Whether a directory (default: the current one) is trusted, and why.")
    ck.add_argument("path", nargs="?", default=".")
    ck.add_argument("--json", action="store_true")
    ck.set_defaults(func=_cmd_check)

    tr = sp.add_parser("trust", help="Record a directory as trusted and write its sentinel.")
    tr.add_argument("path")
    tr.add_argument("--below", action="store_true", help="This directory and everything under it.")
    tr.add_argument("--force", action="store_true", help="Skip the super-repo warning.")
    tr.set_defaults(func=_cmd_trust)

    dn = sp.add_parser("deny", help="Remember a 'no' for a directory.")
    dn.add_argument("path")
    dn.set_defaults(func=_cmd_deny)

    rv = sp.add_parser("revoke", help="Drop the record for a directory (the sentinel inside it is yours to delete).")
    rv.add_argument("path")
    rv.set_defaults(func=_cmd_revoke)


def _cmd_list(args: argparse.Namespace) -> int:
    from acc import workspace_trust as T  # noqa: PLC0415
    if args.json:
        print(json.dumps([r.__dict__ for r in T.records()], indent=2))
    else:
        print(T.describe())
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from acc import workspace_trust as T  # noqa: PLC0415
    target = Path(args.path).expanduser().resolve()
    rec, how = T.lookup(target)
    state = "unknown" if rec is None else rec.decision
    if args.json:
        print(json.dumps({"path": str(target), "state": state, "matched": how,
                          "record": rec.__dict__ if rec else None}))
    elif rec is None:
        print(f"{target}: unknown — `acc` will ask, or `acc-cli workspace trust {target}`")
    else:
        via = "" if how == "exact" else f" (below {rec.path})"
        print(f"{target}: {rec.decision}{via}, since {rec.since} by {rec.by or '-'}")
    return 0 if rec is not None and rec.trusted else 2


def _cmd_trust(args: argparse.Namespace) -> int:
    from acc import workspace_trust as T  # noqa: PLC0415
    target = Path(args.path).expanduser()
    if not target.is_dir():
        print(f"workspace: not a directory: {target}")
        return 1
    scope = T.SCOPE_BELOW if args.below else T.SCOPE_DIR
    if args.below and not args.force:
        n = T.repositories_below(target)
        if n >= T.SUPER_REPO_THRESHOLD:
            print(f"workspace: {target} holds {n} repositories below it — trusting 'below' trusts them all; "
                  f"re-run with --force to confirm, or trust the one repository you mean")
            return 3
    rec = T.trust(target, scope=scope)
    print(f"workspace: trusted {rec.path} ({rec.scope}) by {rec.by or '-'}; sentinel written")
    return 0


def _cmd_deny(args: argparse.Namespace) -> int:
    from acc import workspace_trust as T  # noqa: PLC0415
    rec = T.deny(Path(args.path).expanduser())
    print(f"workspace: denied {rec.path}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    from acc import workspace_trust as T  # noqa: PLC0415
    if T.revoke(Path(args.path).expanduser()):
        print(f"workspace: revoked {Path(args.path).expanduser().resolve()} (the .acc-workspace-trust file inside is yours to delete)")
        return 0
    print("workspace: no record for that directory")
    return 2
