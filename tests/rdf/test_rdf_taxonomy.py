"""RDF-02 — SKOS receptors: a hub-owned domain taxonomy decides who hears a lesson.

Before: ``domain_tag in domain_receptors`` — a role listening for ``lending``
never heard ``mortgage``.  After: a receptor hears its concept and every
narrower one; aliases resolve; a term the taxonomy does not know falls back to
the old exact match; and with no taxonomy at all nothing changes.  The hub owns
the vocabulary (operator decision 2026-10-03): a bound instance uses the hub's
published copy and never a pack it installed itself.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from acc import domain_taxonomy as DT
from acc.agent import Agent, _receptor_allows
from acc.lessons import Lesson
from acc.lib.rdf import RDF, SKOS, domain_iri, lesson_to_graph
from acc.lib.rdf.taxonomy import Taxonomy, TaxonomyError
from acc.signals import SIG_KNOWLEDGE_SHARE

DOMAINS = {
    "scheme": "acme-domains",
    "concepts": {
        "lending": {"label": "Lending"},
        "mortgage": {"label": "Mortgages", "broader": ["lending"], "alt": ["home-loans"]},
        "credit-risk": {"broader": ["lending", "risk"]},
        "risk": {},
    },
}


@pytest.fixture
def tax() -> Taxonomy:
    return Taxonomy.from_mapping(DOMAINS)


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def test_a_receptor_hears_its_domain_and_everything_narrower(tax):
    assert tax.matches("lending", "lending")
    assert tax.matches("lending", "mortgage")
    assert tax.matches("lending", "credit-risk") and tax.matches("risk", "credit-risk")


def test_a_specialist_is_not_sent_the_general_case(tax):
    assert not tax.matches("mortgage", "lending")
    assert not tax.matches("risk", "mortgage")


def test_aliases_and_case_resolve_to_the_concept(tax):
    assert tax.concept_of("Home-Loans") == "mortgage"
    assert tax.matches("LENDING", "home-loans")


def test_an_unknown_term_keeps_the_old_exact_rule(tax):
    assert tax.matches("payments", "payments")
    assert not tax.matches("lending", "payments")
    assert not tax.matches("payments", "lending")


@pytest.mark.parametrize("bad, why", [
    ({"concepts": {"a": {}}}, "scheme"),
    ({"scheme": "s", "concepts": {}}, "non-empty"),
    ({"scheme": "s", "concepts": {"a": {"broader": ["nope"]}}}, "unknown broader"),
    ({"scheme": "s", "concepts": {"a": {"broader": ["b"]}, "b": {"broader": ["a"]}}}, "cycle"),
    ({"scheme": "s", "concepts": {"a": {"alt": ["x"]}, "b": {"alt": ["x"]}}}, "names both"),
    ({"scheme": "s", "concepts": {"Not An Id": {}}}, "lower-case"),
])
def test_a_malformed_taxonomy_is_refused_not_half_loaded(bad, why):
    with pytest.raises(TaxonomyError, match=why):
        Taxonomy.from_mapping(bad)


def test_the_mapping_round_trips(tax):
    again = Taxonomy.from_mapping(tax.to_mapping())
    assert again.to_mapping() == tax.to_mapping()
    assert again.matches("lending", "home-loans")


def test_the_taxonomy_is_skos(tax):
    g = tax.to_graph()
    assert (domain_iri("mortgage"), SKOS.broader, domain_iri("lending")) in g
    assert ("urn:acc:scheme:acme-domains", SKOS.hasTopConcept, domain_iri("lending")) in g
    assert (domain_iri("mortgage"), RDF.type, SKOS.Concept) in g
    rdflib = pytest.importorskip("rdflib")
    from rdflib.compare import isomorphic
    nt = rdflib.Graph().parse(data=g.to_ntriples(), format="nt")
    assert isomorphic(nt, rdflib.Graph().parse(data=g.to_jsonld_text(), format="json-ld"))


# ---------------------------------------------------------------------------
# the receptor
# ---------------------------------------------------------------------------


def test_receptor_with_a_taxonomy(tax):
    assert _receptor_allows(SIG_KNOWLEDGE_SHARE, "mortgage", ["lending"], tax)
    assert not _receptor_allows(SIG_KNOWLEDGE_SHARE, "lending", ["mortgage"], tax)


def test_receptor_without_a_taxonomy_is_unchanged():
    assert not _receptor_allows(SIG_KNOWLEDGE_SHARE, "mortgage", ["lending"])
    assert _receptor_allows(SIG_KNOWLEDGE_SHARE, "lending", ["lending"])
    assert _receptor_allows(SIG_KNOWLEDGE_SHARE, "", ["lending"])          # universal ligand
    assert _receptor_allows(SIG_KNOWLEDGE_SHARE, "mortgage", [])           # universal receptor


def _agent(receptors, taxonomy):
    holder = SimpleNamespace(get=lambda: taxonomy)
    return SimpleNamespace(
        agent_id="b1", _cognitive_core=None, _taxonomy_holder=holder,
        _active_role=SimpleNamespace(domain_receptors=list(receptors), domain_id="",
                                     memory_reflection=True),
        config=SimpleNamespace(agent=SimpleNamespace(role="reviewer", collective_id="c")),
        backends=SimpleNamespace(signaling=SimpleNamespace(publish=AsyncMock()), vector=MagicMock(),
                                 llm=MagicMock()),
        _redis=None, _lessons_journal_id=lambda: "lessons-b1",
    )


def _lesson(tag):
    return Lesson(lesson_id="L1", collective_id="c", from_agent="a1", role_label="analyst",
                  domain_tag=tag, scope="local", ceiling="LOW", summary="s").model_dump()


def test_a_lending_agent_now_hears_a_mortgage_lesson(tax):
    """The lesson passes the membrane; it stops later only because the stub has no core."""
    verdict, _ = Agent._classify_lesson(_agent(["lending"], tax), _lesson("mortgage"))
    assert verdict == "no-core"
    verdict, _ = Agent._classify_lesson(_agent(["lending"], None), _lesson("mortgage"))
    assert verdict == "no-receptor"


# ---------------------------------------------------------------------------
# where the taxonomy comes from -- the hub owns it
# ---------------------------------------------------------------------------


class _Redis:
    def __init__(self):
        self.store = {}

    def set(self, k, v): self.store[k] = v
    def get(self, k): return self.store.get(k)


def _registry(tmp_path, name="@acme/domains", version="1.0.0", doc=DOMAINS):
    root = tmp_path / f"{name.strip('@').replace('/', '-')}-{version}"
    (root / "taxonomy").mkdir(parents=True)
    (root / "taxonomy" / "domains.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    entry = SimpleNamespace(name=name, version=version, content_sha256="0" * 64,
                            install_path=str(root), installed_at="2026-10-03T00:00:00+00:00")
    return SimpleNamespace(list=lambda: [entry])


def test_an_unbound_collective_uses_its_pack_and_publishes_it(tmp_path):
    redis = _Redis()
    got = DT.resolve(collective_id="hub", redis_client=redis, registry=_registry(tmp_path),
                     environ={DT.ENV: "@acme/domains"})
    assert got.taxonomy is not None and got.origin == "pack:@acme/domains@1.0.0"
    doc = json.loads(redis.store[DT.redis_taxonomy_key("hub")])
    assert doc["source"] == {"pack": "@acme/domains", "version": "1.0.0", "content_sha256": "0" * 64}


def test_a_bound_instance_uses_the_hubs_and_ignores_its_own_pack(tmp_path):
    redis = _Redis()
    DT.publish(redis, "hub", Taxonomy.from_mapping(DOMAINS), {"pack": "@acme/domains", "version": "1.0.0"})
    own = {"scheme": "mine", "concepts": {"lending": {}}}
    got = DT.resolve(collective_id="alice-dev", hub_collective_id="hub", redis_client=redis,
                     registry=_registry(tmp_path, doc=own), environ={DT.ENV: "@acme/domains"})
    assert got.taxonomy.scheme == "acme-domains"
    assert got.origin.startswith("hub:hub")
    assert DT.redis_taxonomy_key("alice-dev") not in redis.store     # an instance never publishes


def test_a_bound_instance_whose_hub_published_nothing_matches_exactly():
    got = DT.resolve(collective_id="alice-dev", hub_collective_id="hub", redis_client=_Redis(),
                     environ={DT.ENV: "@acme/domains"})
    assert got.taxonomy is None and "published no taxonomy" in got.origin


@pytest.mark.parametrize("environ, registry_doc, expect", [
    ({}, DOMAINS, "not set"),
    ({DT.ENV: "@acme/other"}, DOMAINS, "not installed"),
    ({DT.ENV: "@acme/domains"}, {"scheme": "s", "concepts": {"a": {"broader": ["x"]}}}, "unknown broader"),
])
def test_no_usable_taxonomy_means_none_never_an_error(tmp_path, environ, registry_doc, expect):
    got = DT.resolve(collective_id="c", redis_client=_Redis(),
                     registry=_registry(tmp_path, doc=registry_doc), environ=environ)
    assert got.taxonomy is None and expect in got.origin


def test_a_bound_instance_rereads_the_hub_after_the_refresh_window():
    calls, now = [], [0.0]

    def resolver():
        calls.append(now[0])
        return DT.Resolved(None, "x")

    holder = DT.TaxonomyHolder(resolver, bound=True, clock=lambda: now[0])
    holder.get(); holder.get()
    now[0] = DT.REFRESH_S
    holder.get()
    assert calls == [0.0, DT.REFRESH_S]
    unbound = DT.TaxonomyHolder(resolver, bound=False, clock=lambda: now[0])
    unbound.get(); now[0] *= 3; unbound.get()
    assert len(calls) == 3                                   # an unbound collective reads once


# ---------------------------------------------------------------------------
# the lesson in RDF, and the CLI
# ---------------------------------------------------------------------------


def test_a_lesson_graph_names_the_concept_and_its_broader_chain(tax):
    g = lesson_to_graph(_lesson("home-loans"), taxonomy=tax)
    node = "urn:acc:collective:c:lesson:L1"
    assert g.objects(node, "urn:acc:vocab:domain") == [domain_iri("mortgage")]
    assert (domain_iri("mortgage"), SKOS.broader, domain_iri("lending")) in g


def test_cli_taxonomy_match_and_check(tmp_path, capsys):
    from acc.cli import _build_parser

    path = tmp_path / "domains.yaml"
    path.write_text(yaml.safe_dump(DOMAINS), encoding="utf-8")

    def run(argv):
        args = _build_parser().parse_args(argv)
        return args.func(args)

    assert run(["rdf", "taxonomy", "--file", str(path), "--match", "lending", "home-loans"]) == 0
    assert "HEARS" in capsys.readouterr().out
    assert run(["rdf", "taxonomy", "--file", str(path), "--match", "mortgage", "lending"]) == 1
    assert "not under 'mortgage'" in capsys.readouterr().out
    assert run(["rdf", "taxonomy", "--file", str(path)]) == 0
    assert "skos:broader" in capsys.readouterr().out
    path.write_text("scheme: s\nconcepts: {a: {broader: [x]}}\n", encoding="utf-8")
    assert run(["rdf", "taxonomy", "--file", str(path)]) == 1
    assert "unknown broader" in capsys.readouterr().err
