"""059 -- the plan card: the assistant's plan is the mission, logged and
learned from (OpenSpec ``20261018-plan-card``)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc import plan_card as PC
from acc.assistant_proposal import (
    PROPOSAL_INFUSE,
    PROPOSAL_ROUTE,
    PROPOSAL_SPAWN,
    AssistantProposal,
    decide_dispatch,
)
from acc.mission import MissionStore, STATUS_CLOSED
from acc.perception import PerceptionSnapshot

CID = "sol-01"


def _infuse(name="@acc/redhat-sre-roles"):
    return AssistantProposal(kind=PROPOSAL_INFUSE, params={"name": name, "constraint": ">=0.0.0"},
                             summary=f"Install {name}", rationale="server security report",
                             collective_id=CID)


def _spawn(role="devops_engineer"):
    return AssistantProposal(kind=PROPOSAL_SPAWN, params={"role": role, "cluster_id": "c"},
                             summary=f"Spawn {role}", collective_id=CID)


def _route(role="devops_engineer"):
    return AssistantProposal(kind=PROPOSAL_ROUTE, params={"target_role": role},
                             summary=f"Route to {role}", collective_id=CID)


P2_SNAPSHOT = PerceptionSnapshot(
    roster={"assistant": ["assistant-1"]},
    available_roles=[{"name": "devops_engineer", "summary": "Ansible, CI/CD"},
                     {"name": "assistant", "summary": "chief of staff"},
                     {"name": "arbiter", "summary": "governs"}],
)


# ---------------------------------------------------------------------------
# Pure module
# ---------------------------------------------------------------------------


def test_parse_plan_reads_the_marker_and_validates():
    text = ('ok </reasoning>\n[PROPOSE_PLAN:{"goal": "security report", "steps": ['
            '{"role": "devops_engineer", "staffing": "reuse", "does": "audit {servers}"},'
            '{"role": "", "staffing": "reuse"}, {"role": "x", "staffing": "weird"}],'
            ' "hosts": ["lab1.example.internal", "bad host!", "10.0.0.5:22"]}]')
    plan = PC.parse_plan(text)
    assert plan is not None and plan.goal == "security report"
    assert [(s.role, s.staffing) for s in plan.steps] == [("devops_engineer", "reuse"), ("x", "reuse")]
    assert plan.steps[0].does == "audit {servers}"
    assert plan.hosts == ["10.0.0.5:22", "lab1.example.internal"]


@pytest.mark.parametrize("text", ["no plan here", "[PROPOSE_PLAN:{not json}]", "[PROPOSE_PLAN:{\"a\": 1"])
def test_parse_plan_returns_none_for_a_missing_or_broken_marker(text):
    assert PC.parse_plan(text) is None


def test_derive_builds_one_step_per_staffing_proposal():
    plan = PC.derive([_infuse(), _spawn(), _route()], goal="g")
    assert plan.derived and plan.goal == "g"
    assert [(s.role, s.staffing, s.pack) for s in plan.steps] == [
        ("", "infuse", "@acc/redhat-sre-roles"), ("devops_engineer", "reuse", ""),
        ("devops_engineer", "reuse", ""),
    ]


def test_p2_the_card_names_the_installed_idle_role_on_the_infuse_step():
    """The chief-of-staff A/B P2: the model infuses although devops_engineer is
    installed and idle.  The card says so, whatever the model emitted."""
    plan = PC.annotate_installed_idle(PC.derive([_infuse()]), P2_SNAPSHOT)
    assert plan.steps[0].installed_idle == ["devops_engineer"]
    q = PC.plan_question(plan)
    assert "installed, idle: devops_engineer" in q.text
    assert [o.key for o in q.options] == [PC.ANSWER_PLAN, PC.ANSWER_STEPS, PC.ANSWER_REFUSE]
    assert [o.proceeds for o in q.options] == [True, True, False]


def test_covers_roles_packs_and_roles_an_infused_pack_provides(monkeypatch):
    plan = PC.Plan(steps=[PC.PlanStep(role="devops_engineer"),
                          PC.PlanStep(role="", staffing="infuse", pack="@acc/redhat-sre-roles")])
    assert PC.covers(plan, _route()) == ""
    assert PC.covers(plan, _infuse()) == ""
    assert "not in the plan" in PC.covers(plan, _infuse("@acc/other"))
    assert "not in the plan" in PC.covers(plan, _route("rhel_reliability_engineer"))
    monkeypatch.setattr(PC, "_pack_of_role",
                        lambda r: "@acc/redhat-sre-roles" if r == "rhel_reliability_engineer" else "")
    assert PC.covers(plan, _route("rhel_reliability_engineer")) == ""


def test_planned_vs_actual_and_lesson_candidates():
    plan = PC.Plan(steps=[PC.PlanStep(role="devops_engineer"), PC.PlanStep(role="analyst")])
    pva = PC.planned_vs_actual(plan, [{"role": "devops_engineer"}], ["assistant", "reviewer"])
    assert pva == {"planned": ["analyst", "devops_engineer"], "ran": ["devops_engineer"],
                   "skipped": ["analyst"], "unplanned": ["reviewer"]}
    closed = [{"mission_id": f"m-{i}", "planned_vs_actual": pva} for i in range(3)]
    found = PC.lesson_candidates(closed)
    assert {(c["kind"], c["role"]) for c in found} == {
        ("planned_not_needed", "analyst"), ("needed_not_planned", "reviewer")}
    assert PC.lesson_candidates(closed[:2]) == []


def test_a_plan_is_never_executed_unasked():
    for mode in ("AUTO", "ASK_ONCE", "ACCEPT_EDITS", "ASK_PERMISSIONS"):
        assert decide_dispatch(mode, "plan") == "queue"
    assert decide_dispatch("PLAN", "plan") == "plan"


# ---------------------------------------------------------------------------
# Agent: hold, approve, amend
# ---------------------------------------------------------------------------


@pytest.fixture
def agent(monkeypatch, tmp_path):
    import acc.assistant_proposal as ap
    from acc.agent import Agent

    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path / "trace"))
    dispatched = AsyncMock(return_value=True)
    announced = AsyncMock()
    monkeypatch.setattr(ap, "dispatch_approved_proposal", dispatched)
    monkeypatch.setattr(ap, "publish_proposal_pending", announced)
    queue = SimpleNamespace(_redis=None, _cid=CID, _timeout_s=300,
                            submit=AsyncMock(return_value="ov-1"),
                            record_auto_approved=AsyncMock(return_value="ov-auto"))
    a = SimpleNamespace(
        agent_id="assistant-1",
        config=SimpleNamespace(agent=SimpleNamespace(collective_id=CID, role="assistant"),
                               security=SimpleNamespace(nkey=SimpleNamespace(enabled=False, role=""))),
        backends=SimpleNamespace(signaling=MagicMock(publish=AsyncMock())),
        _redis=None,
        _oversight_queue=queue,
        _cognitive_core=SimpleNamespace(_perception=P2_SNAPSHOT),
        _may_dispatch_proposal=lambda kind: True,
        _nkey_identity=lambda: "assistant",
        _record_auto_approved=AsyncMock(),
    )
    for name in ("_handle_assistant_proposals", "_hold_staffing_in_plan", "_dispatch_plan",
                 "_handover_store_for", "_publish_handover_outcome"):
        setattr(a, name, getattr(Agent, name).__get__(a))
    a.dispatched, a.announced, a.queue = dispatched, announced, queue
    return a


def _run(a, proposals, mode="ASK_ONCE", output=""):
    result = SimpleNamespace(assistant_proposals_executed=list(proposals),
                             assistant_proposals_queued=[], output=output)
    task = {"task_id": "t-1", "session_id": "s-1", "operating_mode": mode,
            "content": "Give me a security report on the lab servers."}
    asyncio.run(a._handle_assistant_proposals(result, task, CID))


def _card(a):
    return a.announced.await_args.args[1]


def test_ask_once_holds_staffing_in_one_plan_card(agent):
    _run(agent, [_infuse(), _spawn("rhel_reliability_engineer")])
    agent.dispatched.assert_not_awaited()
    agent.queue.submit.assert_awaited_once()
    card = _card(agent)
    assert card.kind == "plan" and card.risk_level == "HIGH"
    assert [p["kind"] for p in card.params["proposals"]] == ["infuse", "spawn"]
    q = agent.queue.submit.await_args.kwargs["question"]
    assert "installed, idle: devops_engineer" in q["text"]
    assert card.params["threads"] == ["s-1", "t-1"]


def test_auto_mode_is_unchanged(agent):
    _run(agent, [_route()], mode="AUTO")
    assert agent.dispatched.await_count == 1
    agent.queue.submit.assert_not_awaited()


def test_approving_the_plan_creates_the_mission_and_runs_what_it_held(agent, tmp_path):
    _run(agent, [_infuse(), _route()])
    card = _card(agent)
    asyncio.run(agent._dispatch_plan(CID, card, "tui:operator", PC.ANSWER_PLAN))
    kinds = [c.args[1].kind for c in agent.dispatched.await_args_list]
    assert kinds == ["infuse", "route"]
    (m,) = asyncio.run(MissionStore.for_queue(agent.queue).all())
    assert m.plan["steps"][0]["pack"] == "@acc/redhat-sre-roles" and m.approver == "tui:operator"
    rec = [json.loads(line) for line in (tmp_path / "trace" / f"mission-{m.mission_id}.jsonl")
           .read_text(encoding="utf-8").splitlines()]
    assert rec[0]["kind"] == "plan" and rec[0]["event"] == PC.EVENT_APPROVED
    assert rec[0]["plan_hash"] == PC.Plan.from_dict(m.plan).digest()


def test_inside_the_plan_runs_outside_it_is_one_amendment_card(agent):
    _run(agent, [_route()])
    asyncio.run(agent._dispatch_plan(CID, _card(agent), "tui:operator", PC.ANSWER_PLAN))
    agent.dispatched.reset_mock()
    agent.queue.submit.reset_mock()
    # A follow-up in the same thread: devops_engineer is in the plan, analyst is not.
    _run(agent, [_route(), _route("analyst"), _spawn("analyst")])
    assert [c.args[1].params.get("target_role") for c in agent.dispatched.await_args_list] == ["devops_engineer"]
    agent.queue.submit.assert_awaited_once()
    card = _card(agent)
    assert card.params["amendment"] is True and card.summary.startswith("Plan amendment")
    assert [p["kind"] for p in card.params["proposals"]] == ["route", "spawn"]
    asyncio.run(agent._dispatch_plan(CID, card, "tui:operator", PC.ANSWER_PLAN))
    (m,) = asyncio.run(MissionStore.for_queue(agent.queue).all())
    assert {s["role"] for s in m.plan["steps"]} == {"devops_engineer", "analyst"}


def test_just_these_steps_runs_them_once_without_a_mission(agent):
    _run(agent, [_route()])
    asyncio.run(agent._dispatch_plan(CID, _card(agent), "tui:operator", PC.ANSWER_STEPS))
    assert agent.dispatched.await_count == 1
    assert asyncio.run(MissionStore.for_queue(agent.queue).all()) == []


def test_closing_the_mission_records_planned_vs_actual(agent, tmp_path):
    _run(agent, [_route(), _route("analyst")])
    asyncio.run(agent._dispatch_plan(CID, _card(agent), "tui:operator", PC.ANSWER_PLAN))
    store = MissionStore.for_queue(agent.queue)
    (m,) = asyncio.run(store.all())
    m.calls.append({"role": "devops_engineer", "kind": "skill", "target": "shell_exec"})
    asyncio.run(store.save(m))
    asyncio.run(store.end(m.mission_id, status=STATUS_CLOSED, by="cli:operator"))
    lines = (tmp_path / "trace" / f"mission-{m.mission_id}.jsonl").read_text(encoding="utf-8").splitlines()
    closed = [json.loads(x) for x in lines if '"PLAN_CLOSED"' in x]
    assert closed[0]["planned_vs_actual"]["skipped"] == ["analyst"]
    assert closed[0]["planned_vs_actual"]["ran"] == ["devops_engineer"]


# ---------------------------------------------------------------------------
# Review fixes (2026-10-19)
# ---------------------------------------------------------------------------


def test_a_plain_approve_runs_the_steps_once_without_a_mission(agent):
    """Compliance / `acc-cli oversight approve` send no option: the narrower yes."""
    _run(agent, [_route()])
    asyncio.run(agent._dispatch_plan(CID, _card(agent), "cli:operator", ""))
    assert agent.dispatched.await_count == 1
    assert asyncio.run(MissionStore.for_queue(agent.queue).all()) == []


def test_an_identical_waiting_card_is_not_queued_again(agent):
    _run(agent, [_infuse()])
    summary = _card(agent).summary
    agent.queue.pending = AsyncMock(return_value=[SimpleNamespace(summary=summary)])
    agent.queue.submit.reset_mock()
    _run(agent, [_infuse()])            # the wake-up proposes the same again
    agent.queue.submit.assert_not_awaited()
    agent.dispatched.assert_not_awaited()


def test_a_mission_closed_by_several_agents_counts_once():
    pva = {"planned": ["analyst"], "ran": [], "skipped": ["analyst"], "unplanned": []}
    closed = [{"mission_id": "m-1", "planned_vs_actual": pva}] * 6
    assert PC.lesson_candidates(closed) == []
    closed += [{"mission_id": f"m-{i}", "planned_vs_actual": pva} for i in (2, 3)]
    (c,) = PC.lesson_candidates(closed)
    assert c["missions"] == 3 and c["of"] == 3
