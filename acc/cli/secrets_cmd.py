"""``acc-cli secrets`` — which credentials each role actually needs.

    acc-cli secrets scope [--role ROLE] [--json]

Reports the derived per-role credential set and, for the environment this
command can see, exactly which credentials scoping **would remove**. That
preview is the point: enforcement is opt-in, and a security feature that
silently breaks a working deployment is one that gets switched off and never
revisited.

Only ever prints credential **names**. Values are never read.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import secret_scope


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("secrets", help="Inspect per-role credential scoping.")
    sp = p.add_subparsers(dest="secrets_command", required=True, metavar="ACTION")

    scope_p = sp.add_parser(
        "scope", help="Show which credentials each role needs, and what scoping removes."
    )
    scope_p.add_argument("--role", default=None, help="Limit to one role.")
    scope_p.add_argument("--json", action="store_true", help="Emit JSON.")
    scope_p.set_defaults(func=_cmd_scope)


def _cmd_scope(args: argparse.Namespace) -> int:
    rows = secret_scope.report([args.role] if args.role else None)
    if args.role and not rows:
        print(f"no role {args.role!r}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "enforcement_enabled": secret_scope.enabled(),
                    "enable_with": secret_scope.ENABLE_VAR,
                    "roles": rows,
                },
                indent=2,
            )
        )
        return 0

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

    state = "ON" if secret_scope.enabled() else "off"
    print(f"credential scoping: {state}  (enable with {secret_scope.ENABLE_VAR}=1)")
    print()
    if not rows:
        print("  no roles mapped in models.yaml role_models")
        return 0

    for row in rows:
        print(f"  {row['role']}")
        for name in row["required"]:
            why = row["reasons"].get(name, "")
            print(f"      needs    {name:<28} {why}")
        for name in row["would_remove"]:
            print(f"      REMOVES  {name:<28} not used by this role")
        if not row["would_remove"]:
            print("      (nothing would be removed in this environment)")
        print()

    if not secret_scope.enabled():
        print(
            "Review the REMOVES lines above before enabling. Derivation sees model\n"
            f"bindings; a skill with its own credential is invisible to it — name\n"
            f"those in {secret_scope.ALLOWLIST_VAR} so they survive scoping."
        )
    return 0
