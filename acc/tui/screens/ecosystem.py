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

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Label, Static

from acc.role_loader import RoleLoader, list_roles
from acc.tui.messages import RolePreloadMessage
from acc.tui.path_resolution import resolve_manifest_root
from acc.tui.widgets.file_picker import FilePickerModal
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

logger = logging.getLogger("acc.tui.screens.ecosystem")


def _roles_root() -> Path:
    """Resolve the roles/ directory — respects ACC_ROLES_ROOT, falls back to
    the repo-anchored ``<repo>/roles`` so the screen works whether the TUI
    runs from inside the repo, from a pip-installed entry point, or from a
    container with ``WORKDIR=/app``."""
    return resolve_manifest_root("ACC_ROLES_ROOT", "roles")


def _skills_root() -> Path:
    """Resolve the skills/ directory.  Same fallback chain as :func:`_roles_root`."""
    return resolve_manifest_root("ACC_SKILLS_ROOT", "skills")


def _mcps_root() -> Path:
    """Resolve the mcps/ directory.  Same fallback chain as :func:`_roles_root`."""
    return resolve_manifest_root("ACC_MCPS_ROOT", "mcps")


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
        # PR-A2 — which upload kind is currently in-flight, set by the
        # button handler before pushing the FilePickerModal so the
        # FileSelected handler knows whether to copy into skills/ or
        # mcps/.  ``""`` means no upload pending.
        self._pending_upload_kind: str = ""

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
                # PR-A2: header row with an Upload button next to the title.
                with Horizontal(classes="panel-header-row"):
                    yield Label("SKILLS", classes="panel-label")
                    yield Button(
                        "Upload skill",
                        id="btn-upload-skill",
                        variant="default",
                        classes="panel-header-button",
                    )
                yield DataTable(id="skills-table")

                # Phase 4.4 — live MCP servers table replaces the roadmap.
                with Horizontal(classes="panel-header-row"):
                    yield Label("MCP SERVERS", classes="panel-label")
                    yield Button(
                        "Upload MCP",
                        id="btn-upload-mcp",
                        variant="default",
                        classes="panel-header-button",
                    )
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

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        """Cursor moved over a role row — populate ROLE DETAIL live.

        Textual's DataTable fires ``RowSelected`` only on Enter / mouse
        click.  Pre-PR-A the operator had to know to press Enter to
        see a role's definition; with this handler the detail panel
        updates as the cursor scrolls, matching how every other
        spreadsheet-style UI behaves.

        ``RowHighlighted`` is also fired on the initial mount of the
        table, so the operator sees a populated detail panel before
        clicking anything.
        """
        if event.data_table.id != "role-table":
            return
        role_name = self._extract_role_name(event.row_key)
        if not role_name:
            return
        # Set _selected_role FIRST so a downstream detail-render failure
        # doesn't leave the Schedule-infusion button disabled.  PR-A's
        # central UX invariant: highlighting a row arms the button.
        self._selected_role = role_name
        self._arm_infusion_button(role_name)
        self._show_role_detail(role_name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter pressed on a role row — pin the selection.

        Same effect as :meth:`on_data_table_row_highlighted` today, but
        kept distinct so a future enhancement (e.g. "double-click to
        infuse immediately") has a hook.
        """
        if event.data_table.id != "role-table":
            return
        role_name = self._extract_role_name(event.row_key)
        if not role_name:
            return
        self._selected_role = role_name
        self._arm_infusion_button(role_name)
        self._show_role_detail(role_name)

    @staticmethod
    def _extract_role_name(row_key) -> str:
        """Pull the role-name string out of a DataTable RowKey.

        Textual's RowKey is an opaque sentinel with a ``.value`` attribute
        carrying the key we set in ``_load_roles``.  Older Textual versions
        return a bare string instead.  We accept both shapes.  Returns an
        empty string for unrecognised inputs so the caller can early-exit
        without raising.
        """
        if row_key is None:
            return ""
        candidate = getattr(row_key, "value", None)
        if candidate is None:
            candidate = str(row_key)
        return str(candidate) if candidate else ""

    def _arm_infusion_button(self, role_name: str) -> None:
        """Enable the Schedule-infusion button + update the hint label.

        Split out so both row-highlight and row-select paths reuse the
        same logic.  Logs (rather than swallows) any widget-lookup
        failure so future regressions are debuggable from the TUI log
        file rather than invisible.
        """
        try:
            btn = self.query_one("#btn-schedule-infusion", Button)
            btn.disabled = False
            hint = self.query_one("#infusion-hint", Static)
            hint.update(f"[dim]Selected: [b]{role_name}[/b][/dim]")
        except Exception:
            logger.exception(
                "ecosystem: failed to arm Schedule-infusion button "
                "for role=%r",
                role_name,
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch by button id.

        Three buttons live on this screen post-PR-A2:

        * ``btn-schedule-infusion`` — preload the selected role into Nucleus.
        * ``btn-upload-skill`` — open the FilePickerModal targeting
          ``skill.yaml`` (PR-A2).
        * ``btn-upload-mcp`` — open the FilePickerModal targeting
          ``mcp.yaml`` (PR-A2).
        """
        bid = event.button.id or ""
        if bid == "btn-schedule-infusion":
            self._handle_schedule_infusion()
        elif bid == "btn-upload-skill":
            self._open_upload_picker(
                kind="skill",
                target_filename="skill.yaml",
                title="Upload a skill — pick the directory's skill.yaml",
            )
        elif bid == "btn-upload-mcp":
            self._open_upload_picker(
                kind="mcp",
                target_filename="mcp.yaml",
                title="Upload an MCP server — pick the directory's mcp.yaml",
            )

    def _handle_schedule_infusion(self) -> None:
        """Schedule-infusion handler split out for clarity."""
        if not self._selected_role:
            self.notify(
                "Highlight or click a role row first",
                severity="warning",
                timeout=4.0,
            )
            return
        # Post to the App; the App routes to InfuseScreen and switches.
        self.app.post_message(RolePreloadMessage(self._selected_role))

    # ------------------------------------------------------------------
    # PR-A2 — Upload flow
    # ------------------------------------------------------------------

    def _open_upload_picker(
        self, *, kind: str, target_filename: str, title: str,
    ) -> None:
        """Push a :class:`FilePickerModal` and remember which kind is pending.

        Args:
            kind: ``"skill"`` or ``"mcp"`` — read by
                :meth:`on_file_picker_modal_file_selected` to decide
                which manifest root to copy into.
            target_filename: Filename the modal requires
                (``skill.yaml`` or ``mcp.yaml``).
            title: Header text rendered inside the modal.
        """
        self._pending_upload_kind = kind
        modal = FilePickerModal(
            target_filename=target_filename,
            title=title,
        )
        self.app.push_screen(modal)

    def on_file_picker_modal_file_selected(
        self, message: FilePickerModal.FileSelected
    ) -> None:
        """Receive the picker's confirm and copy the parent directory.

        The operator selects e.g. ``~/my_new_skill/skill.yaml``; we
        copy the ENTIRE parent directory (``~/my_new_skill``) into the
        target manifest root, preserving co-resident files like
        ``adapter.py``.  Skills + MCPs follow the same workflow even
        though MCPs typically have only the manifest file.

        After the copy succeeds we re-run the corresponding loader so
        the table refreshes without a full screen reload.  Errors are
        surfaced as warning toasts AND logged so the operator sees a
        diagnostic and the rotating TUI log keeps the trace.
        """
        kind = self._pending_upload_kind
        self._pending_upload_kind = ""  # consume; reset for next round
        if not kind:
            logger.warning(
                "ecosystem: file_selected with no pending upload kind — "
                "ignoring (path=%s)",
                message.path,
            )
            return

        source_dir = message.path.parent
        if kind == "skill":
            target_root = _skills_root()
        elif kind == "mcp":
            target_root = _mcps_root()
        else:
            logger.error("ecosystem: unknown upload kind %r", kind)
            return

        # Refuse uploads that would clobber existing manifests rather
        # than risk silent data loss.  The operator can delete + retry
        # if they really mean to overwrite — explicit beats implicit.
        target_dir = target_root / source_dir.name
        if target_dir.exists():
            self.notify(
                f"{kind} '{source_dir.name}' already exists at {target_dir} — "
                f"remove it first to overwrite",
                severity="warning",
                timeout=6.0,
            )
            return

        try:
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, target_dir)
        except Exception:
            logger.exception(
                "ecosystem: copytree %s → %s failed", source_dir, target_dir,
            )
            self.notify(
                f"Upload failed — see TUI log for details",
                severity="error",
                timeout=6.0,
            )
            return

        logger.info(
            "ecosystem: uploaded %s '%s' to %s", kind, source_dir.name, target_dir,
        )
        self.notify(
            f"Uploaded {kind} '{source_dir.name}'",
            severity="information",
            timeout=4.0,
        )

        # Refresh just the affected table rather than re-running every
        # loader.  ``_load_skills`` / ``_load_mcps`` are idempotent
        # post-PR-A2 (they clear the table before repopulating).
        if kind == "skill":
            self._load_skills()
        else:
            self._load_mcps()

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

        Idempotent post-PR-A2: clears the table first so the upload
        flow can call this to refresh after copying a new manifest in.

        Lazy import keeps the TUI startup time unchanged when the
        skills package is absent (e.g. minimal CLI image).  Errors
        are surfaced as a single guidance row so an empty skills/
        directory doesn't crash the screen.
        """
        table = self.query_one("#skills-table", DataTable)
        table.clear()
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
        """Discover loaded MCP servers and render them in the MCP table.

        Idempotent post-PR-A2: clears the table first so the upload
        flow can call this to refresh after copying a new manifest in.
        """
        table = self.query_one("#mcps-table", DataTable)
        table.clear()
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
