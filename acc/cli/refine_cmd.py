"""``acc-cli refine`` — the refinement ledger: what changed, on what evidence,
what it did, and undoing it.

    acc-cli refine list [--kind K] [--limit N] [--json]
    acc-cli refine show <id>
    acc-cli refine trace <target-id>       # a note / lesson / role: its whole story
    acc-cli refine rollback <id> [--apply] # dry run by default

OpenSpec ``20260923-lessons-that-travel`` Phase 2 (PA-04).  ``rollback``
knows three kinds: a ``note`` or ``adopt`` record is revoked (the note leaves
every cache and the ``memory_notes`` table; the lesson copy stays, marked);
a ``role_patch`` record queues a ``role_update`` proposal restoring the old
field values through the oversight queue — a person approves the revert the
same way they approved the change.  Every other kind is refused with the
manual path named.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from acc import refinements
from acc.cli._common import default_collective

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_UNSUPPORTED = 2
EXIT_REFUSED = 3


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("refine", help="The refinement ledger: every durable change, its evidence, its outcome.")
    sp = p.add_subparsers(dest="refine_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Most recent refinement records.")
    ls.add_argument("--collective", default="")
    ls.add_argument("--kind", default="", help=f"One of {', '.join(refinements.KINDS)}.")
    ls.add_argument("--limit", type=int, default=30)
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    sh = sp.add_parser("show", help="One record in full.")
    sh.add_argument("refinement_id")
    sh.add_argument("--collective", default="")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=_cmd_show)

    tr = sp.add_parser("trace", help="Every record about one note / lesson / role id, oldest first.")
    tr.add_argument("target_id")
    tr.add_argument("--collective", default="")
    tr.add_argument("--json", action="store_true")
    tr.set_defaults(func=_cmd_trace)

    rb = sp.add_parser("rollback", help="Undo one record. Dry run unless --apply.")
    rb.add_argument("refinement_id")
    rb.add_argument("--collective", default="")
    rb.add_argument("--apply", action="store_true")
    rb.add_argument("--json", action="store_true")
    rb.set_defaults(func=_cmd_rollback)


def _backends():
    try:
        from acc.cli.memory_cmd import _backends as mem_backends  # noqa: PLC0415
        return mem_backends()
    except Exception:  # noqa: BLE001
        return None, None, None


def _redis_only():
    try:
        from acc.agent import _build_redis_client  # noqa: PLC0415
        from acc.config import load_config  # noqa: PLC0415
        return _build_redis_client(load_config())
    except Exception:  # noqa: BLE001
        return None


def _cid(args: argparse.Namespace) -> str:
    return args.collective or default_collective()


def _line(r: dict) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(r.get("ts", 0) or 0)))
    tgt = r.get("target") or {}
    who = r.get("approver") or r.get("agent_id") or "-"
    extra = ""
    if r.get("kind") == "outcome":
        m = r.get("measured") or {}
        extra = f"  → {m.get('signal', '?')} ({m.get('delta', 0):+.2f})"
    if r.get("rollback_of"):
        extra = f"  ↩ {str(r['rollback_of'])[:8]}"
    return (f"  {str(r.get('refinement_id', ''))[:8]}  {ts}  {r.get('kind', '?'):<11} "
            f"{tgt.get('store', '-')}/{str(tgt.get('id', ''))[:12]:<12} by {who:<18} "
            f"{str(r.get('trigger') or '')[:40]}{extra}")


def _cmd_list(args: argparse.Namespace) -> int:
    cid = _cid(args)
    recs = refinements.load(_redis_only(), cid, limit=max(args.limit, 1) * (4 if args.kind else 1))
    if args.kind:
        recs = [r for r in recs if r.get("kind") == args.kind][: args.limit]
    if args.json:
        print(json.dumps({"collective": cid, "records": recs}, indent=2))
        return EXIT_OK
    if not recs:
        print(f"No refinement records for {cid}.")
        return EXIT_OK
    print(f"Refinements in {cid} (newest first, {len(recs)}):")
    for r in recs:
        print(_line(r))
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    cid = _cid(args)
    recs = refinements.load(_redis_only(), cid, limit=2000)
    rec = refinements.find(recs, args.refinement_id)
    if rec is None:
        print(f"refine: no record matches {args.refinement_id!r}", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(json.dumps(rec, indent=2))
    return EXIT_OK


def _cmd_trace(args: argparse.Namespace) -> int:
    cid = _cid(args)
    recs = refinements.load(_redis_only(), cid, limit=5000)
    hits = refinements.trace(recs, args.target_id)
    if not hits:
        # a prefix of a target id is a reasonable thing to type
        ids = {str((r.get("target") or {}).get("id", "")) for r in recs}
        cands = [i for i in ids if i and i.startswith(args.target_id)]
        if len(cands) == 1:
            hits = refinements.trace(recs, cands[0])
    if args.json:
        print(json.dumps({"target": args.target_id, "records": hits}, indent=2))
        return EXIT_OK if hits else EXIT_NOT_FOUND
    if not hits:
        print(f"refine: nothing recorded about {args.target_id!r}", file=sys.stderr)
        return EXIT_NOT_FOUND
    print(f"Story of {args.target_id} ({len(hits)} record(s)):")
    for r in hits:
        print(_line(r))
    outs = [r for r in hits if r.get("kind") == "outcome"]
    if outs:
        delta = sum(float((r.get("measured") or {}).get("delta", 0) or 0) for r in outs)
        print(f"  measured: {len(outs)} outcome(s), net confidence delta {delta:+.2f}")
    return EXIT_OK


def _cmd_rollback(args: argparse.Namespace) -> int:
    cid = _cid(args)
    cfg, vector, redis_client = _backends()
    recs = refinements.load(redis_client, cid, limit=5000)
    rec = refinements.find(recs, args.refinement_id)
    if rec is None:
        print(f"refine: no record matches {args.refinement_id!r}", file=sys.stderr)
        return EXIT_NOT_FOUND
    if any(r.get("rollback_of") == rec.get("refinement_id") for r in recs):
        print(f"refine: {rec['refinement_id'][:8]} was already rolled back", file=sys.stderr)
        return EXIT_REFUSED
    kind = rec.get("kind")
    tgt = rec.get("target") or {}
    plan: dict = {"refinement_id": rec.get("refinement_id"), "kind": kind, "apply": bool(args.apply)}

    if kind in ("note", "adopt"):
        from acc.memory_reflection import revoke_note  # noqa: PLC0415
        plan["action"] = f"revoke note {tgt.get('id')} for role {rec.get('role_label')} in every cache and memory_notes"
        if args.apply:
            report = revoke_note(redis_client, vector, cid, str(rec.get("role_label") or ""), str(tgt.get("id") or ""))
            plan["result"] = report
    elif kind == "role_patch":
        old = tgt.get("old") or {}
        if not old:
            print("refine: role_patch record carries no old values; cannot build a revert", file=sys.stderr)
            return EXIT_REFUSED
        plan["action"] = f"queue a role_update proposal restoring {sorted(old)} on role {rec.get('role_label')}"
        if args.apply:
            from acc.assistant_proposal import PROPOSAL_ROLE_UPDATE, AssistantProposal  # noqa: PLC0415
            from acc.cli.memory_cmd import _queue_publish_proposal  # noqa: PLC0415
            proposal = AssistantProposal(
                kind=PROPOSAL_ROLE_UPDATE, risk_level="HIGH",
                params={"role": rec.get("role_label") or "", "fields": old,
                        "rollback_of": rec.get("refinement_id")},
                summary=f"Rollback {str(rec.get('refinement_id'))[:8]}: restore {', '.join(sorted(old))}",
                rationale=f"acc-cli refine rollback of {rec.get('refinement_id')}",
                collective_id=cid, agent_id="acc-cli",
            )
            plan["oversight_id"] = _queue_publish_proposal(cfg, redis_client, proposal)
    else:
        manual = {
            "rule": "acc-cli compliance rules reject <id> and edit the overlay",
            "forget": "erasure is not reversible",
            "publish": "acc-cli memory forget --person … or edit the shared tier",
            "hub_promote": "acc-cli memory forget --person … or edit the hub's enterprise tier",
            "outcome": "an observation is not a change",
            "rollback": "a rollback of a rollback: re-apply by hand",
        }.get(str(kind), "no automatic path")
        print(f"refine: cannot roll back a {kind!r} record ({manual})", file=sys.stderr)
        return EXIT_REFUSED

    if args.apply:
        refinements.record(
            "rollback", redis_client=redis_client, collective_id=cid, agent_id="acc-cli",
            role_label=str(rec.get("role_label") or ""), trigger="acc-cli refine rollback",
            target=dict(tgt), rollback_of=str(rec.get("refinement_id") or ""),
            approver=_operator(),
        )
    if args.json:
        print(json.dumps(plan, indent=2, default=str))
    else:
        print(("APPLIED: " if args.apply else "DRY RUN: ") + plan["action"])
        if "oversight_id" in plan:
            print(f"  oversight item {plan['oversight_id']} queued; a person approves the revert")
        if "result" in plan:
            print(f"  {plan['result']}")
        if not args.apply:
            print("  re-run with --apply to do it")
    return EXIT_OK


def _operator() -> str:
    try:
        from acc.identity import current  # noqa: PLC0415
        return current().attribution()
    except Exception:  # noqa: BLE001
        return "acc-cli"
