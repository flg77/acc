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
import logging
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import Footer, Label, ProgressBar, Static

from acc.tui.widgets.agent_card import AgentCard
from acc.tui.widgets.nav_bar import NavigationBar, NavScreen

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

logger = logging.getLogger("acc.tui.screens.dashboard")


class DashboardScreen(NavScreen):
    """Soma — live ACC collective dashboard: agent grid + governance + memory."""

    BINDINGS = [
        ("r", "refresh_subscription", "Refresh"),
    ]

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="soma", id="nav")
        yield Label("ACC Soma — Collective Health Dashboard", id="dashboard-title")
        # 033 WS-F — operator-mode (dev/prod) security-floor badge.
        # Populated at mount via load_operator_mode(); dev is surfaced
        # loudly because it relaxes the signing/auth/secret floors.
        yield Static(id="dashboard-mode-badge")

        with Horizontal(id="main-row"):
            # Left: agent cards
            with ScrollableContainer(id="agents-panel"):
                yield Label("AGENTS", classes="panel-label")
                yield Vertical(id="agent-grid")

            # Right: governance + compliance health + memory + LLM metrics
            with Vertical(id="right-panels"):
                # Governance counters
                # Proposal 003 PR-5 — each row now carries a one-line
                # definition pulled from a single GOVERNANCE_TAXONOMY
                # constant (NOT view-hardcoded — defined at module
                # bottom).  Operators previously got raw counters with
                # no explanation; now they get context too.
                with Vertical(id="governance-panel", classes="info-panel"):
                    yield Label("GOVERNANCE", classes="panel-label")
                    yield Static(id="gov-cat-a")
                    yield Static(id="gov-cat-a-def", classes="gov-definition")
                    yield Static(id="gov-cat-b")
                    yield Static(id="gov-cat-b-def", classes="gov-definition")
                    yield Static(id="gov-cat-c")
                    yield Static(id="gov-cat-c-def", classes="gov-definition")
                    # Compliance health score bar (REQ-TUI-019)
                    yield Label("COMPLIANCE HEALTH", classes="panel-label")
                    yield Static(id="compliance-health-value")
                    yield ProgressBar(
                        id="compliance-health-bar", total=100, show_eta=False
                    )

                # Proposal 003 PR-5 — token-budget-per-cluster panel.
                # Per the operator review: "Token Budget allocation
                # overview of active agent cluster."  Reads
                # snap.cluster_topology + sums member token util.
                with Vertical(id="cluster-budget-panel", classes="info-panel"):
                    yield Label(
                        "TOKEN BUDGET BY CLUSTER", classes="panel-label",
                    )
                    yield Static(id="cluster-budget-content")

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

    def on_mount(self) -> None:
        """Render the operator-mode (dev/prod) badge near the title (033 WS-F)."""
        self._render_mode_badge()

    def _render_mode_badge(self) -> None:
        """Paint the dev/prod security-floor badge into ``#dashboard-mode-badge``.

        Loaded defensively — :func:`load_operator_mode` never raises and
        falls back to ``"prod"``; a failed render is logged but never
        crashes the dashboard.
        """
        from acc.tui.config_helpers import load_operator_mode  # noqa: PLC0415
        from acc.tui.mode_badge import operator_mode_markup  # noqa: PLC0415

        self._operator_mode = load_operator_mode()
        try:
            self.query_one("#dashboard-mode-badge", Static).update(
                operator_mode_markup(self._operator_mode)
            )
        except Exception:
            logger.exception("dashboard: render mode badge failed")

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
        self._render_cluster_budgets(snap)

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
        # Counts on the primary line.
        self.query_one("#gov-cat-a", Static).update(
            f"[bold]Cat-A triggers[/bold]   {snap.total_cat_a_triggers:>4}"
        )
        self.query_one("#gov-cat-b", Static).update(
            f"[bold]Cat-B deviations[/bold] {snap.total_cat_b_deviations:>4}"
        )
        self.query_one("#gov-cat-c", Static).update(
            f"[bold]Cat-C rules[/bold]      {snap.total_cat_c_rules:>4}"
        )
        # Proposal 003 PR-5 — definition rows.  Source = a single
        # GOVERNANCE_TAXONOMY constant at module bottom so the
        # taxonomy text is editable in one place, not hard-coded in
        # the view.
        self.query_one("#gov-cat-a-def", Static).update(
            f"[dim]{GOVERNANCE_TAXONOMY['cat_a']}[/dim]"
        )
        self.query_one("#gov-cat-b-def", Static).update(
            f"[dim]{GOVERNANCE_TAXONOMY['cat_b']}[/dim]"
        )
        self.query_one("#gov-cat-c-def", Static).update(
            f"[dim]{GOVERNANCE_TAXONOMY['cat_c']}[/dim]"
        )

    def _render_cluster_budgets(self, snap: "CollectiveSnapshot") -> None:
        """Render per-cluster token budget rollup.

        Proposal 003 PR-5.  Joins ``snap.cluster_topology`` (cluster
        membership) with ``snap.agents`` (per-agent
        token_budget_utilization) to produce one row per active
        cluster.  Empty state shows a calm placeholder.

        ``token_budget_utilization`` is a 0.0-1.0 fraction per agent;
        the rollup shows the AVERAGE across cluster members (not the
        sum — sum would exceed 100% for a 3-agent cluster) plus the
        worst single agent so the operator can spot outliers.
        """
        try:
            target = self.query_one("#cluster-budget-content", Static)
        except Exception:
            return
        topology = getattr(snap, "cluster_topology", {}) or {}
        if not topology:
            target.update("[dim]No active agent clusters.[/dim]")
            return

        lines: list[str] = []
        for cluster_id, row in topology.items():
            target_role = row.get("target_role", "?")
            members = row.get("members", {}) or {}
            utils: list[float] = []
            for agent_id in members:
                agent = snap.agents.get(agent_id)
                if agent is None:
                    continue
                utils.append(float(agent.token_budget_utilization))
            if not utils:
                continue
            avg = sum(utils) / len(utils)
            worst = max(utils)
            avg_pct = avg * 100
            worst_pct = worst * 100
            colour = (
                "red" if worst >= 0.90
                else "yellow" if worst >= 0.75
                else "green"
            )
            lines.append(
                f"[cyan]{cluster_id[:10]}[/cyan] · [bold]{target_role}[/bold] · "
                f"{len(utils)} agents  "
                f"[{colour}]avg {avg_pct:>4.0f}%  worst {worst_pct:>4.0f}%[/{colour}]"
            )
        if not lines:
            target.update(
                "[dim]Clusters present but no token telemetry yet.[/dim]"
            )
            return
        target.update("\n".join(lines))

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


# Proposal 003 PR-5 — operator-facing one-line definitions for the
# three governance categories.  Rendered alongside the raw counters
# on the Soma / Dashboard screen so the operator gets context, not
# just numbers.  Edit here, not in the view.
GOVERNANCE_TAXONOMY: dict[str, str] = {
    "cat_a": (
        "constitutional — hard rules; "
        "violation blocks the LLM call (e.g. A-017 skill outside allow-list)"
    ),
    "cat_b": (
        "operational — soft setpoints; "
        "deviation degrades compliance_health but does not block (e.g. token budget)"
    ),
    "cat_c": (
        "learned — promoted from episodic patterns; "
        "operator-reviewed before becoming Cat-A or Cat-B"
    ),
}
