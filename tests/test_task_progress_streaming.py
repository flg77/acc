"""Tests for the TASK_PROGRESS streaming pipe (observer → channel → screen).

Three layers covered:

1. ``NATSObserver`` per-task_id progress callback registry +
   ``_route_task_progress`` fan-out.
2. ``TUIPromptChannel.send`` honours the optional ``on_progress`` kwarg
   and registers / cleans up the callback.
3. ``PromptScreen`` renders ``progress`` entries between operator and
   agent blocks, including confidence trend arrows.

Tier 3 (live agent emitting TASK_PROGRESS during ``process_task``) is
out of scope for this PR — the agent-side emitter is a separate
follow-up.  These tests synthesise events directly via
``observer._route_task_progress`` so the full receive pipe is exercised
end-to-end without requiring agent emission.
"""

from __future__ import annotations

import asyncio

import pytest
from textual.app import App
from textual.widgets import Static, TextArea

from acc.channels import TUIPromptChannel
from acc.tui.client import NATSObserver
from acc.tui.screens.prompt import PromptScreen


# ---------------------------------------------------------------------------
# Tier 1 — observer registry + fan-out
# ---------------------------------------------------------------------------


def _make_observer() -> NATSObserver:
    return NATSObserver(
        nats_url="nats://test.invalid:4222",
        collective_id="sol-test",
        update_queue=asyncio.Queue(),
    )


def _progress_payload(task_id: str, *, step: int = 1, total: int = 3,
                     label: str = "", conf: float = 0.0,
                     trend: str = "STABLE") -> dict:
    """Build a TASK_PROGRESS payload mirroring the agent's emission shape."""
    return {
        "signal_type": "TASK_PROGRESS",
        "task_id": task_id,
        "agent_id": "coding_agent-x",
        "progress": {
            "current_step": step,
            "total_steps_estimated": total,
            "step_label": label,
            "confidence": conf,
            "confidence_trend": trend,
        },
    }


def test_progress_callback_fires_on_matching_task_id():
    obs = _make_observer()
    captured: list[dict] = []
    obs.register_task_progress_listener("task-abc", captured.append)

    obs._route_task_progress(
        "coding_agent-x", _progress_payload("task-abc", step=2, total=4),
    )

    assert len(captured) == 1
    assert captured[0]["task_id"] == "task-abc"
    assert captured[0]["progress"]["current_step"] == 2


def test_progress_callback_ignored_for_other_task_ids():
    obs = _make_observer()
    captured: list[dict] = []
    obs.register_task_progress_listener("task-abc", captured.append)

    obs._route_task_progress(
        "coding_agent-x", _progress_payload("task-xyz-stranger"),
    )

    assert captured == []


def test_progress_callback_persists_across_multiple_events():
    """Unlike TASK_COMPLETE listeners, progress callbacks stay registered."""
    obs = _make_observer()
    captured: list[dict] = []
    obs.register_task_progress_listener("task-abc", captured.append)

    for step in (1, 2, 3):
        obs._route_task_progress(
            "coding_agent-x",
            _progress_payload("task-abc", step=step, total=3),
        )

    assert [c["progress"]["current_step"] for c in captured] == [1, 2, 3]


def test_multiple_callbacks_for_same_task_id_all_fire():
    """Multiple subscribers can register; each gets every event."""
    obs = _make_observer()
    a: list[dict] = []
    b: list[dict] = []
    obs.register_task_progress_listener("task-abc", a.append)
    obs.register_task_progress_listener("task-abc", b.append)

    obs._route_task_progress("coding_agent-x", _progress_payload("task-abc"))

    assert len(a) == 1 and len(b) == 1


def test_unregister_progress_listener_drops_all_callbacks():
    obs = _make_observer()
    captured: list[dict] = []
    obs.register_task_progress_listener("task-abc", captured.append)
    obs.unregister_task_progress_listener("task-abc")

    obs._route_task_progress("coding_agent-x", _progress_payload("task-abc"))
    assert captured == []
    # Re-unregistering an unknown id is safe.
    obs.unregister_task_progress_listener("task-abc")
    obs.unregister_task_progress_listener("never-registered")


def test_task_complete_auto_cleans_progress_listeners():
    """TASK_COMPLETE marks end-of-task — progress listeners auto-drop."""
    obs = _make_observer()
    captured: list[dict] = []
    obs.register_task_progress_listener("task-abc", captured.append)

    obs._route_task_complete(
        "coding_agent-x",
        {"signal_type": "TASK_COMPLETE", "task_id": "task-abc"},
    )

    # Subsequent progress events for the same task_id are now ignored.
    obs._route_task_progress("coding_agent-x", _progress_payload("task-abc"))
    assert captured == []


def test_progress_callback_exception_does_not_break_observer():
    """A misbehaving callback must not stop other callbacks or future events."""
    obs = _make_observer()
    survivor: list[dict] = []

    def bad(payload):
        raise RuntimeError("simulated callback failure")

    obs.register_task_progress_listener("task-abc", bad)
    obs.register_task_progress_listener("task-abc", survivor.append)

    obs._route_task_progress("coding_agent-x", _progress_payload("task-abc"))
    # The good callback still received the event.
    assert len(survivor) == 1


# ---------------------------------------------------------------------------
# Tier 2 — TUIPromptChannel honours on_progress
# ---------------------------------------------------------------------------


class _StubObserver:
    """Minimal NATSObserver-shaped stand-in for the channel tests."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._listeners: dict[str, asyncio.Future] = {}
        self._progress_listeners: dict[str, list] = {}

    async def publish(self, subject, payload):
        self.published.append((subject, payload))

    def register_task_listener(self, task_id, future):
        self._listeners[task_id] = future

    def unregister_task_listener(self, task_id):
        self._listeners.pop(task_id, None)

    def register_task_progress_listener(self, task_id, callback):
        self._progress_listeners.setdefault(task_id, []).append(callback)

    def unregister_task_progress_listener(self, task_id):
        self._progress_listeners.pop(task_id, None)

    def deliver_progress(self, task_id: str, payload: dict) -> None:
        for cb in list(self._progress_listeners.get(task_id, [])):
            cb(payload)

    def deliver_complete(self, task_id: str, data: dict) -> None:
        future = self._listeners.pop(task_id, None)
        if future is not None and not future.done():
            future.set_result(data)
        # Auto-clean progress listeners — mirrors the real observer.
        self._progress_listeners.pop(task_id, None)


@pytest.mark.asyncio
async def test_channel_supports_streaming_returns_true():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")
    assert channel.supports_streaming() is True


@pytest.mark.asyncio
async def test_channel_send_registers_progress_listener_when_callback_passed():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")
    captured: list[dict] = []

    task_id = await channel.send(
        prompt="x",
        target_role="coding_agent",
        on_progress=captured.append,
    )

    assert task_id in obs._progress_listeners
    obs.deliver_progress(task_id, {"task_id": task_id, "progress": {"current_step": 1}})
    assert len(captured) == 1


@pytest.mark.asyncio
async def test_channel_send_omits_progress_listener_when_no_callback():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")

    task_id = await channel.send(prompt="x", target_role="coding_agent")
    assert task_id not in obs._progress_listeners


@pytest.mark.asyncio
async def test_channel_close_cleans_progress_listeners():
    obs = _StubObserver()
    channel = TUIPromptChannel(obs, collective_id="sol-test")
    captured: list[dict] = []

    task_id = await channel.send(
        prompt="x", target_role="coding_agent", on_progress=captured.append,
    )
    assert task_id in obs._progress_listeners

    await channel.close()
    assert task_id not in obs._progress_listeners


# ---------------------------------------------------------------------------
# Tier 3 — PromptScreen renders progress entries
# ---------------------------------------------------------------------------


class _PromptHarness(App):
    def __init__(self) -> None:
        super().__init__()
        self.observer = _StubObserver()
        self._observers = [self.observer]
        self._active_collective_idx = 0
        self._collective_ids = ["sol-test"]

    def on_mount(self) -> None:
        self.push_screen(PromptScreen())


@pytest.mark.asyncio
async def test_screen_renders_progress_between_operator_and_agent():
    """Send a prompt, fire 2 progress events, then a TASK_COMPLETE.

    Asserts the history sequence ends up [operator, progress, progress, agent]
    in that order — the live "agent thinking" surface the user requested.
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

        # Fire two progress events.
        app.observer.deliver_progress(task_id, {
            "signal_type": "TASK_PROGRESS",
            "task_id": task_id,
            "agent_id": "coding_agent-x",
            "progress": {
                "current_step": 1, "total_steps_estimated": 3,
                "step_label": "Reading specs", "confidence": 0.5,
                "confidence_trend": "STABLE",
            },
        })
        app.observer.deliver_progress(task_id, {
            "signal_type": "TASK_PROGRESS",
            "task_id": task_id,
            "agent_id": "coding_agent-x",
            "progress": {
                "current_step": 2, "total_steps_estimated": 3,
                "step_label": "Drafting tests", "confidence": 0.7,
                "confidence_trend": "RISING",
            },
        })
        await pilot.pause()

        # Then the final reply.
        app.observer.deliver_complete(task_id, {
            "signal_type": "TASK_COMPLETE",
            "task_id": task_id,
            "agent_id": "coding_agent-x",
            "output": "done",
            "blocked": False,
            "latency_ms": 200.0,
        })
        for _ in range(8):
            await pilot.pause()
            if any(e.get("role") == "agent" for e in screen.history):
                break

        roles = [e.get("role") for e in screen.history]
        assert roles == ["operator", "progress", "progress", "agent"], roles

        progress_entries = [e for e in screen.history if e["role"] == "progress"]
        assert progress_entries[0]["step_label"] == "Reading specs"
        assert progress_entries[0]["current_step"] == 1
        assert progress_entries[0]["total_steps"] == 3
        assert progress_entries[1]["step_label"] == "Drafting tests"
        assert progress_entries[1]["confidence_trend"] == "RISING"


@pytest.mark.asyncio
async def test_progress_renders_with_trend_arrow_in_transcript():
    """Render a progress-only history and capture the rendered markup
    to confirm the trend arrow + step counter + label appear."""
    import time
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.history = [
            {
                "role": "progress",
                "task_id": "t1",
                "agent_id": "coding_agent-x",
                "current_step": 2,
                "total_steps": 5,
                "step_label": "Generating fixtures",
                "confidence": 0.82,
                "confidence_trend": "RISING",
                "ts": time.time(),
            },
            {
                "role": "progress",
                "task_id": "t1",
                "agent_id": "coding_agent-x",
                "current_step": 3,
                "total_steps": 5,
                "step_label": "Refining",
                "confidence": 0.65,
                "confidence_trend": "FALLING",
                "ts": time.time(),
            },
        ]

        widget = screen.query_one("#prompt-transcript", Static)
        captured: list[str] = []
        original = widget.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return original(content, **kwargs)

        widget.update = recording  # type: ignore[assignment]
        screen._render_transcript()
        await pilot.pause()

        rendered = "\n".join(captured)
        assert "step 2/5" in rendered
        assert "Generating fixtures" in rendered
        assert "↑" in rendered  # RISING arrow
        assert "step 3/5" in rendered
        assert "Refining" in rendered
        assert "↓" in rendered  # FALLING arrow


@pytest.mark.asyncio
async def test_progress_with_no_step_label_still_renders_step_counter():
    """Empty step_label is fine — the counter alone is enough signal."""
    import time
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.history = [
            {
                "role": "progress",
                "task_id": "t1",
                "agent_id": "x",
                "current_step": 1,
                "total_steps": 0,  # unknown total
                "step_label": "",
                "confidence": 0.0,
                "confidence_trend": "",
                "ts": time.time(),
            },
        ]

        widget = screen.query_one("#prompt-transcript", Static)
        captured: list[str] = []
        original = widget.update
        def recording(content="", **kwargs):
            captured.append(str(content))
            return original(content, **kwargs)
        widget.update = recording  # type: ignore[assignment]
        screen._render_transcript()
        await pilot.pause()

        rendered = "\n".join(captured)
        # Without total, just "step 1" with no slash.
        assert "step 1" in rendered
        # No confidence string when conf=0.
        assert "→" not in rendered or "0%" not in rendered
