"""An in-memory set of triples and its three serialisations.

N-Triples is the canonical, line-per-fact form (diffable, greppable).  Turtle
is what an operator reads.  JSON-LD is what travels: plain JSON, one node per
subject, with the prefix context of :mod:`acc.lib.rdf.context`.  All three are
written from the same triples, so they cannot disagree.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Iterator

from acc.lib.rdf.terms import PREFIXES, RDF, XSD, BNode, Literal, Object, Subject, Triple

#: A local name we are willing to abbreviate as ``prefix:local``.  Stricter
#: than Turtle's PN_LOCAL on purpose: anything else is written in full.
_LOCAL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


class Graph:
    """A set of triples.  Insertion order is not kept; output is sorted."""

    def __init__(self, triples: Iterable[Triple] = ()) -> None:
        self._triples: set[Triple] = set()
        for t in triples:
            self.add(*t)

    def add(self, s: Subject, p: str, o: Object) -> None:
        if not isinstance(s, (str, BNode)):
            raise TypeError(f"subject must be an IRI or a blank node, got {type(s).__name__}")
        if not isinstance(p, str) or not p:
            raise TypeError("predicate must be an IRI")
        if not isinstance(o, (str, BNode, Literal)):
            raise TypeError(f"object must be an IRI, blank node or Literal, got {type(o).__name__}")
        self._triples.add((s, str(p), o if not isinstance(o, str) else str(o)))

    def __iter__(self) -> Iterator[Triple]:
        return iter(sorted(self._triples, key=_sort_key))

    def __len__(self) -> int:
        return len(self._triples)

    def __contains__(self, triple: object) -> bool:
        return triple in self._triples

    def objects(self, s: Subject, p: str) -> list[Object]:
        return sorted((o for (s2, p2, o) in self._triples if s2 == s and p2 == p), key=_term_key)

    # ------------------------------------------------------------------
    # serialisations
    # ------------------------------------------------------------------
    def to_ntriples(self) -> str:
        return "".join(f"{_nt(s)} {_nt(p)} {_nt(o)} .\n" for (s, p, o) in self)

    def to_turtle(self) -> str:
        lines = [f"@prefix {k}: <{v}> ." for k, v in PREFIXES.items()]
        for s, props in _by_subject(self):
            lines.append("")
            parts = []
            for p, objs in props:
                pred = "a" if p == RDF.type else _ttl(p)
                parts.append(f"    {pred} " + ", ".join(_ttl(o) for o in objs))
            lines.append(_ttl(s) + "\n" + " ;\n".join(parts) + " .")
        return "\n".join(lines) + "\n"

    def to_jsonld(self) -> dict[str, Any]:
        from acc.lib.rdf.context import CONTEXT

        nodes = []
        for s, props in _by_subject(self):
            node: dict[str, Any] = {"@id": _curie(s)}
            for p, objs in props:
                if p == RDF.type:
                    node["@type"] = [_curie(o) for o in objs]
                    continue
                vals = [_jsonld_value(o) for o in objs]
                node[_curie(p)] = vals[0] if len(vals) == 1 else vals
            nodes.append(node)
        return {"@context": CONTEXT, "@graph": nodes}

    def to_jsonld_text(self) -> str:
        return json.dumps(self.to_jsonld(), indent=2, ensure_ascii=False) + "\n"


def _term_key(term: object) -> tuple:
    if isinstance(term, BNode):
        return (1, term.label, "", "")
    if isinstance(term, Literal):
        return (2, term.value, term.datatype, term.lang)
    return (0, str(term), "", "")


def _sort_key(t: Triple) -> tuple:
    return (_term_key(t[0]), t[1], _term_key(t[2]))


def _by_subject(g: Graph) -> list[tuple[Subject, list[tuple[str, list[Object]]]]]:
    grouped: dict[Subject, dict[str, list[Object]]] = {}
    for s, p, o in g:
        grouped.setdefault(s, {}).setdefault(p, []).append(o)
    out = []
    for s in sorted(grouped, key=_term_key):
        preds = grouped[s]
        # rdf:type first, then the rest alphabetically -- what a reader expects.
        order = sorted(preds, key=lambda p: (p != RDF.type, p))
        out.append((s, [(p, preds[p]) for p in order]))
    return out


def _escape(text: str) -> str:
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)


def _check_iri(iri: str) -> str:
    if any(c in iri for c in ' <>"{}|^`\\') or any(ord(c) <= 0x20 for c in iri):
        raise ValueError(f"not a valid IRI: {iri!r}")
    return iri


def _nt(term: object) -> str:
    if isinstance(term, BNode):
        return f"_:{term.label}"
    if isinstance(term, Literal):
        body = f'"{_escape(term.value)}"'
        if term.lang:
            return f"{body}@{term.lang}"
        if term.datatype and term.datatype != XSD.string:
            return f"{body}^^<{_check_iri(term.datatype)}>"
        return body
    return f"<{_check_iri(str(term))}>"


def _curie(iri: object) -> str:
    """``prefix:local`` when safe, else the full IRI (JSON-LD and Turtle)."""
    if isinstance(iri, BNode):
        return f"_:{iri.label}"
    text = str(iri)
    for prefix, ns in PREFIXES.items():
        if text.startswith(ns) and _LOCAL_RE.match(text[len(ns):]):
            return f"{prefix}:{text[len(ns):]}"
    return text


def _ttl(term: object) -> str:
    if isinstance(term, (BNode, Literal)):
        if isinstance(term, Literal) and term.datatype and not term.lang:
            return f'"{_escape(term.value)}"^^{_ttl(term.datatype)}'
        return _nt(term)
    curie = _curie(term)
    return curie if curie != str(term) else f"<{_check_iri(str(term))}>"


def _jsonld_value(o: Object) -> Any:
    if isinstance(o, BNode):
        return {"@id": f"_:{o.label}"}
    if isinstance(o, Literal):
        if o.lang:
            return {"@value": o.value, "@language": o.lang}
        if o.datatype and o.datatype != XSD.string:
            return {"@value": o.value, "@type": _curie(o.datatype)}
        return o.value
    return {"@id": _curie(o)}
