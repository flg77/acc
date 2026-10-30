"""Graph-augmented retrieval — a bounded KNOWLEDGE_GRAPH block (RDF-06).

Vector recall finds *similar text*; it does not follow relations (a policy →
the products it caps → their owners).  This adds a small block of facts from
the collective's knowledge store to the prompt, **beside** the memory notes —
it never changes which notes are retrieved (graph-first retrieval is a later,
measured step).

How a fact gets in:

1. **Seeds.**  Entities whose label (``skos:prefLabel``, ``skos:altLabel``,
   ``dcterms:title``, ``rdfs:label``) occurs in the task text as a whole word,
   longest labels first, at most :data:`MAX_SEEDS`.
2. **One hop.**  Each seed's outgoing and incoming triples, objects and
   subjects shown by label when they have one.
3. **Bounded.**  At most :data:`MAX_FACTS` facts of at most :data:`MAX_FACT_CHARS`
   characters; the context budget then evicts this block **first** (it is the
   least proven block until PA-08 measures it).

Every read goes through :meth:`acc.kg.KnowledgeStore.query`, so the reader's
ceiling and private-graph ownership hold exactly as for ``kg_query``; the
ceiling and requester come from the admitted task.  Off unless the role sets
``graph_retrieval: true``, and ``ACC_GRAPH_RETRIEVAL=0`` switches it off
everywhere; off, or with nothing found, the prompt is byte-identical.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger("acc.kg_retrieval")

HEADING = (
    "KNOWLEDGE_GRAPH (facts from the collective's knowledge graph about terms in this task; "
    "readable at your ceiling -- use them where they apply, do not extend them):"
)
MAX_SEEDS = 5
MAX_FACTS = 20
MAX_FACT_CHARS = 300
MIN_LABEL_CHARS = 3

_LABEL_PROPS = "skos:prefLabel|skos:altLabel|dcterms:title|rdfs:label"


@dataclass
class GraphFacts:
    items: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)     # entity IRIs, for the outcome join


def enabled() -> bool:
    return os.environ.get("ACC_GRAPH_RETRIEVAL", "1").strip().lower() not in ("0", "false", "no", "off")


def _curie(iri: str) -> str:
    from acc.lib.rdf.terms import PREFIXES  # noqa: PLC0415

    for prefix, ns in PREFIXES.items():
        if iri.startswith(ns) and len(iri) > len(ns):
            return f"{prefix}:{iri[len(ns):]}"
    return iri


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_FACT_CHARS else text[: MAX_FACT_CHARS - 1] + "…"


def find_seeds(labels: list[tuple[str, str]], task_text: str, limit: int = MAX_SEEDS) -> list[str]:
    """Entity IRIs whose label occurs in *task_text* as a whole word,
    longest label first (``credit risk`` before ``risk``), one per entity."""
    text = task_text.lower()
    seen: list[str] = []
    for iri, label in sorted(labels, key=lambda p: (-len(p[1]), p[1], p[0])):
        label = label.strip().lower()
        if len(label) < MIN_LABEL_CHARS or iri in seen or not iri.startswith(("urn:", "http:", "https:")):
            continue                     # a blank node cannot be named in a query
        if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text):
            seen.append(iri)
            if len(seen) >= limit:
                break
    return seen


def facts_for(store, task_text: str, *, reader_ceiling: str, reader: str = "") -> GraphFacts:
    """The facts for one task.  Never raises: a missing store, a refused
    query or anything else yields no facts (and a debug line)."""
    out = GraphFacts()
    if not task_text.strip():
        return out
    try:
        rows = store.query(f"SELECT ?s ?l WHERE {{ ?s {_LABEL_PROPS} ?l }}",
                           reader_ceiling=reader_ceiling, reader=reader).rows
        labels = [(r["s"], r["l"]) for r in rows if r.get("s") and r.get("l")]
        label_of: dict[str, str] = {}
        for iri, label in labels:
            label_of.setdefault(iri, label)
        out.seeds = find_seeds(labels, task_text)
        seen: set[str] = set()
        for seed in out.seeds:
            name = label_of.get(seed) or _curie(seed)
            q_out = f"SELECT ?p ?o WHERE {{ <{seed}> ?p ?o }} LIMIT {MAX_FACTS}"
            q_in = f"SELECT ?s ?p WHERE {{ ?s ?p <{seed}> }} LIMIT {MAX_FACTS}"
            for r in store.query(q_out, reader_ceiling=reader_ceiling, reader=reader).rows:
                obj = label_of.get(r["o"]) or (_curie(r["o"]) if r["o"].startswith(("urn:", "http")) else r["o"])
                fact = _clip(f"{name} -- {_curie(r['p'])} -- {obj}")
                if fact not in seen:
                    seen.add(fact)
                    out.items.append(fact)
            for r in store.query(q_in, reader_ceiling=reader_ceiling, reader=reader).rows:
                subj = label_of.get(r["s"]) or _curie(r["s"])
                fact = _clip(f"{subj} -- {_curie(r['p'])} -- {name}")
                if fact not in seen:
                    seen.add(fact)
                    out.items.append(fact)
            if len(out.items) >= MAX_FACTS:
                break
        out.items = out.items[:MAX_FACTS]
    except Exception:  # noqa: BLE001 -- retrieval is advisory; the task never fails on it
        logger.debug("kg_retrieval: no graph facts", exc_info=True)
        return GraphFacts()
    return out


def facts_for_task(collective_id: str, task_text: str, *, reader_ceiling: str,
                   reader: str = "") -> GraphFacts:
    """Open the collective's store read-only and retrieve; no store, no facts."""
    try:
        from acc.kg import KnowledgeStore  # noqa: PLC0415
        store = KnowledgeStore(collective_id, read_only=True)
    except Exception:  # noqa: BLE001 -- no store yet, no extra, no volume
        return GraphFacts()
    return facts_for(store, task_text, reader_ceiling=reader_ceiling, reader=reader)


def graph_parts(facts: GraphFacts | None) -> tuple[str, list[str]]:
    """``(heading, items)`` for the context budget; empty when there are none."""
    if not facts or not facts.items:
        return "", []
    return HEADING, [f"- {fact}" for fact in facts.items]


def render_graph_block(facts: GraphFacts | None) -> str:
    """The unbudgeted rendering -- the same text the packer produces when
    nothing is dropped (heading, then one ``- `` line per fact)."""
    heading, items = graph_parts(facts)
    return "\n".join([heading, *items]) if items else ""
