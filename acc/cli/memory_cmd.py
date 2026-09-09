"""``acc-cli memory`` — what the collective has learned, and removing a person from it.

    acc-cli memory notes [--scope <scope>]
    acc-cli memory forget --person <id> [--apply] [--quorum N]

`forget` **defaults to a dry run**. Erasure is the one operation here that
cannot be undone, and the counts it reports are the point of looking first:
a person may be behind more notes than anyone expected, and finding that out
afterwards is finding it out too late.

What it touches is deliberately narrow. The audit trail records *that* something
happened; memory records *what was said*. This removes what was said. The audit
record of the request stays, and the erasure leaves its own journal entry — there
is no path here that produces a silent deletion.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import memory_forget

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNSUPPORTED = 2


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("memory", help="Durable memory notes, and erasure.")
    sp = p.add_subparsers(dest="memory_command", required=True, metavar="ACTION")

    ls = sp.add_parser("notes", help="Show the notes a scope would read.")
    ls.add_argument("--scope", default="local",
                    help="Memory scope (default: the operator's own).")
    ls.add_argument("--role", default="", help="Role whose notes to read.")
    ls.add_argument("--hub", action="store_true",
                    help="Show the hub's enterprise tier for the role instead (the collective's hub_collective_id).")
    ls.add_argument("--json", action="store_true")

    pr = sp.add_parser(
        "propose",
        help="Propose one note for publication into a scope or the hub (hub:<cid>); a person approves.",
    )
    pr.add_argument("--role", default="", help="Role whose note it is.")
    pr.add_argument("--scope", default="local", help="Scope the note was distilled in.")
    pr.add_argument("--index", type=int, default=0, help="Which note in `memory notes --scope` (0-based).")
    pr.add_argument("--to", required=True, help="Destination: a scope, or hub:<hub collective id>.")
    pr.add_argument("--override", action="store_true",
                    help="Propose a single-source note anyway (marked; the approver sees it).")
    pr.add_argument("--json", action="store_true")
    pr.set_defaults(func=_cmd_propose)

    cu = sp.add_parser(
        "curate",
        help="The hub curator's job, by hand: which shared notes across the bound instances qualify for the hub.",
    )
    cu.add_argument("--hub", default="", help="Hub collective id (default: this collective's hub_collective_id).")
    cu.add_argument("--propose", action="store_true", help="Queue a publish proposal for each candidate.")
    cu.add_argument("--json", action="store_true")
    cu.set_defaults(func=_cmd_curate)
    ls.set_defaults(func=_cmd_notes)

    fg = sp.add_parser(
        "forget",
        help="Remove a person's episodes and reconcile the notes drawn from them.",
    )
    fg.add_argument("--person", required=True,
                    help="Requester identity, e.g. slack:U123 (the room is ignored).")
    fg.add_argument("--apply", action="store_true",
                    help="Actually erase. Without this it is a dry run.")
    fg.add_argument("--quorum", type=int, default=memory_forget.QUORUM_DEFAULT,
                    help="People required to keep a note published.")
    fg.add_argument("--json", action="store_true")
    fg.set_defaults(func=_cmd_forget)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _backends():
    """The vector + redis clients, from the ambient config."""
    from acc.config import load_config
    from acc.memory_reflection import LOCAL_SCOPE  # noqa: F401  (re-export guard)

    cfg = load_config()
    vector = redis_client = None
    try:
        from acc.backends.vector_lancedb import LanceDBBackend
        # ``cfg.vector_db.lancedb_path`` -- the attribute was misspelt as
        # ``cfg.vector.path`` since the command was written, so ``memory
        # forget`` never had a vector backend and always refused (found by
        # the HG-40.1b Phase 2 run).  Diagnostics go to stderr so ``--json``
        # output stays parseable.
        vector = LanceDBBackend(cfg.vector_db.lancedb_path)
    except Exception as exc:  # noqa: BLE001
        print(f"memory: no vector backend ({exc})", file=sys.stderr)
    try:
        from acc.agent import _build_redis_client
        redis_client = _build_redis_client(cfg)
    except Exception:  # noqa: BLE001
        redis_client = None
    return cfg, vector, redis_client


def _cmd_notes(args: argparse.Namespace) -> int:
    _safe()
    cfg, _, redis_client = _backends()
    role = args.role or cfg.agent.role
    from acc.memory_reflection import HUB_TIER, list_notes, read_hot_cache
    if args.hub:
        hub = cfg.agent.hub_collective_id
        if not hub:
            print("memory: this collective has no hub_collective_id")
            return EXIT_UNSUPPORTED
        entries = list_notes(redis_client, hub, role, HUB_TIER, tier="shared")
        if args.json:
            print(json.dumps({"hub": hub, "role": role, "notes": entries}, indent=2))
            return EXIT_OK
        print(f"Enterprise tier of hub {hub} (role {role}): {len(entries)} note(s)")
        for e in entries:
            print(f"  - [{e.get('ceiling') or 'CRITICAL'}] {e.get('summary')}")
        return EXIT_OK
    notes = read_hot_cache(
        redis_client, cfg.agent.collective_id, role, args.scope,
        bandwidth=100, hub_collective_id=cfg.agent.hub_collective_id,
    )
    if args.json:
        print(json.dumps({"scope": args.scope, "role": role, "notes": notes},
                         indent=2))
        return EXIT_OK
    if not notes:
        print(f"No notes readable in scope {args.scope!r} for role {role!r}.")
        return EXIT_OK
    print(f"Notes readable in {args.scope} (role {role}):")
    for note in notes:
        print(f"  - {note}")
    return EXIT_OK


def _cmd_forget(args: argparse.Namespace) -> int:
    _safe()
    cfg, vector, redis_client = _backends()
    if vector is None:
        return EXIT_UNSUPPORTED

    report = memory_forget.forget_person(
        vector, args.person,
        redis_client=redis_client,
        collective_id=cfg.agent.collective_id,
        k=args.quorum,
        dry_run=not args.apply,
        hub_collective_id=cfg.agent.hub_collective_id,
    )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return EXIT_OK if report.supported else EXIT_UNSUPPORTED

    if not report.supported:
        # Said plainly rather than as a warning under a success line: an
        # erasure that quietly did not erase is the worst outcome available.
        print(f"REFUSED: {report.unsupported}")
        print("Nothing was removed. Do not treat this as an erasure.")
        return EXIT_UNSUPPORTED

    verb = "Would remove" if not args.apply else "Removed"
    print(f"{verb} for {report.person}:")
    print(f"  episodes        {report.episodes_removed}")
    print(f"  notes deleted   {report.notes_deleted}  (no sources left)")
    print(f"  notes demoted   {report.notes_demoted}  (below quorum → private)")
    print(f"  notes rebuilt   {report.notes_rebuilt}  (still above quorum)")
    if report.unpublished_from:
        print(f"  unpublished from {', '.join(report.unpublished_from)}")
    if not args.apply:
        print("\nDry run. Re-run with --apply to erase.")
    return EXIT_OK


# ---------------------------------------------------------------------------
# `20260906-enterprise-brain-hub-scope` -- proposing and curating by hand
# ---------------------------------------------------------------------------


def _queue_publish_proposal(cfg, redis_client, proposal) -> str:
    """Queue *proposal* the way the assistant does (`acc/agent.py`): an
    oversight row plus the cached payload the approval dispatcher loads.
    Returns the oversight id."""
    import asyncio
    from acc.oversight import HumanOversightQueue
    from acc import agent as _agent

    cid = cfg.agent.collective_id
    queue = HumanOversightQueue(redis_client=redis_client, collective_id=cid)
    oversight_id = asyncio.run(queue.submit(
        task_id=proposal.proposal_id, risk_level=proposal.risk_level or "HIGH",
        summary=proposal.summary, role_id="memory",
        required_approvals=int((proposal.params or {}).get("required_approvals") or 1),
    ))
    ttl = max(int(getattr(queue, "_timeout_s", 300) or 300),
              int(getattr(_agent, "_ASSISTANT_PROPOSAL_CACHE_TTL_S", 3600) or 3600))
    if redis_client is not None:
        redis_client.setex(f"acc:{cid}:assistant_proposal:{oversight_id}", ttl,
                           json.dumps(proposal.to_payload(), default=str))
        redis_client.setex(f"acc:{cid}:assistant_proposal_meta:{oversight_id}", ttl,
                           json.dumps({"kind": proposal.kind, "proposal_id": proposal.proposal_id,
                                       "summary": proposal.summary}, default=str))
    return oversight_id


def _entry_note(entry: dict, role: str, scope: str):
    from acc.memory_reflection import MemoryNote
    return MemoryNote(
        summary=str(entry.get("summary") or ""), agent_id="", role_label=role,
        source_requesters=[str(r) for r in (entry.get("source_requesters") or [])],
        scope=str(entry.get("scope") or scope), dissent=str(entry.get("dissent") or ""),
        ceiling=str(entry.get("ceiling") or "CRITICAL"),
        note_id=str(entry.get("note_id") or ""),
    )


def _cmd_propose(args: argparse.Namespace) -> int:
    _safe()
    cfg, _, redis_client = _backends()
    if redis_client is None:
        print("memory: no Redis -- nothing to propose from")
        return EXIT_UNSUPPORTED
    role = args.role or cfg.agent.role
    from acc.assistant_proposal import QuorumNotMet, build_publish_proposal
    from acc.memory_reflection import list_notes
    entries = list_notes(redis_client, cfg.agent.collective_id, role, args.scope)
    if not entries or args.index < 0 or args.index >= len(entries):
        print(f"memory: no note #{args.index} in scope {args.scope!r} for role {role!r} "
              f"({len(entries)} available)")
        return EXIT_UNSUPPORTED
    note = _entry_note(entries[args.index], role, args.scope)
    if not note.note_id:
        print("memory: this cache entry predates note ids; wait for the next reflection pass")
        return EXIT_UNSUPPORTED
    from acc.identity import current
    me = current().attribution()
    try:
        proposal = build_publish_proposal(
            note, args.to, collective_id=cfg.agent.collective_id, agent_id="acc-cli",
            override_by=me if args.override else "",
        )
    except QuorumNotMet as exc:
        print(f"REFUSED: {exc}")
        return EXIT_UNSUPPORTED
    proposal.operator_id = me
    oversight_id = _queue_publish_proposal(cfg, redis_client, proposal)
    if args.json:
        print(json.dumps({"oversight_id": oversight_id, "proposal": proposal.to_payload()},
                         indent=2, default=str))
        return EXIT_OK
    print(f"  queued publish proposal {proposal.proposal_id} -> {args.to}")
    print(f"  oversight id {oversight_id}; a person approves it in Compliance / the Prompt pane "
          f"or `acc-cli oversight approve`")
    print(f"  ceiling carried: {note.ceiling}; people behind it: {len(note.people)}")
    return EXIT_OK


def _cmd_curate(args: argparse.Namespace) -> int:
    _safe()
    cfg, _, redis_client = _backends()
    if redis_client is None:
        print("memory: no Redis -- nothing to curate")
        return EXIT_UNSUPPORTED
    hub = args.hub or cfg.agent.hub_collective_id
    if not hub:
        print("memory: name a hub (--hub) or set hub_collective_id")
        return EXIT_UNSUPPORTED
    from acc.memory_curate import candidates
    found = candidates(redis_client, hub)
    if args.json and not args.propose:
        print(json.dumps([c.as_dict() for c in found], indent=2))
        return EXIT_OK
    print(f"Hub {hub}: {len(found)} candidate note(s) from the bound instances' shared tiers")
    for c in found:
        print(f"  - {c.collective_id}/{c.role_label} {c.scope}: [{c.ceiling}] people={c.people} "
              f"{c.summary[:90]}")
    if not args.propose:
        return EXIT_OK
    from acc.assistant_proposal import QuorumNotMet, build_publish_proposal
    from acc.memory_reflection import hub_destination
    from acc.identity import current
    me = current().attribution()
    queued = []
    for c in found:
        try:
            proposal = build_publish_proposal(
                c.note(), hub_destination(hub), collective_id=c.collective_id, agent_id="acc-cli",
            )
        except QuorumNotMet as exc:
            print(f"  skip {c.collective_id}/{c.role_label}: {exc}")
            continue
        proposal.operator_id = me
        proposal.collective_id = c.collective_id
        cfg_like = type("C", (), {"agent": type("A", (), {"collective_id": c.collective_id})()})()
        oversight_id = _queue_publish_proposal(cfg_like, redis_client, proposal)
        queued.append({"collective_id": c.collective_id, "oversight_id": oversight_id,
                       "summary": c.summary})
        print(f"  queued {oversight_id} in {c.collective_id}: {c.summary[:70]}")
    if args.json:
        print(json.dumps(queued, indent=2))
    return EXIT_OK
