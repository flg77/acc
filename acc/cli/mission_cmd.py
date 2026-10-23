"""``acc-cli mission …`` -- the operator's side of "ask once" (058 Phase 1).

* ``list``    -- every mission, newest first, with its live status.
* ``patterns`` -- lesson candidates from closed plans (059); proposals only.
* ``show``    -- one mission's report: what it ran, what it backed up.
* ``close``   -- end a mission; prints the closing report (the deletions it
  backed up are still recoverable).
* ``revoke``  -- end a mission now, as a withdrawal of consent.
* ``purge``   -- the final allowance (058 Q1): delete the backed-up items.
* ``restore`` -- put the backed-up items back where they were.

``purge`` and ``restore`` touch the workspace.  A mission records the path the
agent saw (``/workspace/<project>`` in a container); ``--host-dir`` maps that
mount to the directory on this host (``ACC_WORKSPACE_HOST_DIR`` by default).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    mp = sub.add_parser("mission", help="List, close and settle 'ask once' missions.")
    ms = mp.add_subparsers(dest="mission_command", required=True, metavar="ACTION")

    ls = ms.add_parser("list", help="List missions.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    pt = ms.add_parser("patterns", help="Lesson candidates from closed plans (059): "
                                        "roles planned but not needed, or needed but not planned.")
    pt.add_argument("--min-missions", type=int, default=3)
    pt.add_argument("--json", action="store_true")
    pt.set_defaults(func=_cmd_patterns)

    for verb, helptext in (
        ("show", "Show a mission's report."),
        ("close", "Close a mission and print its report."),
        ("revoke", "Revoke a mission now."),
        ("purge", "Delete the items a mission backed up (the final allowance)."),
        ("restore", "Put the items a mission backed up back where they were."),
    ):
        p = ms.add_parser(verb, help=helptext)
        p.add_argument("mission_id")
        p.add_argument("--by", default="cli:operator", help="Who closes / revokes it.")
        p.add_argument("--host-dir", default=None,
                       help="Host directory of the agents' /workspace mount "
                            "(default: $ACC_WORKSPACE_HOST_DIR).")
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=lambda a, v=verb: _cmd_one(a, v))


def _store():
    from acc.config import load_config
    from acc.mission import MissionStore

    cfg = load_config()
    try:
        from acc.agent import _build_redis_client
        redis_client = _build_redis_client(cfg)
    except Exception:  # noqa: BLE001
        redis_client = None
    if redis_client is None:
        print("mission: no Redis configured -- missions live in the agents' Redis", file=sys.stderr)
    return MissionStore(redis_client, cfg.agent.collective_id)


def _host_view(m, host_dir: str | None):
    """*m* with its workspace mapped from the agents' mount to this host."""
    host = host_dir or os.environ.get("ACC_WORKSPACE_HOST_DIR", "")
    if not host or not m.workspace:
        return m
    mount = os.environ.get("ACC_WORKSPACE_MOUNT", "/workspace").rstrip("/")
    ws = m.workspace.replace("\\", "/")
    if ws == mount or ws.startswith(mount + "/"):
        m.workspace = str(Path(host) / ws[len(mount):].lstrip("/"))
    return m


def _cmd_list(args: argparse.Namespace) -> int:
    store = _store()
    missions = asyncio.run(store.all())
    if args.json:
        print(json.dumps([dict(m.to_dict(), live_status=m.live_status()) for m in missions], indent=2))
        return 0
    if not missions:
        print("no missions")
        return 0
    for m in missions:
        print(f"{m.mission_id}  {m.live_status():<9}  {m.used_calls:>3}/{m.max_calls} calls  "
              f"{len(m.quarantine)} backed up  roles={','.join(m.roles_used) or '-'}  "
              f"by={m.approver or '?'}")
    return 0


def _cmd_patterns(args: argparse.Namespace) -> int:
    """Read every ``mission-*`` tracelog session's PLAN_CLOSED records and
    print lesson candidates.  Candidates only: nothing is applied."""
    from acc import plan_card, tracelog

    closed = [
        r for sid in tracelog.list_sessions() if sid.startswith("mission-")
        for r in tracelog.load_session(sid)
        if r.get("kind") == tracelog.KIND_PLAN and r.get("event") == plan_card.EVENT_CLOSED
    ]
    found = plan_card.lesson_candidates(closed, min_missions=args.min_missions)
    if args.json:
        print(json.dumps(found, indent=2))
        return 0
    if not found:
        print(f"no lesson candidates ({len(closed)} closed plan(s) read)")
        return 0
    for c in found:
        what = "planned but not needed" if c["kind"] == "planned_not_needed" else "needed but not planned"
        print(f"{c['role']}: {what} in {c['missions']} of {c['of']} missions "
              f"(evidence: {', '.join(c['evidence'][:5])})")
    return 0


def _cmd_one(args: argparse.Namespace, verb: str) -> int:
    from acc import mission as M

    store = _store()
    m = asyncio.run(store.get(args.mission_id))
    if m is None:
        print(f"mission {args.mission_id}: not found", file=sys.stderr)
        return 1
    if verb == "close":
        m = asyncio.run(store.end(m.mission_id, status=M.STATUS_CLOSED, by=args.by)) or m
    elif verb == "revoke":
        m = asyncio.run(store.end(m.mission_id, status=M.STATUS_REVOKED, by=args.by)) or m
    elif verb in ("purge", "restore"):
        if m.active:
            print(f"mission {m.mission_id} is still active -- close it first", file=sys.stderr)
            return 1
        view = _host_view(M.Mission.from_dict(m.to_dict()), args.host_dir)
        if verb == "purge":
            n = M.purge(view)
            m.quarantine = []
            asyncio.run(store.save(m))
            print(f"purged {n} backed-up item(s) of {m.mission_id}")
        else:
            done = M.restore(view)
            print(f"restored {len(done)} item(s): {', '.join(done) or '-'}")
        return 0
    if args.json:
        print(json.dumps(dict(m.to_dict(), live_status=m.live_status()), indent=2))
    else:
        print("\n".join(M.report(m)))
    return 0
