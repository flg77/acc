"""``acc-cli oauth`` — connect a person's own account for an ``auth: oauth`` MCP.

    acc-cli oauth connect <provider> [--for PERSON]
    acc-cli oauth complete <provider> --code CODE [--for PERSON]
    acc-cli oauth status <provider> [--for PERSON]
    acc-cli oauth disconnect <provider> [--for PERSON]

The person consents in the provider's **own** screen; ACC never enters a
credential. ``connect`` prints the consent URL and keeps the PKCE verifier sealed;
the provider redirects with a ``code`` that ``complete`` exchanges. Only the refresh
token is stored, sealed with ``ACC_CRED_KEY``, keyed by the person.

``--for`` defaults to the person running the command. A token is minted for the
person behind a task's requester (``source:subject``), so connect for the name your
tasks carry -- ``status`` shows which one that is.

Nothing here prints a token, a code or a verifier.
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("oauth", help="Connect a person's account for an auth: oauth MCP.")
    sp = p.add_subparsers(dest="oauth_command", required=True, metavar="ACTION")

    for name, helptext, func in (
        ("connect", "Print the consent URL for a provider.", _cmd_connect),
        ("complete", "Exchange the code the provider returned.", _cmd_complete),
        ("status", "Is this person connected?", _cmd_status),
        ("disconnect", "Forget this person's token.", _cmd_disconnect),
    ):
        c = sp.add_parser(name, help=helptext)
        c.add_argument("provider")
        c.add_argument("--for", dest="person", default="",
                       help="The person (source:subject). Default: you.")
        if name == "complete":
            c.add_argument("--code", required=True, help="The code from the redirect.")
        c.set_defaults(func=func)


def _person(args: argparse.Namespace) -> str:
    if args.person:
        return args.person.strip()
    from acc.attribution import person_of  # noqa: PLC0415
    from acc.identity import current  # noqa: PLC0415

    return person_of(current().attribution())


def _fail(message: str) -> int:
    print(f"  {message}", file=sys.stderr)
    return 1


def _cmd_connect(args: argparse.Namespace) -> int:
    from acc.credentials import live  # noqa: PLC0415

    who = _person(args)
    try:
        broker = live.broker_for(args.provider)
        cfg = broker._provider(args.provider)
        if not cfg.client_id or not cfg.auth_url:
            return _fail(
                f"{args.provider}: no OAuth client configured -- set "
                f"{args.provider.upper()}_OAUTH_CLIENT_ID (and the auth/token URLs "
                f"for a provider ACC does not know)"
            )
        challenge = broker.start_connect(args.provider, who)
        live.save_pending(args.provider, who, challenge.code_verifier, challenge.state)
    except RuntimeError as exc:
        return _fail(str(exc))
    print(f"  connecting {args.provider} for {who}")
    print("  open this URL, consent in the provider's own screen, then run")
    print(f"  `acc-cli oauth complete {args.provider} --code <code> --for {who}`:")
    print()
    print(f"  {challenge.auth_url}")
    return 0


def _cmd_complete(args: argparse.Namespace) -> int:
    from acc.credentials import live  # noqa: PLC0415

    who = _person(args)
    try:
        pending = live.take_pending(args.provider, who)
        if pending is None:
            return _fail(f"no consent started for {who} on {args.provider}; run connect first")
        broker = live.broker_for(args.provider)
        asyncio.run(broker.complete(args.provider, who, args.code.strip(), pending["verifier"]))
    except RuntimeError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 -- the provider's refusal, legibly
        return _fail(f"{args.provider} refused the exchange ({type(exc).__name__})")
    print(f"  {args.provider} connected for {who}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from acc.credentials import live  # noqa: PLC0415

    who = _person(args)
    try:
        connected = live.broker_for(args.provider).is_connected(args.provider, who)
    except RuntimeError as exc:
        return _fail(str(exc))
    print(f"  {args.provider} for {who}: {'connected' if connected else 'not connected'}")
    return 0 if connected else 1


def _cmd_disconnect(args: argparse.Namespace) -> int:
    from acc.credentials import live  # noqa: PLC0415

    who = _person(args)
    try:
        live.broker_for(args.provider).disconnect(args.provider, who)
    except RuntimeError as exc:
        return _fail(str(exc))
    print(f"  {args.provider} disconnected for {who}")
    return 0
