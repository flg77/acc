"""``acc-cli grants …`` and ``acc-cli posture …`` (vault proposal 058, Phase 5).

grants:
* ``detect``  -- repeated, consistent approvals (``acc.approval_patterns``)
  over the queue's decision history, each as a proposal with its evidence.
* ``accept <evidence-hash> --by <you>`` -- give a standing grant for exactly
  that role and target.  Refused while the posture scan has a HIGH finding.
* ``reject <evidence-hash>`` -- the same evidence is not proposed again.
* ``list`` / ``revoke <grant-id> --by <you>`` (a host grant also leaves the
  agent's OpenShell network policy).
* ``add --role <role> --target <kind:target> --by <you>`` -- give a grant
  directly (vault 060: "granted once by the user").  Targets: ``skill:<id>``,
  ``mcp:<server.tool>``, ``host:<name>[:<port>]``.  A host grant is added to
  the agent's OpenShell network policy.
* ``sync-sandbox`` -- re-apply every active host grant to the sandbox policy.

posture:
* ``scan [--sarif FILE] [--fail-on LEVEL]`` -- the agent-configuration posture scan
  (EC-02; the 061 role QA gate with ``--fail-on MEDIUM``) over
  this checkout and the installed packs; exit 1 when a finding is at LEVEL or
  worse (default HIGH).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    gp = sub.add_parser("grants", help="Standing grants from repeated approvals (a person gives each).")
    gs = gp.add_subparsers(dest="grants_command", required=True, metavar="ACTION")
    d = gs.add_parser("detect", help="Repeated approvals worth a standing grant (proposals).")
    d.add_argument("--threshold", type=int, default=None)
    d.add_argument("--min-window-days", type=float, default=None)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_detect)
    a = gs.add_parser("accept", help="Give a standing grant for one proposal.")
    a.add_argument("evidence_hash")
    a.add_argument("--by", required=True, help="Your name: who gives the grant.")
    a.add_argument("--threshold", type=int, default=None)
    a.add_argument("--min-window-days", type=float, default=None)
    a.set_defaults(func=_cmd_accept)
    r = gs.add_parser("reject", help="Do not propose this evidence again.")
    r.add_argument("evidence_hash")
    r.add_argument("--reason", default="")
    r.set_defaults(func=_cmd_reject)
    ls = gs.add_parser("list", help="List standing grants.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)
    rv = gs.add_parser("revoke", help="Revoke a standing grant.")
    rv.add_argument("grant_id")
    rv.add_argument("--by", required=True)
    rv.set_defaults(func=_cmd_revoke)
    ad = gs.add_parser("add", help="Give a grant directly (skill:, mcp: or host: target).")
    ad.add_argument("--role", required=True, help="The role the grant is for (as the dispatcher names it).")
    ad.add_argument("--target", required=True, help="skill:<id> | mcp:<server.tool> | host:<name>[:<port>]")
    ad.add_argument("--by", required=True, help="Your name: who gives the grant.")
    ad.set_defaults(func=_cmd_add)
    sy = gs.add_parser("sync-sandbox", help="Re-apply active host grants to the OpenShell policy.")
    sy.set_defaults(func=_cmd_sync_sandbox)

    pp = sub.add_parser("posture", help="Agent-configuration posture scan (EC-02).")
    ps = pp.add_subparsers(dest="posture_command", required=True, metavar="ACTION")
    sc = ps.add_parser("scan", help="Scan roles, agentsets and the environment.")
    sc.add_argument("--root", default=None, help="Checkout to scan (default: this one).")
    sc.add_argument("--sarif", default=None, help="Also write a SARIF 2.1.0 log here.")
    sc.add_argument("--fail-on", default="HIGH", type=str.upper,
                    choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    help="Exit 1 when a finding is at this severity or worse (default HIGH; "
                         "the role QA gate uses MEDIUM).")
    sc.add_argument("--no-installed", action="store_true",
                    help="Scan only this checkout, not the installed packs' roles.")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(func=_cmd_scan)


def _backends():
    from acc.config import load_config
    from acc.grants import GrantStore
    from acc.oversight import HumanOversightQueue

    cfg = load_config()
    try:
        from acc.agent import _build_redis_client
        redis_client = _build_redis_client(cfg)
    except Exception:  # noqa: BLE001
        redis_client = None
    cid = cfg.agent.collective_id
    return HumanOversightQueue(redis_client=redis_client, collective_id=cid), GrantStore(redis_client, cid)


def _patterns(args):
    from acc import approval_patterns as P
    from acc.grants import decisions_from

    queue, _ = _backends()
    items = asyncio.run(queue.recent_decisions(limit=500))
    kw = {}
    if args.threshold is not None:
        kw["threshold"] = args.threshold
    if args.min_window_days is not None:
        kw["min_window_days"] = args.min_window_days
    return P.detect(decisions_from(items), **kw)


def _cmd_detect(args) -> int:
    from acc import approval_patterns as P

    found = [p for p in _patterns(args) if P.already_handled(p) != "rejected"]
    docs = [p.as_proposal() for p in found]
    if args.json:
        print(json.dumps(docs, indent=2))
        return 0
    if not docs:
        print("no repeated, consistent approvals yet (needs ≥10 over ≥7 days, no refusal, never CRITICAL)")
        return 0
    for d in docs:
        print(f"{d['evidence_hash'][:16]}  {d['summary']}")
        print(f"   approvers: {', '.join(d['evidence']['approvers'])}  →  acc-cli grants accept {d['evidence_hash'][:16]} --by <you>")
    return 0


def _cmd_accept(args) -> int:
    from acc import approval_patterns as P
    from acc.posture_scan import BLOCKING, scan_environment

    match = [p for p in _patterns(args) if p.evidence_hash().startswith(args.evidence_hash)]
    if len(match) != 1:
        print(f"no single proposal matches {args.evidence_hash!r} (acc-cli grants detect)", file=sys.stderr)
        return 1
    pattern = match[0]
    if P.already_handled(pattern) == "rejected":
        print("this evidence was rejected; it is not proposed again", file=sys.stderr)
        return 1
    blocking = [f for f in scan_environment() if f.severity in BLOCKING]
    if blocking:
        print("refused: the posture scan has blocking findings —", file=sys.stderr)
        for f in blocking:
            print(f"  {f.rule_id} {f.message}", file=sys.stderr)
        return 1
    role, _, target = pattern.subject.partition("/")
    _, store = _backends()
    g = asyncio.run(store.accept(role=role, target=target, approved_by=args.by,
                                 evidence_hash=pattern.evidence_hash(),
                                 evidence=pattern.as_proposal()["evidence"]))
    P.record_raised(pattern)
    print(f"standing grant {g.grant_id}: {role} may run {target} without asking "
          f"(given by {args.by}; revoke with acc-cli grants revoke {g.grant_id} --by <you>)")
    return 0


def _cmd_reject(args) -> int:
    from acc import approval_patterns as P

    P.record_rejected(args.evidence_hash, reason=args.reason)
    print(f"rejected {args.evidence_hash[:16]}: not proposed again")
    return 0


def _cmd_list(args) -> int:
    _, store = _backends()
    grants = asyncio.run(store.all())
    if args.json:
        print(json.dumps([g.to_dict() for g in grants], indent=2))
        return 0
    if not grants:
        print("no standing grants")
    for g in grants:
        print(f"{g.grant_id}  {g.status:<8} {g.role} → {g.target}  by {g.approved_by}  uses={g.uses}")
    return 0


def _cmd_revoke(args) -> int:
    _, store = _backends()
    g = asyncio.run(store.revoke(args.grant_id, by=args.by))
    if g is None:
        print(f"no grant {args.grant_id}", file=sys.stderr)
        return 1
    print(f"{g.grant_id}: {g.status} by {g.revoked_by or args.by}")
    if g.target.startswith("host:"):
        from acc.sandbox.policy import remove_hosts
        if remove_hosts([g.target]):
            print(f"removed {g.target} from the sandbox network policy")
    return 0


_TARGET_KINDS = ("skill:", "mcp:", "host:")


def _cmd_add(args) -> int:
    from acc.posture_scan import BLOCKING, scan_environment

    if not args.target.startswith(_TARGET_KINDS) or args.target.endswith(":"):
        print(f"target must be one of {', '.join(k + '<...>' for k in _TARGET_KINDS)}", file=sys.stderr)
        return 1
    blocking = [f for f in scan_environment() if f.severity in BLOCKING]
    if blocking:
        print("refused: the posture scan has blocking findings --", file=sys.stderr)
        for f in blocking:
            print(f"  {f.rule_id} {f.message}", file=sys.stderr)
        return 1
    _, store = _backends()
    g = asyncio.run(store.accept(role=args.role, target=args.target, approved_by=args.by,
                                 evidence={"source": "cli"}))
    print(f"standing grant {g.grant_id}: {args.role} may use {args.target} without asking "
          f"(given by {args.by}; revoke with acc-cli grants revoke {g.grant_id} --by <you>)")
    if args.target.startswith("host:"):
        from acc.sandbox.policy import allow_hosts
        print("added to the sandbox network policy" if allow_hosts([args.target])
              else "not added to a sandbox policy (no sandbox here, or the update failed; "
                   "run acc-cli grants sync-sandbox where the agent runs)")
    return 0


def _cmd_sync_sandbox(args) -> int:
    from acc.grants import STATUS_ACTIVE
    from acc.sandbox.policy import allow_hosts

    _, store = _backends()
    targets = sorted({g.target for g in asyncio.run(store.all())
                      if g.status == STATUS_ACTIVE and g.target.startswith("host:")})
    if not targets:
        print("no active host grants")
        return 0
    ok = allow_hosts(targets)
    print(f"{'applied' if ok else 'NOT applied'}: {', '.join(targets)}")
    return 0 if ok else 1


def _cmd_scan(args) -> int:
    from acc.posture_scan import at_or_above, scan_tree, to_sarif

    root = Path(args.root) if args.root else Path(__file__).resolve().parents[2]
    findings = scan_tree(root, include_installed=not args.no_installed)
    if args.sarif:
        Path(args.sarif).write_text(json.dumps(to_sarif(findings), indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        if not findings:
            print("posture: no findings")
        for f in sorted(findings, key=lambda f: (f.severity != "CRITICAL", f.severity != "HIGH", f.rule_id)):
            print(f"{f.severity:<8} {f.rule_id}  {f.message}")
        if args.sarif:
            print(f"SARIF written to {args.sarif}")
    failing = at_or_above(findings, args.fail_on)
    if failing and not args.json:
        print(f"posture: {len(failing)} finding(s) at {args.fail_on} or worse — fix each line above",
              file=sys.stderr)
    return 1 if failing else 0
