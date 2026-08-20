"""``acc-cli status`` — what is running, and on what.

    acc-cli status [--json] [--role ROLE] [--listen SECONDS]

Exits non-zero when the collective is not healthy, so it works as a readiness
probe over SSH with no TTY. It is read-only and makes no changes.

The output distinguishes *not-deployed* from *failed* on purpose: a role
configuration declares but nothing is running has not crashed, and sending an
operator to read logs that do not exist is a wasted afternoon.
"""

from __future__ import annotations

import argparse
import sys

from acc import status as status_mod


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("status", help="Report per-agent and collective health.")
    p.add_argument("--json", action="store_true", help="Emit a machine-readable report.")
    p.add_argument("--role", default=None, help="Limit to one role.")
    p.add_argument(
        "--listen",
        type=float,
        default=status_mod.DEFAULT_LISTEN_S,
        help=(
            "Seconds to listen for heartbeats "
            f"(default {status_mod.DEFAULT_LISTEN_S:.0f}s — slightly over one "
            "heartbeat interval so a healthy agent cannot be missed)."
        ),
    )
    p.add_argument(
        "--collective", default=None, help="Collective id (default: $ACC_COLLECTIVE_ID)."
    )
    p.set_defaults(func=_cmd_status)


_CONDITION_LABEL = {
    "running": "running",
    "stale": "STALE",
    "failed": "FAILED",
    "not-deployed": "not deployed",
}


def _cmd_status(args: argparse.Namespace) -> int:
    report = status_mod.collect(args.collective, listen_s=args.listen)

    agents = report.agents
    if args.role:
        agents = [a for a in agents if a.role == args.role]
        if not agents:
            print(f"no role {args.role!r} in this collective", file=sys.stderr)
            return 2

    if args.json:
        data = report.as_dict()
        if args.role:
            data["agents"] = [a.as_dict() for a in agents]
        import json  # noqa: PLC0415

        print(json.dumps(data, indent=2))
        return 0 if report.healthy else 1

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

    print(f"collective: {report.collective_id}")
    bus = "reachable" if report.bus_reachable else f"UNREACHABLE ({report.bus_detail})"
    print(f"  bus:            {bus}")
    if report.memory_reachable is None:
        print(f"  working memory: unknown ({report.memory_detail})")
    else:
        mem = "reachable" if report.memory_reachable else f"UNREACHABLE ({report.memory_detail})"
        print(f"  working memory: {mem}")
    if report.oversight_pending is not None:
        print(f"  oversight queue: {report.oversight_pending} pending")
    print()

    if not report.bus_reachable:
        # Still report what configuration declares. "I can tell you nothing" is
        # the least useful answer during an incident.
        print("  (bus unreachable — the rows below are what configuration declares,")
        print("   not confirmed running state)")
        print()

    if agents:
        rw = max(len(a.role) for a in agents)
        cw = max(len(_CONDITION_LABEL.get(a.condition, a.condition)) for a in agents)
        for a in agents:
            label = _CONDITION_LABEL.get(a.condition, a.condition)
            model = a.model or a.model_id or "-"
            age = f"{a.age_s:.0f}s ago" if a.age_s is not None else "never seen"
            chain = f"  chain:{'>'.join(a.chain)}" if len(a.chain) > 1 else ""
            print(
                f"  {a.role:<{rw}}  {label:<{cw}}  "
                f"{(a.backend or '-'):<14} {model:<34} {age}{chain}"
            )
    else:
        print("  no roles mapped in models.yaml role_models")

    missing = [n for n, present in sorted(report.key_names_present.items()) if not present]
    if missing:
        print()
        print(f"  missing key names: {', '.join(missing)}")

    print()
    unhealthy = [a for a in agents if not a.healthy]
    if report.healthy:
        print(f"healthy — {len(agents)} agent(s) running")
    else:
        absent = [a.role for a in unhealthy if a.condition == "not-deployed"]
        broken = [a.role for a in unhealthy if a.condition != "not-deployed"]
        parts = []
        if broken:
            parts.append(f"{len(broken)} unhealthy ({', '.join(broken)})")
        if absent:
            parts.append(f"{len(absent)} not deployed ({', '.join(absent)})")
        if not report.bus_reachable:
            parts.append("bus unreachable")
        print("; ".join(parts) or "not healthy")
    return 0 if report.healthy else 1
