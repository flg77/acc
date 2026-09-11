"""`20260911-question-envelope` (UX-02) — the question envelope.

An agent asks the operator a typed question on the oversight row and waits for
the answer.  The first thing it asks: a call that deletes or overwrites data.

Layers, tested separately: the classifier, the question value, the row, the
dispatcher, and the Prompt pane (pure core and the real screen).
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.widgets import Static, TextArea

from acc.capability_dispatch import ParsedInvocation, dispatch_invocations
from acc.config import RoleDefinitionConfig
from acc.operating_modes import destructive_evidence
from acc.oversight import HumanOversightQueue, OversightItem
from acc.question import Question, destructive_confirm

# ---------------------------------------------------------------------------
# the classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", [
    "rm -rf build/",
    "cd /srv && rm -f old.log",
    "sudo rm /etc/acc/acc.env",
    "ls *.tmp | xargs -0 rm",
    "rmdir empty",
    "find . -name '*.pyc' -delete",
    "dd if=/dev/zero of=/dev/sdb bs=1M",
    "mkfs.xfs /dev/sdb1",
    "git push --force origin main",
    "git push -f",
    "git reset --hard HEAD~3",
    "git clean -fdx",
    "git branch -D feature",
    "kubectl -n acc delete pod acc-0",
    "oc delete project sandbox",
    "podman volume rm acc-data",
    "docker system prune -af",
    "psql -c 'DROP TABLE users'",
    "mysql -e \"DELETE FROM notes WHERE 1\"",
    "sqlite3 db 'update notes set body = null'",
])
def test_a_destructive_command_is_found(cmd):
    assert destructive_evidence("skill", "shell_exec", {"cmd": cmd})


@pytest.mark.parametrize("cmd", [
    "ls -la",
    "grep -r rm .",
    "echo removed",
    "git push origin main",
    "git push --follow-tags",
    "git clean -n",
    "git branch -a",
    "kubectl get pods",
    "podman ps",
    "cat farm.txt",
    "terraform plan",
    "select * from update_log",
])
def test_an_ordinary_command_is_not(cmd):
    assert destructive_evidence("skill", "shell_exec", {"cmd": cmd}) == ""


def test_the_evidence_is_the_command_that_will_run():
    ev = destructive_evidence("skill", "shell_exec", {"cmd": "cd /srv && rm -rf cache; ls"})
    assert ev == "rm -rf cache"


def test_argv_and_ssh_and_python_are_read_too():
    assert destructive_evidence("skill", "shell_exec", {"argv": ["rm", "-r", "x"]})
    assert destructive_evidence("skill", "ssh_exec", {"host": "h", "cmd": "rm -rf /var/tmp/x"})
    assert destructive_evidence("skill", "python_exec", {"code": "import shutil; shutil.rmtree('x')"})
    assert destructive_evidence("skill", "python_exec", {"code": "os.remove(p)"})
    assert destructive_evidence("skill", "python_exec", {"code": "print(1)"}) == ""


class _M:
    def __init__(self, risk_level="LOW", purpose="stub", **flags):
        self.risk_level = risk_level
        self.purpose = purpose
        for k, v in flags.items():
            setattr(self, k, v)


def test_a_declared_flag_wins_over_the_name():
    assert destructive_evidence("skill", "tidy", {}, _M(destructive=True))
    assert destructive_evidence("skill", "delete_note", {}, _M(destructive=False)) == ""
    assert destructive_evidence("skill", "delete_note", {}, _M())       # undeclared: the name


def test_an_exec_skill_is_read_whatever_it_declares():
    """What will actually run beats what the manifest says about itself."""
    assert destructive_evidence("skill", "shell_exec", {"cmd": "rm x"}, _M(destructive=False))


def test_mcp_tools_are_declared_per_tool():
    assert destructive_evidence("mcp", "files.archive", {}, _M(destructive_tools=["archive"]))
    assert destructive_evidence("mcp", "files.delete_file", {}, _M())
    assert destructive_evidence("mcp", "files.read_file", {}, _M()) == ""


# ---------------------------------------------------------------------------
# the question
# ---------------------------------------------------------------------------


def test_a_question_round_trips_through_the_row():
    q = destructive_confirm("skill", "shell_exec", "rm -rf build/")
    back = Question.from_dict(q.to_dict())
    assert back == q
    assert back.destructive and back.evidence == "rm -rf build/"
    assert "rm -rf build/" in back.text
    assert back.option("1").proceeds is True
    assert back.option("2").proceeds is False
    assert back.option("9") is None


def test_no_question_is_none():
    assert Question.from_dict({}) is None
    assert Question.from_dict(None) is None
    assert Question.from_dict({"text": "x", "options": []}) is None


# ---------------------------------------------------------------------------
# the row
# ---------------------------------------------------------------------------


def _q() -> dict:
    return destructive_confirm("skill", "shell_exec", "rm -rf build/").to_dict()


@pytest.mark.asyncio
async def test_the_answer_is_recorded_on_the_row():
    queue = HumanOversightQueue(redis_client=None, collective_id="t")
    oid = await queue.submit("t-1", "HIGH", "DESTRUCTIVE skill shell_exec", "r", question=_q())
    assert await queue.approve(oid, "tui:op", answer="1") is True
    row = await queue._load(oid)
    assert row.status == "APPROVED" and row.answer == "1"
    assert row.question["destructive"] is True


@pytest.mark.asyncio
async def test_an_answer_that_does_not_fit_the_decision_is_refused():
    queue = HumanOversightQueue(redis_client=None, collective_id="t")
    oid = await queue.submit("t-1", "HIGH", "s", "r", question=_q())
    assert await queue.approve(oid, "tui:op", answer="2") is False   # "don't run it"
    assert await queue.approve(oid, "tui:op", answer="7") is False   # not an option
    assert await queue.reject(oid, "tui:op", answer="1") is False    # "run it"
    assert (await queue._load(oid)).status == "PENDING"
    assert await queue.reject(oid, "tui:op", "no", answer="2") is True
    row = await queue._load(oid)
    assert row.status == "REJECTED" and row.answer == "2"


@pytest.mark.asyncio
async def test_a_decision_without_an_answer_still_decides():
    """The Compliance pane and acc-cli decide without choosing an option."""
    queue = HumanOversightQueue(redis_client=None, collective_id="t")
    oid = await queue.submit("t-1", "HIGH", "s", "r", question=_q())
    assert await queue.approve(oid, "tui:op") is True


@pytest.mark.asyncio
async def test_an_expired_row_refuses_a_late_answer():
    queue = HumanOversightQueue(redis_client=None, collective_id="t")
    oid = await queue.submit("t-1", "HIGH", "s", "r", question=_q())
    assert await queue.expire(oid) is True
    assert (await queue._load(oid)).status == "EXPIRED"
    assert await queue.approve(oid, "tui:op", answer="1") is False
    assert await queue.expire(oid) is False            # already decided


def test_a_row_written_before_the_question_still_loads():
    old = dict(
        oversight_id="o", task_id="t", risk_level="HIGH", summary="s", role_id="r",
        agent_id="a", submitted_at_ms=1, timeout_ms=2,
    )
    item = OversightItem(**old)
    assert item.question == {} and item.answer == ""


# ---------------------------------------------------------------------------
# the dispatcher
# ---------------------------------------------------------------------------


class _Registry:
    def __init__(self, manifests):
        self._m = manifests

    def manifest(self, skill_id):
        return self._m.get(skill_id)


class _Core:
    def __init__(self, manifests):
        self._skill_registry = _Registry(manifests)
        self._mcp_registry = None
        self.calls: list[tuple[str, dict]] = []

    async def invoke_skill(self, skill_id, args, role):
        self.calls.append((skill_id, args))
        return {"ok": True}


def _inv(target="cleanup", **args) -> ParsedInvocation:
    return ParsedInvocation(kind="skill", target=target, args=args, raw=f"[SKILL: {target}]")


def _setup(timeout_s=5, **manifest_flags):
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=timeout_s)
    core = _Core({"cleanup": _M(**manifest_flags), "notes": _M()})
    role = RoleDefinitionConfig(allowed_skills=["cleanup", "notes"])
    return queue, core, role


async def _pending_one(queue):
    for _ in range(100):
        items = await queue.pending()
        if items:
            return items[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no oversight row was submitted")


@pytest.mark.asyncio
async def test_auto_asks_before_a_destructive_call_and_marks_it_critical(monkeypatch):
    monkeypatch.delenv("ACC_OVERSIGHT_HEADLESS", raising=False)
    queue, core, role = _setup(destructive=True)
    task = asyncio.create_task(dispatch_invocations(
        [_inv()], core, role, oversight_queue=queue, task_id="t-1", operating_mode="AUTO",
    ))
    item = await _pending_one(queue)
    assert item.summary.startswith("DESTRUCTIVE skill cleanup")
    assert item.risk_level == "HIGH"                 # a LOW manifest is not LOW here
    assert Question.from_dict(item.question).destructive
    assert core.calls == []                          # nothing ran while it waited

    await queue.approve(item.oversight_id, "tui:op", answer="1")
    [out] = await task
    assert out.ok and out.critical
    assert core.calls == [("cleanup", {})]
    assert out.question["answer"] == "1" and out.question["status"] == "APPROVED"
    assert out.question["approver_id"] == "tui:op"


@pytest.mark.asyncio
async def test_an_ordinary_call_is_neither_asked_nor_critical():
    queue, core, role = _setup()
    [out] = await dispatch_invocations(
        [_inv("notes")], core, role, oversight_queue=queue, operating_mode="AUTO",
    )
    assert out.ok and not out.critical and out.question == {}
    assert await queue.pending() == []


@pytest.mark.asyncio
async def test_with_nobody_to_ask_a_destructive_call_does_not_run():
    _queue, core, role = _setup(destructive=True)
    [out] = await dispatch_invocations([_inv()], core, role, oversight_queue=None)
    assert not out.ok and "no oversight queue to ask" in out.error
    assert core.calls == []


@pytest.mark.asyncio
async def test_don_t_run_it_is_recorded_with_the_refusal(monkeypatch):
    monkeypatch.delenv("ACC_OVERSIGHT_HEADLESS", raising=False)
    queue, core, role = _setup(destructive=True)
    task = asyncio.create_task(dispatch_invocations(
        [_inv()], core, role, oversight_queue=queue, task_id="t-1",
    ))
    item = await _pending_one(queue)
    await queue.reject(item.oversight_id, "tui:op", "not today", answer="2")
    [out] = await task
    assert not out.ok and "oversight_rejected" in out.error
    assert out.question["status"] == "REJECTED" and out.question["answer"] == "2"
    assert core.calls == []


@pytest.mark.asyncio
async def test_a_gate_that_stopped_waiting_expires_its_row(monkeypatch):
    monkeypatch.delenv("ACC_OVERSIGHT_HEADLESS", raising=False)
    queue, core, role = _setup(timeout_s=1, destructive=True)
    [out] = await dispatch_invocations([_inv()], core, role, oversight_queue=queue)
    assert not out.ok and "oversight_expired" in out.error
    oid = out.question["oversight_id"]
    assert (await queue._load(oid)).status == "EXPIRED"
    # the late answer is refused rather than recorded as an approval
    assert await queue.approve(oid, "tui:op", answer="1") is False
    assert core.calls == []


@pytest.mark.asyncio
async def test_headless_refuses_the_question_at_once(monkeypatch):
    monkeypatch.setenv("ACC_OVERSIGHT_HEADLESS", "1")
    queue, core, role = _setup(destructive=True)
    [out] = await dispatch_invocations([_inv()], core, role, oversight_queue=queue)
    assert not out.ok and out.question["status"] == "REJECTED"
    assert core.calls == []


@pytest.mark.asyncio
async def test_plan_mode_asks_nothing_and_runs_nothing():
    queue, core, role = _setup(destructive=True)
    [out] = await dispatch_invocations(
        [_inv()], core, role, oversight_queue=queue, operating_mode="PLAN",
    )
    assert out.error == "PLAN_MODE: not executed"
    assert await queue.pending() == [] and core.calls == []


@pytest.mark.asyncio
async def test_a_shell_listing_is_asked_as_before_without_a_question(monkeypatch):
    """shell_exec is still gated in AUTO (system access), but an `ls` is not a
    destructive question."""
    monkeypatch.delenv("ACC_OVERSIGHT_HEADLESS", raising=False)
    queue = HumanOversightQueue(redis_client=None, collective_id="t", timeout_s=5)
    core = _Core({"shell_exec": _M(risk_level="HIGH", system_access=True)})
    role = RoleDefinitionConfig(allowed_skills=["shell_exec"], max_skill_risk_level="HIGH")
    task = asyncio.create_task(dispatch_invocations(
        [_inv("shell_exec", cmd="ls -la")], core, role, oversight_queue=queue,
    ))
    item = await _pending_one(queue)
    assert item.summary.startswith("SYSTEM-ACCESS") and item.question == {}
    await queue.approve(item.oversight_id, "tui:op")
    [out] = await task
    assert out.ok and not out.critical


# ---------------------------------------------------------------------------
# the agent — the decision handler and the session trace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_agent_carries_the_answer_to_the_queue():
    from acc.agent import Agent
    from acc.signals import subject_oversight_decision_all

    queue = HumanOversightQueue(redis_client=None, collective_id="sol")
    oid = await queue.submit("t-1", "HIGH", "s", "r", question=_q())
    signaling = SimpleNamespace(subscribe=AsyncMock())
    agent = SimpleNamespace(
        _oversight_queue=queue,
        config=SimpleNamespace(agent=SimpleNamespace(collective_id="sol")),
        backends=SimpleNamespace(signaling=signaling),
        _stop_event=SimpleNamespace(wait=AsyncMock()),
        _maybe_dispatch_assistant_proposal=AsyncMock(),
        _discard_assistant_proposal_cache=AsyncMock(),
    )
    await Agent._subscribe_oversight_decisions(agent)
    handler = next(
        c.args[1] for c in signaling.subscribe.await_args_list
        if c.args[0] == subject_oversight_decision_all("sol")
    )

    def decision(answer):
        return json.dumps({
            "signal_type": "OVERSIGHT_DECISION", "oversight_id": oid,
            "decision": "APPROVE", "approver_id": "tui:op", "answer": answer,
        }).encode()

    await handler(decision("2"))                      # "don't run it" cannot approve
    assert (await queue._load(oid)).status == "PENDING"
    agent._maybe_dispatch_assistant_proposal.assert_not_awaited()

    await handler(decision("1"))
    row = await queue._load(oid)
    assert row.status == "APPROVED" and row.answer == "1"


def test_an_answered_question_is_journalled(tmp_path, monkeypatch):
    from acc import tracelog
    from acc.agent import Agent
    from acc.capability_dispatch import InvocationOutcome

    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_TRACELOG_ENABLED", raising=False)
    agent = SimpleNamespace(
        config=SimpleNamespace(agent=SimpleNamespace(role="coder")),
        _active_role=SimpleNamespace(
            role="coder", default_operating_mode="AUTO", category_b_overrides=None,
        ),
        _maybe_redteam=MagicMock(),
    )
    out = InvocationOutcome(
        parsed=_inv("shell_exec", cmd="rm -rf build/"), ok=True, result={}, critical=True,
        question={"oversight_id": "ov-1", "text": "shell_exec will delete ...",
                  "evidence": "rm -rf build/", "status": "APPROVED",
                  "answer": "1", "approver_id": "tui:op"},
    )
    Agent._tracelog_turn(
        agent, {"task_id": "t-q", "session_id": "s-q"},
        SimpleNamespace(output="done", blocked=False), [out], "sol",
    )
    records = tracelog.load_session("s-q")
    [call] = [r for r in records if r["kind"] == tracelog.KIND_TOOL_CALL]
    assert call["critical"] is True
    [asked] = [r for r in records if r["kind"] == tracelog.KIND_OVERSIGHT]
    assert asked["proposal_kind"] == "question"
    assert asked["answer"] == "1" and asked["approver_id"] == "tui:op"
    assert asked["evidence"] == "rm -rf build/" and asked["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# the Prompt pane — pure core
# ---------------------------------------------------------------------------


def _row(oid="ov-d", task_id="t-destroy", risk="HIGH", cmd="rm -rf build/"):
    return {
        "oversight_id": oid, "task_id": task_id, "agent_id": "coder-1",
        "risk_level": risk, "status": "PENDING",
        "summary": f'DESTRUCTIVE skill shell_exec: Run a process\n    args={{"cmd":"{cmd}"}}',
        "submitted_at_ms": int(time.time() * 1000),
        "question": destructive_confirm("skill", "shell_exec", cmd).to_dict(),
    }


def test_a_row_with_a_question_becomes_a_destructive_card():
    from acc.tui.gate_cards import is_destructive, pending_gates, request_options
    [card] = pending_gates([_row()])
    assert is_destructive(card)
    assert card.category == "DESTRUCTIVE" and card.target == "shell_exec"
    opts = request_options([card])
    assert [(o.key, o.approve, o.grant) for o in opts] == [("1", True, False), ("2", False, False)]


def test_the_panel_asks_the_agent_s_question():
    from acc.tui.acc_prompt import build_decision, render_panel
    from acc.tui.gate_cards import pending_gates, request_options
    [card] = pending_gates([_row()])
    d = build_decision([card], request_options([card]))
    assert d.asked and d.destructive
    assert "rm -rf build/" in d.question
    assert any("recorded as a critical function" in line for line in d.options[0].detail)
    assert "deletes or overwrites data" in render_panel(d, width=100)


# ---------------------------------------------------------------------------
# the Prompt pane — the real screen
# ---------------------------------------------------------------------------

from tests.test_prompt_screen_pilot import (  # noqa: E402
    _capture_oversight_actions,
    _PromptHarness,
)


def _snap(items):
    from types import SimpleNamespace
    return SimpleNamespace(
        cluster_topology={}, oversight_pending_items=items, assistant_proposals={},
    )


def _medium_row(oid="ov-m", task_id="t-other"):
    return {
        "oversight_id": oid, "task_id": task_id, "agent_id": "coder-1",
        "risk_level": "MEDIUM", "status": "PENDING",
        "summary": "SYSTEM-ACCESS skill shell_exec: Run a process",
        "submitted_at_ms": int(time.time() * 1000) - 1000,
    }


@pytest.mark.asyncio
async def test_a_destructive_question_is_asked_alone_in_the_panel(monkeypatch):
    monkeypatch.setenv("ACC_PROMPT_PANEL", "0")    # the switch does not hide it
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._refresh_gate_cards(_snap([_medium_row(), _row()]))
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")
        assert panel.display is True and app.focused is panel
        assert panel.decision.oversight_ids == ("ov-d",)
        assert panel.decision.more == 1
        assert screen.query_one("#prompt-gate-cards", Static).display is False


@pytest.mark.asyncio
async def test_run_it_takes_the_key_twice_and_carries_the_answer(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_snap([_row(risk="LOW")]))   # even at LOW
        await pilot.pause()
        panel = screen.query_one("#acc-prompt-panel")

        await pilot.press("1")
        await pilot.pause()
        assert posted == [] and "press 1 again" in panel.last_markup
        await pilot.press("1")
        await pilot.pause()
        assert [(m.action, m.oversight_id, m.answer) for m in posted] == [
            ("approve", "ov-d", "1"),
        ]


@pytest.mark.asyncio
async def test_don_t_run_it_carries_its_answer(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        await pilot.press("2")
        await pilot.pause()
        assert [(m.action, m.answer) for m in posted] == [("reject", "2")]


@pytest.mark.asyncio
async def test_a_task_grant_never_answers_a_destructive_question(monkeypatch):
    """"allow shell_exec for this task", given for an `ls`, is not consent to an `rm`."""
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        screen._task_grants.add(("t-destroy", "SKILL", "shell_exec"))
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        assert posted == []
        assert screen.query_one("#acc-prompt-panel").display is True


@pytest.mark.asyncio
async def test_yes_does_not_approve_a_destructive_question(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        screen.query_one("#prompt-textarea", TextArea).text = "yes"
        screen.action_send()
        for _ in range(4):
            await pilot.pause()
        assert posted == []
        assert not any(s.endswith(".task.assign") for s, _ in app.observer.published)
        assert "does not approve it" in screen.history[-1]["text"]


@pytest.mark.asyncio
async def test_allow_does_not_approve_it_but_disallow_refuses_it(monkeypatch):
    monkeypatch.delenv("ACC_PROMPT_PANEL", raising=False)
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        posted = _capture_oversight_actions(screen)
        screen._refresh_gate_cards(_snap([_row()]))
        await pilot.pause()
        screen._dispatch_slash("/allow")
        await pilot.pause()
        assert posted == []
        screen._dispatch_slash("/disallow")
        await pilot.pause()
        assert [(m.action, m.oversight_id) for m in posted] == [("reject", "ov-d")]


@pytest.mark.asyncio
async def test_a_critical_function_is_marked_in_the_transcript():
    app = _PromptHarness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        transcript = screen.query_one("#prompt-transcript", Static)
        painted: list[str] = []
        real = transcript.update

        def spy(content="", *args, **kwargs):
            painted.append(str(content))
            return real(content, *args, **kwargs)

        transcript.update = spy  # type: ignore[method-assign]
        base = {"role": "trace", "task_id": "t", "agent_id": "a", "ts": time.time(),
                "kind": "skill", "ok": True, "error": ""}
        screen._append_history({**base, "target": "shell_exec", "critical": True})
        screen._append_history({**base, "target": "echo"})
        await pilot.pause()
        marked = [ln for ln in painted[-1].splitlines() if "critical" in ln]
        assert len(marked) == 1 and "shell_exec" in marked[0]
