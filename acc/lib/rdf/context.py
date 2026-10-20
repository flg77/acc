"""The JSON-LD context ACC's graphs are written with.

Prefixes only, on purpose: every key in an ACC JSON-LD document is a CURIE
(``acc:ceiling``, ``prov:wasDerivedFrom``), so a reader sees which vocabulary a
fact comes from and no term can silently mean two things.  Mapping the
*existing* bus JSON (``Lesson`` as it travels today) onto these terms is the
next step (RDF-02) and needs term definitions; this context will grow then,
never change meaning.
"""

from __future__ import annotations

from acc.lib.rdf.terms import PREFIXES

#: The ``@context`` of every JSON-LD document :class:`~acc.lib.rdf.graph.Graph` writes.
CONTEXT: dict[str, str] = dict(PREFIXES)
