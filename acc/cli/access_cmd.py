"""``acc-cli access`` — who may ask this collective for work.

    acc-cli access whoami
    acc-cli access list
    acc-cli access admit <subject> --channel CH [--scope S] [--tier T]
    acc-cli access revoke <subject> --channel CH
    acc-cli access check <subject> --channel CH [--scope S]

Identity comes from the substrate: real RBAC inside OpenShift, system
authentication at the edge, the existing session in the web GUI. ACC does not
define a fourth identity model.

External requesters — a chat account, an inbound webhook — are the exception,
because no substrate vouches for them. They are default deny and must be
admitted by an explicit operator action.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from acc import channel_access, identity


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("access", help="Who may ask this collective for work.")
    sp = p.add_subparsers(dest="access_command", required=True, metavar="ACTION")

    who = sp.add_parser("whoami", help="The identity this process is acting as.")
    who.add_argument("--json", action="store_true")
    who.set_defaults(func=_cmd_whoami)

    ls = sp.add_parser("list", help="Admitted external requesters.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    ad = sp.add_parser("admit", help="Admit an external requester.")
    ad.add_argument("subject")
    ad.add_argument("--channel", required=True)
    ad.add_argument("--scope", default="", help="Limit to one scope (e.g. direct).")
    ad.add_argument("--tier", default=identity.Tier.REQUESTER,
                    choices=[identity.Tier.VIEWER, identity.Tier.REQUESTER])
    ad.add_argument("--note", default="")
    ad.set_defaults(func=_cmd_admit)

    rv = sp.add_parser("revoke", help="Revoke access (effective immediately).")
    rv.add_argument("subject")
    rv.add_argument("--channel", required=True)
    rv.set_defaults(func=_cmd_revoke)

    ck = sp.add_parser("check", help="Would this requester be admitted?")
    ck.add_argument("subject")
    ck.add_argument("--channel", required=True)
    ck.add_argument("--scope", default="")
    ck.set_defaults(func=_cmd_check)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_whoami(args: argparse.Namespace) -> int:
    principal = identity.current()
    if args.json:
        print(json.dumps(principal.as_dict(), indent=2))
        return 0
    _safe()
    print(f"  subject: {principal.subject}")
    print(f"  source:  {principal.source}  ({'vouched by the substrate' if principal.vouched else 'unvouched'})")
    print(f"  tier:    {principal.tier}")
    if principal.groups:
        print(f"  groups:  {', '.join(principal.groups[:6])}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    grants = identity.load_grants()
    if args.json:
        print(json.dumps([g.as_dict() for g in grants], indent=2))
        return 0
    _safe()
    if not grants:
        print("  no external requesters admitted — default deny")
        return 0
    for g in grants:
        when = time.strftime("%Y-%m-%d", time.localtime(g.admitted_at)) if g.admitted_at else "?"
        scope = f" scope={g.scope}" if g.scope else ""
        by = f" by {g.admitted_by}" if g.admitted_by else ""
        print(f"  {g.subject:<24} {g.channel:<10} {g.tier:<10}{scope}  admitted {when}{by}")
        if g.note:
            print(f"      {g.note}")
    return 0


def _cmd_admit(args: argparse.Namespace) -> int:
    me = identity.current()
    try:
        grant = identity.admit(
            args.subject, args.channel, tier=args.tier, scope=args.scope,
            admitted_by=me.subject, note=args.note,
        )
    except identity.AccessError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _safe()
    print(f"  admitted {grant.subject!r} on {grant.channel} as {grant.tier}")
    print(f"  recorded against {me.attribution()}")
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    if not identity.revoke(args.subject, args.channel):
        print(f"{args.subject!r} is not admitted on {args.channel!r}", file=sys.stderr)
        return 2
    _safe()
    print(f"  revoked {args.subject!r} on {args.channel} — effective immediately")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    admission = channel_access.admit_request(
        channel_access.InboundRequest(
            channel=args.channel, subject=args.subject, scope=args.scope
        )
    )
    _safe()
    if admission.allowed:
        print(f"  ADMITTED  {args.subject} on {args.channel} ({admission.principal.tier})")
        return 0
    print(f"  DENIED    {args.subject} on {args.channel}")
    print(f"      {admission.reason}")
    return 1
