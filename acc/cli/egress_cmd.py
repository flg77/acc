"""``acc-cli egress`` — destination policy and brokered credentials.

    acc-cli egress policy [--role ROLE]
    acc-cli egress check <role> <url>
    acc-cli egress journal

What ACC enforces here is that a brokered credential is never in the agent's
environment. Where traffic may go is enforced by the substrate — NetworkPolicy,
an egress proxy, the sandbox — and this check is defence in depth that makes an
honest mistake diagnosable rather than a mysterious timeout.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import egress


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("egress", help="Destination policy and brokered credentials.")
    sp = p.add_subparsers(dest="egress_command", required=True, metavar="ACTION")

    pol = sp.add_parser("policy", help="Show the destination policy.")
    pol.add_argument("--role", default=None)
    pol.add_argument("--json", action="store_true")
    pol.set_defaults(func=_cmd_policy)

    chk = sp.add_parser("check", help="Would this role reach this URL?")
    chk.add_argument("role")
    chk.add_argument("url")
    chk.add_argument("--json", action="store_true")
    chk.set_defaults(func=_cmd_check)

    jr = sp.add_parser("journal", help="Decisions made in this process.")
    jr.set_defaults(func=_cmd_journal)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_policy(args: argparse.Namespace) -> int:
    policy = egress.load_policy()
    roles = (
        {args.role: policy.destinations(args.role)}
        if args.role
        else {r: policy.destinations(r) for r in sorted(policy.roles)}
    )
    if args.json:
        print(json.dumps(
            {r: [d.__dict__ for d in ds] for r, ds in roles.items()}, indent=2
        ))
        return 0

    _safe()
    state = "ON" if egress.enabled() else "off"
    print(f"  brokering: {state}  (enable with {egress.ENABLE_VAR}=1)")
    print(f"  policy:    {egress.policy_path()}")
    print()
    if not roles or not any(roles.values()):
        print("  no destinations permitted for any role — default deny")
        return 0
    for role, destinations in roles.items():
        print(f"  {role}")
        for d in destinations:
            cred = f"  injects {d.credential_env}" if d.credential_env else ""
            print(f"      {d.scheme}://{d.host}{cred}")
    print()
    print("  Destination enforcement belongs to the substrate; this layer makes")
    print("  a mistake diagnosable and keeps brokered credentials out of agents.")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    decision = egress.check(args.role, args.url)
    if args.json:
        print(json.dumps(decision.as_dict(), indent=2))
        return 0 if decision.allowed else 1
    _safe()
    if decision.allowed:
        destination = decision.destination
        print(f"  ALLOWED  {args.role} -> {args.url}")
        if destination and destination.credential_env:
            print(f"      broker injects {destination.credential_env}"
                  f" (the agent never holds it)")
        return 0
    print(f"  DENIED   {args.role} -> {args.url}")
    print(f"      {decision.reason}")
    return 1


def _cmd_journal(args: argparse.Namespace) -> int:
    _safe()
    entries = egress.journal()
    if not entries:
        print("  no egress decisions recorded in this process")
        return 0
    for decision in entries:
        mark = "ALLOW" if decision.allowed else "DENY "
        print(f"  {mark} {decision.role} -> {decision.url}")
        if decision.reason:
            print(f"        {decision.reason}")
    return 0
