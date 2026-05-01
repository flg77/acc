"""Tests for the capability invocation telemetry plumbed through
``CollectiveSnapshot.record_invocation`` →
``NATSObserver._route_task_complete`` → ``PerformanceScreen``.

Three layers covered:

1. ``CapabilityInvocationStats`` aggregation correctness — pure
   dataclass behaviour, no Textual / NATS involvement.
2. ``NATSObserver._route_task_complete`` folding the
   ``TASK_COMPLETE.invocations`` field into the snapshot.
3. ``PerformanceScreen`` Pilot — confirms the new tables render the
   stats from a synthesised snapshot.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from acc.tui.client import NATSObserver
from acc.tui.models import CapabilityInvocationStats, CollectiveSnapshot
from acc.tui.screens.performance import PerformanceScreen


# ---------------------------------------------------------------------------
# CapabilityInvocationStats — pure dataclass
# ---------------------------------------------------------------------------


def test_capability_stats_initial_state_is_clean():
    s = CapabilityInvocationStats(kind="skill", target="echo")
    assert s.total == 0
    assert s.ok == 0
    assert s.fail == 0
    # No invocations yet → ok_rate is 1.0 (don't penalise unseen tools).
    assert s.ok_rate == 1.0
    assert s.last_error == ""


def test_capability_stats_fail_and_ok_rate_track_correctly():
    s = CapabilityInvocationStats(kind="mcp", target="echo_server.echo")
    s.total = 10
    s.ok = 7
    assert s.fail == 3
    assert abs(s.ok_rate - 0.7) < 1e-9


# ---------------------------------------------------------------------------
# CollectiveSnapshot.record_invocation
# ---------------------------------------------------------------------------


def test_record_invocation_creates_stats_on_first_call():
    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.record_invocation(
        {"kind": "skill", "target": "echo", "ok": True, "error": ""},
        agent_id="agent-1",
        task_id="task-aaa",
        ts=1000.0,
    )
    key = "skill:echo"
    assert key in snap.capability_stats
    s = snap.capability_stats[key]
    assert s.total == 1 and s.ok == 1 and s.fail == 0
    assert s.last_seen_ts == 1000.0
    assert s.last_error == ""


def test_record_invocation_accumulates_across_calls():
    snap = CollectiveSnapshot(collective_id="sol-test")
    for ok in (True, True, False, True, False):
        snap.record_invocation(
            {"kind": "skill", "target": "echo", "ok": ok,
             "error": "" if ok else "boom"},
            agent_id="a", task_id="t",
        )
    s = snap.capability_stats["skill:echo"]
    assert s.total == 5
    assert s.ok == 3
    assert s.fail == 2
    assert s.last_error == "boom"


def test_record_invocation_keys_kind_and_target_independently():
    """Same target name on a skill vs MCP must not collide."""
    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.record_invocation({"kind": "skill", "target": "echo", "ok": True})
    snap.record_invocation({"kind": "mcp", "target": "echo", "ok": False,
                            "error": "x"})
    assert set(snap.capability_stats.keys()) == {"skill:echo", "mcp:echo"}
    assert snap.capability_stats["skill:echo"].ok == 1
    assert snap.capability_stats["mcp:echo"].fail == 1


def test_record_invocation_drops_malformed_entries():
    """Missing kind / unknown kind / missing target → silent skip."""
    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.record_invocation({"target": "echo", "ok": True})  # no kind
    snap.record_invocation({"kind": "weird", "target": "echo"})  # bad kind
    snap.record_invocation({"kind": "skill", "ok": True})  # no target
    snap.record_invocation({"kind": "skill", "target": "", "ok": True})
    assert snap.capability_stats == {}
    assert snap.invocation_log == []


def test_record_invocation_appends_to_log_with_fifo_cap():
    snap = CollectiveSnapshot(collective_id="sol-test")
    # Fire 60 invocations to overflow the 50-entry cap.
    for i in range(60):
        snap.record_invocation(
            {"kind": "skill", "target": f"echo{i % 3}", "ok": (i % 5) != 0,
             "error": "" if (i % 5) else "boom"},
            agent_id="agent-x", task_id=f"t{i}", ts=1000.0 + i,
        )
    assert len(snap.invocation_log) == 50
    # Oldest 10 dropped.
    assert snap.invocation_log[0]["task_id"] == "t10"
    assert snap.invocation_log[-1]["task_id"] == "t59"


# ---------------------------------------------------------------------------
# NATSObserver._route_task_complete folding
# ---------------------------------------------------------------------------


def _make_observer() -> NATSObserver:
    return NATSObserver(
        nats_url="nats://test.invalid:4222",
        collective_id="sol-test",
        update_queue=asyncio.Queue(),
    )


def test_route_task_complete_folds_invocations_into_snapshot():
    obs = _make_observer()
    obs._route_task_complete(
        "agent-1",
        {
            "signal_type": "TASK_COMPLETE",
            "agent_id": "agent-1",
            "task_id": "abc",
            "ts": 5000.0,
            "blocked": False,
            "invocations": [
                {"kind": "skill", "target": "echo", "ok": True, "error": ""},
                {"kind": "mcp", "target": "fs.read", "ok": False,
                 "error": "A-018 blocked"},
            ],
        },
    )
    snap = obs._snapshot
    assert "skill:echo" in snap.capability_stats
    assert "mcp:fs.read" in snap.capability_stats
    assert snap.capability_stats["skill:echo"].ok == 1
    assert snap.capability_stats["mcp:fs.read"].fail == 1
    assert snap.capability_stats["mcp:fs.read"].last_error == "A-018 blocked"
    assert len(snap.invocation_log) == 2


def test_route_task_complete_handles_missing_invocations_field():
    """Pre-PR-B agents that never include ``invocations`` must not crash."""
    obs = _make_observer()
    obs._route_task_complete(
        "agent-1",
        {"signal_type": "TASK_COMPLETE", "agent_id": "agent-1",
         "task_id": "abc"},
    )
    assert obs._snapshot.capability_stats == {}
    assert obs._snapshot.invocation_log == []


def test_route_task_complete_handles_non_list_invocations_safely():
    """Garbage in the field shouldn't propagate."""
    obs = _make_observer()
    obs._route_task_complete(
        "agent-1",
        {"signal_type": "TASK_COMPLETE", "invocations": "not a list"},
    )
    assert obs._snapshot.capability_stats == {}


# ---------------------------------------------------------------------------
# PerformanceScreen Pilot — render correctness
# ---------------------------------------------------------------------------


class _PerfHarness(App):
    def on_mount(self) -> None:
        self.push_screen(PerformanceScreen())


def _capture_static_updates(widget) -> list[str]:
    """Same monkeypatch trick PR-A's tests use to read Static content
    across Textual versions."""
    captured: list[str] = []
    real_update = widget.update

    def recording(content="", **kwargs):
        captured.append(str(content))
        return real_update(content, **kwargs)

    widget.update = recording  # type: ignore[assignment]
    return captured


@pytest.mark.asyncio
async def test_capability_invocations_table_renders_sorted_by_total():
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PerformanceScreen)

        # Synthesise a snapshot with three tools at different volumes.
        snap = CollectiveSnapshot(collective_id="sol-test")
        for _ in range(5):
            snap.record_invocation(
                {"kind": "skill", "target": "echo", "ok": True},
            )
        for _ in range(20):
            snap.record_invocation(
                {"kind": "mcp", "target": "fs.read", "ok": True},
            )
        snap.record_invocation(
            {"kind": "skill", "target": "rare", "ok": False,
             "error": "schema mismatch"},
        )

        screen.snapshot = snap
        await pilot.pause()

        cap_table = screen.query_one(
            "#capability-invocations-table", DataTable,
        )
        # First row by sort order should be fs.read (20 calls).
        first_key = list(cap_table.rows.keys())[0]
        first_key_value = getattr(first_key, "value", str(first_key))
        assert first_key_value == "mcp:fs.read"
        assert cap_table.row_count == 3


@pytest.mark.asyncio
async def test_capability_failures_panel_lists_only_failures():
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        snap = CollectiveSnapshot(collective_id="sol-test")
        snap.record_invocation(
            {"kind": "skill", "target": "echo", "ok": True},
            agent_id="agent-1", task_id="t1",
        )
        snap.record_invocation(
            {"kind": "mcp", "target": "fs.read", "ok": False,
             "error": "A-018 blocked"},
            agent_id="agent-2", task_id="t2",
        )

        failures_panel = screen.query_one(
            "#capability-failures-panel", Static,
        )
        captured = _capture_static_updates(failures_panel)
        screen.snapshot = snap
        await pilot.pause()

        rendered = "\n".join(captured)
        # The failure row appears with the error message.
        assert "A-018 blocked" in rendered
        assert "fs.read" in rendered
        assert "agent-2" in rendered
        # The successful invocation does NOT appear in the failures pane.
        assert "agent-1" not in rendered


@pytest.mark.asyncio
async def test_empty_snapshot_renders_guidance_row():
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        snap = CollectiveSnapshot(collective_id="sol-test")
        screen.snapshot = snap
        await pilot.pause()

        cap_table = screen.query_one(
            "#capability-invocations-table", DataTable,
        )
        assert cap_table.row_count == 1  # the guidance row only

        failures_panel = screen.query_one(
            "#capability-failures-panel", Static,
        )
        captured = _capture_static_updates(failures_panel)
        # Force re-render to capture the empty-state hint.
        screen._render_capability_failures(snap)
        await pilot.pause()
        rendered = "\n".join(captured)
        assert "No invocation failures observed" in rendered
