"""ACC TUI — PerformanceScreen: queue depth, backpressure, task progress, latency.

All data sourced exclusively from CollectiveSnapshot built by NATSObserver.

Displays (REQ-TUI-028 – REQ-TUI-032):
  - Per-agent queue depth sparkbar + numeric depth
  - Per-agent backpressure state (OPEN/THROTTLE/CLOSED) with colour coding
  - Current TASK_PROGRESS step label and count (current/total)
  - Per-agent token budget utilisation bar with amber warning ≥ 75%
  - Collective latency percentiles (p50, p90, p95, p99)

This screen imports only from acc.tui.models and acc.tui.widgets (REQ-TUI-051).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Label, Static

from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import AgentSnapshot, CollectiveSnapshot

# Backpressure colours
_BP_COLOUR = {
    "OPEN": "green",
    "THROTTLE": "yellow",
    "CLOSED": "red",
}


class PerformanceScreen(Screen):
    """Agent performance monitoring screen (REQ-TUI-028 – REQ-TUI-032)."""

    BINDINGS = [
        ("q", "app.quit", "Quit"),
        ("1", "navigate('soma')", "Soma"),
        ("2", "navigate('nucleus')", "Nucleus"),
        ("3", "navigate('compliance')", "Compliance"),
        ("4", "navigate('comms')", "Comms"),
        ("5", "navigate('performance')", "Performance"),
        ("6", "navigate('ecosystem')", "Ecosystem"),
        ("7", "navigate('prompt')", "Prompt"),
    ]

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="performance", id="nav")
        yield Label("ACC Performance — Metabolic Rate Monitor", id="performance-title")

        with Horizontal(id="performance-main"):
            # Left: per-agent queue + backpressure + task progress
            with Vertical(id="performance-left"):
                yield Label("AGENT QUEUE & BACKPRESSURE", classes="panel-label")
                yield DataTable(id="agent-perf-table", show_cursor=False)

                yield Label("ACTIVE TASK PROGRESS", classes="panel-label")
                with ScrollableContainer(id="task-progress-container"):
                    yield Static(id="task-progress-panel")

            # Right: token budget + latency percentiles
            with Vertical(id="performance-right"):
                yield Label("TOKEN BUDGET UTILISATION", classes="panel-label")
                with ScrollableContainer(id="token-budget-container"):
                    yield Static(id="token-budget-panel")

                yield Label("COLLECTIVE LATENCY PERCENTILES", classes="panel-label")
                yield Static(id="latency-percentiles-panel")

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#agent-perf-table", DataTable)
        table.add_columns(
            "Agent", "Role", "Queue", "▐", "Backpressure"
        )

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        if snap is None:
            return
        self._render_agent_perf_table(snap)
        self._render_task_progress(snap)
        self._render_token_budgets(snap)
        self._render_latency_percentiles(snap)

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _render_agent_perf_table(self, snap: "CollectiveSnapshot") -> None:
        """Per-agent queue depth sparkbar + backpressure (REQ-TUI-028, REQ-TUI-029)."""
        table = self.query_one("#agent-perf-table", DataTable)
        table.clear()

        for agent_id, agent in snap.agents.items():
            bp_colour = _BP_COLOUR.get(agent.backpressure_state, "green")
            bp_cell = f"[{bp_colour}]{agent.backpressure_state}[/{bp_colour}]"

            table.add_row(
                agent_id[:16],
                agent.role[:12],
                str(agent.queue_depth),
                agent.queue_sparkbar,
                bp_cell,
            )

    def _render_task_progress(self, snap: "CollectiveSnapshot") -> None:
        """Per-agent TASK_PROGRESS step label and step count (REQ-TUI-030)."""
        lines: list[str] = []
        for agent_id, agent in snap.agents.items():
            if agent.total_task_steps == 0:
                continue
            bar_filled = int(
                (agent.current_task_step / max(agent.total_task_steps, 1)) * 20
            )
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            label = agent.task_progress_label[:24] if agent.task_progress_label else ""
            lines.append(
                f"[bold]{agent_id[:14]}[/bold]\n"
                f"  [{bar}] {agent.current_task_step}/{agent.total_task_steps}"
                + (f"  {label}" if label else "")
            )

        if not lines:
            lines = ["[dim]No tasks in progress.[/dim]"]

        self.query_one("#task-progress-panel", Static).update("\n".join(lines))

    def _render_token_budgets(self, snap: "CollectiveSnapshot") -> None:
        """Per-agent token budget utilisation bars (REQ-TUI-031)."""
        lines: list[str] = []
        for agent_id, agent in snap.agents.items():
            pct = agent.token_budget_utilization * 100
            filled = int(pct / 100 * 20)
            bar = "█" * filled + "░" * (20 - filled)

            if pct >= 75.0:
                bar_str = f"[yellow][{bar}][/yellow]  [yellow]{pct:>4.0f}%[/yellow] ⚠"
            else:
                bar_str = f"[{bar}]  {pct:>4.0f}%"

            lines.append(f"[bold]{agent_id[:14]}[/bold]  {bar_str}")

        if not lines:
            lines = ["[dim]No agents observed.[/dim]"]

        self.query_one("#token-budget-panel", Static).update("\n".join(lines))

    def _render_latency_percentiles(self, snap: "CollectiveSnapshot") -> None:
        """Collective latency p50/p90/p95/p99 (REQ-TUI-032)."""
        p = snap.latency_percentiles()

        lines = [
            f"p50   {p['p50']:>7.1f} ms",
            f"p90   {p['p90']:>7.1f} ms",
            f"p95   {p['p95']:>7.1f} ms",
            f"p99   {p['p99']:>7.1f} ms",
        ]
        self.query_one("#latency-percentiles-panel", Static).update(
            "\n".join(lines)
        )

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)
