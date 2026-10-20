"""What an agent remembers, what it learned, and why — one view model, two surfaces.

OpenSpec ``20260928-rdf-provenance-and-memory-views`` Phase 3 (RDF-07).  The
TUI's Memory screen and the Web GUI's Knowledge section render the **same**
four views, built here:

* :func:`memory_view` -- the notes held per tier (private, shared, hub), with
  ceiling, dissent and how many people each rests on.
* :func:`learnings_view` -- the lessons agents passed to each other, who
  adopted them, how many tasks used them and how those tasks ended.
* :func:`lineage_view` -- why one note exists and where it went, as a graph
  (episodes -> note -> proposals -> copies with their approvals -> tasks),
  built on the PROV-O projection of the refinement ledger
  (:mod:`acc.lib.rdf.prov`, RDF-03).
* :func:`contributions_view` -- a person's own notes and how far they
  travelled, counted in **instances**, never in people.

**One visibility policy, decided here and nowhere else.**  The surfaces
render what they are given and filter nothing.  The policy is the board's
(D-017, :func:`acc.work_board.viewer_can_see`) plus the information rule:

* an **operator** sees every note at or under their ceiling;
* anyone else sees only the notes **they are a source of** -- matched by
  person, so ``slack:U1@C1`` and ``slack:U1@C2`` are one person -- and only
  under their ceiling;
* what a viewer may not see is **left out, not counted**: no "3 hidden", no
  dangling edge to a node that is not there (RDF-00 §1.4).  Only an operator
  is told how much sits above their ceiling;
* people are never named to a non-operator: a note says how many people it
  rests on, an approval says *a person* at which tier.

The sources are passed in (:class:`Sources`), so the views are pure over a
Redis client and a list of ledger rows; :func:`default_sources` builds them
from the ambient config for a surface.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from acc.attribution import person_of, people_in
from acc.identity import Tier, exceeds_ceiling

logger = logging.getLogger("acc.memory_views")

TIER_PRIVATE = "private"
TIER_SHARED = "shared"
TIER_HUB = "hub"

#: Ledger rows read per collective; the Redis mirror keeps 30 days.
LEDGER_LIMIT = 5000
#: Lessons listed by the learnings view (newest first).
LESSON_LIMIT = 50


# ---------------------------------------------------------------------------
# Who is looking
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Viewer:
    """The principal a view is built for."""

    subject: str          # the attribution string, e.g. ``webgui:alice``
    tier: str
    ceiling: str          # the effective ceiling

    @property
    def operator(self) -> bool:
        return str(self.tier or "").lower() == Tier.OPERATOR

    @property
    def person(self) -> str:
        return person_of(self.subject)

    @classmethod
    def of(cls, principal: Any) -> "Viewer":
        """From an :class:`acc.identity.Principal`."""
        return cls(subject=principal.attribution(), tier=str(principal.tier),
                   ceiling=principal.effective_ceiling)

    def may_read(self, ceiling: str) -> bool:
        """The information rule.  A blank ceiling reads as CRITICAL."""
        return not exceeds_ceiling(str(ceiling or "CRITICAL"), self.ceiling)

    def is_source(self, requesters: list[str]) -> bool:
        who = self.person
        return bool(who) and who in people_in(requesters)

    def sees(self, ceiling: str, requesters: list[str]) -> bool:
        """The whole policy for one note."""
        if not self.may_read(ceiling):
            return False
        return self.operator or self.is_source(requesters)


# ---------------------------------------------------------------------------
# Where the data comes from
# ---------------------------------------------------------------------------

@dataclass
class Sources:
    """A Redis client, the collective and its hub, and how to read the ledger.

    *ledger_rows* is called with a collective id; the default reads the
    refinement ledger (Redis mirror, then the file).  *vector* is the durable
    note store (a backend with ``rows``); the private tier reads it beside the
    hot cache, which keeps only the newest notes for six hours.
    """

    redis: Any
    collective_id: str
    hub_collective_id: str = ""
    ledger_rows: Any = None
    vector: Any = None

    @property
    def collectives(self) -> list[str]:
        return [c for c in dict.fromkeys((self.collective_id, self.hub_collective_id)) if c]

    def rows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for cid in self.collectives:
            if self.ledger_rows is not None:
                out.extend(self.ledger_rows(cid))
            else:
                from acc import refinements  # noqa: PLC0415
                out.extend(refinements.load(self.redis, cid, limit=LEDGER_LIMIT))
        return out


_REDIS_CLIENT: Any = None


def default_sources(collective_id: str = "") -> Sources:
    """Sources from the ambient config (``acc-config.yaml`` / env).

    The Redis client is built once per process; a missing or unreachable Redis
    yields ``redis=None`` and views that say so rather than fail.
    """
    global _REDIS_CLIENT
    from acc.config import load_config  # noqa: PLC0415
    cfg = load_config()
    if _REDIS_CLIENT is None and cfg.working_memory.url:
        try:
            import redis as redis_lib  # noqa: PLC0415
            _REDIS_CLIENT = redis_lib.from_url(
                cfg.working_memory.url,
                password=cfg.working_memory.password or None,
                decode_responses=False,
            )
        except Exception:  # noqa: BLE001
            logger.warning("memory_views: no Redis client", exc_info=True)
    return Sources(redis=_REDIS_CLIENT,
                   collective_id=collective_id or cfg.agent.collective_id,
                   hub_collective_id=cfg.agent.hub_collective_id,
                   vector=_durable_store(cfg))


_VECTOR_STORE: Any = None


def _durable_store(cfg: Any) -> Any:
    """The LanceDB note table, opened read-side once per process -- only when
    its directory already exists, so a surface never creates a database."""
    global _VECTOR_STORE
    if _VECTOR_STORE is None:
        import os  # noqa: PLC0415
        path = str(getattr(getattr(cfg, "vector_db", None), "lancedb_path", "") or "")
        if path and os.path.isdir(path):
            try:
                from acc.backends.vector_lancedb import LanceDBBackend  # noqa: PLC0415
                _VECTOR_STORE = LanceDBBackend(path)
            except Exception:  # noqa: BLE001
                logger.warning("memory_views: no durable note store at %s", path, exc_info=True)
    return _VECTOR_STORE


def _decode(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, (bytes, bytearray)) else str(value)


def _keys(redis: Any, pattern: str) -> list[str]:
    lister = getattr(redis, "scan_iter", None) or getattr(redis, "keys", None)
    if lister is None:
        return []
    try:
        return sorted(_decode(k) for k in (lister(pattern) or ()))
    except Exception:  # noqa: BLE001
        logger.debug("memory_views: cannot list %s", pattern, exc_info=True)
        return []


def _json(redis: Any, key: str) -> Any:
    try:
        raw = redis.get(key)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(_decode(raw))
    except (json.JSONDecodeError, TypeError):
        return None


def _requesters(entry: dict[str, Any]) -> list[str]:
    value = entry.get("source_requesters") or []
    return [str(r) for r in value] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Memory: the notes held per tier
# ---------------------------------------------------------------------------

def _durable_entries(sources: Sources, seen: set[str]) -> list[dict[str, Any]]:
    """Private notes from the durable ``memory_notes`` table that the hot
    cache no longer (or never) held.

    Safe to show only because the table carries each note's ``ceiling``; a row
    written before the column existed has it blank, which reads as CRITICAL.
    Published copies are not read here -- they live in the shared keys.
    """
    vector = sources.vector
    if vector is None or not hasattr(vector, "rows"):
        return []
    try:
        rows = vector.rows("memory_notes")
    except Exception:  # noqa: BLE001
        logger.debug("memory_views: cannot read memory_notes", exc_info=True)
        return []
    out = []
    for row in rows:
        note_id = str(row.get("id") or "")
        if not note_id or note_id in seen or not row.get("summary"):
            continue
        if str(row.get("tier") or TIER_PRIVATE) != TIER_PRIVATE:
            continue
        seen.add(note_id)
        out.append({
            "summary": row.get("summary"), "note_id": note_id,
            "source_requesters": _json_list(row.get("source_requesters")),
            "source_ids": _json_list(row.get("source_ids")),
            "ceiling": str(row.get("ceiling") or ""), "dissent": row.get("dissent") or "",
            "at": float(row.get("ts") or 0.0),
            "_collective": sources.collective_id, "_role": str(row.get("role_label") or ""),
            "_where": str(row.get("scope") or ""), "_tier": TIER_PRIVATE, "_durable": True,
        })
    return out


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def _note_entries(sources: Sources) -> list[dict[str, Any]]:
    """Every note entry in the hot cache, the published tiers and the durable
    table, unfiltered.

    Private: ``acc:{cid}:memory_notes:{role}:{scope}`` plus the durable
    ``memory_notes`` rows the cache does not hold; shared:
    ``acc:{cid}:memory_notes_shared:{role}:{destination}``; the hub's
    enterprise tier is the hub collective's shared key for ``enterprise``.
    """
    out = _cached_entries(sources)
    seen = {str(e.get("note_id") or "") for e in out if e["_tier"] == TIER_PRIVATE}
    return out + _durable_entries(sources, seen)


def _cached_entries(sources: Sources) -> list[dict[str, Any]]:
    from acc.memory_reflection import HUB_TIER  # noqa: PLC0415
    redis = sources.redis
    if redis is None:
        return []
    out: list[dict[str, Any]] = []
    hub = sources.hub_collective_id
    for cid in sources.collectives:
        for kind, tier in (("memory_notes", TIER_PRIVATE), ("memory_notes_shared", TIER_SHARED)):
            if cid == hub and cid != sources.collective_id and tier == TIER_PRIVATE:
                continue  # a hub's own private notes are not this instance's to show
            prefix = f"acc:{cid}:{kind}:"
            for key in _keys(redis, f"{prefix}*"):
                role, _, where = key[len(prefix):].partition(":")
                if not role or not where:
                    continue
                entries = _json(redis, key)
                if not isinstance(entries, list):
                    continue
                shown_tier = TIER_HUB if (tier == TIER_SHARED and where == HUB_TIER) else tier
                if shown_tier == TIER_HUB and cid != hub:
                    continue
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("summary"):
                        out.append({**entry, "_collective": cid, "_role": role,
                                    "_where": where, "_tier": shown_tier})
    return out


def _note_row(entry: dict[str, Any], viewer: Viewer) -> dict[str, Any]:
    requesters = _requesters(entry)
    return {
        "note_id": str(entry.get("note_id") or ""),
        "collective": entry["_collective"],
        "role": entry["_role"],
        "tier": entry["_tier"],
        # private: the scope it was distilled in; shared / hub: where it was published
        "where": entry["_where"],
        "ceiling": str(entry.get("ceiling") or "CRITICAL").upper(),
        "summary": str(entry.get("summary") or ""),
        "dissent": str(entry.get("dissent") or ""),
        "people": len(people_in(requesters)),
        "episodes": len(entry.get("source_ids") or []),
        "published_at": float(entry.get("at") or 0.0),
        "mine": viewer.is_source(requesters),
        # True when the note came from the durable table, not the hot cache
        "stored_only": bool(entry.get("_durable")),
    }


def memory_view(sources: Sources, viewer: Viewer) -> dict[str, Any]:
    """The notes this viewer may see, grouped by tier."""
    tiers: dict[str, list[dict[str, Any]]] = {TIER_PRIVATE: [], TIER_SHARED: [], TIER_HUB: []}
    above = 0
    for entry in _note_entries(sources):
        requesters = _requesters(entry)
        if not viewer.sees(str(entry.get("ceiling") or ""), requesters):
            if viewer.operator:
                above += 1
            continue
        tiers[entry["_tier"]].append(_note_row(entry, viewer))
    for rows in tiers.values():
        rows.sort(key=lambda r: (r["role"], r["where"], -r["published_at"], r["note_id"]))
    out: dict[str, Any] = {"collectives": sources.collectives, "tiers": tiers,
                           "as_of": time.time(), "available": sources.redis is not None or sources.vector is not None}
    if viewer.operator:
        out["above_your_ceiling"] = above
    return out


# ---------------------------------------------------------------------------
# Learnings: lessons, adoption, use, outcome
# ---------------------------------------------------------------------------

def learnings_view(sources: Sources, viewer: Viewer, *, limit: int = LESSON_LIMIT) -> dict[str, Any]:
    """The lessons agents shared, newest first, with what became of each."""
    from acc.signals import (  # noqa: PLC0415
        redis_lesson_key,
        redis_lesson_used_key,
        redis_lessons_index_key,
    )
    redis, cid = sources.redis, sources.collective_id
    lessons: list[dict[str, Any]] = []
    if redis is None or not cid:
        return {"collective": cid, "lessons": lessons, "as_of": time.time(),
                "available": redis is not None}
    adopted: dict[str, set[str]] = {}
    verdicts: dict[str, dict[str, int]] = {}
    for row in sources.rows():
        if row.get("collective_id") != cid:
            continue
        lid = str((row.get("target") or {}).get("id") or "")
        if row.get("kind") == "adopt" and lid:
            adopted.setdefault(lid, set()).add(str(row.get("role_label") or ""))
        elif row.get("kind") == "outcome" and lid:
            verdict = str((row.get("measured") or {}).get("verdict") or "unknown")
            tally = verdicts.setdefault(lid, {})
            tally[verdict] = tally.get(verdict, 0) + 1
    try:
        ids = [_decode(i) for i in redis.zrevrange(redis_lessons_index_key(cid), 0, -1)]
    except Exception:  # noqa: BLE001
        ids = []
    for lid in ids:
        lesson = _json(redis, redis_lesson_key(cid, lid))
        if not isinstance(lesson, dict):
            continue  # expired: the 7-day copy is gone, the note remains
        if not viewer.sees(str(lesson.get("ceiling") or ""), _requesters(lesson)):
            continue
        try:
            used = int(redis.scard(redis_lesson_used_key(cid, lid)) or 0)
        except Exception:  # noqa: BLE001
            used = 0
        lessons.append({
            "lesson_id": lid,
            "role": str(lesson.get("role_label") or ""),
            "kind": str(lesson.get("kind") or "note"),
            "domain": str(lesson.get("domain_tag") or ""),
            "scope": str(lesson.get("scope") or ""),
            "ceiling": str(lesson.get("ceiling") or "CRITICAL").upper(),
            "summary": str(lesson.get("summary") or ""),
            "dissent": str((lesson.get("evidence") or {}).get("dissent") or ""),
            "confidence": float(lesson.get("confidence") or 0.0),
            "ts": float(lesson.get("ts") or 0.0),
            "addressed": bool(lesson.get("target_agent_id")),
            "adopted_by": sorted(r for r in adopted.get(lid, ()) if r),
            "used_by_tasks": used,
            "outcomes": verdicts.get(lid, {}),
            "mine": viewer.is_source(_requesters(lesson)),
        })
        if len(lessons) >= limit:
            break
    return {"collective": cid, "lessons": lessons, "as_of": time.time(), "available": True}


# ---------------------------------------------------------------------------
# Lineage: why one note exists, and where it went
# ---------------------------------------------------------------------------

def _projection(sources: Sources):
    from acc.lib.rdf.prov import Index, project  # noqa: PLC0415
    proj = project(sources.rows(), person_of=person_of)
    return proj, Index(proj.graph)


def _value(idx: Any, node: Any, pred: str) -> str:
    from acc.lib.rdf.terms import Literal  # noqa: PLC0415
    for obj in sorted(idx.objects(node, pred), key=str):
        return obj.value if isinstance(obj, Literal) else str(obj).rsplit(":", 1)[-1]
    return ""


def _people_of(idx: Any, node: Any) -> set:
    from acc.lib.rdf.terms import ACC  # noqa: PLC0415
    return set(idx.objects(node, ACC.sourceRequester))


def _viewer_is_source(proj: Any, idx: Any, node: Any, viewer: Viewer) -> bool:
    if not viewer.person:
        return False
    return proj.person(viewer.person) in _people_of(idx, node)


def _short(iri: str) -> str:
    return str(iri).replace("urn:acc:collective:", "", 1)


def find_note(proj: Any, idx: Any, prefix: str, viewer: Viewer) -> str | None:
    """The one note *prefix* names that this viewer may see, else ``None``.

    A note the viewer may not see is indistinguishable from no note at all.
    """
    from acc.lib.rdf.prov import find_notes  # noqa: PLC0415
    hits = [n for n in find_notes(proj.graph, prefix)
            if viewer.may_read(_value(idx, n, _ceiling_pred()))
            and (viewer.operator or _viewer_is_source(proj, idx, n, viewer))]
    return hits[0] if len(hits) == 1 else None


def _ceiling_pred() -> str:
    from acc.lib.rdf.terms import ACC  # noqa: PLC0415
    return ACC.ceiling


def lineage_view(sources: Sources, viewer: Viewer, note_prefix: str) -> dict[str, Any] | None:
    """The lineage of one note as ``{nodes, edges}``, or ``None`` when the
    viewer may not see a note by that id (or the id is ambiguous)."""
    from acc.lib.rdf.prov import approvals_of, lineage  # noqa: PLC0415
    from acc.lib.rdf.terms import ACC  # noqa: PLC0415

    proj, idx = _projection(sources)
    note = find_note(proj, idx, note_prefix, viewer)
    if note is None:
        return None
    lin = lineage(proj.graph, note, reader_ceiling=viewer.ceiling, index=idx)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def add(node_id: str, kind: str, label: str, **attrs: Any) -> str:
        nodes.append({"id": node_id, "kind": kind, "label": label, **attrs})
        return node_id

    def link(src: str, dst: str, kind: str) -> None:
        edges.append({"source": src, "target": dst, "kind": kind})

    note_id = add(_short(note), "note", f"note {note.rsplit(':', 1)[-1][:8]}",
                  ceiling=_value(idx, note, ACC.ceiling), role=_value(idx, note, ACC.role),
                  scope=_value(idx, note, ACC.scope), dissent=_value(idx, note, ACC.dissent),
                  people=len(_people_of(idx, note)), tier=TIER_PRIVATE)
    if lin.upstream:
        ep = add("episodes", "episodes", f"{len(lin.upstream)} episode(s)", count=len(lin.upstream))
        link(ep, note_id, "derived_from")
    for proposal in lin.proposals:
        pid = add(_short(proposal), "proposal", f"proposed -> {_value(idx, proposal, ACC.destination)}",
                  destination=_value(idx, proposal, ACC.destination))
        link(note_id, pid, "proposed")
    copy_ids = set(lin.copies)
    for copy in lin.copies:
        dest = _value(idx, copy, ACC.destination)
        cid = add(_short(copy), "copy", f"-> {dest}", destination=dest,
                  tier=TIER_HUB if dest.startswith("hub:") else TIER_SHARED,
                  ceiling=_value(idx, copy, ACC.ceiling), collective=_value(idx, copy, ACC.collective))
        parents = [str(p) for p in idx.objects(copy, _prov("wasDerivedFrom"))
                   if str(p) == note or str(p) in copy_ids]
        for parent in parents or [note]:
            link(_short(parent), cid, "published")
        for i, (person, tier) in enumerate(approvals_of(proj.graph, copy, index=idx)):
            who = (proj.persons.subject_of(person) if viewer.operator else "") or "a person"
            aid = add(f"{_short(copy)}#approval-{i}", "approval",
                      f"approved by {who}" + (f" ({tier})" if tier else ""), tier=tier, who=who)
            link(aid, cid, "approved")
    if lin.used_by:
        tid = add("tasks", "tasks", f"used by {len(lin.used_by)} task(s)", count=len(lin.used_by),
                  verdicts=_verdicts(idx, lin.used_by))
        link(note_id, tid, "used_by")
    for act in lin.invalidated_by:
        xid = add(_short(act), "invalidated", "erased / rolled back")
        link(xid, note_id, "invalidated")
    out: dict[str, Any] = {"note": _short(note), "nodes": nodes, "edges": edges, "as_of": time.time()}
    if viewer.operator:
        out["above_your_ceiling"] = lin.hidden
    return out


def _prov(term: str) -> str:
    from acc.lib.rdf.terms import PROV  # noqa: PLC0415
    return PROV[term]


def _verdicts(idx: Any, tasks: list[str]) -> dict[str, int]:
    from acc.lib.rdf.terms import ACC  # noqa: PLC0415
    tally: dict[str, int] = {}
    for task in tasks:
        verdict = _value(idx, task, ACC.verdict) or "unknown"
        tally[verdict] = tally.get(verdict, 0) + 1
    return tally


# ---------------------------------------------------------------------------
# My contributions: my notes, and how far they travelled
# ---------------------------------------------------------------------------

def contributions_view(sources: Sources, viewer: Viewer) -> dict[str, Any]:
    """The viewer's own notes: where each was published, in how many
    instances, how many tasks used it.  Counts instances, never people."""
    from acc.lib.rdf.prov import contributions  # noqa: PLC0415
    from acc.lib.rdf.terms import ACC, PROV, RDF  # noqa: PLC0415

    if not viewer.person:
        return {"notes": [], "instances": 0, "as_of": time.time()}
    proj, idx = _projection(sources)
    mine = contributions(proj, viewer.person)
    summaries = {str(e.get("note_id") or ""): str(e.get("summary") or "")
                 for e in _note_entries(sources)}
    copies_of: dict[str, list[str]] = {}
    for copy in mine.copies:
        for root in _roots(idx, copy, PROV, ACC):
            copies_of.setdefault(root, []).append(copy)
    rows = []
    instances: set[str] = set()
    for note in mine.notes:
        if not viewer.may_read(_value(idx, note, ACC.ceiling)):
            continue
        copies = [c for c in copies_of.get(note, []) if viewer.may_read(_value(idx, c, ACC.ceiling))]
        where = sorted({_value(idx, c, ACC.destination) for c in copies})
        held_in = {_value(idx, c, ACC.collective) for c in copies} - {""}
        instances |= held_in
        used = [t for t in idx.subjects(PROV.used, note) if ACC.Task in idx.objects(t, RDF.type)]
        note_id = note.rsplit(":", 1)[-1]
        rows.append({
            "note_id": note_id,
            "role": _value(idx, note, ACC.role),
            "ceiling": _value(idx, note, ACC.ceiling),
            "summary": summaries.get(note_id, ""),
            "published_to": where,
            "in_hub": any(w.startswith("hub:") for w in where),
            "instances": len(held_in),
            "used_by_tasks": len(used),
        })
    rows.sort(key=lambda r: (-r["used_by_tasks"], -r["instances"], r["note_id"]))
    return {"notes": rows, "instances": len(instances), "erased": len(mine.already_erased),
            "as_of": time.time()}


def _roots(idx: Any, copy: Any, PROV: Any, ACC: Any) -> list[str]:
    """The note(s) a copy ultimately derives from (through copies of copies)."""
    from acc.lib.rdf.terms import RDF  # noqa: PLC0415
    seen, stack, roots = {copy}, [copy], []
    while stack:
        current = stack.pop()
        for parent in idx.objects(current, PROV.wasDerivedFrom):
            if parent in seen:
                continue
            seen.add(parent)
            kinds = idx.objects(parent, RDF.type)
            if ACC.Note in kinds:
                roots.append(str(parent))
            elif ACC.PublishedNote in kinds:
                stack.append(parent)
    return roots


__all__ = [
    "LEDGER_LIMIT", "LESSON_LIMIT", "Sources", "TIER_HUB", "TIER_PRIVATE", "TIER_SHARED",
    "Viewer", "contributions_view", "default_sources", "find_note", "learnings_view",
    "lineage_view", "memory_view",
]
