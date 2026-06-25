"""End-to-end role-interaction test driven by the exclusive E2E golden set.

This is the "apply the test when the N-series is implemented" gate the
operator asked for: for each exclusive golden prompt in
``examples/golden_prompts/e2e_role_interaction.yaml`` it drives the real
Prompt-pane path with a stub observer + a deterministic synthetic reply
(no live LLM/NATS), then double-checks the role interaction renders
correctly — traces, the failed-capability summary (N5), the self-explaining
block (N6) — and that the data panes reflect the executed task's snapshot
(the token-budget culprit N3, the backend-health rollup N7).

The companion ``acc-dev-harness/tools/e2e_role_interaction`` runs the same
golden set but captures every pane as an SVG + a walkthrough (the
documentation-skill extension); this file is the headless CI/lighthouse
gate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from textual.app import App
from textual.widgets import Input, Select, Static, TextArea

from acc.tui.screens.prompt import PromptScreen
from acc.tui.screens.performance import PerformanceScreen
from acc.tui.screens.configuration import ConfigurationScreen
from acc.tui.models import AgentSnapshot, CollectiveSnapshot


_GOLDEN = (
    Path(__file__).resolve().parent.parent
    / "examples" / "golden_prompts" / "e2e" / "e2e_role_interaction.yaml"
)


def _load_golden() -> dict:
    return yaml.safe_load(_GOLDEN.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Stub observer + harness (mirrors the real App surface; no NATS)
# ---------------------------------------------------------------------------


class _StubObserver:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._listeners: dict[str, asyncio.Future] = {}
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
        self._progress_listeners.pop(task_id, None)


class _PromptHarness(App):
    def __init__(self) -> None:
        super().__init__()
        self.observer = _StubObserver()
        self._observers = [self.observer]
        self._active_collective_idx = 0
        self._collective_ids = ["sol-test"]

    def on_mount(self) -> None:
        self.push_screen(PromptScreen())


def _capture_static(widget) -> list[str]:
    captured: list[str] = []
    real = widget.update

    def recording(content="", **kwargs):
        captured.append(str(content))
        return real(content, **kwargs)

    widget.update = recording  # type: ignore[assignment]
    return captured


# ---------------------------------------------------------------------------
# Per-prompt role-interaction E2E
# ---------------------------------------------------------------------------


def _prompt_cases():
    data = _load_golden()
    return [(p["id"], p) for p in data["prompts"]]


@pytest.mark.parametrize("case_id,case", _prompt_cases(), ids=lambda v: v if isinstance(v, str) else "")
@pytest.mark.asyncio
async def test_golden_role_interaction(case_id, case):
    """Drive one exclusive golden prompt end-to-end and verify the role
    interaction renders as expected."""
    reply = case["reply"]
    expect = case.get("expect", {})

    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#select-target-role", Select).value = case["target_role"]
        screen.query_one("#prompt-textarea", TextArea).text = case["prompt"]
        screen.action_send()
        for _ in range(10):
            await pilot.pause()
            if app.observer.published:
                break
        assert app.observer.published, "send did not publish a TASK_ASSIGN"
        task_id = app.observer.published[0][1]["task_id"]

        app.observer.deliver(task_id, {
            "signal_type": "TASK_COMPLETE",
            "task_id": task_id,
            "agent_id": f"{case['target_role']}-x",
            "output": reply.get("output", ""),
            "blocked": reply.get("blocked", False),
            "block_reason": reply.get("block_reason", ""),
            "latency_ms": reply.get("latency_ms", 0.0),
            "episode_id": "ep",
            "invocations": reply.get("invocations", []),
        })
        for _ in range(12):
            await pilot.pause()
            if any(e.get("role") == "agent" for e in screen.history):
                break

        roles = [e.get("role") for e in screen.history]
        assert "agent" in roles, "no agent reply rendered"
        transcript = "\n".join(str(e.get("text", "")) for e in screen.history)

        for needle in expect.get("transcript_contains", []):
            assert needle in transcript, (
                f"{case_id}: expected {needle!r} in transcript:\n{transcript}"
            )

        # N5 — a failed invocation must be summarised as a system line.
        sys_lines = [e for e in screen.history if e.get("role") == "system"]
        if expect.get("surfaces_failure_summary"):
            assert any("failed" in str(e.get("text", "")) for e in sys_lines), (
                f"{case_id}: failed-invocation summary (N5) not surfaced"
            )
        else:
            assert not any("failed" in str(e.get("text", "")) for e in sys_lines)

        # N6 — a block renders the agent line (the unblock message), flagged.
        if expect.get("blocked"):
            agent_line = next(e for e in screen.history if e.get("role") == "agent")
            assert agent_line.get("blocked") is True
            assert "[BLOCKED]" in str(agent_line.get("text", ""))

        # N4 — when the golden declares a hand-off, it is reasoned + names the
        # target (cross-checked against the runtime announcement helper).
        if expect.get("handover_announced"):
            from acc.assistant_proposal import handover_announcement
            declared = reply.get("handover_announcement", "")
            assert declared.startswith("→ Handing this to")
            for target in expect.get("roles_involved", []):
                if target != case["target_role"]:
                    assert target in declared, (
                        f"{case_id}: hand-off should name {target}"
                    )
            # the helper produces the same shape for the same inputs
            sample = handover_announcement("research_synthesizer", "x", "abcd1234")
            assert sample.startswith("→ Handing this to 'research_synthesizer'")


# ---------------------------------------------------------------------------
# Pane snapshot E2E — the data panes reflect the executed task (N3, N7)
# ---------------------------------------------------------------------------


def _snapshot_from_golden() -> CollectiveSnapshot:
    data = _load_golden()
    snap = CollectiveSnapshot(collective_id="sol-test")
    for spec in data["panes"]["agents"]:
        snap.agents[spec["id"]] = AgentSnapshot(
            agent_id=spec["id"],
            role=spec["role"],
            token_budget_utilization=spec["token_util"],
            llm_backend=spec.get("backend", ""),
            llm_health=spec.get("health", ""),
            llm_p50_latency_ms=spec.get("p50", 0.0),
            last_heartbeat_ts=1234567890.0,
        )
    return snap


@pytest.mark.asyncio
async def test_performance_pane_reflects_task_snapshot():
    """N3 — the Performance pane names the over-budget culprit from the
    executed task's snapshot."""
    expect = _load_golden()["panes"]["expect"]["performance_contains"]

    class _Harness(App):
        def on_mount(self) -> None:
            self.push_screen(PerformanceScreen())

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        captured = _capture_static(screen.query_one("#token-budget-panel", Static))
        screen.snapshot = _snapshot_from_golden()
        await pilot.pause()
        text = "\n".join(captured)
        for needle in expect:
            assert needle in text, f"perf pane missing {needle!r}: {text}"


def test_configuration_health_rollup_reflects_task_snapshot():
    """N7 — the backend-health rollup flags the degraded backend from the
    executed task's snapshot."""
    snap = _snapshot_from_golden()
    rollup = ConfigurationScreen._backend_health_rollup(snap.agents)
    for needle in _load_golden()["panes"]["expect"]["configuration_contains"]:
        assert needle in rollup, f"config rollup missing {needle!r}: {rollup}"
