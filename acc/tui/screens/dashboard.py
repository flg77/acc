"""ACC TUI — DashboardScreen (Soma): live collective metrics view.

All data is sourced exclusively from NATS payloads via NATSObserver.
Updates are driven by incoming messages — no polling timer required (REQ-DASH-005).

ACC-TUI-Evolution updates:
  - NavigationBar widget mounted at top (REQ-TUI-003)
  - Uses shared AgentCard from acc.tui.widgets (REQ-TUI-018)
  - Compliance health score bar below governance panel (REQ-TUI-019)
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Label, ProgressBar, Static

from acc.tui.widgets.agent_card import AgentCard
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot


class DashboardScreen(Screen):
    """Soma — live ACC collective dashboard: agent grid + governance + memory."""

    BINDINGS = [
        ("r", "refresh_subscription", "Refresh"),
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
        yield NavigationBar(active_screen="soma", id="nav")
        yield Label("ACC Soma — Collective Health Dashboard", id="dashboard-title")

        with Horizontal(id="main-row"):
            # Left: agent cards
            with ScrollableContainer(id="agents-panel"):
                yield Label("AGENTS", classes="panel-label")
                yield Vertical(id="agent-grid")

            # Right: governance + compliance health + memory + LLM metrics
            with Vertical(id="right-panels"):
                # Governance counters
                with Vertical(id="governance-panel", classes="info-panel"):
                    yield Label("GOVERNANCE", classes="panel-label")
                    yield Static(id="gov-cat-a")
                    yield Static(id="gov-cat-b")
                    yield Static(id="gov-cat-c")
                    # Compliance health score bar (REQ-TUI-019)
                    yield Label("COMPLIANCE HEALTH", classes="panel-label")
                    yield Static(id="compliance-health-value")
                    yield ProgressBar(
                        id="compliance-health-bar", total=100, show_eta=False
                    )

                # Memory
                with Vertical(id="memory-panel", classes="info-panel"):
                    yield Label("MEMORY", classes="panel-label")
                    yield Static(id="mem-icl")
                    yield Static(id="mem-patterns")
                    yield Static(id="mem-cat-c")

                # LLM Metrics
                with Vertical(id="llm-panel", classes="info-panel"):
                    yield Label("LLM METRICS", classes="panel-label")
                    yield Static(id="llm-p95")
                    yield Static(id="llm-util")
                    yield Static(id="llm-blocked")

        yield Static(id="last-update", classes="footer-bar")
        yield Footer()

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    # ------------------------------------------------------------------
    # Reactive watcher
    # ------------------------------------------------------------------

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        """Re-render all panels when the snapshot changes (REQ-DASH-005)."""
        if snap is None:
            return

        self._render_agent_grid(snap)
        self._render_governance(snap)
        self._render_compliance_health(snap)
        self._render_memory(snap)
        self._render_llm_metrics(snap)

        ts = datetime.datetime.fromtimestamp(snap.last_updated_ts).strftime("%H:%M:%S") \
            if snap.last_updated_ts else "—"
        self.query_one("#last-update", Static).update(
            f"Last update: {ts}   Collective: {snap.collective_id}"
        )

    # ------------------------------------------------------------------
    # Panel renderers
    # ------------------------------------------------------------------

    def _render_agent_grid(self, snap: "CollectiveSnapshot") -> None:
        """Rebuild the agent card grid using the shared AgentCard widget (REQ-TUI-018)."""
        grid = self.query_one("#agent-grid", Vertical)
        existing_ids = {w._agent_id for w in grid.query(AgentCard)}
        incoming_ids = set(snap.agents.keys())

        for card in grid.query(AgentCard):
            if card._agent_id not in incoming_ids:
                card.remove()

        for agent_id in incoming_ids - existing_ids:
            grid.mount(AgentCard(agent_id=agent_id, id=f"card-{_safe_id(agent_id)}"))

        for card in grid.query(AgentCard):
            agent_snap = snap.agents.get(card._agent_id)
            if agent_snap:
                card.refresh_from_snapshot(agent_snap)

    def _render_governance(self, snap: "CollectiveSnapshot") -> None:
        self.query_one("#gov-cat-a", Static).update(
            f"Cat-A triggers   {snap.total_cat_a_triggers:>4}"
        )
        self.query_one("#gov-cat-b", Static).update(
            f"Cat-B deviations {snap.total_cat_b_deviations:>4}"
        )
        self.query_one("#gov-cat-c", Static).update(
            f"Cat-C rules      {snap.total_cat_c_rules:>4}"
        )

    def _render_compliance_health(self, snap: "CollectiveSnapshot") -> None:
        """Render compliance health score bar (REQ-TUI-019)."""
        score = snap.compliance_health_score
        pct = score * 100
        colour = "green" if score >= 0.80 else "yellow" if score >= 0.50 else "red"
        self.query_one("#compliance-health-value", Static).update(
            f"[{colour}]{score:.2f}[/{colour}]  [dim]({pct:.0f}%)[/dim]"
        )
        self.query_one("#compliance-health-bar", ProgressBar).progress = pct

    def _render_memory(self, snap: "CollectiveSnapshot") -> None:
        self.query_one("#mem-icl", Static).update(
            f"ICL episodes  {snap.icl_episode_count:>6}"
        )
        self.query_one("#mem-patterns", Static).update(
            f"Patterns      {snap.pattern_count:>6}"
        )
        self.query_one("#mem-cat-c", Static).update(
            f"Cat-C rules   {snap.total_cat_c_rules:>6}"
        )

    def _render_llm_metrics(self, snap: "CollectiveSnapshot") -> None:
        self.query_one("#llm-p95", Static).update(
            f"p95 latency   {snap.p95_latency_ms:>6.0f}ms"
        )
        self.query_one("#llm-util", Static).update(
            f"token util    {snap.avg_token_utilization * 100:>5.0f}%"
        )
        self.query_one("#llm-blocked", Static).update(
            f"blocked tasks {snap.blocked_task_count:>6}"
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh_subscription(self) -> None:
        self.app.post_message(_RefreshMessage())

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)


# ---------------------------------------------------------------------------
# Internal messages
# ---------------------------------------------------------------------------

from textual.message import Message  # noqa: E402


class _RefreshMessage(Message):
    """Request NATSObserver re-subscription."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_id(agent_id: str) -> str:
    """Convert agent_id to a Textual-safe CSS id."""
    return agent_id.replace("-", "_").replace(".", "_")
