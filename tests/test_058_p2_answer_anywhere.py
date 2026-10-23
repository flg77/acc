"""058 Phase 2 -- answer from anywhere, and say where the work is heading.

* The WebGUI can answer a question's option (the mission card) and close,
  revoke, purge or restore a mission.
* ``acc-cli ask`` answers a task's questions from a plain command line, as the
  same OVERSIGHT_DECISION the TUI publishes.
* Every completed task carries a heading: done / refused / waiting on you /
  mission / next.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from acc.heading import heading, next_step, render  # noqa: E402
from tests.test_webgui import _FakeObserver  # noqa: E402


# ---------------------------------------------------------------------------
# Heading
# ---------------------------------------------------------------------------


def _o(target, ok=True, error=""):
    return SimpleNamespace(parsed=SimpleNamespace(kind="skill", target=target), ok=ok, error=error)


def test_the_agents_own_next_step_is_found():
    assert next_step("All tests pass.\nNext: add a test for zero.") == "add a test for zero."
    assert next_step("- **Next steps:** wire the CLI") == "wire the CLI"
    assert next_step("nothing to add") == ""


def test_heading_sorts_what_happened():
    h = heading([
        _o("fs_write"),
        _o("shell_exec", ok=False, error="oversight_expired: id=ov-1 timed out before approval"),
        _o("nuke", ok=False, error="refused: above the requester's ceiling LOW"),
    ], "Next: run it again", mission_id="m-1")
    assert h["done"] == ["skill:fs_write"]
    assert h["waiting_on_you"] == ["skill:shell_exec (asked; nobody answered)"]
    assert h["refused"][0].startswith("skill:nuke: refused")
    assert h["mission"] == "m-1" and h["next"] == "run it again"
    lines = render(h)
    assert lines[0] == "done: skill:fs_write" and lines[-1] == "next: run it again"


def test_render_says_when_nothing_is_next():
    assert render(heading([], "done."))[-1].startswith("next: nothing named")
    assert render(None) == []


def test_the_reply_carries_the_heading():
    from acc.channels.tui import _payload_to_response

    r = _payload_to_response("t", {"output": "x", "heading": {"done": [], "next": "n"}})
    assert r.heading == {"done": [], "next": "n"}


# ---------------------------------------------------------------------------
# WebGUI
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    import acc.tui.client as tui_client
    monkeypatch.setattr(tui_client, "NATSObserver", _FakeObserver)
    monkeypatch.setenv("ACC_COLLECTIVE_IDS", "sol-01")
    from acc.webgui.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def _published(client):
    return client.app.state.hub.observer("sol-01").published


def test_the_webgui_answers_the_mission_card(client):
    r = client.post("/api/oversight", json={
        "collective_id": "sol-01", "oversight_id": "ov-1", "decision": "APPROVE", "answer": "1",
    })
    assert r.status_code == 200
    subject, payload = _published(client)[-1]
    assert subject == "acc.sol-01.oversight.ov-1"
    assert payload["answer"] == "1" and payload["decision"] == "APPROVE"


def test_a_plain_decision_carries_no_answer(client):
    client.post("/api/oversight", json={
        "collective_id": "sol-01", "oversight_id": "ov-2", "decision": "REJECT",
    })
    assert "answer" not in _published(client)[-1][1]


@pytest.mark.parametrize("action", ["close", "revoke", "purge", "restore"])
def test_the_webgui_publishes_mission_control(client, action):
    r = client.post("/api/mission", json={
        "collective_id": "sol-01", "mission_id": "m-1", "action": action,
    })
    assert r.status_code == 200
    subject, payload = _published(client)[-1]
    assert subject == "acc.sol-01.oversight.mission-control"
    assert payload["signal_type"] == "MISSION_CONTROL" and payload["action"] == action
    assert payload["by"].startswith("webgui:")


def test_an_unknown_mission_action_is_refused(client):
    r = client.post("/api/mission", json={
        "collective_id": "sol-01", "mission_id": "m-1", "action": "approve-everything",
    })
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# acc-cli ask
# ---------------------------------------------------------------------------


def test_ask_renders_the_mission_card():
    from acc.cli.ask_cmd import _render_question
    from acc.mission import mission_question

    q = mission_question("coding_agent", "/workspace/p", kind="skill", target="shell_exec").to_dict()
    text, options = _render_question({"risk_level": "HIGH", "question": q})
    assert "Ask once" in text and "1) allow the mission" in text
    # 060 added "4 always allow" (a standing grant instead of a mission).
    assert [o["key"] for o in options] == ["1", "2", "3", "4"]


@pytest.mark.asyncio
async def test_ask_answers_the_tasks_question_from_the_terminal(monkeypatch):
    from acc.cli import ask_cmd
    from acc.mission import mission_question

    q = mission_question("coding_agent", "/w", kind="skill", target="shell_exec").to_dict()
    published = []

    class _Obs:
        collective_id = "sol-01"
        _snapshot = SimpleNamespace(oversight_pending_items=[
            {"oversight_id": "ov-9", "task_id": "t-1", "risk_level": "HIGH", "question": q},
            {"oversight_id": "ov-x", "task_id": "someone-else", "risk_level": "HIGH"},
        ])

        async def publish(self, subject, payload):
            published.append((subject, payload))

    answers = iter(["9", "1"])          # a wrong key first, then "allow the mission"
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    seen: set[str] = set()
    task = asyncio.create_task(ask_cmd._answer_loop(_Obs(), "t-1", enabled=True, seen=seen))
    for _ in range(40):
        await asyncio.sleep(0.05)
        if published:
            break
    task.cancel()

    assert len(published) == 1, "only this task's question is answered"
    subject, payload = published[0]
    assert subject == "acc.sol-01.oversight.ov-9"
    assert payload["decision"] == "APPROVE" and payload["answer"] == "1"
    assert payload["approver_id"].startswith("cli:")


@pytest.mark.asyncio
async def test_ask_no_answer_leaves_questions_waiting(monkeypatch):
    from acc.cli import ask_cmd

    published = []

    class _Obs:
        collective_id = "sol-01"
        _snapshot = SimpleNamespace(oversight_pending_items=[
            {"oversight_id": "ov-9", "task_id": "t-1", "risk_level": "HIGH"}])

        async def publish(self, subject, payload):
            published.append(payload)

    seen: set[str] = set()
    task = asyncio.create_task(ask_cmd._answer_loop(_Obs(), "t-1", enabled=False, seen=seen))
    await asyncio.sleep(0.8)
    task.cancel()
    assert published == [] and seen == {"ov-9"}


def test_ask_is_registered():
    import argparse

    from acc.cli import ask_cmd

    parser = argparse.ArgumentParser()
    ask_cmd.register(parser.add_subparsers())
    args = parser.parse_args(["ask", "fix", "it", "--role", "coding_agent"])
    assert args.prompt == ["fix", "it"] and args.mode == "ASK_ONCE"
