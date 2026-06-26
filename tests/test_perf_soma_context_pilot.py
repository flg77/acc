"""Pilot tests for proposal 003 PR-5 — Performance + Soma context.

Covers the operator's review items:

* Performance screen: per-agent table gains Cluster / Intent /
  Subagents / Active task columns; a ClusterPanel widget renders
  the cluster topology overview.
* Dashboard / Soma screen: governance counters get one-line
  definitions sourced from the central GOVERNANCE_TAXONOMY
  constant; a new TOKEN BUDGET BY CLUSTER panel rolls up token
  utilisation per active cluster.

Mounts each screen in isolation, feeds a synthetic
:class:`CollectiveSnapshot`, asserts the rendered widgets carry
the expected substrings.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from acc.tui.models import AgentSnapshot, CollectiveSnapshot
from acc.tui.screens.dashboard import DashboardScreen, GOVERNANCE_TAXONOMY
from acc.tui.screens.performance import PerformanceScreen


# ---------------------------------------------------------------------------
# Snapshot fixtures
# ---------------------------------------------------------------------------


def _make_agent(
    agent_id: str,
    *,
    role: str = "coding_agent",
    queue_depth: int = 2,
    backpressure: str = "OPEN",
    progress_label: str = "Calling skill:code_review",
    current_step: int = 1,
    total_steps: int = 3,
    token_util: float = 0.30,
    cat_a: int = 0,
    cat_b_dev: float = 0.0,
) -> AgentSnapshot:
    """Minimal AgentSnapshot wired for the perf + soma tests."""
    return AgentSnapshot(
        agent_id=agent_id,
        role=role,
        queue_depth=queue_depth,
        backpressure_state=backpressure,
        task_progress_label=progress_label,
        current_task_step=current_step,
        total_task_steps=total_steps,
        token_budget_utilization=token_util,
        cat_a_trigger_count=cat_a,
        cat_b_deviation_score=cat_b_dev,
        last_heartbeat_ts=1234567890.0,
    )


def _make_snapshot_with_cluster() -> CollectiveSnapshot:
    """Snapshot with one active cluster of 3 agents, all coding_agent_*."""
    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.agents = {
        "c-arch":  _make_agent("c-arch",  role="coding_agent_architect",   token_util=0.40),
        "c-impl":  _make_agent("c-impl",  role="coding_agent_implementer", token_util=0.75),
        "c-test":  _make_agent("c-test",  role="coding_agent_tester",      token_util=0.55),
        "loner":   _make_agent("loner",   role="analyst",                  token_util=0.20),
    }
    snap.cluster_topology = {
        "cl-abcdef12": {
            "target_role": "coding_agent",
            "subagent_count": 3,
            "members": {
                "c-arch": {
                    "status": "running",
                    "skill_in_use": "code_review",
                    "current_step": 1,
                    "total_steps": 3,
                },
                "c-impl": {
                    "status": "running",
                    "skill_in_use": "code_generation",
                    "current_step": 2,
                    "total_steps": 5,
                },
                "c-test": {
                    "status": "running",
                    "skill_in_use": "test_generation",
                    "current_step": 1,
                    "total_steps": 4,
                },
            },
            "reason": "PR-#27 cluster fan-out",
        },
    }
    return snap


def _make_empty_snapshot() -> CollectiveSnapshot:
    return CollectiveSnapshot(collective_id="sol-test")


# ---------------------------------------------------------------------------
# Performance screen — extended columns + cluster panel
# ---------------------------------------------------------------------------


class _PerfHarness(App):
    def on_mount(self) -> None:
        self.push_screen(PerformanceScreen())


def _capture_static(widget) -> list[str]:
    captured: list[str] = []
    real = widget.update

    def recording(content="", **kwargs):
        captured.append(str(content))
        return real(content, **kwargs)

    widget.update = recording  # type: ignore[assignment]
    return captured


@pytest.mark.asyncio
async def test_perf_table_includes_new_columns():
    """Proposal 003 PR-5 — Cluster, Intent, Subagents, Active task
    columns added to the per-agent perf table."""
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#agent-perf-table", DataTable)
        # DataTable.columns is a dict-like keyed by ColumnKey; the
        # ``label`` attribute is a Text instance — coerce to plain str.
        labels = [
            str(col.label).strip()
            for col in table.columns.values()
        ]
        for required in ("Cluster", "Intent", "Subagents", "Active task"):
            assert required in labels, labels


@pytest.mark.asyncio
async def test_perf_table_populates_cluster_column_from_topology():
    """A row whose agent_id appears in snap.cluster_topology[X].members
    renders that cluster_id in the Cluster column."""
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot_with_cluster()
        await pilot.pause()

        table = screen.query_one("#agent-perf-table", DataTable)
        # row_count == 4 (c-arch, c-impl, c-test, loner).
        assert table.row_count == 4

        # Map agent_id → cluster column contents.
        rendered = {}
        for row_key in table.rows.keys():
            agent_id = getattr(row_key, "value", str(row_key))
            row = table.get_row(row_key)
            # Column order: Agent, Role, Cluster, Intent, Subagents,
            # Active task, Queue, ▐, Backpressure
            rendered[agent_id] = row[2]  # Cluster column

        # The three cluster members carry the short cluster_id;
        # the loner gets the em-dash placeholder.
        assert "cl-abcdef" in rendered["c-arch"]
        assert "cl-abcdef" in rendered["c-impl"]
        assert "cl-abcdef" in rendered["c-test"]
        assert "—" in rendered["loner"]


@pytest.mark.asyncio
async def test_token_budget_panel_names_culprit_when_over_budget():
    """N3 — when an agent is over its per-task token budget, the token panel
    names the culprit (id + role) and flags that its tasks block."""
    from textual.widgets import Static

    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        snap = CollectiveSnapshot(collective_id="sol-test")
        snap.agents = {
            "assistant-x": _make_agent("assistant-x", role="assistant", token_util=2.0),
            "worker-y": _make_agent("worker-y", role="analyst", token_util=0.30),
        }
        captured = _capture_static(screen.query_one("#token-budget-panel", Static))
        screen.snapshot = snap
        await pilot.pause()
        text = "\n".join(captured)
        assert "culprit" in text.lower(), text
        assert "assistant-x" in text, text
        assert "block" in text.lower(), text


@pytest.mark.asyncio
async def test_perf_table_populates_intent_from_progress_label():
    """The Intent column carries the first 80 chars of the agent's
    task_progress_label."""
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        snap = _make_snapshot_with_cluster()
        # Add a long label so the 80-char truncation is exercised.
        snap.agents["c-arch"] = _make_agent(
            "c-arch",
            role="coding_agent_architect",
            progress_label="X" * 200,
            token_util=0.40,
        )
        screen.snapshot = snap
        await pilot.pause()

        table = screen.query_one("#agent-perf-table", DataTable)
        for row_key in table.rows.keys():
            if getattr(row_key, "value", str(row_key)) == "c-arch":
                row = table.get_row(row_key)
                intent_cell = row[3]
                # Trimmed to 80 chars.  Cell value is a raw string,
                # so length is exact.
                assert len(intent_cell) <= 80, len(intent_cell)
                break


@pytest.mark.asyncio
async def test_perf_table_active_task_column_shows_step_counts():
    """Active task column renders ``current/total`` plus a parenthetical
    age when the agent has steps in flight.  Steps come from the
    AgentSnapshot fields (current_task_step / total_task_steps), not
    from the cluster_topology member dict — the per-agent table is
    operator's-own-view, not cluster-relative."""
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        snap = _make_snapshot_with_cluster()
        # Set c-impl's AgentSnapshot to match the cluster member's
        # step counts so we have an unambiguous expectation.
        snap.agents["c-impl"] = _make_agent(
            "c-impl",
            role="coding_agent_implementer",
            current_step=2, total_steps=5,
            token_util=0.75,
        )
        screen.snapshot = snap
        await pilot.pause()

        table = screen.query_one("#agent-perf-table", DataTable)
        for row_key in table.rows.keys():
            if getattr(row_key, "value", str(row_key)) == "c-impl":
                row = table.get_row(row_key)
                active_cell = row[5]
                assert "2/5" in active_cell, active_cell
                break


# ---------------------------------------------------------------------------
# Dashboard / Soma — governance definitions + cluster budget panel
# ---------------------------------------------------------------------------


class _SomaHarness(App):
    def on_mount(self) -> None:
        self.push_screen(DashboardScreen())


def test_governance_taxonomy_constant_shape():
    """The central taxonomy constant carries all three keys with
    non-empty values.  This is the single place definitions live."""
    assert set(GOVERNANCE_TAXONOMY.keys()) == {"cat_a", "cat_b", "cat_c"}
    for key, value in GOVERNANCE_TAXONOMY.items():
        assert isinstance(value, str)
        assert len(value) > 10, f"{key} definition too short"


@pytest.mark.asyncio
async def test_dashboard_governance_renders_definitions():
    """The three governance rows render their one-line definitions
    alongside the counters."""
    app = _SomaHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot_with_cluster()
        await pilot.pause()

        cat_a_def = screen.query_one("#gov-cat-a-def", Static)
        cat_b_def = screen.query_one("#gov-cat-b-def", Static)
        cat_c_def = screen.query_one("#gov-cat-c-def", Static)
        captured_a = _capture_static(cat_a_def)
        captured_b = _capture_static(cat_b_def)
        captured_c = _capture_static(cat_c_def)
        # Force a re-render via the watcher.
        screen._render_governance(screen.snapshot)
        await pilot.pause()

        assert any("constitutional" in c for c in captured_a)
        assert any("operational" in c for c in captured_b)
        assert any("learned" in c for c in captured_c)


@pytest.mark.asyncio
async def test_dashboard_cluster_budget_renders_per_cluster_row():
    """The TOKEN BUDGET BY CLUSTER panel renders one row per active
    cluster, joining cluster_topology with the per-agent
    token_budget_utilization."""
    app = _SomaHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        widget = screen.query_one("#cluster-budget-content", Static)
        captured = _capture_static(widget)

        screen.snapshot = _make_snapshot_with_cluster()
        await pilot.pause()
        # Force handler to fire even if reactive hasn't yet.
        screen._render_cluster_budgets(screen.snapshot)
        await pilot.pause()

        joined = "\n".join(captured)
        # Cluster id (short) + agents count + coding_agent target +
        # avg / worst pct fields all render.
        assert "cl-abcdef" in joined
        assert "3 agents" in joined
        assert "coding_agent" in joined
        # 3-agent average: (0.40 + 0.75 + 0.55) / 3 ≈ 0.567 → 57%
        # Worst: 75%
        assert "57%" in joined, joined
        assert "75%" in joined, joined


@pytest.mark.asyncio
async def test_dashboard_cluster_budget_empty_state():
    """No active clusters → calm placeholder, not a crash."""
    app = _SomaHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        widget = screen.query_one("#cluster-budget-content", Static)
        captured = _capture_static(widget)

        screen.snapshot = _make_empty_snapshot()
        await pilot.pause()
        screen._render_cluster_budgets(screen.snapshot)
        await pilot.pause()

        joined = "\n".join(captured)
        assert "No active agent clusters" in joined


# ---------------------------------------------------------------------------
# 26.6.26 — RECENT FAILURES carries task attribution (where + when)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failures_panel_shows_task_id_and_timestamp():
    """A skill/MCP failure on the Performance pane must be traceable back to
    the prompt/task that triggered it (26.6.26 finding: "I do not know where
    and when the skill failures shown were triggered").  The RECENT FAILURES
    line carries the task_id (where) and a full timestamp (when)."""
    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        widget = screen.query_one("#capability-failures-panel", Static)
        captured = _capture_static(widget)

        snap = CollectiveSnapshot(collective_id="sol-test")
        snap.record_invocation(
            {"kind": "skill", "target": "shell_exec", "ok": False,
             "error": "SkillNotFound: shell_exec"},
            agent_id="assistant-1", task_id="task-deadbeef99", ts=1_700_000_000.0,
        )
        screen.snapshot = snap
        await pilot.pause()

        joined = "\n".join(captured)
        assert "task:task-deadbee" in joined, joined   # task_id[:12] (where)
        assert "shell_exec" in joined
        assert "SkillNotFound" in joined
        # Full date stamp (when) — date present, not just H:M:S (TZ-robust).
        assert "2023-11-1" in joined, joined
