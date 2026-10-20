"""A domain taxonomy — the SKOS vocabulary receptors and lessons share (RDF-02).

ACC's paracrine receptors compared strings: a role listening for ``lending``
never heard a lesson tagged ``mortgage``.  A taxonomy says how domains relate,
so a receptor hears its domain *and everything narrower*:

.. code-block:: yaml

    # taxonomy/domains.yaml, inside a signed pack
    scheme: acme-domains
    concepts:
      lending:       {label: Lending}
      mortgage:      {label: Mortgages, broader: [lending], alt: [home-loans]}
      credit-risk:   {broader: [lending, risk]}
      risk:          {}

``mortgage`` matches a ``lending`` receptor; ``home-loans`` is ``mortgage``;
``lending`` does **not** match a ``mortgage`` receptor (a specialist is not
sent the general case).  Unknown terms match only themselves — exactly the
old string rule, so a tag the taxonomy has never heard of behaves as before.

Who owns it is a governance decision (2026-10-03: **the hub**, one signed pack
per hub; see :mod:`acc.domain_taxonomy`).  This module is pure: a mapping in,
a :class:`Taxonomy` out, refused when malformed (an unknown ``broader``, a
cycle, an alias that names two concepts) rather than half-loaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from acc.lib.rdf.graph import Graph
from acc.lib.rdf.iri import domain_iri
from acc.lib.rdf.terms import RDF, SKOS, Literal

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class TaxonomyError(ValueError):
    """The mapping is not a usable taxonomy."""


def _norm(term: str) -> str:
    return str(term or "").strip().lower()


@dataclass(frozen=True)
class Concept:
    id: str
    label: str = ""
    broader: tuple[str, ...] = ()
    alt: tuple[str, ...] = ()


@dataclass
class Taxonomy:
    scheme: str
    concepts: dict[str, Concept] = field(default_factory=dict)
    #: alias (normalised) -> concept id; every concept id is its own alias.
    _alias: dict[str, str] = field(default_factory=dict, repr=False)
    #: concept id -> every concept it is narrower than, itself included.
    _up: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    # -- construction ----------------------------------------------------
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Taxonomy":
        if not isinstance(data, Mapping):
            raise TaxonomyError("a taxonomy is a mapping with 'scheme' and 'concepts'")
        scheme = str(data.get("scheme") or "").strip()
        if not scheme:
            raise TaxonomyError("'scheme' is required (it names the vocabulary)")
        raw = data.get("concepts")
        if not isinstance(raw, Mapping) or not raw:
            raise TaxonomyError("'concepts' must be a non-empty mapping")
        tax = cls(scheme=scheme)
        for cid, body in raw.items():
            cid = _norm(cid)
            if not _ID_RE.match(cid):
                raise TaxonomyError(f"concept id {cid!r} must be lower-case, start with a letter "
                                    "or digit, and use only a-z 0-9 _ . -")
            body = body or {}
            if not isinstance(body, Mapping):
                raise TaxonomyError(f"concept {cid!r} must be a mapping")
            tax.concepts[cid] = Concept(
                id=cid, label=str(body.get("label") or "").strip(),
                broader=tuple(_norm(b) for b in (body.get("broader") or [])),
                alt=tuple(_norm(a) for a in (body.get("alt") or [])),
            )
        for c in tax.concepts.values():
            unknown = [b for b in c.broader if b not in tax.concepts]
            if unknown:
                raise TaxonomyError(f"concept {c.id!r} names unknown broader {unknown}")
            tax._alias[c.id] = c.id
        for c in tax.concepts.values():
            for a in c.alt:
                if tax._alias.get(a, c.id) != c.id:
                    raise TaxonomyError(f"alias {a!r} names both {tax._alias[a]!r} and {c.id!r}")
                tax._alias[a] = c.id
        for cid in tax.concepts:
            tax._up[cid] = frozenset(tax._ancestors(cid, ()))
        return tax

    def _ancestors(self, cid: str, path: tuple[str, ...]) -> set[str]:
        if cid in path:
            raise TaxonomyError(f"broader cycle: {' -> '.join((*path, cid))}")
        out = {cid}
        for b in self.concepts[cid].broader:
            out |= self._ancestors(b, (*path, cid))
        return out

    # -- questions -------------------------------------------------------
    def concept_of(self, term: str) -> str:
        """The concept id a term names (an id or an alias), or ``""``."""
        return self._alias.get(_norm(term), "")

    def broader_of(self, term: str) -> list[str]:
        """Every concept *term* is narrower than, itself included; ``[]`` if unknown."""
        cid = self.concept_of(term)
        return sorted(self._up.get(cid, ())) if cid else []

    def matches(self, receptor: str, tag: str) -> bool:
        """Does a *receptor* hear a lesson tagged *tag*?

        Yes when the tag's concept is the receptor's concept or narrower than
        it.  A term the taxonomy does not know falls back to plain equality.
        """
        r, t = self.concept_of(receptor), self.concept_of(tag)
        if not r or not t:
            return _norm(receptor) == _norm(tag)
        return r in self._up[t]

    # -- RDF -------------------------------------------------------------
    def scheme_iri(self) -> str:
        return f"urn:acc:scheme:{self.scheme}"

    def to_graph(self) -> Graph:
        """The taxonomy as SKOS: a scheme, its concepts, labels and ``skos:broader``."""
        g = Graph()
        scheme = self.scheme_iri()
        g.add(scheme, RDF.type, SKOS.ConceptScheme)
        for c in self.concepts.values():
            node = domain_iri(c.id)
            g.add(node, RDF.type, SKOS.Concept)
            g.add(node, SKOS.inScheme, scheme)
            g.add(node, SKOS.notation, Literal(c.id))
            if c.label:
                g.add(node, SKOS.prefLabel, Literal(c.label))
            for a in c.alt:
                g.add(node, SKOS.altLabel, Literal(a))
            for b in c.broader:
                g.add(node, SKOS.broader, domain_iri(b))
            if not c.broader:
                g.add(scheme, SKOS.hasTopConcept, node)
        return g

    def to_mapping(self) -> dict[str, Any]:
        """The YAML/JSON form, round-trippable through :meth:`from_mapping`."""
        return {"scheme": self.scheme, "concepts": {
            c.id: {k: v for k, v in (("label", c.label), ("broader", list(c.broader)),
                                     ("alt", list(c.alt))) if v}
            for c in self.concepts.values()}}
