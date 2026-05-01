"""Pilot tests for the PR-B prompt pane.

We mount :class:`PromptScreen` inside a small Textual harness whose
``_observers`` / ``_active_collective_idx`` attributes mimic the real
App's surface, then drive Send and synthesise a TASK_COMPLETE
delivery to verify the chat-history rendering.

These tests deliberately avoid a real NATS connection — the harness'
observer captures every publish call and exposes a manual
``deliver()`` hook the test uses to fan out a synthetic reply.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from textual.app import App
from textual.widgets import Button, Input, Select, Static, TextArea

from acc.tui.screens.prompt import PromptScreen


class _StubObserver:
    """Mimics NATSObserver's PR-B surface end-to-end."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._listeners: dict[str, asyncio.Future] = {}
        # PR-progress: per-task_id callback registry.
        self._progress_listeners: dict[str, list] = {}

    async def publish(self, subject: str, payload: dict) -> None:
        self.published.append((subject, payload))

    def register_task_listener(self, task_id, future) -> None:
        self._listeners[task_id] = future

    def unregister_task_listener(self, task_id) -> None:
        self._listeners.pop(task_id, None)

    def register_task_progress_listener(self, task_id, callback) -> None:
        self._progress_listeners.setdefault(task_id, []).append(callback)

    def unregister_task_progress_listener(self, task_id) -> None:
        self._progress_listeners.pop(task_id, None)

    def deliver(self, task_id: str, data: dict) -> None:
        future = self._listeners.pop(task_id, None)
        if future is not None and not future.done():
            future.set_result(data)
        # Mirror the real observer's auto-clean of progress listeners.
        self._progress_listeners.pop(task_id, None)


class _PromptHarness(App):
    """Minimal app — hosts PromptScreen with a stub observer."""

    def __init__(self) -> None:
        super().__init__()
        self.observer = _StubObserver()
        self._observers = [self.observer]
        self._active_collective_idx = 0
        self._collective_ids = ["sol-test"]

    def on_mount(self) -> None:
        self.push_screen(PromptScreen())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_publishes_task_assign_with_form_values():
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PromptScreen)

        # Fill the form
        screen.query_one("#prompt-textarea", TextArea).text = (
            "Generate a unit test for FizzBuzz"
        )
        screen.query_one("#input-target-agent-id", Input).value = (
            "coding_agent-aaa"
        )
        # Trigger send via action (avoids button-click harness flakiness)
        screen.action_send()
        # Allow the worker to dispatch the publish call.  Multiple
        # ``pilot.pause()`` lets the asyncio task scheduler interleave.
        for _ in range(4):
            await pilot.pause()

        assert len(app.observer.published) == 1
        subject, payload = app.observer.published[0]
        assert subject == "acc.sol-test.task"
        assert payload["signal_type"] == "TASK_ASSIGN"
        assert payload["target_role"] == "coding_agent"
        assert payload["target_agent_id"] == "coding_agent-aaa"
        assert payload["content"] == "Generate a unit test for FizzBuzz"


@pytest.mark.asyncio
async def test_send_with_empty_prompt_notifies_and_skips_publish():
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        notifications: list[tuple[str, str]] = []
        orig = screen.notify

        def capture(message, *, severity="information", timeout=4.0, **kw):
            notifications.append((message, severity))
            return orig(message, severity=severity, timeout=timeout, **kw)

        screen.notify = capture  # type: ignore[assignment]

        # Empty textarea
        screen.query_one("#prompt-textarea", TextArea).text = "   "
        screen.action_send()
        await pilot.pause()

        assert app.observer.published == []
        assert any(
            "type a prompt" in m.lower() for m, _ in notifications
        ), notifications


@pytest.mark.asyncio
async def test_synthetic_task_complete_appended_to_history():
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = "ping"
        screen.action_send()

        # Wait until the publish call records (worker has registered
        # the listener at this point).
        for _ in range(8):
            await pilot.pause()
            if app.observer.published:
                break
        assert app.observer.published, "send worker never published"

        task_id = app.observer.published[0][1]["task_id"]

        # Deliver a synthetic TASK_COMPLETE matching the published id.
        app.observer.deliver(task_id, {
            "signal_type": "TASK_COMPLETE",
            "task_id": task_id,
            "agent_id": "coding_agent-test",
            "output": "pong",
            "blocked": False,
            "latency_ms": 12.3,
            "episode_id": "ep-test",
        })

        # Worker resumes, builds the agent reply entry.
        for _ in range(8):
            await pilot.pause()
            if any(e.get("role") == "agent" for e in screen.history):
                break

        roles = [e.get("role") for e in screen.history]
        assert "operator" in roles
        assert "agent" in roles, screen.history

        agent_entry = next(e for e in screen.history if e["role"] == "agent")
        assert agent_entry["text"] == "pong"
        assert agent_entry["agent_id"] == "coding_agent-test"
        assert agent_entry["task_id"] == task_id


@pytest.mark.asyncio
async def test_history_render_shows_operator_and_agent_blocks():
    """Synthesise two history entries and confirm the Static render
    contains both — exercises ``_render_history`` directly, no NATS."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.history = [
            {
                "role": "operator",
                "task_id": "task-aaaa",
                "text": "hello",
                "ts": time.time(),
                "blocked": False,
                "target_role": "coding_agent",
                "target_agent_id": "",
            },
            {
                "role": "agent",
                "task_id": "task-aaaa",
                "agent_id": "coding_agent-x",
                "text": "world",
                "ts": time.time(),
                "blocked": False,
                "latency_ms": 99.0,
            },
        ]
        screen._render_transcript()
        await pilot.pause()

        # We can't read Static.renderable across Textual versions; tap
        # the same monkeypatch trick used in PR-A's ecosystem tests:
        # replace update with a recorder on a fresh render call.
        history_widget = screen.query_one("#prompt-transcript", Static)
        captured: list[str] = []
        original = history_widget.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return original(content, **kwargs)

        history_widget.update = recording  # type: ignore[assignment]
        screen._render_transcript()
        await pilot.pause()

        rendered = "\n".join(captured)
        assert "operator → coding_agent" in rendered
        assert "hello" in rendered
        assert "coding_agent-x" in rendered
        assert "world" in rendered


@pytest.mark.asyncio
async def test_invocations_render_as_trace_lines_in_transcript():
    """A TASK_COMPLETE carrying ``invocations`` produces one trace
    line per entry between the operator prompt and the agent reply.

    The trace lines are what the operator sees as "agent thinking /
    actions" — green ✓ for OK, red ✗ for failed.
    """
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = "do work"
        screen.action_send()
        for _ in range(8):
            await pilot.pause()
            if app.observer.published:
                break
        task_id = app.observer.published[0][1]["task_id"]

        # Synthetic TASK_COMPLETE with two invocations: one ok, one failed.
        app.observer.deliver(task_id, {
            "signal_type": "TASK_COMPLETE",
            "task_id": task_id,
            "agent_id": "coding_agent-x",
            "output": "done",
            "blocked": False,
            "latency_ms": 5.0,
            "episode_id": "ep",
            "invocations": [
                {"kind": "skill", "target": "echo", "ok": True, "error": ""},
                {"kind": "mcp", "target": "fs.read", "ok": False,
                 "error": "A-018 blocked"},
            ],
        })

        for _ in range(8):
            await pilot.pause()
            if any(e.get("role") == "agent" for e in screen.history):
                break

        roles = [e.get("role") for e in screen.history]
        # Order must be: operator → trace × 2 → agent.
        assert roles == ["operator", "trace", "trace", "agent"], roles

        traces = [e for e in screen.history if e["role"] == "trace"]
        assert traces[0]["kind"] == "skill"
        assert traces[0]["target"] == "echo"
        assert traces[0]["ok"] is True
        assert traces[1]["kind"] == "mcp"
        assert traces[1]["target"] == "fs.read"
        assert traces[1]["ok"] is False
        assert "A-018" in traces[1]["error"]


@pytest.mark.asyncio
async def test_clear_history_empties_the_pane():
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.history = [
            {
                "role": "operator",
                "task_id": "x",
                "text": "stale",
                "ts": time.time(),
                "blocked": False,
            },
        ]
        screen._render_transcript()
        await pilot.pause()

        screen.action_clear_transcript()
        await pilot.pause()

        assert screen.history == []
