"""ACC TUI — EcosystemScreen: role library, LLM backend, Skills/MCP roadmap.

All role data loaded from the roles/ filesystem directory via list_roles() +
RoleLoader at mount time (REQ-TUI-037).  NATS-derived data (LLM backend) comes
from the CollectiveSnapshot (REQ-TUI-040).

Displays (REQ-TUI-037 – REQ-TUI-040):
  - Role DataTable (Role, Domain, Persona, Tasks count)
  - Full role.yaml content in read-only detail panel on row selection
  - Placeholder sections for Skills and MCPs (roadmap)
  - Active LLM backend configuration per agent

This screen imports only from acc.tui.models, acc.tui.widgets,
acc.role_loader, and acc.config (REQ-TUI-051).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Label, Static

from acc.role_loader import RoleLoader, list_roles
from acc.tui.messages import RolePreloadMessage
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot


def _roles_root() -> str:
    """Resolve the roles/ directory — respects ACC_ROLES_ROOT env var."""
    return os.environ.get("ACC_ROLES_ROOT", "roles")


def _skills_root() -> str:
    """Resolve the skills/ directory — respects ACC_SKILLS_ROOT env var."""
    return os.environ.get("ACC_SKILLS_ROOT", "skills")


def _mcps_root() -> str:
    """Resolve the mcps/ directory — respects ACC_MCPS_ROOT env var."""
    return os.environ.get("ACC_MCPS_ROOT", "mcps")


# Risk-level → colour mapping for Skills + MCPs DataTables.  Same
# palette as the Compliance screen so operators read one legend.
_RISK_COLOURS = {
    "LOW":      "green",
    "MEDIUM":   "yellow",
    "HIGH":     "red",
    "CRITICAL": "bold red",
}


def _risk_cell(risk_level: str) -> str:
    """Render a risk-level string with Rich markup for the DataTable."""
    colour = _RISK_COLOURS.get(risk_level, "white")
    return f"[{colour}]{risk_level}[/{colour}]"


class EcosystemScreen(Screen):
    """Genome browser — roles, LLM backends, Skills/MCP roadmap (REQ-TUI-037 – REQ-TUI-040)."""

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

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        self._role_names: list[str] = []
        # Currently-selected role row (set by on_data_table_row_selected).
        # The "Schedule infusion" button reads this to decide which role
        # to pre-load into the Nucleus form.
        self._selected_role: str = ""

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="ecosystem", id="nav")
        yield Label("ACC Ecosystem — Extracellular Matrix", id="ecosystem-title")

        with Horizontal(id="ecosystem-main"):
            # Left: role table + roadmap placeholders
            with Vertical(id="ecosystem-left"):
                yield Label("ROLE LIBRARY", classes="panel-label")
                yield DataTable(id="role-table")

                # Phase 4.4 — live Skills table replaces the roadmap stub.
                # Sourced from SkillRegistry().load_from(_skills_root())
                # at mount time; one row per loaded skill manifest.
                yield Label("SKILLS", classes="panel-label")
                yield DataTable(id="skills-table")

                # Phase 4.4 — live MCP servers table replaces the roadmap.
                yield Label("MCP SERVERS", classes="panel-label")
                yield DataTable(id="mcps-table")

            # Right: role detail panel + infusion action + LLM backends
            with Vertical(id="ecosystem-right"):
                yield Label("ROLE DETAIL", classes="panel-label")
                with ScrollableContainer(id="role-detail-container"):
                    yield Static(
                        "[dim]Select a role row to view its full definition.[/dim]",
                        id="role-detail-panel",
                    )

                # Infusion action — bridge from Ecosystem (extracellular role
                # catalogue) to Nucleus (intracellular role expression).
                # Biologically: reading DNA in the matrix → expressing it in
                # the cell's nucleus.  Disabled until a role row is selected.
                with Horizontal(id="row-infuse-action"):
                    yield Button(
                        "Schedule infusion → Nucleus",
                        id="btn-schedule-infusion",
                        variant="primary",
                        disabled=True,
                    )
                    yield Static(
                        "[dim]Select a role first[/dim]",
                        id="infusion-hint",
                    )

                yield Label("ACTIVE LLM BACKENDS", classes="panel-label")
                yield DataTable(id="llm-table", show_cursor=False)

        yield Footer()

    def on_mount(self) -> None:
        """Populate role table and LLM table columns at mount time (REQ-TUI-037)."""
        role_table = self.query_one("#role-table", DataTable)
        role_table.add_columns("Role", "Domain", "Persona", "Tasks")

        llm_table = self.query_one("#llm-table", DataTable)
        llm_table.add_columns("Agent", "Backend", "Model", "Health", "p50ms")

        # Phase 4.4 — Skills + MCP servers tables.
        skills_table = self.query_one("#skills-table", DataTable)
        skills_table.add_columns("Skill", "Version", "Risk", "Requires")
        skills_table.cursor_type = "row"

        mcps_table = self.query_one("#mcps-table", DataTable)
        mcps_table.add_columns("Server", "Transport", "Risk", "Tools")
        mcps_table.cursor_type = "row"

        self._load_roles()
        self._load_skills()
        self._load_mcps()

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Show full role.yaml content in the detail panel (REQ-TUI-038).

        Also unlocks the "Schedule infusion" button and remembers the
        selected role so the button click can route it to Nucleus.
        """
        table = event.data_table
        if table.id != "role-table":
            return
        try:
            row_key = event.row_key
            # Row key was set to the role name during _load_roles
            role_name = str(row_key.value) if hasattr(row_key, "value") else str(row_key)
            self._show_role_detail(role_name)
            self._selected_role = role_name
            # Enable the infusion button now that a role is selected
            try:
                btn = self.query_one("#btn-schedule-infusion", Button)
                btn.disabled = False
                hint = self.query_one("#infusion-hint", Static)
                hint.update(f"[dim]Selected: [b]{role_name}[/b][/dim]")
            except Exception:
                pass
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Ecosystem button presses — currently just Schedule infusion."""
        if event.button.id == "btn-schedule-infusion" and self._selected_role:
            # Post to the App; the App routes to InfuseScreen and switches.
            self.app.post_message(RolePreloadMessage(self._selected_role))

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        if snap is None:
            return
        self._render_llm_backends(snap)

    # ------------------------------------------------------------------
    # Role loading
    # ------------------------------------------------------------------

    def _load_roles(self) -> None:
        """Scan roles/ directory and populate the DataTable (REQ-TUI-037)."""
        root = _roles_root()
        self._role_names = list_roles(root)
        table = self.query_one("#role-table", DataTable)

        for role_name in self._role_names:
            loader = RoleLoader(root, role_name)
            role_def = loader.load()
            if role_def is None:
                # Role directory exists but failed to load — show minimal row
                table.add_row(role_name, "—", "—", "—", key=role_name)
                continue

            task_count = len(role_def.task_types) if role_def.task_types else 0
            table.add_row(
                role_name,
                getattr(role_def, "domain_id", "") or "—",
                role_def.persona or "—",
                str(task_count),
                key=role_name,
            )

    def _show_role_detail(self, role_name: str) -> None:
        """Render the full role.yaml content in the detail panel (REQ-TUI-038)."""
        root = _roles_root()
        role_path = Path(root) / role_name / "role.yaml"

        panel = self.query_one("#role-detail-panel", Static)
        if not role_path.exists():
            panel.update(f"[red]role.yaml not found for {role_name}[/red]")
            return

        try:
            content = role_path.read_text(encoding="utf-8")
        except OSError as exc:
            panel.update(f"[red]Read error: {exc}[/red]")
            return

        panel.update(f"[bold]{role_name}/role.yaml[/bold]\n\n{content}")

    # ------------------------------------------------------------------
    # Phase 4.4 — Skills + MCP table loading
    # ------------------------------------------------------------------

    def _load_skills(self) -> None:
        """Discover loaded skills and render them in the Skills table.

        Lazy import keeps the TUI startup time unchanged when the
        skills package is absent (e.g. minimal CLI image).  Errors
        are swallowed and replaced with a single "—" row so an empty
        skills/ directory doesn't crash the screen.
        """
        table = self.query_one("#skills-table", DataTable)
        try:
            from acc.skills import SkillRegistry  # noqa: PLC0415
        except Exception:
            table.add_row("[dim]acc.skills not available[/dim]", "—", "—", "—")
            return

        try:
            reg = SkillRegistry()
            reg.load_from(_skills_root())
        except Exception as exc:
            table.add_row(f"[red]load error: {exc}[/red]", "—", "—", "—")
            return

        manifests = reg.manifests()
        if not manifests:
            table.add_row(
                "[dim]no skills loaded — see docs/howto-skills.md[/dim]",
                "—", "—", "—",
            )
            return

        for skill_id in sorted(manifests.keys()):
            manifest = manifests[skill_id]
            table.add_row(
                skill_id,
                manifest.version,
                _risk_cell(manifest.risk_level),
                ", ".join(manifest.requires_actions) or "—",
                key=skill_id,
            )

    def _load_mcps(self) -> None:
        """Discover loaded MCP servers and render them in the MCP table."""
        table = self.query_one("#mcps-table", DataTable)
        try:
            from acc.mcp import MCPRegistry  # noqa: PLC0415
        except Exception:
            table.add_row("[dim]acc.mcp not available[/dim]", "—", "—", "—")
            return

        try:
            reg = MCPRegistry()
            reg.load_from(_mcps_root())
        except Exception as exc:
            table.add_row(f"[red]load error: {exc}[/red]", "—", "—", "—")
            return

        manifests = reg.manifests()
        if not manifests:
            table.add_row(
                "[dim]no MCP servers loaded — see docs/howto-mcp.md[/dim]",
                "—", "—", "—",
            )
            return

        for server_id in sorted(manifests.keys()):
            manifest = manifests[server_id]
            allowed = manifest.allowed_tools
            tools_cell = ", ".join(allowed) if allowed else "all"
            table.add_row(
                server_id,
                manifest.transport,
                _risk_cell(manifest.risk_level),
                tools_cell,
                key=server_id,
            )

    # ------------------------------------------------------------------
    # LLM backend rendering
    # ------------------------------------------------------------------

    def _render_llm_backends(self, snap: "CollectiveSnapshot") -> None:
        """Show active LLM backend info per agent (REQ-TUI-040)."""
        table = self.query_one("#llm-table", DataTable)
        table.clear()

        for agent_id, agent in snap.agents.items():
            if not agent.llm_backend:
                continue
            health_colour = "green" if agent.llm_health == "ok" else "red"
            table.add_row(
                agent_id[:14],
                agent.llm_backend[:10],
                agent.llm_model[:20],
                f"[{health_colour}]{agent.llm_health}[/{health_colour}]",
                f"{agent.llm_p50_latency_ms:.0f}",
            )

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)
