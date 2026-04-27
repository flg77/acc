"""ACC TUI — CommunicationsScreen: PLAN DAG, knowledge feed, signal log, episodes.

All data sourced exclusively from CollectiveSnapshot built by NATSObserver.

Displays (REQ-TUI-033 – REQ-TUI-036):
  - Most recently received PLAN as ASCII DAG with per-step status
  - Scrollable KNOWLEDGE_SHARE feed (last 20, tag + source + snippet)
  - Scrollable signal flow log (last 30, timestamp + type + source + key field)
  - EPISODE_NOMINATE queue (episode_id, agent, score, task_type, status)

This screen imports only from acc.tui.models and acc.tui.widgets (REQ-TUI-051).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, Static

from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot, PlanSnapshot

# Step status symbols and colours
_STEP_STATUS: dict[str, tuple[str, str]] = {
    "PENDING": ("○", "dim"),
    "RUNNING": ("◉", "yellow"),
    "DONE": ("●", "green"),
    "FAILED": ("✗", "red"),
}


def _render_plan_dag(plan: "PlanSnapshot") -> str:
    """Render a plan's steps as an ASCII DAG with current status."""
    if not plan.steps:
        return "[dim]No steps in plan.[/dim]"

    lines: list[str] = [f"[bold]Plan: {plan.plan_id}[/bold]"]
    for step in plan.steps:
        step_id = step.get("step_id", "?")
        role = step.get("role", "?")[:12]
        desc = step.get("task_description", "")[:30]
        deps = step.get("depends_on", [])

        status = plan.step_progress.get(step_id, "PENDING")
        sym, colour = _STEP_STATUS.get(status, ("?", "dim"))

        dep_str = f" ← {', '.join(deps)}" if deps else ""
        lines.append(
            f"  [{colour}]{sym}[/{colour}]  [bold]{step_id}[/bold]"
            f"  [{role}] {desc}{dep_str}"
        )

    return "\n".join(lines)


class CommunicationsScreen(Screen):
    """A2A communications and signal network monitor (REQ-TUI-033 – REQ-TUI-036)."""

    BINDINGS = [
        ("q", "app.quit", "Quit"),
        ("1", "navigate('soma')", "Soma"),
        ("2", "navigate('nucleus')", "Nucleus"),
        ("3", "navigate('compliance')", "Compliance"),
        ("4", "navigate('comms')", "Comms"),
        ("5", "navigate('performance')", "Performance"),
        ("6", "navigate('ecosystem')", "Ecosystem"),
    ]

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="comms", id="nav")
        yield Label("ACC Communications — Synaptic Signal Network", id="comms-title")

        with Horizontal(id="comms-main"):
            # Left column: PLAN DAG + knowledge feed
            with Vertical(id="comms-left"):
                yield Label("ACTIVE PLAN (latest)", classes="panel-label")
                with ScrollableContainer(id="plan-dag-container"):
                    yield Static(id="plan-dag-panel")

                yield Label("KNOWLEDGE SHARE FEED (last 20)", classes="panel-label")
                with ScrollableContainer(id="knowledge-feed-container"):
                    yield Static(id="knowledge-feed-panel")

            # Right column: signal flow log + episode nominees
            with Vertical(id="comms-right"):
                yield Label("SIGNAL FLOW LOG (last 30)", classes="panel-label")
                with ScrollableContainer(id="signal-log-container"):
                    yield Static(id="signal-log-panel")

                yield Label("EPISODE NOMINATE QUEUE", classes="panel-label")
                yield DataTable(id="episode-table", show_cursor=False)

        yield Footer()

    def on_mount(self) -> None:
        ep_table = self.query_one("#episode-table", DataTable)
        ep_table.add_columns(
            "Episode ID", "Agent", "Score", "Task Type", "Status"
        )

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        if snap is None:
            return
        self._render_plan_dag(snap)
        self._render_knowledge_feed(snap)
        self._render_signal_log(snap)
        self._render_episode_nominees(snap)

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _render_plan_dag(self, snap: "CollectiveSnapshot") -> None:
        """Render the most recently received PLAN as ASCII DAG (REQ-TUI-033)."""
        panel = self.query_one("#plan-dag-panel", Static)
        if not snap.active_plans:
            panel.update("[dim]No PLAN signal received yet.[/dim]")
            return

        # Show the most recently received plan (last key insertion order)
        latest_plan = next(reversed(snap.active_plans.values()))
        panel.update(_render_plan_dag(latest_plan))

    def _render_knowledge_feed(self, snap: "CollectiveSnapshot") -> None:
        """Render the knowledge share feed (REQ-TUI-034)."""
        panel = self.query_one("#knowledge-feed-panel", Static)
        if not snap.knowledge_feed:
            panel.update("[dim]No KNOWLEDGE_SHARE signals received yet.[/dim]")
            return

        lines: list[str] = []
        for entry in reversed(snap.knowledge_feed):
            ts_str = time.strftime(
                "%H:%M:%S", time.localtime(entry.get("ts", 0))
            )
            tag = entry.get("tag", "?")[:16]
            source = entry.get("source_agent", "?")[:12]
            content = entry.get("content", "")[:40]
            conf = float(entry.get("confidence", 0.0))
            lines.append(
                f"[dim]{ts_str}[/dim]  [bold]{tag}[/bold]"
                f"  {source}  {conf:.1f}  {content}"
            )

        panel.update("\n".join(lines))

    def _render_signal_log(self, snap: "CollectiveSnapshot") -> None:
        """Render the signal flow log (REQ-TUI-035)."""
        panel = self.query_one("#signal-log-panel", Static)
        if not snap.signal_flow_log:
            panel.update("[dim]No signals received yet.[/dim]")
            return

        lines: list[str] = []
        for entry in reversed(snap.signal_flow_log[-30:]):
            ts_str = time.strftime(
                "%H:%M:%S", time.localtime(entry.get("ts", 0))
            )
            sig_type = entry.get("signal_type", "?")[:16]
            agent = entry.get("agent_id", "")[:12]
            key_field = entry.get("key_field", "")[:20]
            lines.append(
                f"[dim]{ts_str}[/dim]  [yellow]{sig_type}[/yellow]"
                f"  {agent}  {key_field}"
            )

        panel.update("\n".join(lines))

    def _render_episode_nominees(self, snap: "CollectiveSnapshot") -> None:
        """Render episode nomination queue (REQ-TUI-036)."""
        table = self.query_one("#episode-table", DataTable)
        table.clear()

        for entry in snap.episode_nominees:
            episode_id = entry.get("episode_id", "?")[:16]
            agent_id = entry.get("agent_id", "?")[:12]
            score = float(entry.get("score", 0.0))
            task_type = entry.get("task_type", "?")[:16]
            status = entry.get("status", "PENDING")

            score_colour = "green" if score >= 0.85 else "yellow" if score >= 0.70 else "red"
            table.add_row(
                episode_id,
                agent_id,
                f"[{score_colour}]{score:.2f}[/{score_colour}]",
                task_type,
                status,
            )

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)
