"""The IRI scheme — decided once, so every component names a thing the same way.

=====================  ===========================================================
thing                  IRI
=====================  ===========================================================
collective             ``urn:acc:collective:<cid>``
collective-local       ``urn:acc:collective:<cid>:<kind>:<id>``  (lesson, note,
                       episode, task, agent).  A hub is a collective id (D-016),
                       so the enterprise tier needs no scheme of its own.
role                   ``urn:acc:role:<label>``
domain                 ``urn:acc:domain:<tag>``
model                  ``urn:acc:model:<id>``
package pin            ``urn:acc:pkg:<scope>/<name>@<version>``
agent BOM              ``urn:acc:bom:<name>``
OKF concept            ``urn:acc:okf:<bundle>:<rel_path>``
person                 ``urn:acc:person:<pseudonym>`` -- never a name or an email
=====================  ===========================================================

**Persons are pseudonymous.**  A person IRI is an HMAC of the subject under a
deployment key, so two graphs written with the same key join on a person
without either one naming them.  With no key there is no stable IRI at all:
each graph gets a blank node per person, which joins *within* the graph and
nowhere else.  Pseudonymous is not anonymous -- whoever holds the key can test
a guess -- so the key is a secret, read by the caller, never by this module.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote

from acc.lib.rdf.terms import BNode

#: Characters left as they are inside one IRI segment.  ``:`` separates
#: segments, so it is escaped inside one; ``/`` and ``@`` are kept because
#: package pins and OKF paths read better with them.
_SAFE = "-._~@/"


def _seg(value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("an IRI segment may not be empty")
    return quote(text, safe=_SAFE)


def collective_iri(collective_id: str) -> str:
    return f"urn:acc:collective:{_seg(collective_id)}"


def local_iri(collective_id: str, kind: str, ident: str) -> str:
    """A thing that lives in one collective (or hub)."""
    return f"urn:acc:collective:{_seg(collective_id)}:{_seg(kind)}:{_seg(ident)}"


def role_iri(label: str) -> str:
    return f"urn:acc:role:{_seg(label)}"


def domain_iri(tag: str) -> str:
    return f"urn:acc:domain:{_seg(tag)}"


def model_iri(model_id: str) -> str:
    return f"urn:acc:model:{_seg(model_id)}"


def package_iri(pin: str) -> str:
    """``@acc/okf@1.0.0`` → ``urn:acc:pkg:acc/okf@1.0.0``."""
    return f"urn:acc:pkg:{_seg(str(pin).strip().lstrip('@'))}"


def bom_iri(name: str) -> str:
    return f"urn:acc:bom:{_seg(name)}"


def okf_iri(bundle: str, rel_path: str) -> str:
    return f"urn:acc:okf:{_seg(bundle)}:{_seg(rel_path)}"


class Persons:
    """Mints the node for a person.  One instance per graph being written.

    ``key`` given → ``urn:acc:person:<32 hex>``, stable across graphs.
    ``key`` absent → a blank node per subject, stable within this instance only.
    """

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or None
        self._bnodes: dict[str, BNode] = {}

    @property
    def keyed(self) -> bool:
        return self._key is not None

    def node(self, subject: str) -> str | BNode:
        subject = str(subject).strip()
        if not subject:
            raise ValueError("a person needs a subject")
        if self._key is not None:
            digest = hmac.new(self._key, subject.encode("utf-8"), hashlib.sha256).hexdigest()
            return f"urn:acc:person:{digest[:32]}"
        if subject not in self._bnodes:
            self._bnodes[subject] = BNode(f"person{len(self._bnodes)}")
        return self._bnodes[subject]

    def subject_of(self, node: str | BNode) -> str:
        """The subject an unkeyed instance minted *node* for; ``""`` when keyed
        (a pseudonym is not reversible -- that is the point of the key)."""
        for subject, bnode in self._bnodes.items():
            if bnode == node:
                return subject
        return ""
