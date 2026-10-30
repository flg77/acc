"""RDF-06 — graph-augmented retrieval: a bounded KNOWLEDGE_GRAPH block.

Facts about terms the task mentions, read from the collective's knowledge
store at the task's ceiling as its requester, added beside the memory notes
and evicted first.  Off unless the role opts in; off, or with nothing found,
the prompt is byte-identical.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pyoxigraph")
pytest.importorskip("rdflib")

from acc import kg_retrieval as KR  # noqa: E402
from acc.cognitive_core import CognitiveCore  # noqa: E402
from acc.config import RoleDefinitionConfig  # noqa: E402
from acc.context_budget import KIND_GRAPH, KIND_PEER_LESSONS, pack, standard_blocks  # noqa: E402
from acc.kg import KnowledgeStore  # noqa: E402
from acc.lib.rdf import ACC, DCTERMS, RDF, SKOS, Graph, Literal  # noqa: E402

D = "urn:acc:domain:"


@pytest.fixture
def store() -> KnowledgeStore:
    k = KnowledgeStore("c", in_memory=True)
    k.put("taxonomy", Graph([
        (D + "lending", SKOS.prefLabel, Literal("lending")),
        (D + "mortgage", SKOS.prefLabel, Literal("mortgage")),
        (D + "mortgage", SKOS.altLabel, Literal("home loan")),
        (D + "mortgage", SKOS.broader, D + "lending"),
        (D + "credit-risk", SKOS.prefLabel, Literal("credit risk")),
        (D + "risk", SKOS.prefLabel, Literal("risk")),
    ]), ceiling="LOW")
    k.put("policies", Graph([
        ("urn:policy:lp7", DCTERMS.title, Literal("LP-7")),
        ("urn:policy:lp7", ACC.domain, D + "mortgage"),
        ("urn:policy:lp7", ACC.summary, Literal("caps the debt-to-income ratio at 40 %")),
    ]), ceiling="HIGH")
    mine = k.private_graph_name("webgui:alice", "LOW")
    k.put(mine, Graph([("urn:note:a", ACC.domain, D + "mortgage"),
                       ("urn:note:a", ACC.summary, Literal("alice's private note"))]),
          ceiling="LOW", owner="webgui:alice")
    return k


# ---------------------------------------------------------------------------
# seeds
# ---------------------------------------------------------------------------


def test_seeds_are_whole_words_longest_first():
    labels = [(D + "risk", "risk"), (D + "credit-risk", "credit risk"), (D + "lending", "lending")]
    assert KR.find_seeds(labels, "What is our credit risk on lending?") == [D + "credit-risk", D + "lending",
                                                                            D + "risk"]
    assert KR.find_seeds(labels, "riskiness") == []                     # not a whole word


def test_seeds_skip_short_labels_blank_nodes_and_respect_the_limit():
    labels = [("urn:a", "ab"), ("_:b0", "mortgage"), *[(f"urn:x{i}", f"term{i}") for i in range(9)]]
    text = "ab mortgage " + " ".join(f"term{i}" for i in range(9))
    seeds = KR.find_seeds(labels, text)
    assert "urn:a" not in seeds and "_:b0" not in seeds and len(seeds) == KR.MAX_SEEDS


# ---------------------------------------------------------------------------
# facts, under the information rule
# ---------------------------------------------------------------------------


def test_facts_follow_relations_one_hop(store):
    f = KR.facts_for(store, "Does the home loan rule apply?", reader_ceiling="HIGH")
    assert f.seeds == [D + "mortgage"]
    assert "mortgage -- skos:broader -- lending" in f.items
    assert "LP-7 -- acc:domain -- mortgage" in f.items                  # incoming, by label


def test_a_low_reader_gets_no_high_facts(store):
    f = KR.facts_for(store, "anything about mortgage?", reader_ceiling="LOW")
    assert all("LP-7" not in i for i in f.items)
    assert "mortgage -- skos:broader -- lending" in f.items


def test_private_facts_reach_their_owner_only(store):
    alice = KR.facts_for(store, "mortgage", reader_ceiling="LOW", reader="webgui:alice")
    bob = KR.facts_for(store, "mortgage", reader_ceiling="CRITICAL", reader="webgui:bob")
    assert any("urn:note:a" in i for i in alice.items)
    assert not any("urn:note:a" in i for i in bob.items)


def test_facts_are_capped_and_clipped():
    k = KnowledgeStore("c", in_memory=True)
    g = Graph([(D + "big", SKOS.prefLabel, Literal("bigtopic"))])
    for i in range(60):
        g.add(D + "big", ACC.summary, Literal(f"fact {i} " + "x" * 400))
    k.put("g", g, ceiling="LOW")
    f = KR.facts_for(k, "bigtopic", reader_ceiling="LOW")
    assert len(f.items) == KR.MAX_FACTS and all(len(i) <= KR.MAX_FACT_CHARS for i in f.items)


def test_nothing_to_match_or_a_broken_store_yields_nothing(store):
    assert KR.facts_for(store, "", reader_ceiling="CRITICAL").items == []
    assert KR.facts_for(store, "nothing relevant here", reader_ceiling="CRITICAL").items == []
    broken = SimpleNamespace(query=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert KR.facts_for(broken, "mortgage", reader_ceiling="CRITICAL").items == []


def test_no_store_means_no_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_KG_PATH", str(tmp_path / "none"))
    assert KR.facts_for_task("c", "mortgage", reader_ceiling="CRITICAL").items == []


# ---------------------------------------------------------------------------
# the block in the prompt
# ---------------------------------------------------------------------------


def _facts(*items):
    return KR.GraphFacts(items=list(items), seeds=["urn:x"])


def test_without_graph_facts_the_prompt_is_byte_identical():
    base = dict(task="do x", notes_heading="MEMORY_NOTES:", notes_items=("- n",),
                peer_heading="PEER:", peer_items=("- p",), episodes_heading="EPS:", episodes_items=("- e",))
    before = pack(standard_blocks(**base), 10_000).text
    after = pack(standard_blocks(**base, graph_heading="", graph_items=()), 10_000).text
    assert before == after


def test_the_block_renders_after_the_notes_and_before_peer_lessons():
    h, items = KR.graph_parts(_facts("mortgage -- skos:broader -- lending"))
    text = pack(standard_blocks(task="do x", notes_heading="MEMORY_NOTES:", notes_items=("- n",),
                                peer_heading="PEER:", peer_items=("- p",),
                                graph_heading=h, graph_items=tuple(items)), 10_000).text
    assert text.index("MEMORY_NOTES:") < text.index("KNOWLEDGE_GRAPH") < text.index("PEER:") < text.index("do x")


def test_graph_facts_are_evicted_before_peer_lessons():
    h, items = KR.graph_parts(_facts(*[f"fact {i} " + "y" * 200 for i in range(10)]))
    blocks = standard_blocks(task="do x", peer_heading="PEER:", peer_items=("- p",),
                             graph_heading=h, graph_items=tuple(items))
    result = pack(blocks, 80)
    assert result.kept.get(KIND_PEER_LESSONS) == 1                       # the peer lesson survives whole
    assert result.kept.get(KIND_GRAPH, 0) < 10                           # graph facts give way
    assert {d.kind for d in result.drops} == {KIND_GRAPH}                # and nothing else does


def test_the_unbudgeted_and_budgeted_renderings_agree():
    facts = _facts("mortgage -- skos:broader -- lending", "LP-7 -- acc:domain -- mortgage")
    h, items = KR.graph_parts(facts)
    packed = pack(standard_blocks(task="do x", graph_heading=h, graph_items=tuple(items)), 10_000).text
    assert packed == KR.render_graph_block(facts) + "\n\n" + "do x"


# ---------------------------------------------------------------------------
# the gate in the cognitive core
# ---------------------------------------------------------------------------


def _core():
    core = CognitiveCore.__new__(CognitiveCore)
    core._collective_id = "c"
    return core


def test_off_by_default_and_never_consulted(monkeypatch):
    monkeypatch.setattr(KR, "facts_for_task", lambda *a, **k: pytest.fail("must not be called"))
    assert RoleDefinitionConfig().graph_retrieval is False
    assert _core()._graph_facts_for(RoleDefinitionConfig(), {}, "mortgage") is None


def test_the_kill_switch_wins(monkeypatch):
    monkeypatch.setattr(KR, "facts_for_task", lambda *a, **k: pytest.fail("must not be called"))
    monkeypatch.setenv("ACC_GRAPH_RETRIEVAL", "0")
    assert _core()._graph_facts_for(RoleDefinitionConfig(graph_retrieval=True), {}, "mortgage") is None


def test_ceiling_and_requester_come_from_the_task(monkeypatch):
    seen = {}
    monkeypatch.delenv("ACC_GRAPH_RETRIEVAL", raising=False)
    monkeypatch.setattr(KR, "facts_for_task", lambda cid, text, **kw: seen.update(cid=cid, text=text, **kw)
                        or _facts("x"))
    task = {"requested_by": "webgui:alice@team", "requester_tier": "requester", "requester_ceiling": "LOW"}
    _core()._graph_facts_for(RoleDefinitionConfig(graph_retrieval=True), task, "mortgage")
    assert seen == {"cid": "c", "text": "mortgage", "reader_ceiling": "LOW", "reader": "webgui:alice@team"}
    seen.clear()
    _core()._graph_facts_for(RoleDefinitionConfig(graph_retrieval=True), {}, "mortgage")
    assert seen["reader_ceiling"] == "CRITICAL"                       # unattributed = the operator's own


def test_the_result_carries_the_seeds_for_the_outcome_join():
    from acc.cognitive_core import CognitiveResult
    assert "graph_seeds" in CognitiveResult.__dataclass_fields__
