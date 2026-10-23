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
from acc.operator_identity import resolve_operator_id

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
    fg.add_argument("--plan", action="store_true",
                    help="Read-only: everything resting on this person across the collectives' "
                         "refinement ledgers (notes and every copy derived from them). Erases nothing.")
    fg.add_argument("--collective", action="append", default=[],
                    help="--plan: a collective whose ledger to read (repeatable; "
                         "default: this collective and its hub).")
    fg.set_defaults(func=_cmd_forget)

    ln = sp.add_parser(
        "lineage",
        help="Where a note came from and everywhere it went, from the refinement ledger (RDF-03).",
    )
    ln.add_argument("note_id", nargs="?", default="", help="Note id or unique prefix.")
    ln.add_argument("--collective", action="append", default=[],
                    help="A collective whose ledger to read (repeatable; default: this collective and its hub).")
    ln.add_argument("--as", dest="as_rdf", choices=("turtle", "ntriples", "jsonld"), default="",
                    help="Print the lineage as RDF (PROV-O) instead of a tree.")
    ln.add_argument("--check", action="store_true",
                    help="Report on the projection itself: rows read, notes, copies, and notes "
                         "something derives from that no ledger row explains.")
    ln.add_argument("--json", action="store_true")
    ln.set_defaults(func=_cmd_lineage)

    ex = sp.add_parser(
        "export",
        help="Write the memory you may see as an OKF bundle (open it in Obsidian; RDF-07).",
    )
    ex.add_argument("--okf", required=True, metavar="DIR", help="Directory to write the bundle into.")
    ex.add_argument("--collective", default="", help="Collective id (default: this collective).")
    ex.add_argument("--force", action="store_true",
                    help="Write into a directory that is not empty (files are overwritten).")
    ex.set_defaults(func=_cmd_export)


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
    if args.plan:
        return _forget_plan(args, cfg, redis_client)
    if vector is None:
        return EXIT_UNSUPPORTED

    report = memory_forget.forget_person(
        vector, args.person,
        redis_client=redis_client,
        collective_id=cfg.agent.collective_id,
        k=args.quorum,
        dry_run=not args.apply,
        hub_collective_id=cfg.agent.hub_collective_id,
        operator=resolve_operator_id(),
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
        deadline=False,
    ))
    if redis_client is not None:
        _agent.cache_proposal_payload(redis_client, cid, oversight_id, proposal)
    from acc.assistant_proposal import record_publish_proposed
    record_publish_proposed(proposal, oversight_id, redis_client)
    return oversight_id


def _entry_note(entry: dict, role: str, scope: str):
    from acc.memory_reflection import MemoryNote
    return MemoryNote(
        summary=str(entry.get("summary") or ""), agent_id="", role_label=role,
        source_requesters=[str(r) for r in (entry.get("source_requesters") or [])],
        scope=str(entry.get("scope") or scope), dissent=str(entry.get("dissent") or ""),
        ceiling=str(entry.get("ceiling") or "CRITICAL"),
        note_id=str(entry.get("note_id") or ""),
        source_ids=[str(i) for i in (entry.get("source_ids") or [])],
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


# ---------------------------------------------------------------------------
# RDF-03 Phase 2b -- the provenance projection of the refinement ledger
# ---------------------------------------------------------------------------

#: Rows read per collective.  The Redis mirror keeps 30 days; the file, the
#: agent's lifetime -- a lineage is only as long as what is read.
_LEDGER_LIMIT = 100_000


def _projection(args, cfg, redis_client):
    """The provenance graph over the named collectives' ledgers."""
    from acc import refinements
    from acc.attribution import person_of
    from acc.lib.rdf.prov import project

    cids = list(dict.fromkeys(
        args.collective or [c for c in (cfg.agent.collective_id, cfg.agent.hub_collective_id) if c]))
    rows = []
    for cid in cids:
        rows.extend(refinements.load(redis_client, cid, limit=_LEDGER_LIMIT))
    return cids, len(rows), project(rows, person_of=person_of)


def _reader() -> tuple[str, str]:
    """``(ceiling, tier)`` of whoever runs the command (D-014)."""
    try:
        from acc.identity import current
        me = current()
        return me.effective_ceiling, str(me.tier)
    except Exception:  # noqa: BLE001
        return "LOW", "none"


def _short(iri: str) -> str:
    """``urn:acc:collective:c:note:abc`` -> ``c:note:abc`` for a terminal."""
    return str(iri).replace("urn:acc:collective:", "", 1)


def _value(index, node, pred) -> str:
    """One object of (*node*, *pred*) as plain text: a literal's value, an IRI's last segment."""
    from acc.lib.rdf.terms import Literal
    for obj in sorted(index.objects(node, pred), key=str):
        return obj.value if isinstance(obj, Literal) else str(obj).rsplit(":", 1)[-1]
    return ""


def _cmd_lineage(args: argparse.Namespace) -> int:
    _safe()
    cfg, _, redis_client = _backends()
    from acc.lib.rdf.prov import Index, approvals_of, find_notes, lineage, unexplained
    from acc.lib.rdf.terms import ACC, PROV, RDF

    cids, n_rows, proj = _projection(args, cfg, redis_client)
    g = proj.graph
    if args.check:
        gaps = unexplained(g)
        report = {
            "collectives": cids, "rows": n_rows, "rows_projected": proj.rows_read,
            "rows_skipped": proj.rows_skipped, "triples": len(g),
            "notes": len(find_notes(g, "")),
            "copies": sum(1 for _, p, o in g if p == RDF.type and o == ACC.PublishedNote),
            "unexplained": len(gaps), "unexplained_sample": [_short(x) for x in gaps[:5]],
        }
        if args.json:
            print(json.dumps(report, indent=2))
            return EXIT_OK
        print(f"Ledgers of {', '.join(cids) or '(none)'}: {n_rows} row(s), "
              f"{proj.rows_read} projected, {proj.rows_skipped} skipped (not memory)")
        print(f"  {report['triples']} triples, {report['notes']} note(s), {report['copies']} copy(ies)")
        print(f"  {len(gaps)} note(s) referenced that no ledger row explains"
              + (" -- older than the 30-day mirror, or never journalled" if gaps else ""))
        for x in report["unexplained_sample"]:
            print(f"    - {x}")
        return EXIT_OK

    if not args.note_id:
        print("memory: name a note id (or use --check)", file=sys.stderr)
        return EXIT_UNSUPPORTED
    hits = find_notes(g, args.note_id)
    if len(hits) != 1:
        print(f"memory: {len(hits)} note(s) match {args.note_id!r} in the ledgers of "
              f"{', '.join(cids) or '(none)'}", file=sys.stderr)
        for h in hits[:10]:
            print(f"  - {_short(h)}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    ceiling, tier = _reader()
    idx = Index(g)
    lin = lineage(g, hits[0], reader_ceiling=ceiling, index=idx)
    if not lin.note_visible:
        print(f"memory: note {_short(hits[0])} is above your ceiling ({ceiling})", file=sys.stderr)
        return EXIT_UNSUPPORTED

    if args.as_rdf:
        from acc.cli.rdf_cmd import render
        from acc.lib.rdf.graph import Graph
        keep = {lin.note, *lin.upstream, *lin.proposals, *lin.copies, *lin.used_by}
        acts = {o for s, p, o in g if s in keep and p in (PROV.wasGeneratedBy, PROV.wasInvalidatedBy)}
        sub = Graph(t for t in g if t[0] in keep or t[0] in acts)
        assoc = {o for _, p, o in sub if p == PROV.qualifiedAssociation}
        people = {o for s, p, o in g if (s in keep or s in assoc) and p in (ACC.sourceRequester, PROV.agent)}
        for t in g:
            if t[0] in assoc or t[0] in people:
                sub.add(*t)
        sys.stdout.write(render(sub, args.as_rdf))
        return EXIT_OK

    def who(person) -> str:
        # Names only for an operator; anyone else sees that a person decided.
        return (proj.persons.subject_of(person) if tier == "operator" else "") or "a person"

    out = {
        "note": _short(lin.note), "ceiling": _value(idx, lin.note, ACC.ceiling),
        "role": _value(idx, lin.note, ACC.role), "scope": _value(idx, lin.note, ACC.scope),
        "episodes": len(lin.upstream),
        "people": len(idx.objects(lin.note, ACC.sourceRequester)),
        "proposals": [{"proposal": _short(x), "destination": _value(idx, x, ACC.destination)}
                      for x in lin.proposals],
        "copies": [{"copy": _short(c), "destination": _value(idx, c, ACC.destination),
                    "approved_by": [{"who": who(p), "tier": t} for p, t in approvals_of(g, c, index=idx)]}
                   for c in lin.copies],
        "invalidated_by": [_short(a) for a in lin.invalidated_by],
        "used_by_tasks": len(lin.used_by), "hidden_above_your_ceiling": lin.hidden,
    }
    if args.json:
        print(json.dumps(out, indent=2))
        return EXIT_OK
    print(f"Note {out['note']}  [{out['ceiling']}]  role {out['role'] or '?'}  scope {out['scope'] or '?'}")
    print(f"  rests on {out['episodes']} episode(s) from {out['people']} person(s)")
    for prop in out["proposals"]:
        print(f"  proposed -> {prop['destination']}  ({prop['proposal']})")
    for c in out["copies"]:
        approvers = ", ".join(f"{a['who']} ({a['tier'] or 'tier unknown'})" for a in c["approved_by"])
        print(f"  -> {c['destination']}  {c['copy']}"
              + (f"  approved by {approvers}" if approvers else "  (adopted)"))
    for a in out["invalidated_by"]:
        print(f"  x invalidated by {a}")
    if out["used_by_tasks"]:
        print(f"  shaped {out['used_by_tasks']} task(s)")
    if lin.hidden:
        print(f"  ({lin.hidden} more above your ceiling, {ceiling})")
    return EXIT_OK


def _forget_plan(args, cfg, redis_client) -> int:
    """``memory forget --plan`` -- read-only: what an erasure would have to reach."""
    from acc.lib.rdf.prov import contributions

    _, tier = _reader()
    if tier != "operator":
        print("memory: an erasure plan names everything a person contributed; operator tier only",
              file=sys.stderr)
        return EXIT_UNSUPPORTED
    cids, n_rows, proj = _projection(args, cfg, redis_client)
    plan = contributions(proj, args.person)
    if args.json:
        print(json.dumps({"person": plan.person, "collectives": cids, "rows": n_rows,
                          "notes": plan.notes, "copies": plan.copies,
                          "already_erased": plan.already_erased}, indent=2))
        return EXIT_OK
    print(f"Erasure plan for {plan.person} -- from the refinement ledgers of {', '.join(cids)} "
          f"({n_rows} row(s)); nothing is erased")
    print(f"  {len(plan.notes)} note(s) rest on them:")
    for n in plan.notes:
        print(f"    - {_short(n)}")
    print(f"  {len(plan.copies)} copy(ies) derived from those, in every destination:")
    for c in plan.copies:
        print(f"    - {_short(c)}")
    if plan.already_erased:
        print(f"  {len(plan.already_erased)} note(s) already invalidated")
    print("\n`memory forget --person ... --apply` erases this collective's episodes and notes "
          "and unpublishes them; run it in each collective listed above.")
    return EXIT_OK


def _cmd_export(args: argparse.Namespace) -> int:
    """``memory export --okf DIR`` -- the Memory view as an OKF bundle.

    Built from :func:`acc.memory_views.memory_view` for whoever runs it, so the
    bundle holds what they may see and nothing more.
    """
    import time
    from pathlib import Path

    from acc.identity import current
    from acc.lib.okf import write_bundle
    from acc.memory_export import bundle_from_view
    from acc.memory_views import Viewer, default_sources, memory_view

    dest = Path(args.okf)
    if dest.exists() and any(dest.iterdir()) and not args.force:
        print(f"memory: {dest} is not empty; use --force to write into it", file=sys.stderr)
        return EXIT_REFUSED
    view = memory_view(default_sources(args.collective), Viewer.of(current()))
    if not view.get("available"):
        print("memory: no Redis and no note store -- nothing to export", file=sys.stderr)
        return EXIT_UNSUPPORTED
    bundle = bundle_from_view(view, exported_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    write_bundle(bundle, dest)
    notes = sum(1 for c in bundle.concepts if c.rel_path.startswith("notes/"))
    print(f"Wrote {notes} note(s) to {dest} (open the folder as an Obsidian vault).")
    if view.get("above_your_ceiling"):
        print(f"  {view['above_your_ceiling']} note(s) above your ceiling were left out.")
    return EXIT_OK
