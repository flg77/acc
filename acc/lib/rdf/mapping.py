"""ACC shapes → RDF.  Pure functions; each returns a :class:`Graph`.

Every mapping takes the shape as it already exists -- a pydantic model, or the
dict it dumps to -- and changes nothing about it.  RDF is a *view* of what ACC
records, not a second record:

* :func:`lesson_to_graph`   -- ``acc.lessons.Lesson`` (a note on the wire)
* :func:`task_to_graph`     -- a task payload's attribution (``acc.attribution``)
* :func:`agent_bom_to_graph` -- ``acc.pkg.agent_bom.AgentBOM``
* :func:`okf_concept_to_graph` -- ``acc.lib.okf.Concept``

Content travels with its ceiling.  A lesson's summary is in the graph, so the
lesson's ``acc:ceiling`` is too, and an unset ceiling is written as CRITICAL
(:func:`acc.lib.rdf.vocab.ceiling_iri`) -- the same rule the note follows.
Evidence is ids, never content: episodes appear as IRIs, not text.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any, Mapping

from acc.lib.rdf.graph import Graph
from acc.lib.rdf.iri import (
    Persons,
    bom_iri,
    collective_iri,
    domain_iri,
    local_iri,
    model_iri,
    okf_iri,
    package_iri,
    role_iri,
)
from acc.lib.rdf.terms import ACC, DCTERMS, PROV, RDF, RDFS, SKOS, BNode, datetime_lit, lit
from acc.lib.rdf.vocab import ceiling_iri

#: The collective a shape is filed under when it names none.
LOCAL_COLLECTIVE = "local"


def _as_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, Mapping):
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    raise TypeError(f"expected a mapping or a pydantic model, got {type(obj).__name__}")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _person(g: Graph, persons: Persons, subject: str) -> str | BNode:
    node = persons.node(subject)
    g.add(node, RDF.type, PROV.Person)
    return node


# ---------------------------------------------------------------------------
# Lesson
# ---------------------------------------------------------------------------
def lesson_to_graph(lesson: Any, *, persons: Persons | None = None, taxonomy: Any = None) -> Graph:
    """One lesson.  ``patch`` (a role / skill / rule change) is not mapped:
    it is a proposal payload, and proposals get their own shape (RDF-05).

    With a *taxonomy* (:class:`acc.lib.rdf.taxonomy.Taxonomy`), the lesson's
    domain is written as the concept it names (an alias resolves to its
    concept) together with its ``skos:broader`` chain, so the graph says what
    the receptors already know: a ``mortgage`` lesson is about ``lending``."""
    d = _as_dict(lesson)
    persons = persons or Persons()
    lesson_id = _text(d.get("lesson_id"))
    if not lesson_id:
        raise ValueError("a lesson needs a lesson_id")
    cid = _text(d.get("collective_id")) or LOCAL_COLLECTIVE
    g = Graph()
    node = local_iri(cid, "lesson", lesson_id)

    g.add(node, RDF.type, ACC.Lesson)
    g.add(node, ACC.collective, collective_iri(cid))
    g.add(node, ACC.kind, lit(_text(d.get("kind")) or "note"))
    g.add(node, ACC.summary, lit(_text(d.get("summary"))))
    g.add(node, ACC.scope, lit(_text(d.get("scope")) or "local"))
    g.add(node, ACC.ceiling, ceiling_iri(_text(d.get("ceiling"))))
    if d.get("ts"):
        g.add(node, PROV.generatedAtTime, datetime_lit(float(d["ts"])))

    agent = _text(d.get("from_agent"))
    if agent:
        agent_node = local_iri(cid, "agent", agent)
        g.add(agent_node, RDF.type, ACC.Agent)
        g.add(node, PROV.wasAttributedTo, agent_node)
    role = _text(d.get("role_label"))
    if role:
        g.add(node, ACC.role, role_iri(role))
        g.add(role_iri(role), RDF.type, ACC.Role)
    tag = _text(d.get("domain_tag"))
    if tag:
        concept = (taxonomy.concept_of(tag) if taxonomy is not None else "") or tag
        g.add(node, ACC.domain, domain_iri(concept))
        g.add(domain_iri(concept), RDF.type, SKOS.Concept)
        for up in (taxonomy.broader_of(concept) if taxonomy is not None else []):
            g.add(domain_iri(up), RDF.type, SKOS.Concept)
            for b in taxonomy.concepts[up].broader:
                g.add(domain_iri(up), SKOS.broader, domain_iri(b))
    target = _text(d.get("target_agent_id"))
    if target:
        g.add(node, ACC.targetAgent, local_iri(cid, "agent", target))

    for key, prop in (("trigger", ACC.trigger), ("expected_outcome", ACC.expectedOutcome)):
        if _text(d.get(key)):
            g.add(node, prop, lit(_text(d.get(key))))
    if float(d.get("confidence") or 0.0):
        g.add(node, ACC.confidence, lit(float(d["confidence"])))
    if float(d.get("probation_s") or 0.0):
        g.add(node, ACC.probationSeconds, lit(float(d["probation_s"])))

    for subject in d.get("source_requesters") or []:
        if _text(subject):
            g.add(node, ACC.sourceRequester, _person(g, persons, _text(subject)))

    evidence = d.get("evidence") or {}
    for episode in evidence.get("source_episode_ids") or []:
        if _text(episode):
            ep = local_iri(cid, "episode", _text(episode))
            g.add(ep, RDF.type, ACC.Episode)
            g.add(node, PROV.wasDerivedFrom, ep)
    if _text(evidence.get("dissent")):
        g.add(node, ACC.dissent, lit(_text(evidence["dissent"])))
    return g


# ---------------------------------------------------------------------------
# Task attribution
# ---------------------------------------------------------------------------
def requester_subject(task: Mapping[str, Any]) -> str:
    """The person behind a task, as ``source:subject`` -- or ``""``.

    The explicit pair wins; ``requested_by`` (``source:subject@scope``) is the
    fallback, with the scope cut only when the task names it, because a
    subject may itself contain ``@`` (an email).
    """
    source = _text(task.get("requester_source"))
    subject = _text(task.get("requester_subject"))
    if source and subject:
        return f"{source}:{subject}"
    who = _text(task.get("requested_by"))
    scope = _text(task.get("requester_scope"))
    if who and scope and who.endswith("@" + scope):
        who = who[: -(len(scope) + 1)]
    return who


def task_to_graph(
    task: Mapping[str, Any], *, collective_id: str = "", persons: Persons | None = None
) -> Graph:
    """A task and who asked for it (the seven ``ATTRIBUTION_KEYS``).

    An unattributed task yields the task node and nothing else -- the graph
    never invents a requester the payload does not name.
    """
    persons = persons or Persons()
    task_id = _text(task.get("task_id"))
    if not task_id:
        raise ValueError("a task needs a task_id")
    cid = _text(collective_id or task.get("collective_id")) or LOCAL_COLLECTIVE
    g = Graph()
    node = local_iri(cid, "task", task_id)
    g.add(node, RDF.type, ACC.Task)
    g.add(node, ACC.collective, collective_iri(cid))

    who = requester_subject(task)
    if not who:
        return g
    g.add(node, PROV.wasAssociatedWith, _person(g, persons, who))
    g.add(node, ACC.ceiling, ceiling_iri(_text(task.get("requester_ceiling"))))
    for key, prop in (
        ("requester_tier", ACC.requesterTier),
        ("requester_source", ACC.surface),
        ("requester_channel", ACC.channel),
        ("requester_scope", ACC.requesterScope),
    ):
        if _text(task.get(key)):
            g.add(node, prop, lit(_text(task.get(key))))
    return g


# ---------------------------------------------------------------------------
# AgentBOM
# ---------------------------------------------------------------------------
def agent_bom_to_graph(bom: Any) -> Graph:
    """An AgentBOM in ACC terms.  The SPDX 3.0 view is a separate export (RDF-08)."""
    d = _as_dict(bom)
    name = _text((d.get("metadata") or {}).get("name"))
    if not name:
        raise ValueError("an AgentBOM needs metadata.name")
    spec = d.get("spec") or {}
    g = Graph()
    node = bom_iri(name)
    g.add(node, RDF.type, ACC.AgentBOM)
    g.add(node, RDFS.label, lit(name))
    if _text(spec.get("intent")):
        g.add(node, ACC.intent, lit(_text(spec["intent"])))

    for binding in spec.get("roles") or []:
        role = _text(binding.get("name"))
        if not role:
            continue
        b = f"{node}#role-{role_iri(role).rsplit(':', 1)[1]}"
        g.add(node, ACC.roleBinding, b)
        g.add(b, RDF.type, ACC.RoleBinding)
        g.add(b, ACC.role, role_iri(role))
        g.add(role_iri(role), RDF.type, ACC.Role)
        if _text(binding.get("model")):
            g.add(b, ACC.model, model_iri(_text(binding["model"])))
            g.add(model_iri(_text(binding["model"])), RDF.type, ACC.Model)

    for key, prop in (("packages", ACC.package), ("governance", ACC.governancePack)):
        for pin in spec.get(key) or []:
            if _text(pin):
                g.add(node, prop, package_iri(pin))
                g.add(package_iri(pin), RDF.type, ACC.Package)
                g.add(package_iri(pin), RDFS.label, lit(_text(pin)))
    for target in spec.get("targets") or []:
        g.add(node, ACC.target, lit(_text(target)))
    for key, prop in (("policy", ACC.policy), ("residency", ACC.residency)):
        if _text(spec.get(key)):
            g.add(node, prop, lit(_text(spec[key])))
    signer = spec.get("required_signer") or {}
    for key, prop in (("issuer", ACC.signerIssuer), ("subject_pattern", ACC.signerSubjectPattern)):
        if _text(signer.get(key)):
            g.add(node, prop, lit(_text(signer[key])))
    return g


# ---------------------------------------------------------------------------
# OKF concept
# ---------------------------------------------------------------------------
#: ``[text](target)`` -- the link form OKF concepts cross-reference with.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def concept_links(rel_path: str, body: str) -> list[str]:
    """Bundle-relative paths of the ``.md`` concepts *body* links to.

    External links (a scheme), anchors and non-markdown targets are skipped;
    a broken link is still returned -- OKF consumers must tolerate them, and
    a dangling ``dcterms:references`` is exactly that.
    """
    out: list[str] = []
    here = posixpath.dirname(rel_path)
    for target in _LINK_RE.findall(body or ""):
        target = target.split("#", 1)[0]
        if not target or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
            continue
        if not target.lower().endswith(".md"):
            continue
        path = target.lstrip("/") if target.startswith("/") else posixpath.join(here, target)
        path = posixpath.normpath(path)
        if path.startswith("..") or path in out:
            continue
        out.append(path)
    return out


def okf_concept_to_graph(concept: Any, *, bundle: str) -> Graph:
    """One OKF concept as a ``skos:Concept``; its links as ``dcterms:references``."""
    rel_path = _text(getattr(concept, "rel_path", ""))
    if not rel_path:
        raise ValueError("an OKF concept needs a rel_path")
    fm = dict(getattr(concept, "frontmatter", {}) or {})
    g = Graph()
    node = okf_iri(bundle, rel_path)
    g.add(node, RDF.type, SKOS.Concept)
    g.add(node, ACC.sourcePath, lit(rel_path))
    if _text(fm.get("type")):
        g.add(node, ACC.okfType, lit(_text(fm["type"])))
    if _text(fm.get("title")):
        g.add(node, DCTERMS.title, lit(_text(fm["title"])))
        g.add(node, SKOS.prefLabel, lit(_text(fm["title"])))
    if _text(fm.get("description")):
        g.add(node, DCTERMS.description, lit(_text(fm["description"])))
    if _text(fm.get("domain")):
        g.add(node, ACC.domain, domain_iri(_text(fm["domain"])))
        g.add(domain_iri(_text(fm["domain"])), RDF.type, SKOS.Concept)
    if _text(fm.get("sensitivity")):
        g.add(node, ACC.sensitivity, lit(_text(fm["sensitivity"])))
    for tag in fm.get("tags") or []:
        if _text(tag):
            g.add(node, DCTERMS.subject, lit(_text(tag)))
    for path in concept_links(rel_path, getattr(concept, "body", "")):
        g.add(node, DCTERMS.references, okf_iri(bundle, path))
    return g
