"""Cluster topology aggregation + panel render tests (PR-4 of subagent
clustering).

Two layers:

* :class:`acc.tui.client.NATSObserver._update_cluster_topology` —
  pure-function fold of TASK_PROGRESS / TASK_COMPLETE payloads into a
  :class:`acc.tui.models.CollectiveSnapshot.cluster_topology` row.
* :class:`acc.tui.widgets.cluster_panel.ClusterPanel` — render-only
  widget; we feed it a synthetic snapshot dict and inspect the
  rendered string instead of running a Pilot.

The panel is intentionally renderer-only (no I/O) so tests need
neither NATS nor a Textual App to validate the wire-protocol → UI
behaviour.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from acc.tui.client import NATSObserver
from acc.tui.widgets.cluster_panel import ClusterPanel


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def _build_observer():
    queue: asyncio.Queue = asyncio.Queue()
    return NATSObserver(
        nats_url="nats://unused", collective_id="sol-01", update_queue=queue,
    )


def test_progress_event_creates_cluster_row_and_member():
    obs = _build_observer()
    obs._route_task_progress("coding_agent-1", {
        "signal_type": "TASK_PROGRESS",
        "task_id": "t-1",
        "agent_id": "coding_agent-1",
        "cluster_id": "c-abc",
        "progress": {
            "current_step": 2,
            "total_steps_estimated": 6,
            "step_label": "Calling skill:echo",
        },
    })
    topology = obs._snapshot.cluster_topology
    assert "c-abc" in topology
    row = topology["c-abc"]
    assert row["subagent_count"] == 1
    member = row["members"]["coding_agent-1"]
    assert member["current_step"] == 2
    assert member["total_steps"] == 6
    assert member["skill_in_use"] == "echo"
    assert member["status"] == "running"


def test_skill_in_use_extracted_from_mcp_label():
    obs = _build_observer()
    obs._route_task_progress("agent-1", {
        "signal_type": "TASK_PROGRESS",
        "task_id": "t-1",
        "agent_id": "agent-1",
        "cluster_id": "c-x",
        "progress": {"current_step": 1, "step_label": "Calling mcp:fs.read"},
    })
    member = obs._snapshot.cluster_topology["c-x"]["members"]["agent-1"]
    assert member["skill_in_use"] == "mcp:fs.read"


def test_subagent_count_grows_as_new_members_appear():
    """The aggregator never knows the authoritative size — it tracks
    a running max as new agent_ids arrive within the same cluster_id."""
    obs = _build_observer()
    for aid in ("a-1", "a-2", "a-3"):
        obs._route_task_progress(aid, {
            "signal_type": "TASK_PROGRESS",
            "task_id": f"t-{aid}",
            "agent_id": aid,
            "cluster_id": "c-grow",
            "progress": {"current_step": 1},
        })
    assert obs._snapshot.cluster_topology["c-grow"]["subagent_count"] == 3


def test_task_complete_marks_member_complete_and_finishes_cluster():
    obs = _build_observer()
    # Two members observed via PROGRESS first.
    for aid in ("a-1", "a-2"):
        obs._route_task_progress(aid, {
            "signal_type": "TASK_PROGRESS",
            "task_id": f"t-{aid}",
            "agent_id": aid,
            "cluster_id": "c-fin",
            "progress": {"current_step": 1},
        })
    # Both complete cleanly.
    for aid in ("a-1", "a-2"):
        obs._route_task_complete(aid, {
            "signal_type": "TASK_COMPLETE",
            "task_id": f"t-{aid}",
            "agent_id": aid,
            "cluster_id": "c-fin",
            "blocked": False,
        })
    row = obs._snapshot.cluster_topology["c-fin"]
    assert all(m["status"] == "complete" for m in row["members"].values())
    assert row["finished_at"] is not None


def test_task_complete_blocked_member_marked_blocked():
    obs = _build_observer()
    obs._route_task_progress("a-1", {
        "signal_type": "TASK_PROGRESS",
        "agent_id": "a-1",
        "task_id": "t-1",
        "cluster_id": "c-b",
        "progress": {"current_step": 1},
    })
    obs._route_task_complete("a-1", {
        "signal_type": "TASK_COMPLETE",
        "agent_id": "a-1",
        "task_id": "t-1",
        "cluster_id": "c-b",
        "blocked": True,
        "block_reason": "A-017",
    })
    member = obs._snapshot.cluster_topology["c-b"]["members"]["a-1"]
    assert member["status"] == "blocked"


def test_payloads_without_cluster_id_do_not_create_rows():
    """Legacy single-agent traffic must NOT pollute the topology view —
    the cluster panel only ever shows real clusters."""
    obs = _build_observer()
    obs._route_task_progress("a-1", {
        "signal_type": "TASK_PROGRESS",
        "agent_id": "a-1",
        "task_id": "t-1",
        # no cluster_id
        "progress": {"current_step": 1},
    })
    assert obs._snapshot.cluster_topology == {}


# ---------------------------------------------------------------------------
# ClusterPanel render
# ---------------------------------------------------------------------------


def _capture_panel_renders(panel: ClusterPanel) -> list[str]:
    """Monkeypatch ``Static.update`` to capture the rendered string +
    treat the panel as mounted so the reactive watchers fire.

    Avoids needing a Pilot / running App — the panel's render path is
    a pure ``self.update(text)`` call, so this captures everything we
    care about without spinning up Textual.
    """
    captured: list[str] = []
    panel.update = captured.append  # type: ignore[assignment]
    # Pretend we're attached so watch_snapshot / watch_expanded
    # actually run their _render() body.
    panel._is_mounted = True  # type: ignore[attr-defined]
    return captured


def test_panel_collapsed_header_shows_zero_when_empty():
    panel = ClusterPanel()
    out = _capture_panel_renders(panel)
    panel.snapshot = {}
    assert out
    assert "Clusters: 0" in out[-1]


def test_panel_collapsed_header_counts_clusters_and_total_members():
    panel = ClusterPanel()
    out = _capture_panel_renders(panel)
    panel.snapshot = {
        "c-1": {
            "cluster_id": "c-1", "target_role": "coding_agent",
            "subagent_count": 3, "members": {}, "created_at": time.time(),
            "finished_at": None, "reason": "",
        },
        "c-2": {
            "cluster_id": "c-2", "target_role": "analyst",
            "subagent_count": 2, "members": {}, "created_at": time.time(),
            "finished_at": None, "reason": "",
        },
    }
    rendered = out[-1]
    assert "Clusters: 2" in rendered
    assert "5 agents" in rendered            # 3 + 2 totals


def test_panel_expanded_renders_member_rows():
    panel = ClusterPanel()
    out = _capture_panel_renders(panel)
    panel.expanded = True
    panel.snapshot = {
        "c-xyz": {
            "cluster_id": "c-xyz",
            "target_role": "coding_agent",
            "subagent_count": 2,
            "members": {
                "coding_agent-aaa": {
                    "task_id": "t-aaa",
                    "step_label": "Calling skill:echo",
                    "current_step": 2,
                    "total_steps": 4,
                    "status": "running",
                    "skill_in_use": "echo",
                    "last_seen": time.time(),
                },
                "coding_agent-bbb": {
                    "task_id": "t-bbb",
                    "step_label": "",
                    "current_step": 4,
                    "total_steps": 4,
                    "status": "complete",
                    "skill_in_use": "echo",
                    "last_seen": time.time(),
                },
            },
            "created_at": time.time(),
            "finished_at": None,
            "reason": "fixed strategy, count=2",
        },
    }
    rendered = out[-1]
    # Member ids render truncated at 14 chars (panel formatting).
    assert "coding_agent-a" in rendered
    assert "coding_agent-b" in rendered
    assert "skill:echo" in rendered
    assert "step 2/4" in rendered
    assert "running" in rendered
    assert "complete" in rendered


def test_panel_drops_finished_clusters_after_grace_window():
    """Finished clusters disappear from the rendered output once the
    30 s grace window has elapsed.  The aggregator stays at full
    fidelity; the panel just hides them."""
    panel = ClusterPanel()
    out = _capture_panel_renders(panel)
    long_ago = time.time() - 60.0
    panel.snapshot = {
        "c-old": {
            "cluster_id": "c-old", "target_role": "coding_agent",
            "subagent_count": 1, "members": {},
            "created_at": long_ago,
            "finished_at": long_ago,
            "reason": "",
        },
    }
    assert "Clusters: 0" in out[-1]


def test_panel_keeps_recent_finished_clusters_visible():
    """A cluster that finished moments ago is still rendered — the
    operator gets to read the final state before it disappears."""
    panel = ClusterPanel()
    out = _capture_panel_renders(panel)
    just_now = time.time() - 1.0
    panel.snapshot = {
        "c-recent": {
            "cluster_id": "c-recent", "target_role": "coding_agent",
            "subagent_count": 1, "members": {},
            "created_at": just_now, "finished_at": just_now, "reason": "",
        },
    }
    assert "Clusters: 1" in out[-1]
