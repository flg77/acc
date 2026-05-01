"""Tests for the agent-side TASK_PROGRESS emitter.

Covers three layers:

1. ``CognitiveCore.process_task`` invokes ``progress_callback`` at
   every step boundary in the canonical pipeline.
2. ``dispatch_invocations`` invokes ``progress_callback`` once per
   invocation, BEFORE dispatch.
3. End-to-end through the prompt-pane receive surface (PR #19): a
   process_task callback that publishes via the agent's signaling
   backend → NATSObserver fan-out → on_progress callback → transcript
   row.

These are unit-scope: no real NATS, no live agent process.  We
mock the LLM + vector backend with stubs and use the existing
synthetic-event harness for the receive side.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from acc.capability_dispatch import (
    InvocationOutcome,
    ParsedInvocation,
    dispatch_invocations,
)
from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.progress import ProgressContext


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubLLM:
    """Mimics LLMBackend with a fixed response."""

    async def complete(self, system, user, response_schema=None):
        return {
            "content": "ok",
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

    async def embed(self, text):
        return [0.0] * 384


class _StubVector:
    def insert(self, *_args, **_kwargs):
        pass

    def search(self, *_args, **_kwargs):
        return []


# ---------------------------------------------------------------------------
# Tier 1 — CognitiveCore.process_task emits at every step boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_task_emits_six_progress_events_in_order():
    """Happy path: PRE-GATE → DRIFT produces 6 emits, in order, with
    distinct step labels and current_step from 1..6."""
    captured: list[ProgressContext] = []

    core = CognitiveCore(
        agent_id="t",
        collective_id="c",
        llm=_StubLLM(),
        vector=_StubVector(),
    )
    role = RoleDefinitionConfig(purpose="test")

    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc",
                      "content": "do work"},
        role=role,
        progress_callback=captured.append,
    )

    # Six events, current_step strictly increasing, total_steps_estimated
    # constant at 6 throughout.
    assert len(captured) == 6, [c.step_label for c in captured]
    assert [c.current_step for c in captured] == [1, 2, 3, 4, 5, 6]
    assert all(c.total_steps_estimated == 6 for c in captured)
    # Step labels match the documented pipeline.
    labels = [c.step_label for c in captured]
    assert "Pre-reasoning gate" in labels[0]
    assert "Building system prompt" in labels[1]
    assert "Calling LLM" in labels[2]
    assert "Post-reasoning governance" in labels[3]
    assert "Persisting episode" in labels[4]
    assert "Drift scoring" in labels[5]


@pytest.mark.asyncio
async def test_process_task_emit_carries_token_counts_after_llm_call():
    """The post-LLM emit (step 4) carries non-zero token counts."""
    captured: list[ProgressContext] = []

    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLM(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=captured.append,
    )

    # Steps 1-3 emit BEFORE the LLM call, so token counts are zero.
    assert captured[0].tokens_in_so_far == 0
    assert captured[2].tokens_out_so_far == 0
    # Step 4 emits AFTER the LLM call — the stub reports 12 + 8.
    assert captured[3].tokens_in_so_far == 12
    assert captured[3].tokens_out_so_far == 8
    assert captured[3].llm_calls_so_far == 1


@pytest.mark.asyncio
async def test_process_task_emits_elapsed_ms_monotonically():
    """elapsed_ms must be non-negative and (weakly) increasing."""
    captured: list[ProgressContext] = []
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLM(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=captured.append,
    )

    elapsed = [c.elapsed_ms for c in captured]
    assert all(e >= 0 for e in elapsed)
    # Weak monotonicity — clock doesn't go backwards.
    assert elapsed == sorted(elapsed)


@pytest.mark.asyncio
async def test_process_task_progress_callback_none_is_zero_overhead():
    """No callback → no crash, no emit, normal completion."""
    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLM(), vector=_StubVector(),
    )
    result = await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=None,
    )
    # process_task still returns a real result.
    assert result.output == "ok"
    assert result.blocked is False


@pytest.mark.asyncio
async def test_process_task_progress_callback_exception_does_not_break_pipeline():
    """A misbehaving callback must not stop the cognitive pipeline."""
    captured: list[ProgressContext] = []

    def explosive(ctx):
        captured.append(ctx)
        if ctx.current_step == 2:
            raise RuntimeError("simulated callback crash")

    core = CognitiveCore(
        agent_id="t", collective_id="c",
        llm=_StubLLM(), vector=_StubVector(),
    )
    result = await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=explosive,
    )

    # All 6 emits still fired (exception isolated).
    assert len(captured) == 6
    # Pipeline finished cleanly.
    assert result.blocked is False
    assert result.output == "ok"


# ---------------------------------------------------------------------------
# Tier 2 — dispatch_invocations emits per invocation
# ---------------------------------------------------------------------------


class _StubCore:
    """Minimal CognitiveCore stand-in for dispatch_invocations tests.

    The real CognitiveCore exposes ``invoke_skill`` / ``invoke_mcp_tool``;
    we stub them to record calls + return a synthetic dict.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._stress = type("_S", (), {"cat_a_trigger_count": 0})()

    async def invoke_skill(self, skill_id, args, role):
        self.calls.append(("skill", skill_id, args or {}))
        return {"echo": (args or {}).get("text", "")}

    async def invoke_mcp_tool(self, server_id, tool, args, role):
        self.calls.append(("mcp", f"{server_id}.{tool}", args or {}))
        return {"content": [{"type": "text", "text": "ok"}]}


@pytest.mark.asyncio
async def test_dispatch_invocations_emits_one_progress_per_invocation():
    captured: list[ProgressContext] = []
    core = _StubCore()
    role = RoleDefinitionConfig(allowed_skills=["echo"], allowed_mcps=["fs"])

    invs = [
        ParsedInvocation(kind="skill", target="echo", args={"text": "hi"}),
        ParsedInvocation(kind="mcp", target="fs.read", args={}),
    ]

    outcomes = await dispatch_invocations(
        invs, core, role,
        progress_callback=captured.append,
    )

    # One progress emit per invocation, in source order.
    assert len(captured) == 2
    assert captured[0].current_step == 1
    assert captured[0].total_steps_estimated == 2
    assert captured[0].step_label == "Calling skill:echo"
    assert captured[1].current_step == 2
    assert captured[1].step_label == "Calling mcp:fs.read"
    # Underlying dispatches still happened.
    assert [c[0] for c in core.calls] == ["skill", "mcp"]


@pytest.mark.asyncio
async def test_dispatch_invocations_emits_before_dispatch():
    """Order invariant: progress event for invocation N fires BEFORE
    invocation N is dispatched.  Critical so the operator sees
    'Calling X' BEFORE the call's outcome lands in the transcript."""
    events: list[str] = []

    class _OrderingCore:
        async def invoke_skill(self, skill_id, args, role):
            events.append(f"dispatch:{skill_id}")
            return {}

    role = RoleDefinitionConfig(allowed_skills=["echo"])

    def progress(ctx):
        events.append(f"progress:{ctx.step_label}")

    invs = [
        ParsedInvocation(kind="skill", target="echo", args={}),
    ]
    await dispatch_invocations(
        invs, _OrderingCore(), role,
        progress_callback=progress,
    )

    assert events == ["progress:Calling skill:echo", "dispatch:echo"]


@pytest.mark.asyncio
async def test_dispatch_invocations_progress_callback_none_is_zero_overhead():
    """None callback → no emit, dispatch still works."""
    core = _StubCore()
    role = RoleDefinitionConfig(allowed_skills=["echo"])
    invs = [ParsedInvocation(kind="skill", target="echo", args={})]
    outcomes = await dispatch_invocations(invs, core, role)
    assert len(outcomes) == 1
    assert outcomes[0].ok is True


@pytest.mark.asyncio
async def test_dispatch_invocations_progress_callback_exception_isolated():
    """Bad callback for invocation 1 must not skip invocation 2."""
    captured_steps: list[int] = []

    def bad(ctx):
        captured_steps.append(ctx.current_step)
        if ctx.current_step == 1:
            raise RuntimeError("callback crash")

    core = _StubCore()
    role = RoleDefinitionConfig(allowed_skills=["echo"], allowed_mcps=["fs"])
    invs = [
        ParsedInvocation(kind="skill", target="echo", args={}),
        ParsedInvocation(kind="mcp", target="fs.read", args={}),
    ]
    outcomes = await dispatch_invocations(
        invs, core, role, progress_callback=bad,
    )

    # Both progress emits fired; both dispatches ran.
    assert captured_steps == [1, 2]
    assert len(core.calls) == 2
    assert all(o.ok for o in outcomes)


# ---------------------------------------------------------------------------
# Tier 3 — End-to-end across the receive pipe (PR #19)
# ---------------------------------------------------------------------------


class _RecordingObserver:
    """Mimics NATSObserver's progress-listener registry only."""

    def __init__(self) -> None:
        self._listeners: dict[str, list] = {}

    def register_task_progress_listener(self, task_id, cb):
        self._listeners.setdefault(task_id, []).append(cb)

    def unregister_task_progress_listener(self, task_id):
        self._listeners.pop(task_id, None)

    def deliver(self, task_id, payload):
        for cb in list(self._listeners.get(task_id, [])):
            cb(payload)


@pytest.mark.asyncio
async def test_emitter_callback_payload_round_trips_through_observer():
    """Build the same callback shape the agent uses + deliver it
    through the observer to a downstream consumer.  Confirms the
    emitter's ``ctx.to_dict()`` shape matches what TUIPromptChannel
    expects.
    """
    observer = _RecordingObserver()
    received: list[dict] = []
    observer.register_task_progress_listener("task-abc", received.append)

    # Simulate the agent's callback (from acc.agent._handle_task).
    def agent_callback(ctx: ProgressContext) -> None:
        payload = {
            "signal_type": "TASK_PROGRESS",
            "task_id": "task-abc",
            "agent_id": "coding-1",
            "progress": ctx.to_dict(),
        }
        # Bypass NATS — directly fan out as the observer would.
        for cb in list(observer._listeners.get(payload["task_id"], [])):
            cb(payload)

    # Drive process_task end-to-end with the agent_callback.
    core = CognitiveCore(
        agent_id="coding-1", collective_id="sol-test",
        llm=_StubLLM(), vector=_StubVector(),
    )
    await core.process_task(
        task_payload={"signal_type": "TASK_ASSIGN", "task_id": "task-abc"},
        role=RoleDefinitionConfig(purpose="test"),
        progress_callback=agent_callback,
    )

    # All 6 emits made it to the downstream consumer with the right
    # payload shape (signal_type + task_id + agent_id + nested progress).
    assert len(received) == 6
    for i, payload in enumerate(received, start=1):
        assert payload["signal_type"] == "TASK_PROGRESS"
        assert payload["task_id"] == "task-abc"
        assert payload["agent_id"] == "coding-1"
        assert payload["progress"]["current_step"] == i
        assert payload["progress"]["total_steps_estimated"] == 6
        # to_dict must include every ProgressContext field downstream
        # consumers may read.
        for key in ("step_label", "elapsed_ms", "confidence",
                    "confidence_trend", "llm_calls_so_far",
                    "tokens_in_so_far", "tokens_out_so_far"):
            assert key in payload["progress"]
