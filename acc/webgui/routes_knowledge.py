"""acc-webgui — the Knowledge section: memory, learnings, lineage, contributions,
and (over the named-graph store, #516) the knowledge map and conflicts.

OpenSpec ``20260928-rdf-provenance-and-memory-views`` Phase 3 (RDF-07).  Every
endpoint returns a view from :mod:`acc.memory_views`, built for the session's
principal: the visibility policy (an operator sees everything under their
ceiling; anyone else their own notes) is applied there, before the response
exists, so nothing here filters and nothing in the browser can widen it.

Reads only, viewer role.  ``?format=jsonld`` on the lineage returns the same
subgraph as JSON-LD (RDF-00 §2, the JSON-LD read endpoint).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from acc.webgui.auth import Principal, require_viewer

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


def get_sources(collective_id: str):
    """The view sources for *collective_id* (overridable in tests)."""
    from acc.memory_views import default_sources  # noqa: PLC0415
    return default_sources(collective_id)


def get_store(collective_id: str):
    """The collective's knowledge store, or ``None`` (overridable in tests)."""
    from acc.knowledge_map import store_for  # noqa: PLC0415
    return store_for(collective_id)


def _viewer(principal: Principal):
    from acc.identity import from_web  # noqa: PLC0415
    from acc.memory_views import Viewer  # noqa: PLC0415
    return Viewer.of(from_web(principal.user, principal.role))


@router.get("/{collective_id}/memory")
def memory(collective_id: str, principal: Principal = Depends(require_viewer),
           sources=Depends(get_sources)) -> dict:
    """The notes held per tier (private · shared · hub) that this viewer may see."""
    from acc.memory_views import memory_view  # noqa: PLC0415
    return memory_view(sources, _viewer(principal))


@router.get("/{collective_id}/learnings")
def learnings(collective_id: str, limit: int = Query(50, ge=1, le=200),
              principal: Principal = Depends(require_viewer),
              sources=Depends(get_sources)) -> dict:
    """Lessons agents shared, who adopted them, how many tasks used them."""
    from acc.memory_views import learnings_view  # noqa: PLC0415
    return learnings_view(sources, _viewer(principal), limit=limit)


@router.get("/{collective_id}/lineage/{note_id}")
def lineage(collective_id: str, note_id: str,
            format: str = Query("graph", pattern="^(graph|jsonld)$"),
            principal: Principal = Depends(require_viewer),
            sources=Depends(get_sources)) -> dict:
    """Why one note exists and where it went.  404 covers "no such note",
    "ambiguous id" and "not yours to see" alike -- deliberately."""
    from acc.memory_views import lineage_view  # noqa: PLC0415
    viewer = _viewer(principal)
    view = lineage_view(sources, viewer, note_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no note {note_id!r} you can see")
    if format == "jsonld":
        return _jsonld(sources, viewer, view)
    return view


@router.get("/{collective_id}/contributions")
def contributions(collective_id: str, principal: Principal = Depends(require_viewer),
                  sources=Depends(get_sources)) -> dict:
    """The session user's own notes and how far they travelled (in instances)."""
    from acc.memory_views import contributions_view  # noqa: PLC0415
    return contributions_view(sources, _viewer(principal))


@router.get("/{collective_id}/map")
def knowledge_map(collective_id: str, focus: str = Query("", max_length=2048),
                  principal: Principal = Depends(require_viewer),
                  store=Depends(get_store)) -> dict:
    """The graphs this viewer may read, the domain tree, and (with ``focus``)
    one node's neighbourhood, from the named-graph store (#516)."""
    from acc.knowledge_map import map_view  # noqa: PLC0415
    return map_view(store, _viewer(principal), focus=focus)


@router.get("/{collective_id}/conflicts")
def conflicts(collective_id: str, limit: int = Query(100, ge=1, le=500),
              principal: Principal = Depends(require_viewer),
              store=Depends(get_store)) -> dict:
    """Two sources disagreeing: one subject and predicate, different values,
    different graphs.  Shown, never resolved."""
    from acc.knowledge_map import conflicts_view  # noqa: PLC0415
    return conflicts_view(store, _viewer(principal), limit=limit)


def _jsonld(sources, viewer, view: dict) -> dict:
    """The lineage's subgraph as JSON-LD: the drawn entities and the
    activities that generated them.  People appear only for an operator, and
    then as the projection's person nodes, never as subjects."""
    from acc.attribution import person_of  # noqa: PLC0415
    from acc.lib.rdf.graph import Graph  # noqa: PLC0415
    from acc.lib.rdf.prov import project  # noqa: PLC0415
    from acc.lib.rdf.terms import ACC, PROV  # noqa: PLC0415

    proj = project(sources.rows(), person_of=person_of)
    prefix = "urn:acc:collective:"
    keep = {prefix + n["id"] for n in view["nodes"] if n["kind"] in ("note", "proposal", "copy")}
    acts = {o for s, p, o in proj.graph if s in keep and p == PROV.wasGeneratedBy}
    # Every predicate whose object can be a person: the sources, the approvers,
    # and an activity's association (a proposal's proposer is a person).
    personal = (ACC.sourceRequester, PROV.agent, PROV.qualifiedAssociation,
                PROV.wasAssociatedWith)
    sub = Graph(t for t in proj.graph
                if (t[0] in keep or t[0] in acts) and (viewer.operator or t[1] not in personal))
    return sub.to_jsonld()
