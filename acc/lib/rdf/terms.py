"""RDF terms — IRIs, blank nodes, literals, triples — and the namespaces ACC uses.

Deliberately tiny: an IRI is a ``str``, a blank node is :class:`BNode`, a
literal is :class:`Literal`.  No parser, no store; :mod:`acc.lib.rdf.graph`
serialises.  ``rdflib`` reads the output in the tests, nothing here imports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Union


@dataclass(frozen=True, order=True)
class BNode:
    """A blank node.  ``label`` is unique within one graph, nothing more."""

    label: str


@dataclass(frozen=True, order=True)
class Literal:
    """A literal.  ``datatype`` is a full IRI or empty (= ``xsd:string``)."""

    value: str
    datatype: str = ""
    lang: str = ""


#: Subject = IRI or blank node; object = IRI, blank node or literal.
Subject = Union[str, BNode]
Object = Union[str, BNode, Literal]
Triple = tuple[Subject, str, Object]


class Namespace:
    """``NS["term"]`` / ``NS.term`` → the full IRI; ``str(NS)`` → the base.

    Not a ``str`` subclass: ``DCTERMS.title`` would then be ``str.title``.
    """

    __slots__ = ("_base",)

    def __init__(self, base: str) -> None:
        self._base = base

    def __str__(self) -> str:
        return self._base

    def __repr__(self) -> str:
        return f"Namespace({self._base!r})"

    def __getitem__(self, local: str) -> str:
        return self._base + local

    def __getattr__(self, local: str) -> str:
        if local.startswith("_"):
            raise AttributeError(local)
        return self._base + local


RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
PROV = Namespace("http://www.w3.org/ns/prov#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
DCTERMS = Namespace("http://purl.org/dc/terms/")
#: ACC's own terms -- only what no standard vocabulary already has.
ACC = Namespace("urn:acc:vocab:")

#: Prefixes every serialisation declares, in this order.
PREFIXES: dict[str, str] = {
    "acc": str(ACC),
    "prov": str(PROV),
    "skos": str(SKOS),
    "dcterms": str(DCTERMS),
    "rdf": str(RDF),
    "rdfs": str(RDFS),
    "owl": str(OWL),
    "xsd": str(XSD),
}


def lit(value: object) -> Literal:
    """A literal typed from the Python value (bool before int: bool is an int)."""
    if isinstance(value, bool):
        return Literal("true" if value else "false", XSD.boolean)
    if isinstance(value, int):
        return Literal(str(value), XSD.integer)
    if isinstance(value, float):
        return Literal(repr(value), XSD.double)
    return Literal(str(value))


def datetime_lit(ts: float) -> Literal:
    """A POSIX timestamp as an ``xsd:dateTime`` in UTC."""
    text = datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    return Literal(text.replace("+00:00", "Z"), XSD.dateTime)
