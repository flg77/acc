"""The ACC vocabulary — the few terms no standard vocabulary already has.

The rule (RDF-01): reuse PROV-O for lineage, SKOS for domains, Dublin Core for
titles; mint an ``acc:`` term only for what is ACC's own -- the ceiling scale,
the memory scope, the lesson, the bill of materials.  :func:`vocabulary_graph`
describes every term, so ``acc-cli rdf vocab`` is the documentation.
"""

from __future__ import annotations

from acc.lib.rdf.graph import Graph
from acc.lib.rdf.terms import ACC, OWL, PROV, RDF, RDFS, SKOS, lit

#: The ceiling scale (D-014), lowest first.  Mirrors ``acc.identity.CEILING_RANK``;
#: duplicated rather than imported so this library stays free of the runtime.
CEILINGS: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# name -> (kind, parent, comment).  kind: class | object | data.
_TERMS: dict[str, tuple[str, str, str]] = {
    # classes
    "Collective": ("class", "", "A collective or a hub (a hub is a collective id, D-016)."),
    "Agent": ("class", PROV.SoftwareAgent, "One running ACC agent."),
    "Role": ("class", "", "A role definition, by label."),
    "Task": ("class", PROV.Activity, "One task an agent ran."),
    "Episode": ("class", PROV.Entity, "One stored episode (what a task produced)."),
    "Lesson": ("class", PROV.Entity, "A learning on the wire (KNOWLEDGE_SHARE), or a note travelling."),
    "Ceiling": ("class", "", "A level of the one risk scale: LOW < MEDIUM < HIGH < CRITICAL."),
    "AgentBOM": ("class", PROV.Entity, "A signed agent bill of materials (proposal 040)."),
    "RoleBinding": ("class", "", "A role and the model it runs on, inside an AgentBOM."),
    "Package": ("class", PROV.Entity, "An exact @scope/name@version pin."),
    "Model": ("class", "", "A models.yaml entry."),
    "Note": ("class", PROV.Entity, "A memory note reflection distilled from episodes."),
    "PublishedNote": ("class", PROV.Entity, "A note's copy in a destination: a shared tier, a hub, an adopting role."),
    "Proposal": ("class", PROV.Entity, "A publish proposal: a note put up for publication."),
    "Refinement": ("class", PROV.Activity, "One refinement-ledger row: a durable change to what an agent knows."),
    "KnowledgeGraph": ("class", PROV.Entity, "A named graph in a collective's knowledge store; carries one ceiling (RDF-04)."),
    "Erasure": ("class", ACC.Refinement, "A person's contribution erased (memory forget)."),
    # object properties
    "ceiling": ("object", "", "The ceiling a reader needs to see this; never read below it."),
    "collective": ("object", "", "The collective (or hub) this belongs to."),
    "role": ("object", "", "The role that produced or holds this."),
    "domain": ("object", "", "The domain (a skos:Concept) this is about."),
    "sourceRequester": ("object", PROV.wasInfluencedBy, "A person whose task this was learned from."),
    "targetAgent": ("object", "", "The one agent a directed lesson is for."),
    "roleBinding": ("object", "", "A role binding of an AgentBOM."),
    "model": ("object", "", "The model a role binding runs on."),
    "package": ("object", "", "A pinned capability package of an AgentBOM."),
    "governancePack": ("object", "", "A pinned governance pack an AgentBOM was assessed against."),
    "revised": ("object", "", "A note an erasure rebuilt or demoted (it survives, changed)."),
    # data properties
    "rank": ("data", "", "Position of a ceiling on the scale, 0 = LOW."),
    "scope": ("data", "", "The memory scope key (source, source#group or source@who)."),
    "kind": ("data", "", "The kind of a lesson: note, role_patch, skill_patch, rule."),
    "summary": ("data", "", "What was learned, in prose."),
    "trigger": ("data", "", "What caused the learning."),
    "expectedOutcome": ("data", "", "What should change if the lesson is right."),
    "dissent": ("data", "", "Recorded disagreement."),
    "confidence": ("data", "", "The producer's confidence, 0..1."),
    "probationSeconds": ("data", "", "How long the lesson stays on probation."),
    "requesterTier": ("data", "", "The requester's tier: viewer, requester, operator."),
    "surface": ("data", "", "Where a request came in (webgui, tui, slack, cli, ...)."),
    "channel": ("data", "", "The channel on that surface."),
    "requesterScope": ("data", "", "The requester's group or conversation on that surface."),
    "intent": ("data", "", "The plain-English purpose captured at onboarding."),
    "policy": ("data", "", "The install-time Enterprise Contract policy ref."),
    "target": ("data", "", "A deploy scenario an AgentBOM is trusted on."),
    "residency": ("data", "", "The data-residency posture."),
    "signerIssuer": ("data", "", "The keyless signing floor: OIDC issuer."),
    "signerSubjectPattern": ("data", "", "The keyless signing floor: subject pattern."),
    "okfType": ("data", "", "The OKF concept type."),
    "sensitivity": ("data", "", "An OKF concept's declared sensitivity."),
    "sourcePath": ("data", "", "Path of the source file, relative to its bundle."),
    "refinementKind": ("data", "", "The ledger kind: note, propose, publish, hub_promote, adopt, forget, rollback, outcome."),
    "destination": ("data", "", "Where a note was published to: a scope, or hub:<cid>."),
    "approverTier": ("data", "", "The tier an approval was given at."),
    "written": ("data", "", "Whether the destination took the note."),
    "verdict": ("data", "", "How a task that used a lesson ended."),
    "tripleCount": ("data", "", "How many triples a knowledge graph holds."),
}


def ceiling_iri(level: str) -> str:
    """``"high"`` → ``acc:HIGH``.  An unknown or empty level reads as CRITICAL:
    an unlabelled fact reaches only the operator (the note rule)."""
    text = str(level or "").strip().upper()
    return ACC[text if text in CEILINGS else "CRITICAL"]


def vocabulary_graph() -> Graph:
    g = Graph()
    kinds = {"class": OWL.Class, "object": OWL.ObjectProperty, "data": OWL.DatatypeProperty}
    for name, (kind, parent, comment) in _TERMS.items():
        iri = ACC[name]
        g.add(iri, RDF.type, kinds[kind])
        g.add(iri, RDFS.label, lit(name))
        g.add(iri, RDFS.comment, lit(comment))
        if parent:
            rel = RDFS.subClassOf if kind == "class" else RDFS.subPropertyOf
            g.add(iri, rel, parent)
    for rank, level in enumerate(CEILINGS):
        g.add(ACC[level], RDF.type, ACC.Ceiling)
        g.add(ACC[level], RDFS.label, lit(level))
        g.add(ACC[level], ACC.rank, lit(rank))
    g.add(ACC.domain, RDFS.range, SKOS.Concept)
    g.add(ACC.ceiling, RDFS.range, ACC.Ceiling)
    return g


def is_acc_term(iri: str) -> bool:
    """True for a term this vocabulary defines (classes, properties, ceilings)."""
    if not str(iri).startswith(str(ACC)):
        return False
    local = str(iri)[len(str(ACC)):]
    return local in _TERMS or local in CEILINGS
