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
        assert subject == "acc.sol-test.task.assign"
        assert payload["signal_type"] == "TASK_ASSIGN"
        assert payload["target_role"] == "coding_agent"
        assert payload["target_agent_id"] == "coding_agent-aaa"
        assert payload["content"] == "Generate a unit test for FizzBuzz"


@pytest.mark.asyncio
async def test_select_directory_button_present():
    """PR-U2b/PR-V — the Prompt screen exposes a compact '+' workspace
    button (relabelled from 'Select Directory' to save space)."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        btn = screen.query_one("#btn-select-workspace", Button)
        assert str(btn.label) == "+"
        # Its purpose is discoverable via the tooltip.
        assert "working directory" in (btn.tooltip or "").lower()


@pytest.mark.asyncio
async def test_mode_hint_and_shift_tab_cycles():
    """PR-V2 — the Mode dropdown is gone; a tiny hint shows the mode and
    shift+tab (action_cycle_mode) cycles AUTO→PLAN→ACCEPT_EDITS→ASK."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # No dropdown; the tiny hint widget exists instead.
        assert not screen.query("#select-operating-mode")
        screen.query_one("#prompt-mode-hint", Static)
        assert screen._operating_mode == "AUTO"
        # Cycle.
        screen.action_cycle_mode()
        await pilot.pause()
        assert screen._operating_mode == "PLAN"
        for _ in range(3):
            screen.action_cycle_mode()
        assert screen._operating_mode == "AUTO"  # wrapped around


@pytest.mark.asyncio
async def test_enter_keypress_submits_real():
    """PR-V2 — pressing Enter in the focused prompt input actually
    submits (exercises the _PromptInput._on_key override end-to-end)."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        ta = screen.query_one("#prompt-textarea", TextArea)
        ta.focus()
        ta.text = "real enter probe"
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(6):
            await pilot.pause()
            if app.observer.published:
                break
        assert app.observer.published, "Enter did not submit"
        assert app.observer.published[0][1]["content"] == "real enter probe"
        # Enter must NOT have inserted a newline into the input.
        assert "\n" not in ta.text


@pytest.mark.asyncio
async def test_no_send_button_enter_sends():
    """PR-V2 — there is no Send button; the prompt input submits on Enter
    (via _PromptInput.PromptSubmitted → action_send)."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert not screen.query("#btn-prompt-send")
        from acc.tui.screens.prompt import _PromptInput
        screen.query_one("#prompt-textarea", TextArea).text = "ping enter"
        # Simulate the input's Enter submission.
        screen.on__prompt_input_prompt_submitted(_PromptInput.PromptSubmitted())
        for _ in range(4):
            await pilot.pause()
            if app.observer.published:
                break
        assert app.observer.published
        assert app.observer.published[0][1]["content"] == "ping enter"


@pytest.mark.asyncio
async def test_send_threads_selected_workspace_into_payload():
    """PR-U2b — a chosen project dir rides along on the TASK_ASSIGN."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PromptScreen)

        screen.query_one("#prompt-textarea", TextArea).text = "write a scraper"
        # Simulate the modal callback having stored a relative project.
        screen._workspace_project = "myproject"
        screen.action_send()
        for _ in range(4):
            await pilot.pause()

        assert len(app.observer.published) == 1
        _, payload = app.observer.published[0]
        assert payload["workspace"] == "myproject"


@pytest.mark.asyncio
async def test_send_without_workspace_omits_field():
    """No directory selected → no ``workspace`` key in the payload."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = "hello"
        screen.action_send()
        for _ in range(4):
            await pilot.pause()

        _, payload = app.observer.published[0]
        assert "workspace" not in payload, payload


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
async def test_executed_prompt_captured_as_golden_candidate(
    tmp_path, monkeypatch,
):
    """PR-Y-2c — a successful reply captures the prompt into the
    writable golden store so it shows up in Diagnostics."""
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#prompt-textarea", TextArea).text = (
            "Write a unique capture probe prompt"
        )
        screen.action_send()
        for _ in range(8):
            await pilot.pause()
            if app.observer.published:
                break
        task_id = app.observer.published[0][1]["task_id"]
        app.observer.deliver(task_id, {
            "signal_type": "TASK_COMPLETE", "task_id": task_id,
            "agent_id": "coding_agent-x", "output": "done",
            "blocked": False, "latency_ms": 10.0, "episode_id": "ep",
        })
        for _ in range(8):
            await pilot.pause()
            if list(tmp_path.glob("*.yaml")):
                break

    from acc.golden_prompts import load_merged
    captured = [
        p for p in load_merged([tmp_path])
        if "capture probe" in p.prompt
    ]
    assert captured, "executed prompt was not captured as a candidate"


@pytest.mark.asyncio
async def test_blocked_reply_not_captured(tmp_path, monkeypatch):
    """A blocked reply must NOT be captured (we only persist
    successful executions)."""
    monkeypatch.setenv("ACC_GOLDEN_WRITABLE_ROOT", str(tmp_path))
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#prompt-textarea", TextArea).text = "blocked probe"
        screen.action_send()
        for _ in range(8):
            await pilot.pause()
            if app.observer.published:
                break
        task_id = app.observer.published[0][1]["task_id"]
        app.observer.deliver(task_id, {
            "signal_type": "TASK_COMPLETE", "task_id": task_id,
            "agent_id": "a", "output": "nope", "blocked": True,
            "block_reason": "cat-a", "latency_ms": 1.0, "episode_id": "e",
        })
        for _ in range(8):
            await pilot.pause()

    assert not list(tmp_path.glob("*.yaml"))


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
async def test_timeout_publishes_task_cancel(monkeypatch):
    """Proposal 003 PR-1 — when the prompt receive times out, the
    screen MUST publish TASK_CANCEL on ``acc.{cid}.task.cancel`` so
    the agent (and its downstream LLM backend) stops generating.
    Without this the operator's work is silently abandoned while
    vLLM keeps producing tokens against a dropped task.
    """
    # Force a tiny timeout so the test finishes in milliseconds.
    monkeypatch.setenv("ACC_PROMPT_TIMEOUT_S", "0.05")

    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = "ping"
        screen.action_send()

        # Wait for the send to register, then for the timeout to fire
        # and the cancel publish to land.
        for _ in range(40):
            await pilot.pause()
            cancel_calls = [
                p for p in app.observer.published
                if p[0].endswith(".task.cancel")
            ]
            if cancel_calls:
                break

        # Two publishes expected: the TASK_ASSIGN + the timeout-fired
        # TASK_CANCEL.
        subjects = [s for s, _ in app.observer.published]
        assert any(s.endswith(".task.cancel") for s in subjects), \
            f"no cancel publish; got subjects={subjects}"

        # The cancel payload must carry the same task_id as the assign.
        assign_payload = next(
            p for s, p in app.observer.published if s.endswith(".task.assign")
        )
        cancel_payload = next(
            p for s, p in app.observer.published if s.endswith(".task.cancel")
        )
        assert cancel_payload["task_id"] == assign_payload["task_id"]
        assert cancel_payload["signal_type"] == "TASK_CANCEL"
        assert cancel_payload["collective_id"] == "sol-test"


@pytest.mark.asyncio
async def test_timeout_records_cancelled_task_id(monkeypatch):
    """Proposal 003 PR-1 — the screen tracks cancelled-on-timeout
    task_ids in a FIFO-capped set so late TASK_COMPLETE replies can
    be suppressed downstream."""
    monkeypatch.setenv("ACC_PROMPT_TIMEOUT_S", "0.05")

    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = "ping"
        screen.action_send()

        for _ in range(40):
            await pilot.pause()
            if screen._cancelled_task_ids:
                break

        assert screen._cancelled_task_ids, \
            "timeout did not record the cancelled task_id"

        task_id = screen._cancelled_task_ids[0]
        assert screen._is_cancelled(task_id) is True
        assert screen._is_cancelled("never-seen-task-id") is False


@pytest.mark.asyncio
async def test_timeout_transcript_says_cancelled_not_timed_out(monkeypatch):
    """The operator-visible transcript message must read 'cancelled'
    (not 'timed out') after proposal 003 PR-1 — the system DID
    cancel, not just give up."""
    monkeypatch.setenv("ACC_PROMPT_TIMEOUT_S", "0.05")

    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = "ping"
        screen.action_send()

        for _ in range(40):
            await pilot.pause()
            if any(e.get("role") == "system" for e in screen.history):
                break

        sys_entries = [e for e in screen.history if e.get("role") == "system"]
        assert sys_entries, "no system entry after timeout"
        text = sys_entries[-1]["text"].lower()
        assert "cancel" in text, f"transcript should say 'cancel', got: {text}"
        assert "task_cancel" in text or "published" in text


def test_resolve_timeout_default(monkeypatch):
    """Defaults to ``_RECEIVE_TIMEOUT_S`` when the env var is unset."""
    from acc.tui.screens.prompt import _resolve_timeout, _RECEIVE_TIMEOUT_S
    monkeypatch.delenv("ACC_PROMPT_TIMEOUT_S", raising=False)
    assert _resolve_timeout() == _RECEIVE_TIMEOUT_S


def test_resolve_timeout_reads_env(monkeypatch):
    """A valid positive ACC_PROMPT_TIMEOUT_S overrides the default."""
    from acc.tui.screens.prompt import _resolve_timeout
    monkeypatch.setenv("ACC_PROMPT_TIMEOUT_S", "42.5")
    assert _resolve_timeout() == 42.5


def test_resolve_timeout_ignores_garbage(monkeypatch):
    """Non-numeric env values fall back to the default + log a warning."""
    from acc.tui.screens.prompt import _resolve_timeout, _RECEIVE_TIMEOUT_S
    monkeypatch.setenv("ACC_PROMPT_TIMEOUT_S", "not-a-number")
    assert _resolve_timeout() == _RECEIVE_TIMEOUT_S


def test_resolve_timeout_ignores_non_positive(monkeypatch):
    """Zero / negative env values fall back to the default."""
    from acc.tui.screens.prompt import _resolve_timeout, _RECEIVE_TIMEOUT_S
    monkeypatch.setenv("ACC_PROMPT_TIMEOUT_S", "-1")
    assert _resolve_timeout() == _RECEIVE_TIMEOUT_S


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


# ---------------------------------------------------------------------------
# PR-F — task-progress bar + invocation-waterfall + detail modal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_render_populates_task_progress_line():
    """`_render_task_progress_line` writes a bar+ratio+confidence
    summary into `#task-progress-line`."""
    from textual.widgets import Static
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Patch update() to capture the rendered text.
        line = screen.query_one("#task-progress-line", Static)
        captured: list[str] = []
        real = line.update

        def rec(content="", **kw):
            captured.append(str(content))
            return real(content, **kw)

        line.update = rec  # type: ignore[assignment]
        screen._render_task_progress_line({
            "task_id": "abcd1234",
            "current_step": 5,
            "total_steps": 8,
            "step_label": "Composing",
            "confidence": 0.78,
            "confidence_trend": "STABLE",
        })
        await pilot.pause()
        assert captured, "progress line should have been updated"
        text = captured[-1]
        assert "processing" in text   # PR-V2 activity line
        assert "5/8" in text
        assert "62%" in text   # 5/8 → 62%
        assert "78%" in text   # confidence
        assert "Composing" in text


@pytest.mark.asyncio
async def test_render_task_progress_line_none_resets_to_idle():
    """Passing `None` resets the progress line to the idle placeholder."""
    from textual.widgets import Static
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        line = screen.query_one("#task-progress-line", Static)
        captured: list[str] = []
        real = line.update

        def rec(content="", **kw):
            captured.append(str(content))
            return real(content, **kw)

        line.update = rec  # type: ignore[assignment]
        screen._render_task_progress_line(None)
        await pilot.pause()

        # PR-V2 — idle clears the activity line to blank (it only shows
        # while a task is in flight).
        assert captured and captured[-1] == ""


def _capture_line(screen):
    """Patch `#task-progress-line`.update to record painted strings."""
    from textual.widgets import Static
    line = screen.query_one("#task-progress-line", Static)
    captured: list[str] = []
    real = line.update

    def rec(content="", **kw):
        captured.append(str(content))
        return real(content, **kw)

    line.update = rec  # type: ignore[assignment]
    return captured


@pytest.mark.asyncio
async def test_begin_activity_paints_continuous_line():
    """PR-V3 — `_begin_activity` shows a live processing line before any
    TASK_PROGRESS arrives, and `_tick_activity` keeps repainting it."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        captured = _capture_line(screen)

        screen._begin_activity("task-xyz")
        await pilot.pause()
        assert captured and "processing" in captured[-1]
        assert screen._active_progress is not None

        # The ticker advances the spinner without an agent event.
        before = screen._spinner_i
        screen._tick_activity()
        await pilot.pause()
        assert screen._spinner_i == before + 1
        assert "processing" in captured[-1]


@pytest.mark.asyncio
async def test_activity_line_shows_token_tally():
    """PR-V3 — a TASK_PROGRESS carrying tokens renders `N tok` on the
    activity line (the field the pre-V3 line never populated)."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        captured = _capture_line(screen)

        screen._append_progress_entry({
            "task_id": "abcd1234",
            "progress": {
                "current_step": 2,
                "total_steps_estimated": 6,
                "step_label": "Generating",
                "confidence": 0.8,
                "confidence_trend": "RISING",
                "tokens_in_so_far": 1000,
                "tokens_out_so_far": 536,
            },
        })
        await pilot.pause()
        text = captured[-1]
        assert "1536 tok" in text
        assert "2/6" in text
        assert "Generating" in text


@pytest.mark.asyncio
async def test_end_activity_clears_and_is_task_scoped():
    """PR-V3 — `_end_activity` blanks the line, but only for the task it
    is showing (a slow task finishing must not wipe a newer one)."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        captured = _capture_line(screen)

        screen._begin_activity("task-new")
        await pilot.pause()
        # An older task finishing must NOT clear the newer one's line.
        screen._end_activity("task-old")
        await pilot.pause()
        assert screen._active_progress is not None
        assert "processing" in captured[-1]

        # Ending the shown task clears it.
        screen._end_activity("task-new")
        await pilot.pause()
        assert screen._active_progress is None
        assert captured[-1] == ""


def _capture_transcript(screen):
    from textual.widgets import Static
    t = screen.query_one("#prompt-transcript", Static)
    cap: list[str] = []
    real = t.update

    def rec(content="", **kw):
        cap.append(str(content))
        return real(content, **kw)

    t.update = rec  # type: ignore[assignment]
    return cap


_REASONING = (
    "Prior learnings: none found\nOptions: A vs B\n"
    "Evaluation: B is safer\nPlan: use a CSV library\nReview: check edge cases"
)


def test_reasoning_summary_prefers_plan():
    from acc.tui.screens.prompt import PromptScreen
    assert PromptScreen._reasoning_summary(_REASONING) == "use a CSV library"
    assert PromptScreen._reasoning_summary("") == ""


@pytest.mark.asyncio
async def test_reasoning_collapsed_shows_summary_only():
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        cap = _capture_transcript(screen)
        screen._append_history({
            "role": "reasoning", "task_id": "abcd1234", "agent_id": "coding-1",
            "text": _REASONING, "ts": time.time(),
        })
        await pilot.pause()
        txt = cap[-1]
        assert "🧠" in txt and "reasoning" in txt
        assert "use a CSV library" in txt      # summary = Plan line
        assert "▸" in txt                       # collapsed marker
        assert "Options: A vs B" not in txt     # body hidden when collapsed


@pytest.mark.asyncio
async def test_reasoning_expand_then_hide():
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        cap = _capture_transcript(screen)
        screen._append_history({
            "role": "reasoning", "task_id": "abcd1234", "agent_id": "coding-1",
            "text": _REASONING, "ts": time.time(),
        })
        await pilot.pause()

        screen.action_toggle_reasoning()   # Ctrl+O → expand
        await pilot.pause()
        txt = cap[-1]
        assert "▾" in txt
        assert "Options: A vs B" in txt        # full body now shown

        screen.action_toggle_reasoning_visible()   # Ctrl+R → hide stream
        await pilot.pause()
        txt = cap[-1]
        assert "reasoning" not in txt           # entry suppressed entirely


@pytest.mark.asyncio
async def test_trace_entry_appends_invocation_waterfall_row():
    """A `trace` history entry adds a row to `#invocation-waterfall`
    and stashes the full record on `_waterfall_records`."""
    from textual.widgets import DataTable
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        wf = screen.query_one("#invocation-waterfall", DataTable)
        assert wf.row_count == 0

        screen._append_history({
            "role": "trace",
            "task_id": "abcd1234",
            "agent_id": "coding-1",
            "kind": "skill",
            "target": "echo",
            "ok": True,
            "error": "",
            "ts": time.time(),
        })
        await pilot.pause()

        assert wf.row_count == 1
        assert len(screen._waterfall_records) == 1
        rec = next(iter(screen._waterfall_records.values()))
        assert rec["kind"] == "skill"
        assert rec["target"] == "echo"
        assert rec["ok"] is True


@pytest.mark.asyncio
async def test_waterfall_caps_at_configured_limit():
    """Inserting more than _WATERFALL_CAP traces drops the oldest."""
    from textual.widgets import DataTable
    from acc.tui.screens.prompt import _WATERFALL_CAP
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        for i in range(_WATERFALL_CAP + 5):
            screen._append_history({
                "role": "trace",
                "task_id": "abcd1234",
                "agent_id": "coding-1",
                "kind": "skill",
                "target": f"echo_{i}",
                "ok": True,
                "error": "",
                "ts": time.time() + i * 0.001,
            })

        wf = screen.query_one("#invocation-waterfall", DataTable)
        assert wf.row_count == _WATERFALL_CAP
        assert len(screen._waterfall_records) == _WATERFALL_CAP


def test_invocation_detail_modal_renders_known_fields():
    """The modal body lists known fields then dumps the raw JSON.

    Pure unit test on the static body-builder so we don't have to
    run the modal under Pilot.
    """
    from acc.tui.widgets.invocation_detail_modal import InvocationDetailModal
    body = InvocationDetailModal._render_body({
        "task_id": "abcd1234",
        "agent_id": "coding-1",
        "kind": "mcp",
        "target": "fs.read",
        "ok": False,
        "error": "permission denied",
    })
    assert "task_id" in body
    assert "abcd1234" in body
    assert "mcp" in body
    assert "fs.read" in body
    assert "permission denied" in body
    assert "raw record" in body
    assert '"target": "fs.read"' in body


# ---------------------------------------------------------------------------
# PR-P (L-2) — Mode dropdown auto-prefills from role.default_operating_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_prefills_from_role_default(monkeypatch):
    """PR-P — changing the target role sets the Mode dropdown to that
    role's default_operating_mode."""
    from acc.config import RoleDefinitionConfig
    import acc.role_loader as rl

    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # Patch RoleLoader.load to return a role pinned to
        # ASK_PERMISSIONS regardless of disk.
        def _fake_load(self):
            return RoleDefinitionConfig.model_validate({
                "purpose": "p", "persona": "concise", "version": "0.1.0",
                "default_operating_mode": "ASK_PERMISSIONS",
            })
        monkeypatch.setattr(rl.RoleLoader, "load", _fake_load)

        # Fire the select-changed handler as if the operator picked a role.
        screen.on_select_changed(
            Select.Changed(
                screen.query_one("#select-target-role", Select),
                "coding_agent",
            )
        )
        await pilot.pause()

        # PR-V2 — prefill now sets the internal mode (no dropdown).
        assert screen._operating_mode == "ASK_PERMISSIONS"


@pytest.mark.asyncio
async def test_mode_prefill_only_reacts_to_target_role(monkeypatch):
    """PR-P/V2 — the handler ignores Changed events that aren't the
    target-role Select (no spurious role loads)."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        called = {"n": 0}
        import acc.role_loader as rl
        orig = rl.RoleLoader.load

        def _counting_load(self):
            called["n"] += 1
            return orig(self)
        monkeypatch.setattr(rl.RoleLoader, "load", _counting_load)

        # A Changed event from some other Select must not trigger a load.
        screen.on_select_changed(
            Select.Changed(
                screen.query_one("#select-target-role", Select),
                # simulate a non-target select id by faking the event source
                screen.query_one("#select-target-role", Select),
            )
        ) if False else None  # guard: we test the id filter below
        # Directly assert the id-filter: a fake event whose select id is
        # not 'select-target-role' is ignored.
        class _FakeSel:
            id = "something-else"
        class _FakeEvent:
            select = _FakeSel()
            value = "x"
        screen.on_select_changed(_FakeEvent())  # type: ignore[arg-type]
        await pilot.pause()
        assert called["n"] == 0


@pytest.mark.asyncio
async def test_mode_prefill_tolerates_missing_role(monkeypatch):
    """PR-P/V2 — a role that doesn't load leaves the mode untouched."""
    import acc.role_loader as rl
    monkeypatch.setattr(rl.RoleLoader, "load", lambda self: None)

    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        before = screen._operating_mode
        screen.on_select_changed(
            Select.Changed(
                screen.query_one("#select-target-role", Select),
                "ghost_role",
            )
        )
        await pilot.pause()
        assert screen._operating_mode == before
