"""Unit tests for the per-task_id listener registry on NATSObserver.

PR-B added two methods + one fan-out hook to
:class:`acc.tui.client.NATSObserver`:

* :meth:`NATSObserver.register_task_listener(task_id, future)`
* :meth:`NATSObserver.unregister_task_listener(task_id)`
* :meth:`NATSObserver._route_task_complete` now resolves the matching
  Future when a TASK_COMPLETE carrying ``task_id`` arrives.

These tests exercise the registry directly without a live NATS
connection — :meth:`_route_task_complete` is a plain method we can
call with a synthesised payload.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.tui.client import NATSObserver


def _make_observer() -> NATSObserver:
    """Build an observer without connecting — we only need its
    in-memory state for these tests."""
    queue: asyncio.Queue = asyncio.Queue()
    return NATSObserver(
        nats_url="nats://test.invalid:4222",
        collective_id="sol-test",
        update_queue=queue,
    )


@pytest.mark.asyncio
async def test_route_task_complete_resolves_matching_future():
    obs = _make_observer()
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    obs.register_task_listener("task-abc", fut)

    obs._route_task_complete(
        "agent-1",
        {
            "signal_type": "TASK_COMPLETE",
            "task_id": "task-abc",
            "agent_id": "agent-1",
            "output": "done",
            "blocked": False,
        },
    )

    assert fut.done()
    payload = await fut
    assert payload["task_id"] == "task-abc"
    assert payload["output"] == "done"


@pytest.mark.asyncio
async def test_route_task_complete_ignores_unknown_task_id():
    """A TASK_COMPLETE for an unregistered task must NOT raise.

    Real-world: the same observer fans out TASK_COMPLETEs from agents
    that the prompt pane never solicited (e.g. PLAN dispatches).
    Those messages should pass through harmlessly.
    """
    obs = _make_observer()
    obs._route_task_complete(
        "agent-1",
        {
            "signal_type": "TASK_COMPLETE",
            "task_id": "task-xyz-stranger",
            "agent_id": "agent-1",
        },
    )
    # No assertion needed — the test passes if no exception was raised.


@pytest.mark.asyncio
async def test_route_task_complete_handles_missing_task_id():
    """Pre-PR-B agents that don't echo task_id must not crash the fan-out."""
    obs = _make_observer()
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    obs.register_task_listener("task-abc", fut)

    obs._route_task_complete(
        "agent-1",
        {"signal_type": "TASK_COMPLETE", "agent_id": "agent-1"},
    )

    # Future stays pending; old payloads don't accidentally resolve it.
    assert not fut.done()
    obs.unregister_task_listener("task-abc")


@pytest.mark.asyncio
async def test_unregister_task_listener_is_idempotent():
    obs = _make_observer()
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    obs.register_task_listener("task-abc", fut)
    obs.unregister_task_listener("task-abc")
    obs.unregister_task_listener("task-abc")  # second call must not raise
    obs.unregister_task_listener("never-registered")

    # Subsequent _route_task_complete with the same id resolves nothing.
    obs._route_task_complete(
        "agent-1",
        {"signal_type": "TASK_COMPLETE", "task_id": "task-abc"},
    )
    assert not fut.done()


@pytest.mark.asyncio
async def test_register_replaces_previous_listener_for_same_id():
    """Edge case: same task_id registered twice — second wins."""
    obs = _make_observer()
    fut_a: asyncio.Future = asyncio.get_event_loop().create_future()
    fut_b: asyncio.Future = asyncio.get_event_loop().create_future()

    obs.register_task_listener("task-abc", fut_a)
    obs.register_task_listener("task-abc", fut_b)

    obs._route_task_complete(
        "agent-1",
        {"signal_type": "TASK_COMPLETE", "task_id": "task-abc"},
    )

    # The second future resolves; the first stays pending (caller's
    # responsibility to cancel — same contract as register_task_listener
    # docstring promises).
    assert fut_b.done()
    assert not fut_a.done()
