"""`20260902-assistant-autonomy-prompt-pane-approvals` 1.5 -- outcomes in the thread.

What became of a proposal, and the reply that follows an infuse, both land
in the Prompt pane's thread instead of a container log / "already received".
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.tui.outcomes import WORKER_POOL_HINT, outcome_key, outcome_lines
from tests.test_prompt_screen_pilot import _PromptHarness, _StubObserver


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------


def test_outcome_lines_by_trigger():
    assert outcome_lines({"trigger": "infuse_completed", "name": "@acc/x", "version": "1.0"}) == [
        "✓ installed @acc/x@1.0",
    ]
    assert outcome_lines({
        "trigger": "infuse_completed", "name": "@acc/x", "version": "1.0",
        "was_already_installed": True,
    }) == ["✓ installed @acc/x@1.0 (already installed)"]
    assert outcome_lines({
        "trigger": "proposal_dispatch_failed", "spec": "@acc/y@^1", "reason": "unsigned",
    }) == ["✗ @acc/y@^1: unsigned"]
    lines = outcome_lines({
        "trigger": "reconcile_result", "role": "product_security_advisor",
        "assigned": [{"role": "reviewer", "target_agent_id": "worker-00"}],
        "unmet": ["product_security_advisor"],
    })
    assert lines[0] == "✓ spawned reviewer → worker-00"
    assert lines[1] == f"✗ spawn product_security_advisor: {WORKER_POOL_HINT}"
    assert outcome_lines({"trigger": "reconcile_result", "role": "r", "already_active": 2}) == [
        "· r: already active (2 running)",
    ]
    assert outcome_lines({"trigger": "something_else"}) == []


def test_outcome_key_dedupes_on_trigger_proposal_ts():
    a = {"trigger": "infuse_completed", "proposal_id": "p", "ts": 1.0}
    assert outcome_key(a) == outcome_key(dict(a))
    assert outcome_key(a) != outcome_key({**a, "ts": 2.0})


# ---------------------------------------------------------------------------
# observer: outcome route + follow-up listeners
# ---------------------------------------------------------------------------


def _observer():
    from acc.tui.client import NATSObserver  # noqa: PLC0415
    return NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())


def test_observer_keeps_outcomes_capped():
    obs = _observer()
    for i in range(60):
        obs._route_assistant_outcome("assistant-1", {"trigger": "infuse_completed", "ts": i})
    assert len(obs._snapshot.assistant_outcomes) == 50
    assert obs._snapshot.assistant_outcomes[-1]["ts"] == 59


def test_followup_listener_gets_the_second_reply_only_until_released():
    obs = _observer()
    got: list[dict] = []
    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        obs.register_task_listener("t-1", fut)
        obs.register_task_followup_listener("t-1", got.append)

        obs._route_task_complete("assistant-1", {"task_id": "t-1", "output": "first"})
        assert fut.done() and fut.result()["output"] == "first"
        assert got == []                                   # the Future took it

        obs._route_task_complete("assistant-1", {"task_id": "t-1", "output": "continuation"})
        assert [d["output"] for d in got] == ["continuation"]

        obs.unregister_task_followup_listener("t-1")
        obs._route_task_complete("assistant-1", {"task_id": "t-1", "output": "stale"})
        assert [d["output"] for d in got] == ["continuation"]   # released: ignored
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# publishers
# ---------------------------------------------------------------------------


def test_slash_done_parses():
    from acc.slash_commands import KIND_DONE, parse  # noqa: PLC0415
    assert parse("/done").kind == KIND_DONE


def test_arbiter_publishes_reconcile_result_for_a_named_role_with_no_worker():
    from tests.test_worker_reconcile import _arbiter  # noqa: PLC0415

    arb = _arbiter(None, roster=[])
    arb._absorb_reconcile_trigger({"trigger": "assistant_proposal", "proposal_id": "p-1",
                                   "role": "product_security_advisor", "cluster_id": ""})
    asyncio.run(arb._run_worker_reconcile(trigger={"proposal_id": "p-1", "role": "product_security_advisor"}))
    payloads = [c.args[1] for c in arb.backends.signaling.publish.await_args_list]
    (notice,) = [p for p in payloads if p.get("trigger") == "reconcile_result"]
    assert notice["signal_type"] == "ASSISTANT_PROPOSAL_OUTCOME"
    assert notice["unmet"] == ["product_security_advisor"] and notice["assigned"] == []
    assert notice["proposal_id"] == "p-1"


def test_bare_nudge_with_nothing_to_do_publishes_no_result():
    from tests.test_worker_reconcile import _arbiter, _dormant  # noqa: PLC0415

    arb = _arbiter(None, roster=_dormant(1))
    asyncio.run(arb._run_worker_reconcile(trigger={}))
    assert arb.backends.signaling.publish.await_args_list == []


def test_infuse_completed_notice_is_stamped(monkeypatch):
    import acc.pkg.install_infuse as install_mod  # noqa: PLC0415
    from acc.assistant_proposal import PROPOSAL_INFUSE, AssistantProposal, _dispatch_infuse  # noqa: PLC0415
    from acc.pkg.install_infuse import InfuseInstallResult  # noqa: PLC0415

    monkeypatch.setattr(install_mod, "execute_infuse_install", lambda spec, **kw: InfuseInstallResult(
        ok=True, name="@acc/x", version="1.0", installed_ref="@acc/x@1.0",
        install_path="/p", already_satisfied=True,
    ))
    sig = MagicMock(publish=AsyncMock())
    p = AssistantProposal(kind=PROPOSAL_INFUSE, params={"name": "@acc/x", "constraint": "^1"},
                          summary="s", collective_id="sol-01", task_id="t-1")
    assert asyncio.run(_dispatch_infuse(sig, "sol-01", p)) is True
    (notice,) = [c.args[1] for c in sig.publish.await_args_list]
    assert notice["signal_type"] == "ASSISTANT_PROPOSAL_OUTCOME"
    assert notice["trigger"] == "infuse_completed" and notice["task_id"] == "t-1"


# ---------------------------------------------------------------------------
# pane
# ---------------------------------------------------------------------------


class _ThreadObserver(_StubObserver):
    def __init__(self) -> None:
        super().__init__()
        self.followups: dict[str, list] = {}

    def register_task_followup_listener(self, task_id, callback) -> None:
        self.followups.setdefault(task_id, []).append(callback)

    def unregister_task_followup_listener(self, task_id) -> None:
        self.followups.pop(task_id, None)


class _Harness(_PromptHarness):
    def __init__(self) -> None:
        super().__init__()
        self.observer = _ThreadObserver()
        self._observers = [self.observer]


async def _send(app, pilot, text: str) -> str:
    from textual.widgets import TextArea  # noqa: PLC0415
    app.screen.query_one("#prompt-textarea", TextArea).text = text
    app.screen.action_send()
    for _ in range(4):
        await pilot.pause()
    return app.observer.published[-1][1]["task_id"]


@pytest.mark.asyncio
async def test_continuation_lands_under_the_exchange_until_done():
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        task_id = await _send(app, pilot, "engage the RH SRE roles")
        app.observer.deliver(task_id, {"task_id": task_id, "agent_id": "assistant-1",
                                       "output": "[PROPOSE_INFUSE:@acc/redhat-sre-roles:need SRE]"})
        for _ in range(4):
            await pilot.pause()
        assert screen._thread_task_id == task_id
        assert task_id in app.observer.followups

        (cb,) = app.observer.followups[task_id]
        cb({"task_id": task_id, "agent_id": "assistant-1", "output": "installed; spawning"})
        await pilot.pause()
        agent_entries = [e for e in screen.history if e.get("role") == "agent"]
        assert agent_entries[-1]["text"] == "↩ installed; spawning"
        assert agent_entries[-1]["task_id"] == task_id

        screen._dispatch_slash("/done")
        await pilot.pause()
        assert screen._thread_task_id == "" and task_id not in app.observer.followups
        assert any("thread released" in e.get("text", "") for e in screen.history)


@pytest.mark.asyncio
async def test_new_send_releases_the_previous_thread():
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        first = await _send(app, pilot, "one")
        app.observer.deliver(first, {"task_id": first, "agent_id": "a", "output": "r1"})
        for _ in range(4):
            await pilot.pause()
        assert first in app.observer.followups
        second = await _send(app, pilot, "two")
        assert first not in app.observer.followups
        assert second != first


@pytest.mark.asyncio
async def test_outcomes_render_once_as_system_lines():
    app = _Harness()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        snap = SimpleNamespace(
            cluster_topology={}, oversight_pending_items=[], assistant_proposals={},
            assistant_outcomes=[
                {"trigger": "infuse_completed", "proposal_id": "p-1", "ts": 1.0,
                 "name": "@acc/redhat-sre-roles", "version": "0.1.0", "task_id": "t-1"},
                {"trigger": "reconcile_result", "proposal_id": "p-2", "ts": 2.0,
                 "role": "product_security_advisor", "assigned": [],
                 "unmet": ["product_security_advisor"]},
            ],
        )
        screen.watch_snapshot(snap)
        await pilot.pause()
        texts = [e["text"] for e in screen.history if e.get("role") == "system"]
        assert "✓ installed @acc/redhat-sre-roles@0.1.0" in texts
        assert any(t.startswith("✗ spawn product_security_advisor:") and "apply worker-pool" in t
                   for t in texts)
        n = len(screen.history)
        screen.watch_snapshot(snap)                       # next tick: nothing new
        await pilot.pause()
        assert len(screen.history) == n
