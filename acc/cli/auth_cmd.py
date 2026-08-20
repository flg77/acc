"""``acc-cli auth`` — several credentials per provider, rotated on throttle.

    acc-cli auth list [<provider>]
    acc-cli auth add <provider> --env VAR
    acc-cli auth remove <provider> --env VAR
    acc-cli auth status [<provider>]
    acc-cli auth reset <provider>

Everything here deals in environment variable **names**. No credential value is
read, printed or stored by this command or by the pool it manages.

``status`` distinguishes *cooling* from *faulted* on purpose: a rested key comes
back on its own, a rejected one never will. A pool that showed both the same way
is how an operator discovers at renewal that only one of four keys ever worked.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import credential_pool as pool_mod


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("auth", help="Credential pools (names only; never values).")
    sp = p.add_subparsers(dest="auth_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Show configured pools.")
    ls.add_argument("provider", nargs="?", default=None)
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    st = sp.add_parser("status", help="Health per credential.")
    st.add_argument("provider", nargs="?", default=None)
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=_cmd_status)

    ad = sp.add_parser("add", help="Add a credential name to a pool.")
    ad.add_argument("provider")
    ad.add_argument("--env", required=True, help="Environment variable NAME.")
    ad.set_defaults(func=_cmd_add)

    rm = sp.add_parser("remove", help="Remove a credential name from a pool.")
    rm.add_argument("provider")
    rm.add_argument("--env", required=True)
    rm.set_defaults(func=_cmd_remove)

    rs = sp.add_parser("reset", help="Clear cooldowns and faults for a provider.")
    rs.add_argument("provider")
    rs.set_defaults(func=_cmd_reset)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_list(args: argparse.Namespace) -> int:
    pools = pool_mod.load_pools()
    if args.provider:
        pools = {k: v for k, v in pools.items() if k == args.provider}
        if not pools:
            print(f"no pool for {args.provider!r}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps({k: v.as_dict() for k, v in pools.items()}, indent=2))
        return 0
    _safe()
    if not pools:
        print(f"  no credential pools configured ({pool_mod.pools_path()})")
        return 0
    for name, pool in sorted(pools.items()):
        print(f"  {name}  (cooldown {pool.cooldown_s:g}s)")
        for entry in pool.entries:
            print(f"      {entry.env_var}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    rows = pool_mod.status(args.provider)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 1 if any(r["health"] == pool_mod.Health.FAULTED for r in rows) else 0
    _safe()
    if not rows:
        print("  no credential pools configured")
        return 0

    width = max(len(r["env_var"]) for r in rows)
    for r in rows:
        if not r["present"]:
            state = "NOT SET"
        elif r["health"] == pool_mod.Health.COOLING:
            state = f"cooling {r['cooldown_remaining_s']:.0f}s"
        elif r["health"] == pool_mod.Health.FAULTED:
            state = "FAULTED"
        else:
            state = "healthy"
        print(f"  {r['provider']:<12} {r['env_var']:<{width}}  {state:<16} uses={r['uses']}")
        if r["reason"]:
            print(f"  {'':<12} {'':<{width}}  {r['reason']}")

    faulted = [r for r in rows if r["health"] == pool_mod.Health.FAULTED]
    if faulted:
        print()
        print("  FAULTED credentials were rejected by the provider. These do not")
        print("  recover on their own — fix or replace them, then `auth reset`.")
    return 1 if faulted else 0


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        pool_mod.add(args.provider, args.env)
    except pool_mod.CredentialPoolError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"  added {args.env} to the {args.provider!r} pool")
    print("  (the name only — set the value in the environment as usual)")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    if not pool_mod.remove(args.provider, args.env):
        print(f"{args.env} is not in the {args.provider!r} pool", file=sys.stderr)
        return 2
    print(f"  removed {args.env} from the {args.provider!r} pool")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    try:
        cleared = pool_mod.reset(args.provider)
    except pool_mod.CredentialPoolError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"  cleared {cleared} cooldown/fault(s) for {args.provider!r}")
    return 0
