"""RDF-05 — kg_query and kg_propose: graph skills behind a SHACL gate.

Operator decisions 2026-10-03: the skills are opt-in (``knowledge_graph``);
accepted facts land in the requester's **private** graph and become shared
only through a ``kg_share`` proposal a person approves; the store sits on a
shared volume, agents open it read-only, the **arbiter is the only writer**.

What these assert: the ceiling and requester come from governance, never from
the skill arguments; nothing is written without shapes; a private graph is
its owner's alone; the arbiter re-checks everything it is sent.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pyoxigraph")
pytest.importorskip("pyshacl")

from acc import kg_writes  # noqa: E402
from acc.agent import Agent  # noqa: E402
from acc.assistant_proposal import (  # noqa: E402
    DISPATCH_QUEUE,
    PROPOSAL_KG_SHARE,
    decide_dispatch,
    dispatch_approved_proposal,
)
from acc.call_context import CallContext, calling, current  # noqa: E402
from acc.config import RoleDefinitionConfig  # noqa: E402
from acc.kg import KGError, KnowledgeStore  # noqa: E402
from acc.kg_shapes import ShapesError, load_shapes, parse_proposal, validate  # noqa: E402
from acc.lib.rdf import ACC, RDF, Graph, Literal  # noqa: E402

SHAPES = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix acc: <urn:acc:vocab:> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<urn:acc:shapes:note> a sh:NodeShape ;
    sh:targetClass acc:Note ;
    sh:property [ sh:path acc:summary ; sh:minCount 1 ; sh:maxCount 1 ; sh:datatype xsd:string ] .
"""
GOOD = 'acc:n1 a acc:Note ; acc:summary "LP-7 caps DTI at 40 %" .'
BAD = 'acc:n2 a acc:Note .'


def _registry(tmp_path, ttl=SHAPES, name="@acme/kg-shapes"):
    root = tmp_path / "pack"
    (root / "shapes").mkdir(parents=True)
    if ttl:
        (root / "shapes" / "notes.ttl").write_text(ttl, encoding="utf-8")
    entry = SimpleNamespace(name=name, version="1.0.0", install_path=str(root),
                            installed_at="2026-10-03T00:00:00+00:00", content_sha256="0" * 64)
    return SimpleNamespace(list=lambda: [entry])


@pytest.fixture
def shapes(tmp_path):
    graph, _ = load_shapes("@acme/kg-shapes", _registry(tmp_path))
    return graph


@pytest.fixture
def kg_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_KG_PATH", str(tmp_path / "kg"))
    return tmp_path / "kg"


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_no_shapes_means_nothing_may_be_written(tmp_path, monkeypatch):
    monkeypatch.delenv("ACC_KG_SHAPES", raising=False)
    with pytest.raises(ShapesError, match="not set"):
        load_shapes()
    with pytest.raises(ShapesError, match="not installed"):
        load_shapes("@acme/other", _registry(tmp_path))
    with pytest.raises(ShapesError, match="no shapes"):
        load_shapes("@acme/kg-shapes", _registry(tmp_path / "empty", ttl=""))


def test_conforming_and_non_conforming(shapes):
    ok = validate(GOOD, shapes)
    assert ok.conforms and ok.triples == 2 and ok.graph is not None
    bad = validate(BAD, shapes)
    assert not bad.conforms and bad.graph is None
    assert any("summary" in m for m in bad.messages)


@pytest.mark.parametrize("text, why", [
    ("", "no triples"),
    ("this is not turtle", "not Turtle"),
    ("\n".join(f"<urn:x:{i}> <urn:p> 'v' ." for i in range(201)), "at most 200"),
    (f'<urn:x> <urn:p> "{"x" * 4001}" .', "longer than"),
])
def test_unreadable_or_oversized_proposals_are_refused(text, why):
    with pytest.raises(ShapesError, match=why):
        parse_proposal(text)


# ---------------------------------------------------------------------------
# private graphs: the owner alone reads them
# ---------------------------------------------------------------------------


def test_a_private_graph_is_its_owners_alone():
    k = KnowledgeStore("c", in_memory=True)
    name = k.private_graph_name("webgui:alice", "LOW")
    k.put(name, Graph([("urn:n", ACC.summary, Literal("alice's"))]), ceiling="LOW", owner="webgui:alice")
    q = "SELECT ?t WHERE { ?s acc:summary ?t }"
    assert k.query(q, reader_ceiling="LOW", reader="webgui:alice").rows == [{"t": "alice's"}]
    assert k.query(q, reader_ceiling="CRITICAL", reader="webgui:bob").rows == []      # ceiling is not enough
    assert k.query(q, reader_ceiling="CRITICAL").rows == []                           # nor is anonymity
    assert k.query(q, reader_ceiling="LOW", reader="webgui:alice@team-a").rows == [{"t": "alice's"}]  # same person
    assert "alice" not in k._all_graphs()[0].owner                                    # pseudonymous


def test_one_private_graph_per_ceiling():
    k = KnowledgeStore("c", in_memory=True)
    low, high = k.private_graph_name("webgui:alice", "LOW"), k.private_graph_name("webgui:alice", "HIGH")
    assert low != high and low.endswith("-low") and high.endswith("-high")


def test_owner_pseudonyms_survive_reopening_and_read_only(tmp_path):
    w = KnowledgeStore("c", tmp_path / "kg")
    name = w.private_graph_name("webgui:alice", "LOW")
    w.put(name, Graph([("urn:n", RDF.type, ACC.Note)]), ceiling="LOW", owner="webgui:alice")
    del w
    r = KnowledgeStore("c", tmp_path / "kg", read_only=True)
    assert [g.name for g in r.graphs("LOW", "webgui:alice")] == [name]
    with pytest.raises(KGError, match="read-only"):
        r.put("x", Graph(), ceiling="LOW")


def test_a_reader_never_creates_the_salt(tmp_path):
    (tmp_path / "kg").mkdir()
    import pyoxigraph
    pyoxigraph.Store(str(tmp_path / "kg"))      # a store with no salt yet
    r = KnowledgeStore("c", tmp_path / "kg", read_only=True)
    assert r.owner_iri("webgui:alice") == ""          # nothing can be owned yet
    assert r.graphs("CRITICAL", "webgui:alice") == []
    assert not (tmp_path / "kg" / ".owner-salt").exists()


# ---------------------------------------------------------------------------
# the write path: arbiter applies, re-validating
# ---------------------------------------------------------------------------


def _payload(text=GOOD, **kw):
    ctx = CallContext(requester="webgui:alice", ceiling="MEDIUM", collective_id="c", task_id="t1")
    body = kg_writes.write_request(validate(text, _shapes_cache()).graph.to_ntriples()
                                   if text == GOOD else text, ctx, **kw)
    body.update(from_agent="analyst-1", from_role="analyst")
    return body


_CACHE: dict = {}


def _shapes_cache():
    if "g" not in _CACHE:
        import rdflib
        _CACHE["g"] = rdflib.Graph().parse(data=SHAPES, format="turtle")
    return _CACHE["g"]


def test_apply_write_lands_in_the_requesters_private_graph(shapes):
    k = KnowledgeStore("c", in_memory=True)
    name, n = kg_writes.apply_write(k, _payload(), shapes)
    assert n == 2 and name == k.private_graph_name("webgui:alice", "MEDIUM")
    (g,) = k.graphs("MEDIUM", "webgui:alice")
    assert g.ceiling == "MEDIUM" and g.scope == "private" and g.owner
    kg_writes.apply_write(k, _payload(), shapes)                     # appends, does not replace
    assert k.graphs("MEDIUM", "webgui:alice")[0].triples == 2       # same triples: a set


def test_the_arbiter_does_not_trust_the_senders_check(shapes):
    k = KnowledgeStore("c", in_memory=True)
    with pytest.raises(ShapesError, match="does not conform"):
        kg_writes.apply_write(k, _payload(text=BAD), shapes)
    bad = _payload()
    bad["requester"] = ""
    with pytest.raises(ShapesError, match="no requester"):
        kg_writes.apply_write(k, bad, shapes)
    assert k._all_graphs() == []


def test_requester_and_ceiling_come_from_the_call_context_not_the_model():
    ctx = CallContext(requester="webgui:alice", ceiling="LOW", collective_id="c", task_id="t9")
    body = kg_writes.write_request("<urn:a> <urn:b> 'c' .", ctx, share=True)
    assert (body["requester"], body["ceiling"], body["task_id"], body["share"]) == ("webgui:alice", "LOW", "t9", True)
    assert "from_agent" not in body          # the sending agent stamps itself


# ---------------------------------------------------------------------------
# sharing: a proposal a person approves; dispatched by the arbiter
# ---------------------------------------------------------------------------


def test_kg_share_never_runs_on_its_own():
    for mode in ("AUTO", "ACCEPT_EDITS", "ASK_PERMISSIONS"):
        assert decide_dispatch(mode, PROPOSAL_KG_SHARE) == DISPATCH_QUEUE


def test_an_approved_share_lands_in_the_shared_graph(kg_path, tmp_path, monkeypatch):
    monkeypatch.setattr("acc.kg_shapes.load_shapes",
                        lambda name=None, registry=None: (_shapes_cache(), "@acme/kg-shapes@1.0.0"))
    p = kg_writes.share_proposal(_payload(share=True), "private-x-medium")
    assert p.kind == PROPOSAL_KG_SHARE and p.params["destination_graph"] == "shared-medium"
    p.operator_id = "k8s:user/carol"
    sig = AsyncMock()
    assert asyncio.run(dispatch_approved_proposal(sig, p, None)) is True
    k = KnowledgeStore("c", read_only=True)
    q = "SELECT ?t WHERE { ?s acc:summary ?t }"
    assert k.query(q, reader_ceiling="MEDIUM", reader="webgui:bob").rows == [{"t": "LP-7 caps DTI at 40 %"}]
    assert k.query(q, reader_ceiling="LOW").rows == []                           # still ceiling-bound
    assert sig.publish.await_args.args[1]["trigger"] == "kg_shared"


def test_a_share_with_no_named_approver_is_refused(kg_path):
    p = kg_writes.share_proposal(_payload(share=True), "private-x-medium")
    p.operator_id = "tui:anonymous"
    assert asyncio.run(dispatch_approved_proposal(AsyncMock(), p, None)) is False


# ---------------------------------------------------------------------------
# the agent: who may write, who may dispatch
# ---------------------------------------------------------------------------


def _arbiter(*, role="arbiter", definitions=None, roster=None):
    defs = definitions if definitions is not None else {"analyst": {"knowledge_graph": True}}
    stub = SimpleNamespace(
        agent_id="arbiter-1", _worker_roster=roster or {},
        config=SimpleNamespace(agent=SimpleNamespace(role=role, collective_id="c")),
        _resolve_role_definition=lambda r: defs.get(r),
        _sender_refusal=lambda payload, claimed_role="": "",
        _queue_assistant_proposal=AsyncMock(return_value="ov-1"),
    )
    stub._kg_write_refusal = lambda p: Agent._kg_write_refusal(stub, p)
    return stub


@pytest.mark.parametrize("change, reason", [
    ({"ntriples": ""}, "incomplete"),
    ({"collective_id": "other"}, "another collective"),
    ({"from_role": "observer"}, "not resolvable"),
])
def test_the_arbiter_refuses_what_it_cannot_honour(change, reason):
    body = {**_payload(), **change}
    assert reason in Agent._kg_write_refusal(_arbiter(), body)


def test_a_role_without_the_flag_may_not_write():
    stub = _arbiter(definitions={"analyst": {"knowledge_graph": False}})
    assert "does not carry knowledge_graph" in Agent._kg_write_refusal(stub, _payload())


def test_the_roster_must_agree_on_the_senders_role():
    stub = _arbiter(roster={"analyst-1": SimpleNamespace(role="observer")})
    assert "roster says" in Agent._kg_write_refusal(stub, _payload())


def test_an_unproven_sender_is_refused():
    stub = _arbiter()
    stub._sender_refusal = lambda payload, claimed_role="": "sender not proven: bad signature"
    assert "not proven" in Agent._kg_write_refusal(stub, _payload())


def test_the_arbiter_writes_and_queues_a_share(kg_path, monkeypatch):
    monkeypatch.setattr("acc.kg_shapes.load_shapes",
                        lambda name=None, registry=None: (_shapes_cache(), "@acme/kg-shapes@1.0.0"))
    stub = _arbiter()
    name = asyncio.run(Agent._handle_kg_write(stub, _payload(share=True)))
    assert name.startswith("private-") and name.endswith("-medium")
    (proposal, cid), _ = stub._queue_assistant_proposal.await_args
    assert proposal.kind == PROPOSAL_KG_SHARE and cid == "c"


def test_only_the_arbiter_writes():
    assert asyncio.run(Agent._handle_kg_write(_arbiter(role="analyst"), _payload())) == ""


def test_only_the_arbiter_may_claim_a_kg_share():
    for role, allowed in (("arbiter", True), ("analyst", False)):
        stub = SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(role=role)))
        assert Agent._may_dispatch_proposal(stub, PROPOSAL_KG_SHARE) is allowed


def test_the_agent_stamps_and_signs_what_it_sends():
    sig = AsyncMock()
    stub = SimpleNamespace(agent_id="analyst-1", backends=SimpleNamespace(signaling=sig),
                           config=SimpleNamespace(agent=SimpleNamespace(role="analyst", collective_id="c")),
                           _sign_ask=lambda body: {**body, "proof": "signed"})
    body = kg_writes.write_request("<urn:a> <urn:b> 'c' .",
                                   CallContext(requester="webgui:alice", ceiling="LOW", task_id="t1"))
    assert asyncio.run(Agent._submit_kg_write(stub, body)) is True
    subject, sent = sig.publish.await_args.args
    assert subject == "acc.c.kg.write"
    assert (sent["from_agent"], sent["from_role"], sent["collective_id"], sent["proof"]) == (
        "analyst-1", "analyst", "c", "signed")


# ---------------------------------------------------------------------------
# the skills
# ---------------------------------------------------------------------------


def _skill(sid):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "skills" / sid / "adapter.py"
    spec = importlib.util.spec_from_file_location(f"_{sid}_adapter", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = {"kg_query": "KgQuerySkill", "kg_propose": "KgProposeSkill"}[sid]
    return getattr(mod, cls).__new__(getattr(mod, cls))


def test_skills_refuse_outside_a_task():
    assert current().ceiling == ""
    assert "inside a task" in asyncio.run(_skill("kg_query").invoke({"sparql": "ASK {}"}))["error"]
    assert "inside a task" in asyncio.run(_skill("kg_propose").invoke({"turtle": GOOD}))["error"]


def test_kg_query_reads_at_the_tasks_ceiling_and_as_its_requester(kg_path):
    w = KnowledgeStore("c")
    w.put("low", Graph([("urn:a", ACC.summary, Literal("low"))]), ceiling="LOW")
    w.put("high", Graph([("urn:b", ACC.summary, Literal("high"))]), ceiling="HIGH")
    mine = w.private_graph_name("webgui:alice", "LOW")
    w.put(mine, Graph([("urn:c", ACC.summary, Literal("alice's"))]), ceiling="LOW", owner="webgui:alice")
    del w
    q = {"sparql": "SELECT ?t WHERE { ?s acc:summary ?t } ORDER BY ?t"}
    with calling(CallContext(requester="webgui:alice", ceiling="LOW", collective_id="c")):
        out = asyncio.run(_skill("kg_query").invoke(q))
    assert [r["t"] for r in out["rows"]] == ["alice's", "low"]
    with calling(CallContext(requester="webgui:bob", ceiling="HIGH", collective_id="c")):
        out = asyncio.run(_skill("kg_query").invoke(q))
    assert [r["t"] for r in out["rows"]] == ["high", "low"]
    with calling(CallContext(requester="webgui:bob", ceiling="LOW", collective_id="c")):
        out = asyncio.run(_skill("kg_query").invoke({"sparql": "SELECT ?o WHERE { SERVICE <http://x/> { ?s ?p ?o } }"}))
    assert "refused" in out["error"]


def test_kg_propose_validates_then_sends(monkeypatch):
    monkeypatch.setattr("acc.kg_shapes.load_shapes",
                        lambda name=None, registry=None: (_shapes_cache(), "@acme/kg-shapes@1.0.0"))
    sent = []

    async def submitter(payload):
        sent.append(payload)
        return True

    kg_writes.register_submitter(submitter)
    try:
        ctx = CallContext(requester="webgui:alice", ceiling="MEDIUM", collective_id="c", task_id="t1")
        with calling(ctx):
            refused = asyncio.run(_skill("kg_propose").invoke({"turtle": BAD}))
            assert refused["accepted"] is False and refused["messages"]
            assert sent == []                                       # a refused proposal is never sent
            ok = asyncio.run(_skill("kg_propose").invoke({"turtle": GOOD, "share": True}))
        assert ok == {"accepted": True, "sent": True, "triples": 2, "share": True}
        (payload,) = sent
        assert (payload["requester"], payload["ceiling"], payload["share"]) == ("webgui:alice", "MEDIUM", True)
    finally:
        kg_writes.register_submitter(None)


def test_kg_propose_without_a_bus_writes_nothing(monkeypatch):
    monkeypatch.setattr("acc.kg_shapes.load_shapes",
                        lambda name=None, registry=None: (_shapes_cache(), "x"))
    with calling(CallContext(requester="webgui:alice", ceiling="LOW", collective_id="c")):
        out = asyncio.run(_skill("kg_propose").invoke({"turtle": GOOD}))
    assert out["accepted"] is True and out["sent"] is False and "nothing was written" in out["error"]


def test_kg_propose_needs_a_requester():
    with calling(CallContext(requester="", ceiling="CRITICAL", collective_id="c")):
        out = asyncio.run(_skill("kg_propose").invoke({"turtle": GOOD}))
    assert "no requester" in out["error"]


# ---------------------------------------------------------------------------
# the grant, and the dispatcher setting the context
# ---------------------------------------------------------------------------


def test_the_flag_grants_both_skills_and_nothing_without_it():
    assert {"kg_query", "kg_propose"} <= set(RoleDefinitionConfig(knowledge_graph=True).allowed_skills)
    assert not {"kg_query", "kg_propose"} & set(RoleDefinitionConfig().allowed_skills)


def test_dispatch_sets_the_call_context_from_the_task(monkeypatch):
    from acc import capability_dispatch as CD

    seen = []

    async def fake_one(inv, core, role, **kw):
        seen.append(current())
        return SimpleNamespace()

    monkeypatch.setattr(CD, "_dispatch_one", fake_one)
    inv = CD.ParsedInvocation(kind="skill", target="kg_query", args={"sparql": "ASK {}"}, args_error="", raw="")
    asyncio.run(CD.dispatch_invocations([inv], SimpleNamespace(_collective_id="c"), RoleDefinitionConfig(),
                                        task_id="t1", requester="webgui:alice", requester_ceiling="LOW"))
    assert seen == [CallContext(requester="webgui:alice", ceiling="LOW", collective_id="c", task_id="t1")]
    assert current() == CallContext()                                   # reset after the call


# ---------------------------------------------------------------------------
# the store lock: a write waits for it, never vanishes (found on lighthouse)
# ---------------------------------------------------------------------------


def _flaky_writer(monkeypatch, failures, exc=None):
    calls = {"n": 0}

    @__import__("contextlib").contextmanager
    def fake(collective_id, path=None):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise exc or OSError("IO error: While lock file: .../LOCK: Resource temporarily unavailable")
        yield SimpleNamespace(name="store")

    monkeypatch.setattr(kg_writes, "writer", fake)
    return calls


def test_a_locked_store_is_waited_for(monkeypatch):
    calls = _flaky_writer(monkeypatch, failures=3)
    slept = []

    async def sleep(s):
        slept.append(s)

    out = asyncio.run(kg_writes.with_writer("c", lambda store: store.name, sleep=sleep))
    assert out == "store" and calls["n"] == 4 and slept == [0.25, 0.5, 1.0]


def test_a_lock_held_past_the_wait_is_a_loud_error(monkeypatch):
    _flaky_writer(monkeypatch, failures=10_000)

    async def sleep(s):
        return None

    with pytest.raises(OSError, match="lock"):
        asyncio.run(kg_writes.with_writer("c", lambda store: None, wait_s=0.0, sleep=sleep))


def test_other_errors_are_not_retried(monkeypatch):
    calls = _flaky_writer(monkeypatch, failures=1, exc=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(kg_writes.with_writer("c", lambda store: None))
    assert calls["n"] == 1


def test_a_real_lock_held_by_another_process_is_waited_out(kg_path):
    import subprocess
    import sys
    KnowledgeStore("c")                                   # create, release
    holder = subprocess.Popen([sys.executable, "-c",
                               "import time; from acc.kg import KnowledgeStore\n"
                               "k=KnowledgeStore('c'); print('held', flush=True); time.sleep(1.5)"],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    t0 = __import__("time").monotonic()
    names = asyncio.run(kg_writes.with_writer(
        "c", lambda store: store.put("g", Graph([("urn:a", RDF.type, ACC.Note)]), ceiling="LOW").name))
    holder.wait()
    assert names == "g" and __import__("time").monotonic() - t0 > 0.5
