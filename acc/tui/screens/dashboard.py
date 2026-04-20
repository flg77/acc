"""ACC TUI — DashboardScreen: live collective metrics view.

All data is sourced exclusively from NATS payloads via NATSObserver.
Updates are driven by incoming messages — no polling timer required (REQ-DASH-005).
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, Static

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot, AgentSnapshot

# State indicator symbols
_ACTIVE_DOT = "●"
_STALE_DOT = "○"


class AgentCard(Static):
    """Widget rendering a single agent's live state."""

    DEFAULT_CSS = """
    AgentCard {
        border: solid $primary;
        padding: 0 1;
        margin: 0 1 1 0;
        min-width: 26;
        max-width: 30;
        height: auto;
    }
    AgentCard .stale {
        color: $text-muted;
    }
    """

    def __init__(self, agent_id: str, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        self._agent_id = agent_id
        self._content = ""

    def refresh_from_snapshot(self, snap: "AgentSnapshot") -> None:
        """Re-render card content from *snap*."""
        state = snap.display_state
        dot = _ACTIVE_DOT if state == "ACTIVE" else _STALE_DOT
        color_class = "" if state == "ACTIVE" else " stale"

        lines = [
            f"[bold]{snap.agent_id[:20]}[/bold]",
            f"{dot} {state}",
            f"drift  {snap.drift_score:.2f} {snap.drift_sparkbar}",
            f"ladder {snap.ladder_label}",
            f"lat    {snap.last_task_latency_ms:.0f}ms",
        ]
        self.update("\n".join(lines))


class DashboardScreen(Screen):
    """Live ACC collective dashboard — agent grid + governance + memory + LLM panels."""

    BINDINGS = [
        ("tab", "switch_to_infuse", "Infuse"),
        ("r", "refresh_subscription", "Refresh"),
        ("q", "app.quit", "Quit"),
    ]

    # Reactive snapshot — watch_snapshot re-renders all panels (REQ-DASH-005)
    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Label("ACC Collective Dashboard", id="dashboard-title")

        with Horizontal(id="main-row"):
            # Left: agent cards
            with ScrollableContainer(id="agents-panel"):
                yield Label("AGENTS", classes="panel-label")
                yield Vertical(id="agent-grid")

            # Right: governance + memory + LLM panels
            with Vertical(id="right-panels"):
                # Governance
                with Vertical(id="governance-panel", classes="info-panel"):
                    yield Label("GOVERNANCE", classes="panel-label")
                    yield Static(id="gov-cat-a")
                    yield Static(id="gov-cat-b")
                    yield Static(id="gov-cat-c")

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

    # ------------------------------------------------------------------
    # Reactive watcher
    # ------------------------------------------------------------------

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        """Re-render all panels when the snapshot changes (REQ-DASH-005)."""
        if snap is None:
            return

        self._render_agent_grid(snap)
        self._render_governance(snap)
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
        """Rebuild the agent card grid from *snap* (REQ-DASH-001)."""
        grid = self.query_one("#agent-grid", Vertical)
        # Update or create cards
        existing_ids = {w._agent_id for w in grid.query(AgentCard)}
        incoming_ids = set(snap.agents.keys())

        # Remove cards for agents no longer in snapshot
        for card in grid.query(AgentCard):
            if card._agent_id not in incoming_ids:
                card.remove()

        # Add cards for new agents
        for agent_id in incoming_ids - existing_ids:
            grid.mount(AgentCard(agent_id=agent_id, id=f"card-{_safe_id(agent_id)}"))

        # Update all existing cards
        for card in grid.query(AgentCard):
            agent_snap = snap.agents.get(card._agent_id)
            if agent_snap:
                card.refresh_from_snapshot(agent_snap)

    def _render_governance(self, snap: "CollectiveSnapshot") -> None:
        """Update governance panel (REQ-DASH-002)."""
        self.query_one("#gov-cat-a", Static).update(
            f"Cat-A triggers   {snap.total_cat_a_triggers:>4}"
        )
        self.query_one("#gov-cat-b", Static).update(
            f"Cat-B deviations {snap.total_cat_b_deviations:>4}"
        )
        self.query_one("#gov-cat-c", Static).update(
            f"Cat-C rules      {snap.total_cat_c_rules:>4}"
        )

    def _render_memory(self, snap: "CollectiveSnapshot") -> None:
        """Update memory panel (REQ-DASH-003)."""
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
        """Update LLM metrics panel (REQ-DASH-004)."""
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
    # Actions (REQ-DASH-007)
    # ------------------------------------------------------------------

    def action_switch_to_infuse(self) -> None:
        self.app.switch_screen("infuse")

    def action_refresh_subscription(self) -> None:
        """Request the app to re-subscribe to NATS."""
        self.app.post_message(_RefreshMessage())


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
