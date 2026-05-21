"""PR-D regression tests — Nucleus Apply spawns via reconcile.

Covers the three new behaviours added to ``InfuseScreen`` in PR-D:

1. Apply writes the ``(role, cluster_id, purpose)`` tuple into
   ``collective.yaml`` via :func:`acc.collective.upsert_agent_entry`.
2. Apply touches the ``.acc-apply.request`` marker next to the spec.
3. ``apply_snapshot`` flips status to "Agent <id> registered" when a
   HEARTBEAT arrives from a NEW agent (registered after
   ``_apply_started_ts``) whose role + cluster_id match the pending
   tuple — and ignores agents that were already there.

Also pins the agent-side heartbeat envelope to include
``cluster_id`` so the consumer side has something to filter on.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from acc.tui.app import ACCTUIApp
from acc.tui.models import AgentSnapshot, CollectiveSnapshot


# ---------------------------------------------------------------------------
# Fixtures + harness
# ---------------------------------------------------------------------------


def _mock_observer(collective_id: str = "sol-01") -> MagicMock:
    obs = MagicMock()
    obs.connect = AsyncMock()
    obs.close = AsyncMock()
    obs.subscribe = AsyncMock()
    obs.publish = AsyncMock()
    obs.snapshot = CollectiveSnapshot(collective_id=collective_id)
    return obs


class _TestApp(ACCTUIApp):
    def __init__(self, mock_observer: MagicMock) -> None:
        super().__init__(nats_url="nats://localhost:4222", collective_id="sol-01")
        # The real attribute storage is `_observers` (a list); the
        # public `nats_observer` is a read-only property.
        self._observers = [mock_observer]


@pytest.fixture
def isolated_collective(tmp_path: Path, monkeypatch):
    """Point InfuseScreen at a throwaway collective.yaml in *tmp_path*."""
    spec_path = tmp_path / "collective.yaml"
    spec_path.write_text(
        "collective_id: sol-01\nagents: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(spec_path))
    # cwd-relative reads (e.g. infuse's marker write) land in tmp_path
    monkeypatch.chdir(tmp_path)
    return spec_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_upserts_agent_entry_into_collective_yaml(isolated_collective):
    """Apply must add the role + cluster_id + purpose to collective.yaml."""
    from textual.widgets import Input, TextArea, Select

    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("nucleus")
        await pilot.pause()

        from acc.tui.screens.infuse import InfuseScreen
        assert isinstance(app.screen, InfuseScreen)

        # Pick a role (the Select defaults to the first; just trust it).
        screen = app.screen
        role_select = screen.query_one("#select-role", Select)
        # Ensure a non-blank role is set; fall back gracefully.
        if role_select.value in (Select.BLANK, None):
            opts = list(role_select._options)  # type: ignore[attr-defined]
            if opts:
                role_select.value = opts[0][1]
                await pilot.pause()

        screen.query_one("#input-cluster-id", Input).value = "backend"
        screen.query_one("#textarea-purpose", TextArea).text = "Implement Fibonacci"
        await pilot.pause()

        screen.action_apply()
        await pilot.pause()

        data = yaml.safe_load(isolated_collective.read_text(encoding="utf-8"))
        agents = data.get("agents", [])
        assert len(agents) >= 1, f"expected upsert, got {data!r}"
        match = [a for a in agents if a.get("cluster_id") == "backend"]
        assert match, f"no agent with cluster_id=backend in {agents!r}"
        assert match[0]["purpose"] == "Implement Fibonacci"


@pytest.mark.asyncio
async def test_apply_touches_acc_apply_request(isolated_collective):
    """Apply must drop a .acc-apply.request marker next to the spec."""
    from textual.widgets import Select

    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("nucleus")
        await pilot.pause()

        screen = app.screen
        role_select = screen.query_one("#select-role", Select)
        if role_select.value in (Select.BLANK, None):
            opts = list(role_select._options)  # type: ignore[attr-defined]
            if opts:
                role_select.value = opts[0][1]
                await pilot.pause()

        screen.action_apply()
        await pilot.pause()

        marker = isolated_collective.parent / ".acc-apply.request"
        assert marker.exists(), "expected .acc-apply.request marker"


@pytest.mark.asyncio
async def test_apply_snapshot_marks_new_agent_registered(isolated_collective):
    """apply_snapshot must flip status when a NEW agent matching the
    pending (role, cluster_id) heartbeats after _apply_started_ts."""
    from textual.widgets import Input, Select

    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("nucleus")
        await pilot.pause()

        screen = app.screen
        role_select = screen.query_one("#select-role", Select)
        if role_select.value in (Select.BLANK, None):
            opts = list(role_select._options)  # type: ignore[attr-defined]
            if opts:
                role_select.value = opts[0][1]
                await pilot.pause()
        role_value = role_select.value

        screen.query_one("#input-cluster-id", Input).value = "backend"
        await pilot.pause()

        # Pin a known started-ts so the registered_ts comparison is
        # deterministic.
        screen._apply_started_ts = 1000.0
        screen._pending_apply = (str(role_value), "backend")

        snap = CollectiveSnapshot(collective_id="sol-01")
        # Existing agent — same role, no cluster, OLD registration ts.
        # Must be ignored.
        snap.agents["existing-1"] = AgentSnapshot(
            agent_id="existing-1",
            role=str(role_value),
            cluster_id="",
            last_heartbeat_ts=999.0,
        )
        # New agent — matching role + cluster, registered AFTER ts.
        new = AgentSnapshot(
            agent_id="new-2",
            role=str(role_value),
            cluster_id="backend",
            last_heartbeat_ts=1500.0,
        )
        # Add the registered_ts attribute that the watcher reads.
        new.registered_ts = 1500.0  # type: ignore[attr-defined]
        snap.agents["new-2"] = new

        screen.apply_snapshot(snap)
        await pilot.pause()

        assert "new-2" in screen.status_text
        assert "registered" in screen.status_text
        # Pending cleared so a re-apply starts fresh.
        assert screen._pending_apply is None
        assert screen._apply_started_ts == 0.0


@pytest.mark.asyncio
async def test_apply_snapshot_ignores_pre_existing_agent(isolated_collective):
    """Agents whose registered_ts predates _apply_started_ts must NOT
    flip the status — they were already there before Apply."""
    from textual.widgets import Input, Select

    app = _TestApp(mock_observer=_mock_observer())
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen("nucleus")
        await pilot.pause()

        screen = app.screen
        role_select = screen.query_one("#select-role", Select)
        if role_select.value in (Select.BLANK, None):
            opts = list(role_select._options)  # type: ignore[attr-defined]
            if opts:
                role_select.value = opts[0][1]
                await pilot.pause()
        role_value = role_select.value

        screen.query_one("#input-cluster-id", Input).value = "backend"
        await pilot.pause()

        screen._apply_started_ts = 2000.0
        screen._pending_apply = (str(role_value), "backend")
        baseline_status = screen.status_text

        snap = CollectiveSnapshot(collective_id="sol-01")
        stale = AgentSnapshot(
            agent_id="old-1",
            role=str(role_value),
            cluster_id="backend",
            last_heartbeat_ts=2500.0,
        )
        stale.registered_ts = 500.0  # type: ignore[attr-defined]
        snap.agents["old-1"] = stale

        screen.apply_snapshot(snap)
        await pilot.pause()

        assert "old-1" not in screen.status_text
        # Pending tuple still set (no match found).
        assert screen._pending_apply == (str(role_value), "backend")


def test_agent_heartbeat_payload_includes_cluster_id(monkeypatch):
    """Producer-side check: ``acc.agent`` must include cluster_id in
    the HEARTBEAT JSON envelope so the consumer's _route_heartbeat
    can filter on it."""
    import inspect

    from acc import agent as agent_mod

    # The heartbeat payload is constructed inline in _heartbeat_loop.
    src = inspect.getsource(agent_mod)
    assert '"cluster_id"' in src and 'ACC_CLUSTER_ID' in src, (
        "expected cluster_id propagation in acc/agent.py heartbeat payload"
    )


def test_tui_route_heartbeat_reads_cluster_id():
    """Consumer-side check: NATSObserver._route_heartbeat must read
    ``cluster_id`` from the HEARTBEAT and stash it on the
    AgentSnapshot."""
    from acc.tui.client import NATSObserver

    obs = NATSObserver.__new__(NATSObserver)
    obs._snapshot = CollectiveSnapshot(collective_id="sol-01")
    obs._snapshot_lock = MagicMock()
    obs._snapshot_lock.__enter__ = lambda self_: None
    obs._snapshot_lock.__exit__ = lambda self_, *a: None
    obs._update_callbacks = []
    obs._render_lock = MagicMock()
    obs._render_lock.__enter__ = lambda self_: None
    obs._render_lock.__exit__ = lambda self_, *a: None

    payload = {
        "signal_type": "HEARTBEAT",
        "agent_id": "coding-1",
        "collective_id": "sol-01",
        "role": "coding_agent",
        "cluster_id": "backend",
        "state": "ACTIVE",
        "ts": time.time(),
    }
    obs._route_heartbeat("coding-1", payload)

    agent = obs._snapshot.agents.get("coding-1")
    assert agent is not None
    assert getattr(agent, "cluster_id", "") == "backend"
