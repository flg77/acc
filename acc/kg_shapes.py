"""The SHACL gate on what an agent may write into the knowledge store (RDF-05).

An LLM that extracts facts writes wrong predicates, invents entities and
repeats itself; a graph that fills from that unchecked is worse than no graph.
So nothing an agent proposes reaches the store unless it conforms to SHACL
shapes from a **signed pack** — the shapes are governance, never model input.

* The pack is named by ``ACC_KG_SHAPES`` and carries ``shapes/*.ttl``.
* **No shapes, no writes.**  A collective without a shapes pack refuses every
  ``kg_propose``: an unconstrained write path is exactly what this exists to
  prevent.
* What an agent sends is parsed with ACC's prefixes predeclared, bounded
  (``MAX_TRIPLES``, ``MAX_LITERAL``), and validated with ``pyshacl``.  A
  refusal returns the SHACL report so the model can correct and retry.

The arbiter validates again on receipt; the agent's check is a courtesy to the
model, the arbiter's is the gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acc.lib.rdf.graph import Graph
from acc.lib.rdf.terms import PREFIXES, BNode, Literal

ENV = "ACC_KG_SHAPES"
PACK_DIR = "shapes"
MAX_TRIPLES = 200
MAX_LITERAL = 4000


class ShapesError(ValueError):
    """No usable shapes, or a proposal that cannot be parsed or is too large."""


@dataclass
class Verdict:
    conforms: bool
    triples: int
    messages: list[str] = field(default_factory=list)
    graph: Graph | None = None


def load_shapes(name: str | None = None, registry: Any = None):
    """The rdflib graph of every ``shapes/*.ttl`` in the pack *name*
    (default: ``ACC_KG_SHAPES``), and the pack's ``name@version``."""
    import rdflib  # noqa: PLC0415

    from acc.pkg.registry import Registry  # noqa: PLC0415

    name = (name or os.environ.get(ENV, "")).strip()
    if not name:
        raise ShapesError(f"{ENV} is not set: no shapes, so nothing may be written")
    entries = [e for e in (registry or Registry()).list() if e.name == name]
    if not entries:
        raise ShapesError(f"shapes pack {name!r} is not installed")
    entry = sorted(entries, key=lambda e: e.installed_at)[-1]
    files = sorted((Path(entry.install_path) / PACK_DIR).glob("*.ttl"))
    if not files:
        raise ShapesError(f"pack {name}@{entry.version} has no {PACK_DIR}/*.ttl")
    shapes = rdflib.Graph()
    for f in files:
        shapes.parse(f, format="turtle")
    return shapes, f"{name}@{entry.version}"


def parse_proposal(text: str):
    """The rdflib graph of a proposal in Turtle or N-Triples, ACC prefixes predeclared."""
    import rdflib  # noqa: PLC0415

    head = "".join(f"@prefix {k}: <{v}> .\n" for k, v in PREFIXES.items())
    g = rdflib.Graph()
    try:
        g.parse(data=head + str(text or ""), format="turtle")
    except Exception as exc:  # noqa: BLE001 -- rdflib raises several kinds
        raise ShapesError(f"not Turtle: {str(exc).splitlines()[0][:200]}") from exc
    if len(g) == 0:
        raise ShapesError("no triples")
    if len(g) > MAX_TRIPLES:
        raise ShapesError(f"{len(g)} triples; at most {MAX_TRIPLES} per proposal")
    for _, _, o in g:
        if isinstance(o, rdflib.Literal) and len(str(o)) > MAX_LITERAL:
            raise ShapesError(f"a literal longer than {MAX_LITERAL} characters")
    return g


def to_acc_graph(rdflib_graph) -> Graph:
    """rdflib terms -> :mod:`acc.lib.rdf` terms, for the store."""
    import rdflib  # noqa: PLC0415

    def term(t):
        if isinstance(t, rdflib.BNode):
            return BNode(str(t))
        if isinstance(t, rdflib.Literal):
            return Literal(str(t), str(t.datatype or ""), str(t.language or ""))
        return str(t)

    out = Graph()
    for s, p, o in rdflib_graph:
        out.add(term(s), str(p), term(o))
    return out


def validate(text: str, shapes) -> Verdict:
    """Parse *text* and check it against *shapes*.  Never raises on a
    non-conforming proposal; raises :class:`ShapesError` on one that cannot
    be read at all."""
    from pyshacl import validate as shacl  # noqa: PLC0415

    data = parse_proposal(text)
    conforms, report_graph, _ = shacl(data, shacl_graph=shapes, inference="none",
                                      abort_on_first=False, allow_warnings=False)
    messages: list[str] = []
    if not conforms:
        import rdflib  # noqa: PLC0415
        sh = rdflib.Namespace("http://www.w3.org/ns/shacl#")
        for result in report_graph.subjects(rdflib.RDF.type, sh.ValidationResult):
            focus = report_graph.value(result, sh.focusNode)
            path = report_graph.value(result, sh.resultPath)
            msg = report_graph.value(result, sh.resultMessage)
            messages.append(f"{focus} {path or ''}: {msg}".strip())
        messages.sort()
    return Verdict(conforms=bool(conforms), triples=len(data), messages=messages,
                   graph=to_acc_graph(data) if conforms else None)
