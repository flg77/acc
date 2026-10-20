"""Knowledge map and conflicts — the named-graph store, seen (RDF-07 on RDF-04).

`20260928-rdf-provenance-and-memory-views`, the last follow-up; stacked on
the knowledge store (:mod:`acc.kg`, #516).  Two views, rendered by the Web
GUI's Knowledge section and the TUI's Memory screen like the other four:

* :func:`map_view` -- what the store holds that the viewer may read: the
  graphs (name, ceiling, size, where they came from), the domain taxonomy as
  a tree (``skos:broader``), and -- given a *focus* node -- that node's
  neighbourhood across every readable graph.
* :func:`conflicts_view` -- the same subject and predicate carrying different
  literal values in **different graphs**: two sources disagree.  The view
  shows both values with their graph, ceiling and source; it never picks one
  (RDF-00 §3: the graph finds the disagreement, people decide).  Today the
  store holds the ledger projection and the taxonomy, which rarely disagree;
  the detector is generic, so the private and shared fact graphs agents
  propose into (RDF-05, #517) feed it as they arrive.

**The same policy as the other views** (:class:`acc.memory_views.Viewer`):

* the store binds the ceiling -- :meth:`KnowledgeStore.query` reads only the
  graphs at or below it, whatever the query says;
* a non-operator is not shown the **provenance** graphs here: who learned what
  from whom is the Lineage / My-contributions views' business, filtered there
  to the notes the viewer is a source of.  The map would otherwise show every
  LOW note of the collective to anyone;
* a non-operator is never shown a person: person nodes and the predicates that
  point at people are dropped before the response is built;
* only an operator is told how many graphs sit above their ceiling.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("acc.knowledge_map")

#: Graph scopes a non-operator may read on the map.  Provenance is excluded on
#: purpose (see the module docstring); the store's own ``_`` graphs are never read.
_PROVENANCE_SCOPE = "provenance"

#: Predicates whose object can be a person.
_PERSONAL = (
    "urn:acc:vocab:sourceRequester",
    "http://www.w3.org/ns/prov#agent",
    "http://www.w3.org/ns/prov#qualifiedAssociation",
    "http://www.w3.org/ns/prov#wasAssociatedWith",
    "urn:acc:vocab:owner",
)

#: Predicates that hold several values by design -- never a conflict.
_MULTI_VALUED = (
    "http://www.w3.org/2004/02/skos/core#altLabel",
    "http://www.w3.org/2004/02/skos/core#hiddenLabel",
    "http://www.w3.org/2004/02/skos/core#example",
    "http://www.w3.org/2004/02/skos/core#note",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
    "http://purl.org/dc/terms/modified",
)

NEIGHBOURHOOD_LIMIT = 200
CONFLICT_LIMIT = 100


def store_for(collective_id: str) -> Any:
    """The collective's knowledge store, opened only when it already exists on
    disk (a surface never creates one); ``None`` otherwise."""
    from acc.kg import KGError, KnowledgeStore, default_path  # noqa: PLC0415
    path = default_path(collective_id)
    if not path.is_dir():
        return None
    try:
        return KnowledgeStore(collective_id, path)
    except KGError:
        logger.warning("knowledge_map: cannot open the store at %s", path, exc_info=True)
        return None


def _is_person(value: str) -> bool:
    return value.startswith("urn:acc:person:") or value.startswith("_:person")


def _short(value: str) -> str:
    """A readable label: an IRI's last segment, a literal as it is."""
    text = str(value)
    for sep in ("#", ":", "/"):
        if text.startswith(("urn:", "http")) and sep in text:
            text = text.rsplit(sep, 1)[-1] or text
    return text


def _readable(store: Any, viewer: Any) -> list[Any]:
    graphs = store.graphs(viewer.ceiling)
    if viewer.operator:
        return graphs
    return [g for g in graphs if g.scope != _PROVENANCE_SCOPE]


def _graph_filter(store: Any, graphs: list[Any], var: str) -> str:
    """A FILTER keeping *var* inside *graphs* (the store already bounds the
    dataset by ceiling; this narrows it further for a non-operator)."""
    iris = ", ".join(f"<{g.iri}>" for g in graphs)
    return f"FILTER({var} IN ({iris}))" if iris else "FILTER(false)"


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------

def map_view(store: Any, viewer: Any, *, focus: str = "", limit: int = NEIGHBOURHOOD_LIMIT) -> dict[str, Any]:
    """The readable graphs, the domain tree, and (with *focus*) a neighbourhood."""
    out: dict[str, Any] = {"available": store is not None, "graphs": [], "concepts": [],
                           "focus": focus, "nodes": [], "edges": [], "as_of": time.time()}
    if store is None:
        return out
    graphs = _readable(store, viewer)
    out["graphs"] = [{"name": g.name, "ceiling": g.ceiling, "triples": g.triples,
                      "scope": g.scope, "source": g.source, "modified": g.modified}
                     for g in graphs]
    if viewer.operator:
        out["above_your_ceiling"] = store.hidden_count(viewer.ceiling)
    if not graphs:
        return out
    out["concepts"] = _concepts(store, viewer, graphs)
    if focus:
        out["nodes"], out["edges"] = _neighbourhood(store, viewer, graphs, focus, limit)
    return out


def _concepts(store: Any, viewer: Any, graphs: list[Any]) -> list[dict[str, str]]:
    """Every ``skos:Concept`` with its label and broader concept."""
    q = f"""
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT DISTINCT ?c ?label ?broader WHERE {{
      GRAPH ?g {{ ?c a skos:Concept . OPTIONAL {{ ?c skos:prefLabel ?label }}
                  OPTIONAL {{ ?c skos:broader ?broader }} }}
      {_graph_filter(store, graphs, "?g")}
    }} ORDER BY ?c LIMIT 2000"""
    rows = store.query(q, reader_ceiling=viewer.ceiling).rows
    seen: dict[str, dict[str, str]] = {}
    for r in rows:
        c = r.get("c", "")
        entry = seen.setdefault(c, {"iri": c, "label": r.get("label") or _short(c), "broader": ""})
        if r.get("broader") and not entry["broader"]:
            entry["broader"] = r["broader"]
    return list(seen.values())


def _neighbourhood(store: Any, viewer: Any, graphs: list[Any], focus: str, limit: int):
    """The focus node's outgoing and incoming triples, as nodes and edges."""
    if not focus.startswith(("urn:", "http://", "https://")):
        return [], []
    f = focus.replace(">", "")
    q = f"""
    SELECT ?g ?dir ?p ?o (isLiteral(?o) AS ?lit) WHERE {{
      {{ GRAPH ?g {{ <{f}> ?p ?o }} BIND("out" AS ?dir) }}
      UNION
      {{ GRAPH ?g {{ ?o ?p <{f}> }} BIND("in" AS ?dir) }}
      {_graph_filter(store, graphs, "?g")}
    }} LIMIT {int(limit)}"""
    rows = store.query(q, reader_ceiling=viewer.ceiling).rows
    ceiling_of = {g.iri: g.ceiling for g in graphs}
    nodes: dict[str, dict[str, Any]] = {focus: {"id": focus, "kind": "focus", "label": _short(focus),
                                                "attributes": {}}}
    edges: list[dict[str, str]] = []
    for r in rows:
        p, o, g = r.get("p", ""), r.get("o", ""), r.get("g", "")
        if not viewer.operator and (p in _PERSONAL or _is_person(o)):
            continue
        if r.get("lit") in ("true", "1") and r.get("dir") == "out":
            nodes[focus]["attributes"].setdefault(_short(p), []).append(o)
            continue
        if o not in nodes:
            nodes[o] = {"id": o, "kind": "person" if _is_person(o) else "entity",
                        "label": "a person" if _is_person(o) else _short(o),
                        "ceiling": ceiling_of.get(g, "")}
        src, dst = (focus, o) if r.get("dir") == "out" else (o, focus)
        edges.append({"source": src, "target": dst, "kind": _short(p), "graph": _short(g)})
    return list(nodes.values()), edges


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

def conflicts_view(store: Any, viewer: Any, *, limit: int = CONFLICT_LIMIT) -> dict[str, Any]:
    """Subject + predicate with different literal values in different graphs."""
    out: dict[str, Any] = {"available": store is not None, "conflicts": [], "as_of": time.time()}
    if store is None:
        return out
    graphs = [g for g in _readable(store, viewer) if g.scope != _PROVENANCE_SCOPE]
    if len(graphs) < 2:
        return out
    by_iri = {g.iri: g for g in graphs}
    skip = ", ".join(f"<{p}>" for p in _MULTI_VALUED)
    q = f"""
    SELECT ?s ?p ?o1 ?g1 ?o2 ?g2 WHERE {{
      GRAPH ?g1 {{ ?s ?p ?o1 }}
      GRAPH ?g2 {{ ?s ?p ?o2 }}
      FILTER(STR(?g1) < STR(?g2))
      FILTER(isLiteral(?o1) && isLiteral(?o2) && STR(?o1) != STR(?o2))
      FILTER(LANG(?o1) = LANG(?o2))
      FILTER(?p NOT IN ({skip}))
      {_graph_filter(store, graphs, "?g1")}
      {_graph_filter(store, graphs, "?g2")}
    }} ORDER BY ?s ?p LIMIT {int(limit) * 4}"""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for r in store.query(q, reader_ceiling=viewer.ceiling).rows:
        s, p = r.get("s", ""), r.get("p", "")
        if not viewer.operator and (p in _PERSONAL or _is_person(s)):
            continue
        entry = grouped.setdefault((s, p), {"subject": s, "subject_label": _short(s),
                                            "predicate": p, "predicate_label": _short(p),
                                            "values": []})
        for o, g in ((r.get("o1", ""), r.get("g1", "")), (r.get("o2", ""), r.get("g2", ""))):
            info = by_iri.get(g)
            claim = {"value": o, "graph": info.name if info else _short(g),
                     "ceiling": info.ceiling if info else "", "source": info.source if info else ""}
            if claim not in entry["values"]:
                entry["values"].append(claim)
        if len(grouped) >= limit:
            break
    out["conflicts"] = list(grouped.values())
    return out


__all__ = ["CONFLICT_LIMIT", "NEIGHBOURHOOD_LIMIT", "conflicts_view", "map_view", "store_for"]
