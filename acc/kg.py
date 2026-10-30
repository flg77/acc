"""A collective's knowledge store — named graphs, one ceiling each (RDF-04).

One store per collective, so one per instance and one at the hub (T2,
confirmed 2026-09-24).  Embedded Oxigraph (``pyoxigraph``, the ``rdf`` extra),
on disk under ``ACC_KG_PATH`` or ``~/.acc/kg/<collective>``.

**A named graph is a unit of readability.**  Every graph carries exactly one
ceiling in the store's catalogue graph; a reader sees the graphs at or below
their ceiling and nothing else.  The rule is enforced by the *dataset*, not by
the query text: :meth:`KnowledgeStore.query` hands Oxigraph the visible graphs
as both the default and the named graphs, which Oxigraph applies over any
``FROM`` / ``FROM NAMED`` the query writes (checked on 0.5.11) — a query
cannot name its way into a graph it was not given.  The catalogue itself is
never in a query's dataset.

**Reads only, and no network.**  A query is parsed first (rdflib's SPARQL
parser): anything but SELECT / ASK / CONSTRUCT / DESCRIBE is refused, and so
is ``SERVICE`` anywhere in it — Oxigraph would make the HTTP call, which is
egress (HG-37), not a read.  A query the parser cannot read is refused too.

**Inference carries the highest ceiling.**  :meth:`KnowledgeStore.infer`
writes what it derives into its own graph whose ceiling is the highest of its
sources', so nothing derived from a HIGH graph is ever readable at LOW.

**Who writes.**  Nothing an agent says.  Phase 1 graphs are rebuilt from
records that already passed the gates — the refinement ledger (projected by
:mod:`acc.lib.rdf.prov`) and the hub's signed taxonomy (RDF-02) — by
:func:`sync`, run by an operator.  Agent-proposed triples arrive with RDF-05,
as proposals.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from acc.lib.rdf.graph import Graph
from acc.lib.rdf.iri import local_iri
from acc.lib.rdf.terms import ACC, DCTERMS, PREFIXES, PROV, RDF, RDFS, SKOS, BNode, Literal, datetime_lit, lit
from acc.lib.rdf.vocab import CEILINGS, ceiling_iri

ENV_PATH = "ACC_KG_PATH"

#: Graph names: lower-case slugs; ``_``-prefixed names are the store's own.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_CATALOGUE = "_catalogue"

#: Query forms a reader may run.
READ_FORMS = frozenset({"SelectQuery", "AskQuery", "ConstructQuery", "DescribeQuery"})


class KGError(Exception):
    """The store cannot do what was asked."""


class QueryRefused(KGError):
    """A query that is not a plain read."""


def default_path(collective_id: str) -> Path:
    raw = os.environ.get(ENV_PATH, "").strip()
    base = Path(raw) if raw else Path.home() / ".acc" / "kg"
    return base / collective_id


def _oxigraph():
    try:
        import pyoxigraph  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise KGError("the knowledge store needs the rdf extra: pip install 'acc[rdf]'") from exc
    return pyoxigraph


def rank(level: str) -> int:
    text = str(level or "").strip().upper()
    return CEILINGS.index(text) if text in CEILINGS else len(CEILINGS) - 1


def check_query(sparql: str) -> str:
    """The query's form (``SelectQuery`` …), or :class:`QueryRefused`."""
    try:
        from rdflib.plugins.sparql import prepareQuery  # noqa: PLC0415
        from rdflib.plugins.sparql.parserutils import CompValue  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise KGError("query checking needs the rdf extra: pip install 'acc[rdf]'") from exc
    try:
        parsed = prepareQuery(sparql, initNs=PREFIXES)
    except Exception as exc:  # noqa: BLE001 -- rdflib raises several kinds
        raise QueryRefused(f"not a query this store can check: {str(exc).splitlines()[0][:160]}") from exc
    form = parsed.algebra.name
    if form not in READ_FORMS:
        raise QueryRefused(f"{form} is not a read")

    def walk(node: Any) -> bool:
        if isinstance(node, CompValue):
            return node.name == "ServiceGraphPattern" or any(walk(v) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(walk(v) for v in node)
        return False

    if walk(parsed.algebra):
        raise QueryRefused("SERVICE reaches another endpoint: that is egress, not a read")
    return form


@dataclass
class GraphInfo:
    name: str
    iri: str
    ceiling: str
    scope: str = ""
    source: str = ""
    triples: int = 0
    modified: str = ""
    derived_from: list[str] = field(default_factory=list)
    #: The owner pseudonym of a private graph; empty = not private.
    owner: str = ""


@dataclass
class QueryResult:
    form: str
    variables: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    boolean: bool | None = None
    triples: list[tuple[str, str, str]] = field(default_factory=list)
    graphs_read: list[str] = field(default_factory=list)


class KnowledgeStore:
    """One collective's named graphs.  ``in_memory=True`` for tests."""

    def __init__(self, collective_id: str, path: Path | str | None = None, *, in_memory: bool = False,
                 read_only: bool = False):
        if not collective_id:
            raise KGError("a knowledge store belongs to a collective")
        self.ox = _oxigraph()
        self.collective_id = collective_id
        self.read_only = read_only
        self._persons = None
        if in_memory:
            self.store = self.ox.Store()
            self.path: Path | None = None
            self._memory_salt = os.urandom(32)
        else:
            self.path = Path(path) if path else default_path(collective_id)
            if read_only:
                # Many readers beside one writer (the arbiter, RDF-05): a
                # read-only open sees the store as of the moment it opened.
                if not self.path.is_dir():
                    raise KGError(f"no knowledge store at {self.path}")
                self.store = self.ox.Store.read_only(str(self.path))
            else:
                self.path.mkdir(parents=True, exist_ok=True)
                self.store = self.ox.Store(str(self.path))

    # -- owners (RDF-05) ---------------------------------------------------
    def owner_iri(self, subject: str) -> str:
        """The pseudonym a person's private graphs are owned by in this store.

        A keyed hash under a salt kept with the store (``.owner-salt``): the
        catalogue names no person, and every process that opens the same
        store maps the same person to the same owner.
        """
        from acc.attribution import person_of  # noqa: PLC0415
        from acc.lib.rdf.iri import Persons  # noqa: PLC0415

        if self._persons is None:
            salt = self._salt()
            if salt is None:
                return ""          # no salt yet = no private graph exists to own
            self._persons = Persons(salt)
        return str(self._persons.node(person_of(subject)))

    def _salt(self) -> bytes | None:
        if self.path is None:
            return self._memory_salt
        salt_file = self.path / ".owner-salt"
        if not salt_file.exists():
            if self.read_only:
                return None        # a reader never creates it; the writer does on first private write
            salt_file.write_bytes(os.urandom(32))
            try:
                os.chmod(salt_file, 0o600)
            except OSError:  # pragma: no cover -- Windows
                pass
        return salt_file.read_bytes()

    def private_graph_name(self, subject: str, ceiling: str) -> str:
        """``private-<16 hex>-<ceiling>``: one graph per person per ceiling, so
        every graph keeps exactly one ceiling."""
        token = self.owner_iri(subject).rsplit(":", 1)[-1][:16]
        level = str(ceiling or "").upper() if str(ceiling or "").upper() in CEILINGS else "CRITICAL"
        return f"private-{token}-{level.lower()}"

    # -- naming ------------------------------------------------------------
    def graph_iri(self, name: str) -> str:
        return local_iri(self.collective_id, "graph", name)

    def _catalogue(self):
        return self.ox.NamedNode(self.graph_iri(_CATALOGUE))

    @staticmethod
    def _check_name(name: str) -> None:
        if not _NAME_RE.match(name or ""):
            raise KGError(f"graph name {name!r} must be a lower-case slug")

    # -- terms -------------------------------------------------------------
    def _term(self, term: Any, bnode_scope: str):
        ox = self.ox
        if isinstance(term, BNode):
            # Blank-node labels are only unique within one serialisation; in a
            # shared store they are namespaced by the batch that wrote them,
            # so two syncs never merge each other's blank nodes.
            safe = re.sub(r"[^A-Za-z0-9_]", "_", term.label)
            return ox.BlankNode(f"b{hashlib.sha256(bnode_scope.encode()).hexdigest()[:10]}_{safe}")
        if isinstance(term, Literal):
            if term.lang:
                return ox.Literal(term.value, language=term.lang)
            if term.datatype:
                return ox.Literal(term.value, datatype=ox.NamedNode(term.datatype))
            return ox.Literal(term.value)
        return ox.NamedNode(str(term))

    @staticmethod
    def _text(term: Any) -> str:
        if term is None:
            return ""
        if hasattr(term, "datatype") or hasattr(term, "language"):
            return term.value
        if type(term).__name__ == "BlankNode":
            return f"_:{term.value}"
        return getattr(term, "value", str(term))

    # -- writing -----------------------------------------------------------
    def put(self, name: str, graph: Graph, *, ceiling: str, scope: str = "", source: str = "",
            bnode_scope: str = "", derived_from: Iterable[str] = (), owner: str = "",
            append: bool = False) -> GraphInfo:
        """Replace graph *name* with *graph*, readable at *ceiling* and above.

        Only governed loaders call this (:func:`sync`, :meth:`infer`); an
        agent never does.
        """
        self._check_name(name)
        ox = self.ox
        if self.read_only:
            raise KGError("this store was opened read-only")
        g = ox.NamedNode(self.graph_iri(name))
        if self.store.contains_named_graph(g) and not append:
            self.store.remove_graph(g)
        scope_key = bnode_scope or name
        self.store.extend(ox.Quad(self._term(s, scope_key), ox.NamedNode(p),
                                  self._term(o, scope_key), g) for s, p, o in graph)
        cat = self._catalogue()
        for quad in list(self.store.quads_for_pattern(g, None, None, cat)):
            self.store.remove(quad)
        level = str(ceiling or "").upper() if str(ceiling or "").upper() in CEILINGS else "CRITICAL"
        meta = [
            (RDF.type, ox.NamedNode(ACC.KnowledgeGraph)),
            (ACC.ceiling, ox.NamedNode(ceiling_iri(level))),
            (ACC.tripleCount, self._term(lit(len(self._graph_triples(name))), "")),
            (DCTERMS.modified, self._term(datetime_lit(time.time()), "")),
        ]
        if scope:
            meta.append((ACC.scope, ox.Literal(scope)))
        if source:
            meta.append((DCTERMS.source, ox.Literal(source)))
        for src in derived_from:
            meta.append((PROV.wasDerivedFrom, ox.NamedNode(self.graph_iri(src))))
        if owner:
            meta.append((ACC.owner, ox.NamedNode(self.owner_iri(owner))))
        self.store.extend(ox.Quad(g, ox.NamedNode(p), o, cat) for p, o in meta)
        return next(i for i in self._all_graphs() if i.name == name)

    # -- the catalogue -------------------------------------------------------
    def graphs(self, reader_ceiling: str = "CRITICAL", reader: str = "") -> list[GraphInfo]:
        """The graphs a reader at *reader_ceiling* may read, by name.

        A private graph (RDF-05) is readable only by its owner -- *reader* is
        the requester -- whatever their ceiling."""
        mine = self.owner_iri(reader) if reader else ""
        return [g for g in self._all_graphs()
                if rank(g.ceiling) <= rank(reader_ceiling) and (not g.owner or g.owner == mine)]

    def _all_graphs(self) -> list[GraphInfo]:
        cat = self._catalogue()
        prefix = self.graph_iri("x")[:-1]
        info: dict[str, GraphInfo] = {}
        for quad in self.store.quads_for_pattern(None, None, None, cat):
            iri = quad.subject.value
            gi = info.setdefault(iri, GraphInfo(name=iri[len(prefix):], iri=iri, ceiling="CRITICAL"))
            p, o = quad.predicate.value, quad.object
            if p == ACC.ceiling:
                gi.ceiling = o.value.rsplit(":", 1)[-1]
            elif p == ACC.scope:
                gi.scope = o.value
            elif p == DCTERMS.source:
                gi.source = o.value
            elif p == ACC.tripleCount:
                gi.triples = int(o.value)
            elif p == DCTERMS.modified:
                gi.modified = o.value
            elif p == PROV.wasDerivedFrom:
                gi.derived_from.append(o.value[len(prefix):])
            elif p == ACC.owner:
                gi.owner = o.value
        for gi in info.values():
            gi.derived_from.sort()
        return sorted(info.values(), key=lambda g: g.name)

    def hidden_count(self, reader_ceiling: str, reader: str = "") -> int:
        return len(self._all_graphs()) - len(self.graphs(reader_ceiling, reader))

    # -- reading -------------------------------------------------------------
    def query(self, sparql: str, *, reader_ceiling: str, reader: str = "") -> QueryResult:
        form = check_query(sparql)
        visible = self.graphs(reader_ceiling, reader)
        result = QueryResult(form=form, graphs_read=[g.name for g in visible])
        if not visible:
            # An empty dataset, not the store default graph: nothing is readable.
            if form == "AskQuery":
                result.boolean = False
            return result
        nodes = [self.ox.NamedNode(g.iri) for g in visible]
        answer = self.store.query(sparql, prefixes=dict(PREFIXES),
                                  default_graph=nodes, named_graphs=nodes)
        if form == "AskQuery":
            result.boolean = bool(answer)
        elif form in ("ConstructQuery", "DescribeQuery"):
            result.triples = [(self._text(t.subject), self._text(t.predicate), self._text(t.object))
                              for t in answer]
        else:
            result.variables = [v.value for v in answer.variables]
            for solution in answer:
                result.rows.append({v: self._text(solution[v]) for v in result.variables})
        # pyoxigraph's result objects are thread-bound ("unsendable"), and the
        # exception that ends iteration over them keeps this frame -- and so
        # ``answer`` -- alive until the cycle collector runs, on whatever thread
        # that is.  A surface serving requests from a thread pool (the Web GUI)
        # then panics on drop.  Release it here, on the thread that made it.
        del answer
        return result

    def _graph_triples(self, name: str) -> list:
        g = self.ox.NamedNode(self.graph_iri(name))
        return list(self.store.quads_for_pattern(None, None, None, g))

    # -- inference -------------------------------------------------------------
    def infer(self, name: str, sources: list[str]) -> GraphInfo:
        """RDFS/SKOS closure over *sources* into graph *name*, at the highest
        source ceiling: ``skos:broaderTransitive`` from ``skos:broader``, and
        ``rdf:type`` along ``rdfs:subClassOf``."""
        known = {g.name: g for g in self._all_graphs() if not g.owner}
        missing = [s for s in sources if s not in known]
        if not sources or missing:
            raise KGError(f"inference needs existing source graphs; missing {missing or 'all'}")
        broader: dict[str, set[str]] = {}
        sub: dict[str, set[str]] = {}
        types: dict[str, set[str]] = {}
        for src in sources:
            for q in self._graph_triples(src):
                s, p, o = q.subject, q.predicate.value, q.object
                if type(o).__name__ != "NamedNode" or type(s).__name__ != "NamedNode":
                    continue
                if p == SKOS.broader:
                    broader.setdefault(s.value, set()).add(o.value)
                elif p == RDFS.subClassOf:
                    sub.setdefault(s.value, set()).add(o.value)
                elif p == RDF.type:
                    types.setdefault(s.value, set()).add(o.value)

        def closure(edges: dict[str, set[str]], start: str) -> set[str]:
            seen, stack = set(), list(edges.get(start, ()))
            while stack:
                n = stack.pop()
                if n not in seen:
                    seen.add(n)
                    stack.extend(edges.get(n, ()))
            return seen

        out = Graph()
        for concept in broader:
            for up in closure(broader, concept):
                out.add(concept, SKOS.broaderTransitive, up)
        for node, classes in types.items():
            for cls in classes:
                for sup in closure(sub, cls):
                    if sup not in classes:
                        out.add(node, RDF.type, sup)
        ceiling = CEILINGS[max(rank(known[s].ceiling) for s in sources)]
        return self.put(name, out, ceiling=ceiling, scope="inferred",
                        source=f"rdfs/skos closure of {', '.join(sources)}", derived_from=sources)


# ---------------------------------------------------------------------------
# sync: rebuild the governed graphs from the records that passed the gates
# ---------------------------------------------------------------------------


def provenance_graphs(rows: list[dict[str, Any]]) -> dict[str, Graph]:
    """The provenance projection split into one graph per ceiling.

    An entity with a ceiling goes to that ceiling's graph.  Anything without
    one (an activity, an episode id, a blank-node approval, a person) goes
    where its most protected neighbour within two hops goes: an approval of
    a HIGH copy is HIGH, because who approved what is about that copy.
    Unconnected, it is LOW.
    """
    from acc.attribution import person_of  # noqa: PLC0415
    from acc.lib.rdf.prov import project  # noqa: PLC0415

    g = project(rows, person_of=person_of).graph
    adjacent: dict[Any, set[Any]] = {}
    direct: dict[Any, int] = {}
    for s, p, o in g:
        if p == ACC.ceiling:
            direct[s] = max(direct.get(s, 0), rank(str(o).rsplit(":", 1)[-1]))
            continue
        if not isinstance(o, Literal):
            adjacent.setdefault(s, set()).add(o)
            adjacent.setdefault(o, set()).add(s)
    level: dict[Any, int] = {}
    for s, _, _ in g:
        if s in level:
            continue
        if s in direct:
            level[s] = direct[s]
            continue
        hood = set(adjacent.get(s, ()))
        for n in list(hood):
            hood |= adjacent.get(n, set())
        level[s] = max((direct[n] for n in hood if n in direct), default=0)
    out: dict[str, Graph] = {c: Graph() for c in CEILINGS}
    for s, p, o in g:
        out[CEILINGS[level[s]]].add(s, p, o)
    return out


def sync(store: KnowledgeStore, *, redis_client: Any = None, hub_collective_id: str = "",
         ledger_limit: int = 100_000) -> list[GraphInfo]:
    """Rebuild this collective's governed graphs.  Operator-run."""
    from acc import refinements  # noqa: PLC0415
    from acc.domain_taxonomy import resolve  # noqa: PLC0415

    rows = refinements.load(redis_client, store.collective_id, limit=ledger_limit)
    written = []
    for ceiling, graph in provenance_graphs(rows).items():
        written.append(store.put(
            f"provenance-{ceiling.lower()}", graph, ceiling=ceiling, scope="provenance",
            source=f"refinement ledger of {store.collective_id} ({len(rows)} rows)",
            bnode_scope="provenance"))
    tax = resolve(collective_id=store.collective_id, hub_collective_id=hub_collective_id,
                  redis_client=redis_client, publish_pack=False)
    if tax.taxonomy is not None:
        written.append(store.put("taxonomy", tax.taxonomy.to_graph(), ceiling="LOW",
                                 scope="taxonomy", source=tax.origin))
    return written
