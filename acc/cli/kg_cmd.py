"""``acc-cli kg`` — a collective's knowledge store (RDF-04).

    acc-cli kg sync    [--collective C]                        operator
    acc-cli kg graphs  [--collective C] [--ceiling X] [--json]
    acc-cli kg query   'SPARQL' [--collective C] [--ceiling X] [--json]
    acc-cli kg infer   NAME --from G [--from G ...] [--collective C]   operator

Every read runs at the caller's ceiling (D-014); ``--ceiling`` can only narrow
it, so an operator can see what a requester would see and nobody can widen.
``sync`` rebuilds the governed graphs from the refinement ledger and the hub's
taxonomy; it writes the store, never Redis.
"""

from __future__ import annotations

import argparse
import json
import sys

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNSUPPORTED = 2


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("kg", help="The collective's knowledge store: named graphs, one ceiling each (RDF-04).")
    sp = p.add_subparsers(dest="kg_command", required=True, metavar="ACTION")

    def common(a, reader: bool = True):
        a.add_argument("--collective", default="", help="Collective (default: ACC_COLLECTIVE_ID / config).")
        if reader:
            a.add_argument("--ceiling", default="", help="Read as if at this ceiling (narrows only).")
            a.add_argument("--json", action="store_true")

    s = sp.add_parser("sync", help="Rebuild the governed graphs (provenance by ceiling, taxonomy). Operator.")
    common(s, reader=False)
    s.set_defaults(func=_cmd_sync)

    g = sp.add_parser("graphs", help="The graphs you may read, with their ceilings.")
    common(g)
    g.set_defaults(func=_cmd_graphs)

    q = sp.add_parser("query", help="Run a SPARQL read over the graphs you may read.")
    q.add_argument("sparql", help="SELECT / ASK / CONSTRUCT / DESCRIBE; acc:, prov:, skos: … are predeclared.")
    common(q)
    q.set_defaults(func=_cmd_query)

    i = sp.add_parser("infer", help="RDFS/SKOS closure of some graphs into a new one, at their highest ceiling. Operator.")
    i.add_argument("name")
    i.add_argument("--from", dest="sources", action="append", required=True, help="A source graph (repeatable).")
    common(i, reader=False)
    i.set_defaults(func=_cmd_infer)


def _context(args):
    """``(collective_id, hub_collective_id, redis_client)``."""
    from acc.config import load_config  # noqa: PLC0415
    cfg = load_config()
    try:
        from acc.agent import _build_redis_client  # noqa: PLC0415
        redis_client = _build_redis_client(cfg)
    except Exception:  # noqa: BLE001
        redis_client = None
    return (args.collective or cfg.agent.collective_id,
            str(getattr(cfg.agent, "hub_collective_id", "") or ""), redis_client)


def _reader(args) -> tuple[str, str]:
    """``(ceiling, tier)``: the caller's, narrowed by ``--ceiling``."""
    from acc.kg import rank  # noqa: PLC0415
    try:
        from acc.identity import current  # noqa: PLC0415
        me = current()
        have, tier = me.effective_ceiling, str(me.tier)
    except Exception:  # noqa: BLE001
        have, tier = "LOW", "none"
    asked = str(getattr(args, "ceiling", "") or "").upper()
    if asked and rank(asked) < rank(have):
        have = asked
    return have, tier


def _store(collective_id: str):
    from acc.kg import KnowledgeStore  # noqa: PLC0415
    return KnowledgeStore(collective_id)


def _operator_only(what: str) -> int | None:
    _, tier = _reader(argparse.Namespace())
    if tier != "operator":
        print(f"kg: {what} writes the store; operator tier only", file=sys.stderr)
        return EXIT_REFUSED
    return None


def _cmd_sync(args) -> int:
    from acc.kg import KGError, sync  # noqa: PLC0415
    refused = _operator_only("sync")
    if refused is not None:
        return refused
    cid, hub, redis_client = _context(args)
    try:
        written = sync(_store(cid), redis_client=redis_client, hub_collective_id=hub)
    except KGError as exc:
        print(f"kg: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    for g in written:
        print(f"  {g.name:22} [{g.ceiling:8}] {g.triples:>7} triples  {g.source}")
    return EXIT_OK


def _cmd_graphs(args) -> int:
    from acc.kg import KGError  # noqa: PLC0415
    cid, _, _ = _context(args)
    ceiling, _ = _reader(args)
    try:
        store = _store(cid)
        graphs, hidden = store.graphs(ceiling), store.hidden_count(ceiling)
    except KGError as exc:
        print(f"kg: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    if args.json:
        print(json.dumps({"collective": cid, "reader_ceiling": ceiling, "hidden": hidden,
                          "graphs": [g.__dict__ for g in graphs]}, indent=2))
        return EXIT_OK
    print(f"Knowledge store of {cid}, read at {ceiling}:")
    for g in graphs:
        derived = f"  <- {', '.join(g.derived_from)}" if g.derived_from else ""
        print(f"  {g.name:22} [{g.ceiling:8}] {g.triples:>7} triples  {g.scope}{derived}")
    if hidden:
        print(f"  ({hidden} more above {ceiling})")
    return EXIT_OK


def _cmd_query(args) -> int:
    from acc.kg import KGError, QueryRefused  # noqa: PLC0415
    cid, _, _ = _context(args)
    ceiling, _ = _reader(args)
    try:
        result = _store(cid).query(args.sparql, reader_ceiling=ceiling)
    except QueryRefused as exc:
        print(f"kg: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except KGError as exc:
        print(f"kg: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    if args.json:
        print(json.dumps(result.__dict__, indent=2))
        return EXIT_OK
    if result.boolean is not None:
        print("true" if result.boolean else "false")
    elif result.form in ("ConstructQuery", "DescribeQuery"):
        for t in result.triples:
            print("\t".join(t))
    else:
        print("\t".join(result.variables))
        for row in result.rows:
            print("\t".join(row.get(v, "") for v in result.variables))
    print(f"# read {len(result.graphs_read)} graph(s) at {ceiling}", file=sys.stderr)
    return EXIT_OK


def _cmd_infer(args) -> int:
    from acc.kg import KGError  # noqa: PLC0415
    refused = _operator_only("infer")
    if refused is not None:
        return refused
    cid, _, _ = _context(args)
    try:
        g = _store(cid).infer(args.name, list(args.sources))
    except KGError as exc:
        print(f"kg: {exc}", file=sys.stderr)
        return EXIT_UNSUPPORTED
    print(f"  {g.name} [{g.ceiling}] {g.triples} triples <- {', '.join(g.derived_from)}")
    return EXIT_OK
