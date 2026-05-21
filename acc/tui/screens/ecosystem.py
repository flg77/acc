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

import asyncio
import logging
# shutil removed in proposal 009 (upload flow moved to Configuration pane).
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Footer,
    Input,
    Label,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from acc.role_loader import RoleLoader, list_roles
from acc.tui.messages import RolePreloadMessage, RolesChangedMessage
from acc.tui.path_resolution import resolve_manifest_root
# FilePickerModal import removed in proposal 009 (upload flow moved
# to acc/tui/screens/configuration.py).
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


# Proposal 003 PR-3 — directory-level watcher.  Polls the roles/ tree
# every WATCH_POLL_INTERVAL_S seconds and posts a RolesChangedMessage
# when the fingerprint (set of role names + per-file mtimes) changes.
#
# Polling-only (no watchdog) — keeps the dependency surface small,
# matches the existing role_loader's polling fallback semantics
# (acc/role_loader.py:L25–L30), and is portable across Windows / POSIX
# without an external lib.  The fingerprint is a sorted tuple so
# additions, removals, and modifications all produce distinct values.
WATCH_POLL_INTERVAL_S: float = 2.0
WATCH_POLL_INTERVAL_ENV: str = "ACC_TUI_ROLE_WATCH_INTERVAL_S"


def _resolve_watch_interval() -> float:
    """Return the configured role-watch poll interval in seconds.

    Reads ``ACC_TUI_ROLE_WATCH_INTERVAL_S`` from the environment when
    set; falls back to ``WATCH_POLL_INTERVAL_S``.  Tests use this
    hook to drive the watcher fast.
    """
    import os  # noqa: PLC0415
    raw = os.environ.get(WATCH_POLL_INTERVAL_ENV, "")
    if not raw:
        return WATCH_POLL_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "ecosystem: %s=%r is not a number; using default %.1fs",
            WATCH_POLL_INTERVAL_ENV, raw, WATCH_POLL_INTERVAL_S,
        )
        return WATCH_POLL_INTERVAL_S
    if value <= 0:
        logger.warning(
            "ecosystem: %s=%.1f must be > 0; using default %.1fs",
            WATCH_POLL_INTERVAL_ENV, value, WATCH_POLL_INTERVAL_S,
        )
        return WATCH_POLL_INTERVAL_S
    return value


def _fingerprint_roles_dir(roles_root: Path) -> tuple:
    """Cheap fingerprint of the roles/ tree.

    Returns a sorted tuple of ``(role_name, role.yaml mtime,
    role.md mtime)`` per existing role directory.  ``mtime`` is 0.0
    when the file is absent — so adding a role.md is reflected, not
    silently identical to "role had no role.md before."

    Errors short-circuit to an empty tuple so a transient stat
    failure doesn't spam the bus with spurious changes.
    """
    if not roles_root.is_dir():
        return tuple()
    out: list[tuple[str, float, float]] = []
    try:
        for child in roles_root.iterdir():
            if not child.is_dir() or child.name in _EXCLUDED_NAMES:
                continue
            yaml_path = child / "role.yaml"
            md_path = child / "role.md"
            if not yaml_path.exists():
                continue
            try:
                yaml_mtime = yaml_path.stat().st_mtime
            except OSError:
                yaml_mtime = 0.0
            try:
                md_mtime = md_path.stat().st_mtime if md_path.exists() else 0.0
            except OSError:
                md_mtime = 0.0
            out.append((child.name, yaml_mtime, md_mtime))
    except OSError:
        return tuple()
    return tuple(sorted(out))


# Mirrors role_loader._EXCLUDED_ROLE_NAMES — duplicated here to avoid
# a cross-module import path for one constant.
_EXCLUDED_NAMES = frozenset({"_base", "TEMPLATE"})


# Proposal 007 — $EDITOR resolution for in-pane role editing.

import os  # noqa: PLC0415,E402
import shlex  # noqa: PLC0415,E402
import subprocess  # noqa: PLC0415,E402


def _resolve_editor_command(file_path: str) -> list[str]:
    """Return the argv list to spawn an editor on ``file_path``.

    Resolution order:

    1. ``$EDITOR`` from the environment, split via :func:`shlex.split`
       so values like ``"code --wait"`` work.
    2. ``$VISUAL`` (POSIX convention) as a fallback.
    3. Platform default — ``notepad`` on Windows, ``vi`` otherwise.

    The file path is appended as the final argument.
    """
    editor = os.environ.get("EDITOR", "").strip()
    if not editor:
        editor = os.environ.get("VISUAL", "").strip()
    if not editor:
        editor = "notepad" if os.name == "nt" else "vi"
    cmd = shlex.split(editor)
    cmd.append(file_path)
    return cmd


def _spawn_editor(cmd: list[str]) -> None:
    """Spawn the editor without blocking the TUI.

    Uses ``Popen`` with detached I/O so the editor process is fully
    independent — the operator can switch terminals / close the
    editor without the TUI being aware.
    """
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def _read_role_md(md_path: Path, role_name: str) -> str:
    """Return the role's ``role.md`` body, or a friendly placeholder.

    Proposal 003 PR-2 helper.  Roles without a ``role.md`` show a
    short note explaining that narrative authoring is optional and
    pointing operators at the convention.  Read errors surface as
    inline italics in the rendered Markdown (won't crash the
    screen).
    """
    if not md_path.exists():
        return (
            f"_No `role.md` authored for **{role_name}** yet._\n\n"
            "Add `roles/" + role_name + "/role.md` to surface "
            "operator-facing narrative here: what this role is for, "
            "when to pick it, example prompts, anti-patterns.  See "
            "proposal 006 for the authoring guideline."
        )
    try:
        return md_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"_Could not read `role.md`: `{exc}`._"


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


def _subrole_siblings(
    roles_root: Path, role_name: str,
) -> tuple[list[str], str]:
    """Return ``(siblings, source_label)`` for the role's children.

    Two-pass lookup:

    1. **Declared** — scan every role.yaml; collect those whose
       ``role_definition.parent_role`` equals ``role_name``.
       Proposal 004 — first-class hierarchy.
    2. **Directory-derived (legacy)** — if no role declares this
       parent, fall back to the directory-name glob from
       proposal 003 PR-6 so unmigrated roles still surface.

    Returns ``([role_names…], "declared" | "directory-derived")``.
    Empty list + label "" when nothing matches.

    Excludes ``_base`` / ``TEMPLATE`` and the parent itself.
    """
    if not roles_root.is_dir() or not role_name:
        return [], ""

    # Pass 1 — declared parent_role.
    declared: list[str] = []
    try:
        import yaml  # noqa: PLC0415
        for child in roles_root.iterdir():
            if (
                not child.is_dir()
                or child.name in _EXCLUDED_NAMES
                or child.name == role_name
            ):
                continue
            yaml_path = child / "role.yaml"
            if not yaml_path.exists():
                continue
            try:
                with yaml_path.open("r", encoding="utf-8") as fh:
                    doc = yaml.safe_load(fh)
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            role_def = doc.get("role_definition") or {}
            if not isinstance(role_def, dict):
                continue
            if role_def.get("parent_role") == role_name:
                declared.append(child.name)
    except Exception:
        logger.exception("ecosystem: declared-parent scan failed")

    if declared:
        return sorted(declared), "declared"

    # Pass 2 — directory-name glob fallback (proposal 003 PR-6).
    prefix = f"{role_name}_"
    try:
        siblings = sorted(
            child.name for child in roles_root.iterdir()
            if (
                child.is_dir()
                and child.name.startswith(prefix)
                and child.name not in _EXCLUDED_NAMES
                and (child / "role.yaml").exists()
            )
        )
    except OSError:
        return [], ""
    return siblings, ("directory-derived" if siblings else "")


def _format_subrole_section(
    siblings: list[str], role_name: str, source: str = "declared",
) -> str:
    """Render the "Subroles" markdown section.

    Proposal 004 — accepts a ``source`` label so the caller's
    two-pass lookup (declared vs directory-derived) is reflected
    in the rendered text.

    Empty list → returns empty string (caller skips appending).
    """
    if not siblings:
        return ""
    if source == "declared":
        heading = f"## Subroles of `{role_name}` (declared)"
        note = (
            "_Joined via `role_definition.parent_role` in each "
            "subrole's role.yaml — first-class hierarchy per "
            "proposal 004._"
        )
    else:
        heading = f"## Subroles of `{role_name}` (directory-derived)"
        note = (
            "_Listed by directory-name convention `"
            + role_name
            + "_*`.  Migrate to declared parent_role per proposal 004._"
        )
    lines = ["", "", heading, "", note, ""]
    for s in siblings:
        if s.startswith(f"{role_name}_"):
            suffix = s[len(role_name) + 1 :]
            lines.append(f"- **{s}** — `{suffix}` persona")
        else:
            lines.append(f"- **{s}**")
    return "\n".join(lines)


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
        ("7", "navigate('prompt')", "Prompt"),
        ("8", "navigate('configuration')", "Configuration"),
    ]

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        self._role_names: list[str] = []
        # Currently-selected role row (set by on_data_table_row_selected).
        # The "Schedule infusion" button reads this to decide which role
        # to pre-load into the Nucleus form.
        self._selected_role: str = ""
        # PR-A — inline role.yaml editor dirty flag.  True iff the
        # TextArea contents differ from the last on-disk snapshot
        # (loaded into `_last_saved_yaml_text`).  Computed in
        # `on_text_area_changed`; checked by the file-watcher refresh
        # path so an external `$EDITOR` save doesn't clobber unsaved
        # edits.
        self._yaml_dirty: bool = False
        self._last_saved_yaml_text: str = ""
        # Proposal 009 — _pending_upload_kind state removed (Upload
        # flow moved to the Configuration pane).
        # Proposal 003 PR-2 — cached role-row data so the search filter
        # can repopulate the DataTable without re-reading disk on every
        # keystroke.  Populated by _load_roles(); list of tuples
        # (role_name, domain, persona, task_count_str).
        self._all_role_rows: list[tuple[str, str, str, str]] = []

        # Proposal 003 PR-3 — file-watcher state.
        # Background polling task; cancelled in on_unmount.
        self._watch_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
        # Last fingerprint we observed; used by the poll loop to decide
        # whether to post a RolesChangedMessage.
        self._last_role_fingerprint: tuple = tuple()
        # Advisory file lock on the currently-selected role's yaml +
        # md.  ``filelock.FileLock``-shaped (or ``None`` when no lock
        # is held / lock library unavailable).
        self._selection_lock: Any = None
        self._selection_lock_role: str = ""

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="ecosystem", id="nav")
        yield Label("ACC Ecosystem — Extracellular Matrix", id="ecosystem-title")

        # PR-C — wrap the screen in TabbedContent so the operator can
        # toggle between the existing "Roles" library view (left+right
        # panes, infusion path) and the new "Agentset" view (declarative
        # collective.yaml editor + reconcile-Apply).
        with TabbedContent(id="ecosystem-tabs"):
            with TabPane("Roles", id="tab-roles"):
                yield from self._compose_roles_tab()
            with TabPane("Agentset", id="tab-agentset"):
                yield from self._compose_agentset_tab()
        yield Footer()

    def _compose_roles_tab(self) -> ComposeResult:
        """The existing Ecosystem left+right panes — role library + detail."""
        with Horizontal(id="ecosystem-main"):
            # Left: role table + roadmap placeholders
            with Vertical(id="ecosystem-left"):
                yield Label("ROLE LIBRARY", classes="panel-label")
                # Proposal 003 PR-2 — incremental substring filter on
                # role name / domain / persona.  Empty input shows all
                # rows; clearing restores the full list.
                yield Input(
                    placeholder="Filter roles (substring of name / domain / persona)…",
                    id="role-filter",
                )
                yield DataTable(id="role-table")

                # Proposal 009 — Skills + MCPs widgets moved to the
                # Configuration pane (pane 8) in proposal 003 PR-4.
                # Removed from the Ecosystem screen here after the
                # one-release migration window.

            # Right: role detail panel + infusion + edit actions
            with Vertical(id="ecosystem-right"):
                yield Label("ROLE DETAIL", classes="panel-label")
                # Proposal 003 PR-2 — split detail into two collapsibles.
                # role.md (narrative, human-authored) opens by default so
                # the operator sees prose first; role.yaml (raw machine
                # config) is opt-in.
                with ScrollableContainer(id="role-detail-container"):
                    # Proposal 010 PR-5 — role-sync badge.  Empty by
                    # default; populated from
                    # app._role_sync_listener.render_badge() on row
                    # select + on every _RoleSyncEvent broadcast.
                    yield Static("", id="role-sync-badge")
                    yield Static(
                        "[dim]Select a role row to view its definition.[/dim]",
                        id="role-detail-placeholder",
                    )
                    with Collapsible(
                        title="role.md (narrative)",
                        collapsed=False,
                        id="role-md-collapsible",
                    ):
                        yield Markdown("", id="role-md-content")
                    # PR-A — role.yaml is now always visible and
                    # inline-editable.  Operator types in the TextArea
                    # and hits Save (`btn-save-yaml`); the new contents
                    # are atomically written through
                    # `acc.tui.role_writeback.upsert_role_yaml` after a
                    # Pydantic pre-write validation pass.  The existing
                    # 2s file-watcher then picks up the change and
                    # refreshes the per-role caches downstream.
                    yield Label("role.yaml", classes="panel-label")
                    yield TextArea(
                        "",
                        id="role-yaml-editor",
                        language="yaml",
                        show_line_numbers=True,
                        soft_wrap=False,
                    )
                    yield Static("", id="yaml-save-status")

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

                # PR-A — Save the inline TextArea (atomic write, validated).
                # The "Open in $EDITOR" buttons stay as power-user fallbacks
                # for vim / emacs / VS Code workflows; PR-3's file-watcher
                # refreshes the detail pane when the external editor saves.
                with Horizontal(id="row-edit-actions"):
                    yield Button(
                        "Save role.yaml",
                        id="btn-save-yaml",
                        variant="primary",
                        disabled=True,
                    )
                    yield Button(
                        "Open role.yaml in $EDITOR",
                        id="btn-edit-yaml",
                        variant="default",
                        disabled=True,
                    )
                    yield Button(
                        "Open role.md in $EDITOR",
                        id="btn-edit-md",
                        variant="default",
                        disabled=True,
                    )

                # Proposal 009 — Active LLM Backends moved to the
                # Configuration pane (pane 8).

    def _compose_agentset_tab(self) -> ComposeResult:
        """PR-C — declarative agentset editor + reconcile-Apply.

        Mirrors the structure of the Roles tab's right-pane editor
        (PR-A pattern): a live-snapshot DataTable on top, an inline
        YAML TextArea, action buttons, and a status line that
        surfaces validation errors or apply progress.
        """
        with Vertical(id="agentset-tab"):
            yield Label(
                "AGENTSET — declarative collective.yaml.  "
                "Edit + Save to persist; Apply to reconcile podman state.",
                classes="panel-label",
            )
            yield DataTable(id="agentset-table", show_cursor=False)
            yield Label("collective.yaml", classes="panel-label")
            yield TextArea(
                "",
                id="collective-editor",
                language="yaml",
                show_line_numbers=True,
                soft_wrap=False,
            )
            with Horizontal(id="agentset-actions"):
                yield Button("Save", id="btn-collective-save",
                              variant="primary")
                yield Button("Validate", id="btn-collective-validate",
                              variant="default")
                yield Button("Apply", id="btn-collective-apply",
                              variant="success")
            yield Static("", id="agentset-status")

    def on_mount(self) -> None:
        """Populate role table and LLM table columns at mount time (REQ-TUI-037)."""
        role_table = self.query_one("#role-table", DataTable)
        role_table.add_columns("Role", "Domain", "Persona", "Tasks")

        # Proposal 009 — Skills / MCPs / LLM tables removed from
        # Ecosystem.  Their canonical home is the Configuration
        # pane (pane 8) since proposal 003 PR-4.

        # PR-C — Agentset tab.  Initialise the DataTable columns and
        # load `./collective.yaml` into the inline TextArea.  Errors
        # are tolerated (collective.yaml may be absent on a fresh
        # checkout — operator runs `./acc-deploy.sh setup` to scaffold).
        try:
            ag_table = self.query_one("#agentset-table", DataTable)
            ag_table.add_columns(
                "Role", "Replicas", "cluster_id", "purpose", "live",
            )
        except Exception:
            logger.exception("ecosystem: agentset table init failed")
        self._load_collective_into_editor()

        self._load_roles()

        # Bug-fix (post-PR-A regression): the inline role.yaml editor
        # stayed empty and the "Schedule infusion" button stayed
        # disabled on first paint because Textual's DataTable does NOT
        # fire ``RowHighlighted`` from ``add_row`` — only from cursor
        # movement / focus.  Force-render the detail for the first row
        # and arm the button up-front so the operator sees a populated
        # editor without having to click into the table.  Focusing the
        # table also makes arrow-key navigation work without an
        # initial click into the pane.
        try:
            if self._all_role_rows:
                first_role = self._all_role_rows[0][0]
                self._selected_role = first_role
                self._arm_infusion_button(first_role)
                self._show_role_detail(first_role)
                try:
                    role_table.move_cursor(row=0)
                    role_table.focus()
                except Exception:
                    logger.debug(
                        "ecosystem: initial cursor/focus failed",
                        exc_info=True,
                    )
        except Exception:
            logger.exception(
                "ecosystem: initial detail render failed; "
                "operator will need to click a row manually",
            )

        # Proposal 003 PR-3 — start the roles/ directory watcher.
        # Captures the initial fingerprint synchronously so the very
        # first poll tick doesn't post a spurious change message.
        self._last_role_fingerprint = _fingerprint_roles_dir(_roles_root())
        self._watch_task = asyncio.create_task(
            self._watch_roles_loop(),
            name="ecosystem-role-watch",
        )

    def on_unmount(self) -> None:
        """Stop the watcher + release any held selection lock.

        Proposal 003 PR-3.  Idempotent — safe to call multiple times.
        """
        if self._watch_task is not None and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = None
        self._release_selection_lock()

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
        # Proposal 003 PR-3 — advisory lock on the selected role's files.
        self._acquire_selection_lock(role_name)

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
        # Proposal 003 PR-3 — advisory lock on the selected role's files.
        self._acquire_selection_lock(role_name)

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
        """Enable the Schedule-infusion button + the Edit buttons +
        update the hint label.

        Split out so both row-highlight and row-select paths reuse
        the same logic.  Logs (rather than swallows) any widget-
        lookup failure so future regressions are debuggable from the
        TUI log file rather than invisible.

        Proposal 007 — also arms the role.yaml / role.md edit
        buttons since they live in the same selection state.
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
        # Proposal 007 — arm the edit buttons too.
        # PR-A — also arm the inline Save-yaml button (same selection
        # state — operator can edit + save the highlighted role).
        for btn_id in ("btn-edit-yaml", "btn-edit-md", "btn-save-yaml"):
            try:
                self.query_one(f"#{btn_id}", Button).disabled = False
            except Exception:
                logger.exception("ecosystem: failed to arm %s", btn_id)

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
        elif bid == "btn-save-yaml":
            self._handle_save_yaml()
        elif bid == "btn-edit-yaml":
            self._handle_edit_in_editor("role.yaml")
        elif bid == "btn-edit-md":
            self._handle_edit_in_editor("role.md")
        # PR-C — Agentset tab actions.
        elif bid == "btn-collective-save":
            self._handle_collective_save()
        elif bid == "btn-collective-validate":
            self._handle_collective_validate()
        elif bid == "btn-collective-apply":
            self._handle_collective_apply()
        # Proposal 009 — Upload skill / Upload MCP buttons moved to
        # the Configuration pane (pane 8).

    def _handle_edit_in_editor(self, filename: str) -> None:
        """Spawn $EDITOR on the selected role's ``filename``.

        Proposal 007.  Non-blocking ``Popen`` — the operator's
        editor opens in a sibling terminal / window; PR-3's
        file-watcher refreshes the detail pane when they save.
        """
        if not self._selected_role:
            self.notify(
                "Highlight or click a role row first",
                severity="warning",
                timeout=4.0,
            )
            return
        path = _roles_root() / self._selected_role / filename
        if not path.exists() and filename == "role.md":
            # Create an empty role.md on demand so the editor opens
            # a non-empty buffer rather than silently failing on
            # some editors.
            try:
                path.write_text(
                    f"# {self._selected_role}\n\n"
                    "<!-- Authored per docs/role-authoring.md -->\n",
                    encoding="utf-8",
                )
            except OSError:
                logger.exception("ecosystem: could not create %s", path)
                self.notify(
                    f"Could not create {path}",
                    severity="error", timeout=4.0,
                )
                return
        if not path.exists():
            self.notify(
                f"{filename} not found for {self._selected_role}",
                severity="error", timeout=4.0,
            )
            return
        try:
            cmd = _resolve_editor_command(str(path))
            _spawn_editor(cmd)
            self.notify(
                f"Opened {filename} for {self._selected_role} in "
                + " ".join(cmd[:-1]),
                severity="information", timeout=3.0,
            )
        except Exception as exc:
            logger.exception("ecosystem: spawn editor failed")
            self.notify(
                f"Could not launch editor: {exc}",
                severity="error", timeout=4.0,
            )

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Track inline-editor dirty-state by comparing against the
        last on-disk snapshot (PR-A).

        Set `_yaml_dirty = (text != last_saved)` rather than a naive
        bump-on-every-keystroke: the synchronous `editor.text = ...`
        the screen uses to load a fresh row queues a `Changed` event
        that fires AFTER the load, which would otherwise leave dirty
        flipped to True with no operator action.  Comparing against
        `_last_saved_yaml_text` reflects real intent.
        """
        if event.text_area.id == "role-yaml-editor":
            self._yaml_dirty = (event.text_area.text
                                 != self._last_saved_yaml_text)

    def _handle_save_yaml(self) -> None:
        """PR-A — atomically save the inline role.yaml TextArea contents.

        Validates via :func:`acc.tui.role_writeback.upsert_role_yaml`'s
        Pydantic pre-write check first; on failure surfaces a short
        bullet list of pydantic errors in `#yaml-save-status` and
        leaves the file untouched.
        """
        if not self._selected_role:
            self.notify("Highlight or click a role row first",
                        severity="warning", timeout=4.0)
            return
        try:
            editor = self.query_one("#role-yaml-editor", TextArea)
            status = self.query_one("#yaml-save-status", Static)
        except Exception:
            logger.exception("ecosystem: save handler — widgets missing")
            return

        path = _roles_root() / self._selected_role / "role.yaml"
        text = editor.text
        try:
            from acc.tui.role_writeback import (  # noqa: PLC0415
                RoleValidationError,
                upsert_role_yaml,
            )
            upsert_role_yaml(
                path,
                text,
                role_name=self._selected_role,
                roles_root=_roles_root(),
            )
        except RoleValidationError as exc:
            # Render the first few pydantic errors as a short list.
            bullets = []
            for err in (exc.errors or [])[:5]:
                loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
                bullets.append(f"  • {loc}: {err.get('msg', '')}")
            detail = "\n".join(bullets) if bullets else f"  • {exc}"
            status.update(
                f"[red]⚠ invalid role.yaml — not saved[/red]\n{detail}"
            )
            return
        except OSError as exc:
            logger.exception("ecosystem: role.yaml write failed")
            status.update(f"[red]⚠ write failed: {exc}[/red]")
            return

        self._yaml_dirty = False
        # Record the saved snapshot so the Changed-event handler can
        # compute dirty as `text != last_saved`.
        self._last_saved_yaml_text = text
        status.update(
            f"[green]✓ saved[/green] [dim]{path}[/dim]"
        )
        self.notify(
            f"Saved {self._selected_role}/role.yaml",
            severity="information", timeout=3.0,
        )

    # ------------------------------------------------------------------
    # PR-C — Agentset tab: load + edit + save + apply collective.yaml
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_collective_path() -> Path:
        """Resolve the path of `./collective.yaml`.

        Precedence: ``ACC_COLLECTIVE_PATH`` env var > ``/app/collective.yaml``
        (the canonical mount inside the acc-tui container, added in
        ``container/production/podman-compose.yml`` for PR-C) >
        ``./collective.yaml`` (cwd fallback for the host-run TUI).
        """
        explicit = os.environ.get("ACC_COLLECTIVE_PATH", "").strip()
        if explicit:
            return Path(explicit)
        container_path = Path("/app/collective.yaml")
        if container_path.is_file():
            return container_path
        return Path("collective.yaml")

    def _load_collective_into_editor(self) -> None:
        """Populate `#collective-editor` from the on-disk spec.

        Tolerates a missing file (fresh checkout) by leaving the editor
        empty and surfacing a hint in `#agentset-status`.
        """
        path = self._resolve_collective_path()
        try:
            editor = self.query_one("#collective-editor", TextArea)
            status = self.query_one("#agentset-status", Static)
        except Exception:
            return
        if not path.exists():
            editor.text = (
                "# collective.yaml not found at " + str(path) + "\n"
                "# Run `./acc-deploy.sh setup` or copy "
                "`./collective.yaml.example` to get started.\n"
            )
            status.update(
                "[yellow]no collective.yaml on disk yet[/yellow]"
            )
            self._refresh_agentset_table(None)
            return
        try:
            editor.text = path.read_text(encoding="utf-8")
        except OSError as exc:
            editor.text = f"# Read error: {exc}\n"
            status.update(f"[red]read failed: {exc}[/red]")
            return
        status.update(f"[dim]Loaded {path}[/dim]")
        # Refresh the table from the (now-on-disk) spec.
        try:
            from acc.collective import load_collective  # noqa: PLC0415
            spec = load_collective(path)
            self._refresh_agentset_table(spec)
        except Exception as exc:  # noqa: BLE001
            from rich.markup import escape  # noqa: PLC0415
            logger.exception("ecosystem: refresh agentset table failed")
            status.update(f"[yellow]spec invalid: {escape(str(exc))}[/yellow]")
            self._refresh_agentset_table(None)

    def _refresh_agentset_table(self, spec: Any) -> None:
        """Rebuild ``#agentset-table`` rows from a CollectiveSpec.

        Pass ``None`` (or an invalid spec) to clear the table.  The
        live-count column reads from ``self.snapshot`` when present;
        falls back to ``—`` otherwise.
        """
        try:
            table = self.query_one("#agentset-table", DataTable)
        except Exception:
            return
        table.clear()
        if spec is None:
            return
        # Build a role → live-count map from the current snapshot.
        live_counts: dict[str, int] = {}
        snap = self.snapshot
        if snap is not None:
            try:
                for a in snap.agents.values():
                    role = getattr(a, "role", "")
                    if role:
                        live_counts[role] = live_counts.get(role, 0) + 1
            except Exception:
                pass
        for agent in spec.agents:
            live = str(live_counts.get(agent.role, 0))
            table.add_row(
                agent.role,
                str(agent.replicas),
                agent.cluster_id or "—",
                (agent.purpose or "—")[:60],
                live,
            )

    def _handle_collective_save(self) -> bool:
        """Save the editor contents to collective.yaml after validation.

        Returns True on success, False otherwise.  Surfaces status in
        `#agentset-status` either way.  Used directly by Save and
        composed by Apply (which only proceeds on True).
        """
        try:
            editor = self.query_one("#collective-editor", TextArea)
            status = self.query_one("#agentset-status", Static)
        except Exception:
            return False
        from acc.collective import CollectiveSpec, dump_collective  # noqa: PLC0415
        import yaml as _yaml  # noqa: PLC0415

        from rich.markup import escape  # noqa: PLC0415

        text = editor.text
        try:
            data = _yaml.safe_load(text) or {}
            spec = CollectiveSpec.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            status.update(
                "[red]⚠ invalid collective.yaml — not saved[/red]\n"
                f"  • {escape(str(exc))}"
            )
            return False
        path = self._resolve_collective_path()
        try:
            dump_collective(spec, path)
        except OSError as exc:
            logger.exception("ecosystem: collective.yaml write failed")
            status.update(f"[red]⚠ write failed: {exc}[/red]")
            return False
        status.update(
            f"[green]✓ saved[/green] [dim]{path}[/dim] — "
            f"{len(spec.agents)} agent slot(s)"
        )
        self._refresh_agentset_table(spec)
        self.notify(
            f"Saved {path.name}", severity="information", timeout=3.0,
        )
        return True

    def _handle_collective_validate(self) -> None:
        """Validate the editor contents without writing."""
        try:
            editor = self.query_one("#collective-editor", TextArea)
            status = self.query_one("#agentset-status", Static)
        except Exception:
            return
        from acc.collective import CollectiveSpec  # noqa: PLC0415
        from rich.markup import escape  # noqa: PLC0415
        import yaml as _yaml  # noqa: PLC0415

        try:
            data = _yaml.safe_load(editor.text) or {}
            spec = CollectiveSpec.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            status.update(
                f"[red]⚠ invalid[/red]\n  • {escape(str(exc))}"
            )
            return
        status.update(
            f"[green]✓ valid[/green] [dim]collective_id={spec.collective_id} "
            f"agents={len(spec.agents)}[/dim]"
        )
        self._refresh_agentset_table(spec)

    def _handle_collective_apply(self) -> None:
        """Save + signal the apply-watcher to reconcile podman state.

        Writes the file (same validation as Save), then touches
        ``./.acc-apply.request`` next to the spec — the host-side
        watcher script ``scripts/acc-apply-watcher.sh`` (operator
        installs once via ``./acc-deploy.sh setup``) wakes on that
        file and runs ``./acc-deploy.sh apply``.

        When no watcher is running, the file still persists and the
        status line tells the operator to run apply by hand.
        """
        try:
            status = self.query_one("#agentset-status", Static)
        except Exception:
            return

        # Save first (validates + writes).  If save fails, the status
        # line already surfaces the error — abort the apply.
        if not self._handle_collective_save():
            return

        path = self._resolve_collective_path()
        request_path = path.parent / ".acc-apply.request"
        try:
            # Touch the request marker.  Content holds the spec path
            # so the watcher can pick it up.
            request_path.write_text(
                f"{path}\n", encoding="utf-8",
            )
        except OSError as exc:
            logger.exception("ecosystem: apply request touch failed")
            status.update(
                f"[yellow]✓ saved but watcher signal failed: {exc}.  "
                f"Run `./acc-deploy.sh apply {path.name}` by hand.[/yellow]"
            )
            return
        status.update(
            f"[green]✓ saved + apply requested[/green] [dim]{request_path}[/dim]\n"
            f"  Run `./acc-deploy.sh apply {path.name}` on the host if no "
            f"watcher is installed."
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
    # Proposal 009 — Upload flow + snapshot LLM render moved to the
    # Configuration pane (pane 8).  EcosystemScreen no longer reacts
    # to ``watch_snapshot`` for LLM telemetry.
    # ------------------------------------------------------------------

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        """Refresh the Agentset tab's live-count column from each snapshot.

        PR-C — the rest of the screen (role table, role detail) is
        snapshot-agnostic; only the `#agentset-table`'s last column
        depends on live agent presence.

        Skip the initial `None` reactive — `on_mount` already loaded
        the table.  Re-firing here would double-add rows because
        Textual's DataTable defers visual updates across the watcher
        boundary (clear + add interleave with the on_mount path).
        """
        if snap is None:
            return
        try:
            # Cheap: reuse the existing on-disk spec rather than
            # re-validating the editor's working copy.
            from acc.collective import load_collective  # noqa: PLC0415
            path = self._resolve_collective_path()
            if path.exists():
                spec = load_collective(path)
                self._refresh_agentset_table(spec)
        except Exception:
            # Snapshot ticks are noisy; don't spam the log.
            pass

    # ------------------------------------------------------------------
    # Role loading
    # ------------------------------------------------------------------

    def _load_roles(self) -> None:
        """Scan roles/ directory + cache role rows (REQ-TUI-037).

        Proposal 003 PR-2 — caches the loaded rows in
        ``self._all_role_rows`` so the filter handler can re-populate
        the table without re-reading disk on every keystroke.  Calls
        ``_apply_filter()`` for the initial render.

        TUI Review 14.5 — when the resolved roles/ path yields zero
        entries (the operator's primary failure mode: pip-installed
        acc-tui from non-repo cwd → repo anchor misses → empty
        table), surface an actionable diagnostic via ``notify()``
        so the operator sees what to fix instead of staring at an
        empty pane.
        """
        root = _roles_root()
        self._role_names = list_roles(root)
        self._all_role_rows = []

        for role_name in self._role_names:
            loader = RoleLoader(root, role_name)
            role_def = loader.load()
            if role_def is None:
                # Role directory exists but failed to load — show minimal row
                self._all_role_rows.append((role_name, "—", "—", "—"))
                continue

            task_count = len(role_def.task_types) if role_def.task_types else 0
            self._all_role_rows.append((
                role_name,
                getattr(role_def, "domain_id", "") or "—",
                role_def.persona or "—",
                str(task_count),
            ))

        # Empty roster diagnostic (TUI Review 14.5).  We don't notify
        # when rows DO load — too noisy on every refresh tick.  Only
        # the operator-facing "nothing loaded, here's why" case.
        if not self._all_role_rows:
            try:
                self.notify(
                    f"No roles loaded from {root}.  Either set "
                    "ACC_REPO_ROOT to your agentic-cell-corpus checkout, "
                    "set ACC_ROLES_ROOT directly, or run acc-tui from "
                    "the repo (or any subdirectory — walk-up finds "
                    "acc-deploy.sh automatically).",
                    severity="warning",
                    timeout=10.0,
                )
            except Exception:
                logger.exception("ecosystem: empty-roles notify failed")

        self._apply_filter("")

    def _apply_filter(self, query: str) -> None:
        """Repopulate the role DataTable, keeping only rows whose
        name / domain / persona contain ``query`` (case-insensitive).

        Proposal 003 PR-2.  Empty / whitespace-only query → show every
        cached row.
        """
        try:
            table = self.query_one("#role-table", DataTable)
        except Exception:
            logger.exception("ecosystem: role table missing")
            return
        table.clear()
        q = query.strip().lower()
        for role_name, domain, persona, tasks in self._all_role_rows:
            if q and not (
                q in role_name.lower()
                or q in domain.lower()
                or q in persona.lower()
            ):
                continue
            table.add_row(role_name, domain, persona, tasks, key=role_name)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Proposal 003 PR-2 — repopulate the role table on every
        keystroke in the filter input.  Other Input widgets on the
        screen (none today; future-proof) get a no-op."""
        if (event.input.id or "") != "role-filter":
            return
        self._apply_filter(event.value or "")

    # ------------------------------------------------------------------
    # Proposal 003 PR-3 — file-watcher + selection lock
    # ------------------------------------------------------------------

    async def _watch_roles_loop(self) -> None:
        """Periodically diff the roles/ tree fingerprint and post a
        :class:`RolesChangedMessage` on change.

        Posting via the Textual message bus keeps the actual reload
        on the UI thread, where DataTable mutation is safe.  The
        loop runs until the screen unmounts or the task is cancelled.
        """
        interval = _resolve_watch_interval()
        roles_root = _roles_root()
        try:
            while True:
                await asyncio.sleep(interval)
                fp = _fingerprint_roles_dir(roles_root)
                if fp != self._last_role_fingerprint:
                    self._last_role_fingerprint = fp
                    try:
                        self.post_message(RolesChangedMessage("modified"))
                    except Exception:
                        logger.exception("ecosystem: post RolesChanged failed")
        except asyncio.CancelledError:
            logger.debug("ecosystem: role watcher cancelled")
            raise
        except Exception:
            # Defensive: a transient OSError in fingerprint shouldn't
            # take the whole screen down.  Log + exit the loop; the
            # operator can reopen the screen to restart watching.
            logger.exception("ecosystem: role watcher crashed")

    def on_roles_changed_message(
        self, message: RolesChangedMessage,
    ) -> None:
        """React to filesystem changes by reloading the cache + re-
        applying the current filter.

        Proposal 003 PR-3.  Detail-pane content stays in sync because
        ``_show_role_detail()`` reads role files directly each call.
        Status bar gets a small note so the operator knows a refresh
        happened (helps debugging external-edit workflows).
        """
        try:
            # Preserve the operator's current filter substring across
            # the refresh.
            current_query = ""
            try:
                current_query = self.query_one(
                    "#role-filter", Input,
                ).value or ""
            except Exception:
                pass

            self._load_roles()
            self._apply_filter(current_query)

            # If the operator had a role selected and it still exists
            # post-refresh, re-render its detail pane.  Otherwise leave
            # the placeholder visible.
            #
            # PR-A — but DO NOT re-render when the inline yaml editor
            # has unsaved edits: an external `$EDITOR` save (or another
            # TUI session) would otherwise clobber the operator's
            # in-progress typing.  Surface a hint instead so they know
            # the file moved underneath them.
            if self._selected_role and self._selected_role in (
                row[0] for row in self._all_role_rows
            ):
                if self._yaml_dirty:
                    try:
                        status = self.query_one("#yaml-save-status", Static)
                        status.update(
                            "[yellow]⚠ role.yaml changed on disk — "
                            "your edits are unsaved.  Save to overwrite, "
                            "or re-select the row to discard.[/yellow]"
                        )
                    except Exception:
                        pass
                else:
                    self._show_role_detail(self._selected_role)

            # Best-effort operator note — non-fatal if the panel widget
            # changes shape later.
            try:
                self.notify(
                    f"Roles directory changed ({message.reason}); "
                    "table refreshed.",
                    severity="information",
                    timeout=3.0,
                )
            except Exception:
                pass
        except Exception:
            logger.exception("ecosystem: on_roles_changed handler failed")

    def _acquire_selection_lock(self, role_name: str) -> None:
        """Take a best-effort advisory file lock on the selected role's
        ``role.yaml`` (and ``role.md`` if present).

        Proposal 003 PR-3 — protects against two TUI sessions stomping
        on the same file when both have a row selected.  Does NOT
        block the operator's external ``$EDITOR``; the lock is
        advisory and most editors (vim, notepad, …) ignore it.

        Lock library is :mod:`filelock` (already a project dependency).
        Failure modes:

        * ``filelock`` import fails → no lock; quiet (logged).
        * Lock acquisition raises → status-bar notification; no lock
          held.  Operator can still proceed.
        """
        # Release any prior lock first.
        self._release_selection_lock()
        if not role_name:
            return
        try:
            from filelock import FileLock, Timeout  # noqa: PLC0415
        except Exception:
            logger.debug("ecosystem: filelock unavailable; selection lock skipped")
            return

        yaml_path = _roles_root() / role_name / "role.yaml"
        lock_path = yaml_path.with_suffix(yaml_path.suffix + ".lock")
        try:
            lock = FileLock(str(lock_path), timeout=0.1)
            lock.acquire()
            self._selection_lock = lock
            self._selection_lock_role = role_name
        except Timeout:
            self._selection_lock = None
            self._selection_lock_role = ""
            try:
                self.notify(
                    f"role.yaml for {role_name} is locked by another process; "
                    "edits made now may be lost.",
                    severity="warning",
                    timeout=4.0,
                )
            except Exception:
                pass
        except Exception:
            logger.exception("ecosystem: selection lock acquire failed")
            self._selection_lock = None
            self._selection_lock_role = ""

    def _release_selection_lock(self) -> None:
        """Release the selection lock (idempotent)."""
        lock = self._selection_lock
        self._selection_lock = None
        self._selection_lock_role = ""
        if lock is None:
            return
        try:
            lock.release()
        except Exception:
            logger.exception("ecosystem: selection lock release failed")

    def _show_role_detail(self, role_name: str) -> None:
        """Render the role's narrative + raw definition in the detail
        panel (REQ-TUI-038).

        Proposal 003 PR-2 — splits into two surfaces:

        * ``role-md-content`` (Markdown) renders ``role.md`` if
          present; if missing, shows a "no role.md authored yet"
          placeholder so the collapsible isn't deceptively empty.
        * ``role-yaml-content`` (Static) renders ``role.yaml``
          verbatim — same content the old single-pane render produced.

        The placeholder Static above the collapsibles is hidden once
        a role is selected.
        """
        root = _roles_root()
        yaml_path = Path(root) / role_name / "role.yaml"
        md_path = Path(root) / role_name / "role.md"

        # Hide the "select a role" placeholder.
        try:
            placeholder = self.query_one("#role-detail-placeholder", Static)
            placeholder.update("")
            placeholder.display = False
        except Exception:
            pass

        # Proposal 010 PR-5 — refresh the role-sync badge for the
        # newly-selected role.  Empty when the listener has no events
        # recorded for this role (typical fresh-boot state).
        self._refresh_role_sync_badge(role_name)

        # role.md surface.
        try:
            md_widget = self.query_one("#role-md-content", Markdown)
        except Exception:
            md_widget = None
        if md_widget is not None:
            md_text = _read_role_md(md_path, role_name)
            # Proposal 003 PR-6 — append a directory-derived
            # "Subroles" section so operators can see persona
            # hierarchies (coding_agent_*, research_*) without
            # an explicit parent_role field (deferred to 004).
            siblings, source = _subrole_siblings(root, role_name)
            subrole_section = _format_subrole_section(
                siblings, role_name, source=source,
            )
            md_widget.update(md_text + subrole_section)

        # role.yaml surface — inline editable TextArea (PR-A).
        try:
            yaml_editor = self.query_one("#role-yaml-editor", TextArea)
        except Exception:
            yaml_editor = None
        if yaml_editor is not None:
            if not yaml_path.exists():
                placeholder = f"# role.yaml not found for {role_name}\n"
                yaml_editor.text = placeholder
                self._last_saved_yaml_text = placeholder
                self._yaml_dirty = False
            else:
                try:
                    yaml_text = yaml_path.read_text(encoding="utf-8")
                except OSError as exc:
                    placeholder = f"# Read error: {exc}\n"
                    yaml_editor.text = placeholder
                    self._last_saved_yaml_text = placeholder
                    self._yaml_dirty = False
                else:
                    # PR-A — replace the editor text via the public
                    # property; record the loaded snapshot so the
                    # `Changed` event handler can compute dirty as
                    # `text != last_saved` rather than naively flip
                    # on the very change we just queued.
                    yaml_editor.text = yaml_text
                    self._last_saved_yaml_text = yaml_text
                    self._yaml_dirty = False
        # Clear any prior save-status line; a stale "✓ saved" message
        # from the previously-selected role would mislead the operator.
        try:
            self.query_one("#yaml-save-status", Static).update("")
        except Exception:
            pass

        # The legacy `#role-yaml-content` Static was retired in PR-A;
        # the inline TextArea above is the new surface.  Keep this
        # branch for back-compat with any external code-path that
        # tried to update the old widget; harmless no-op now.
        try:
            yaml_widget = self.query_one("#role-yaml-content", Static)
        except Exception:
            yaml_widget = None
        if yaml_widget is not None:
            if not yaml_path.exists():
                yaml_widget.update(f"[red]role.yaml not found for {role_name}[/red]")
            else:
                try:
                    yaml_text = yaml_path.read_text(encoding="utf-8")
                except OSError as exc:
                    yaml_widget.update(f"[red]Read error: {exc}[/red]")
                else:
                    yaml_widget.update(
                        f"[bold]{role_name}/role.yaml[/bold]\n\n{yaml_text}"
                    )

    # Proposal 009 — Skills / MCPs / LLM-backends rendering moved
    # entirely to the Configuration pane (acc/tui/screens/configuration.py).
    # The methods that lived here previously (_load_skills,
    # _load_mcps, _render_llm_backends) are removed.

    # ------------------------------------------------------------------
    # Proposal 010 PR-5 — role-sync badge
    # ------------------------------------------------------------------

    def _refresh_role_sync_badge(self, role_name: str) -> None:
        """Re-render the badge for *role_name* from the App-wide
        :class:`RoleSyncListener`.

        Safe to call even when the listener isn't reachable (test
        harnesses, ConnectionErrorScreen path) — the badge silently
        stays empty.
        """
        try:
            listener = getattr(self.app, "_role_sync_listener", None)
            if listener is None:
                return
            badge_widget = self.query_one("#role-sync-badge", Static)
        except Exception:
            return
        try:
            badge_widget.update(listener.render_badge(role_name) or "")
        except Exception:
            logger.exception("ecosystem: render_badge failed for %r", role_name)

    def on__role_sync_event(self, _message: Any) -> None:
        """App-broadcast event: a new role-sync NATS message landed.

        If a role is currently selected, refresh its badge — operators
        see conflicts appear without re-clicking the row.  The
        underscore prefix matches Textual's convention for app-level
        message routing (see ``app.py`` `_RoleSyncEvent`).
        """
        if self._selected_role:
            self._refresh_role_sync_badge(self._selected_role)

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)
