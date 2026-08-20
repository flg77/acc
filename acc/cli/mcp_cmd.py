"""``acc-cli mcp`` — inspect, diagnose and gate MCP servers.

    acc-cli mcp list [--role ROLE]
    acc-cli mcp test <server>
    acc-cli mcp add <server> --url URL [--api-key-env NAME]
    acc-cli mcp remove <server>
    acc-cli mcp tools <server> [--role ROLE] [--enable T | --disable T]

Host overrides only ever **subtract**. A role's governed declaration is the
ceiling; this command can lower it for a host and cannot raise it. Where
something is unavailable, the output names which layer said no — host, role or
manifest — because those need different fixes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from acc import mcp_admin


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("mcp", help="Inspect and gate MCP servers.")
    sp = p.add_subparsers(dest="mcp_command", required=True, metavar="ACTION")

    list_p = sp.add_parser("list", help="Show servers, their source and state.")
    list_p.add_argument("--role", default=None, help="Show the effective view for a role.")
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=_cmd_list)

    test_p = sp.add_parser("test", help="Connect and list tools; report where it fails.")
    test_p.add_argument("server")
    test_p.add_argument("--json", action="store_true")
    test_p.set_defaults(func=_cmd_test)

    add_p = sp.add_parser("add", help="Register an operator-added server.")
    add_p.add_argument("server")
    add_p.add_argument("--url", default="", help="Base URL (required for http transport).")
    add_p.add_argument("--transport", default="http", choices=["http", "stdio"])
    add_p.add_argument("--api-key-env", default="", help="Env var holding the bearer token.")
    add_p.add_argument("--purpose", default="", help="One-line description.")
    add_p.set_defaults(func=_cmd_add)

    rm_p = sp.add_parser("remove", help="Remove an operator-added server.")
    rm_p.add_argument("server")
    rm_p.set_defaults(func=_cmd_remove)

    tools_p = sp.add_parser("tools", help="Show or gate individual tools.")
    tools_p.add_argument("server")
    tools_p.add_argument("--role", default=None, help="Show the effective view for a role.")
    tools_p.add_argument("--enable", default=None, help="Remove a host-level block.")
    tools_p.add_argument("--disable", default=None, help="Block a tool on this host.")
    tools_p.add_argument("--json", action="store_true")
    tools_p.set_defaults(func=_cmd_tools)

    en_p = sp.add_parser("enable", help="Re-enable a server on this host.")
    en_p.add_argument("server")
    en_p.set_defaults(func=_cmd_enable)

    dis_p = sp.add_parser("disable", help="Disable a server on this host.")
    dis_p.add_argument("server")
    dis_p.set_defaults(func=_cmd_disable)


# ---------------------------------------------------------------------------


def _registry():
    from acc.mcp.registry import MCPRegistry  # noqa: PLC0415

    reg = MCPRegistry()
    try:
        reg.load_from()
    except Exception as exc:  # noqa: BLE001
        print(f"warning: MCP registry unreadable ({exc})", file=sys.stderr)
    return reg


def _role_allowed(role_name: str | None) -> list[str] | None:
    """The MCP servers a role declares, or None when not asking about a role."""
    if not role_name:
        return None
    try:
        from acc.config import load_config  # noqa: PLC0415

        cfg = load_config()
        return list(cfg.role_definition.allowed_mcps or [])
    except Exception:  # noqa: BLE001
        return []


def _safe_stdout() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_list(args: argparse.Namespace) -> int:
    reg = _registry()
    ov = mcp_admin.load_overrides()
    ids = sorted(set(reg.list_server_ids()) | set(ov.added))
    role_allowed = _role_allowed(args.role)

    rows = []
    for server_id in ids:
        decision = mcp_admin.server_decision(
            server_id, role_allowed=role_allowed, overrides=ov, known=ids
        )
        rows.append(
            {
                "server_id": server_id,
                "source": mcp_admin.source_of(
                    server_id, registry_ids=reg.list_server_ids(), overrides=ov
                ),
                **decision.as_dict(),
            }
        )

    if args.json:
        print(json.dumps({"role": args.role, "servers": rows}, indent=2))
        return 0

    _safe_stdout()
    if not rows:
        print("  no MCP servers registered")
        return 0
    width = max(len(r["server_id"]) for r in rows)
    for r in rows:
        state = "available" if r["allowed"] else f"BLOCKED by {r['denied_by']}"
        print(f"  {r['server_id']:<{width}}  {r['source']:<9} {state}")
        if not r["allowed"] and r["reason"]:
            print(f"  {'':<{width}}  {'':<9} {r['reason']}")
    print()
    print("Host overrides only subtract; a role's declaration is the ceiling.")
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    reg = _registry()
    manifest = reg.manifest(args.server)
    if manifest is None:
        print(f"no MCP server {args.server!r} in the registry", file=sys.stderr)
        return 2

    result = asyncio.run(mcp_admin.test_server(manifest))
    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return 0 if result.ok else 1

    _safe_stdout()
    if result.ok:
        print(f"  {result.server_id}: ok — {len(result.tools)} tool(s)")
        for name in result.tools:
            print(f"      {name}")
        return 0

    explain = {
        "connect": "could not reach the server or the handshake failed",
        "auth": "the server rejected our credentials",
        "tools": "connected, but listing tools failed",
    }
    print(f"  {result.server_id}: FAILED at {result.stage} — {explain.get(result.stage, '')}")
    if result.detail:
        print(f"      {result.detail}")
    return 1


def _cmd_add(args: argparse.Namespace) -> int:
    try:
        definition = mcp_admin.add_server(
            args.server,
            url=args.url,
            transport=args.transport,
            api_key_env=args.api_key_env,
            purpose=args.purpose,
        )
    except mcp_admin.MCPAdminError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"added {definition['server_id']!r} ({definition['transport']})")
    print(f"  written to {mcp_admin.overrides_path()}")
    print("  a role must still declare it in allowed_mcps before any agent can use it")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    if not mcp_admin.remove_server(args.server):
        print(
            f"{args.server!r} is not an operator-added server. Packaged servers "
            f"keep their own trust rules and are not removed here; disable it "
            f"instead with `acc-cli mcp disable {args.server}`.",
            file=sys.stderr,
        )
        return 2
    print(f"removed {args.server!r}")
    return 0


def _cmd_enable(args: argparse.Namespace) -> int:
    mcp_admin.enable_server(args.server)
    print(f"{args.server!r} re-enabled on this host")
    print("  (this restores the governed baseline; it grants nothing)")
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    mcp_admin.disable_server(args.server)
    print(f"{args.server!r} disabled on this host — no role can use it here")
    return 0


def _cmd_tools(args: argparse.Namespace) -> int:
    reg = _registry()
    manifest = reg.manifest(args.server)
    if manifest is None:
        print(f"no MCP server {args.server!r} in the registry", file=sys.stderr)
        return 2

    if args.disable:
        mcp_admin.disable_tool(args.server, args.disable)
        print(f"{args.server}:{args.disable} disabled on this host")
        return 0
    if args.enable:
        mcp_admin.enable_tool(args.server, args.enable)
        print(f"{args.server}:{args.enable} host block removed")
        print("  (if the manifest or the role denies it, it stays denied)")
        return 0

    ov = mcp_admin.load_overrides()
    advertised = list(getattr(manifest, "allowed_tools", None) or [])
    if not advertised:
        result = asyncio.run(mcp_admin.test_server(manifest))
        advertised = result.tools
        if not result.ok:
            print(
                f"  could not list tools ({result.stage}: {result.detail})",
                file=sys.stderr,
            )
            return 1

    decisions = mcp_admin.effective_tools(
        args.server, advertised, manifest=manifest, overrides=ov
    )
    if args.json:
        print(
            json.dumps(
                {k: v.as_dict() for k, v in decisions.items()}, indent=2, sort_keys=True
            )
        )
        return 0

    _safe_stdout()
    if not decisions:
        print("  no tools advertised")
        return 0
    width = max(len(t) for t in decisions)
    for tool, decision in decisions.items():
        state = "allowed" if decision.allowed else f"BLOCKED by {decision.denied_by}"
        print(f"  {tool:<{width}}  {state}")
        if not decision.allowed and decision.reason:
            print(f"  {'':<{width}}  {decision.reason}")
    return 0
