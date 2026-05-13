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
import shutil
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
)

from acc.role_loader import RoleLoader, list_roles
from acc.tui.messages import RolePreloadMessage, RolesChangedMessage
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
                # Proposal 003 PR-2 — split detail into two collapsibles.
                # role.md (narrative, human-authored) opens by default so
                # the operator sees prose first; role.yaml (raw machine
                # config) is opt-in.
                with ScrollableContainer(id="role-detail-container"):
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
                    with Collapsible(
                        title="role.yaml (raw)",
                        collapsed=True,
                        id="role-yaml-collapsible",
                    ):
                        yield Static("", id="role-yaml-content")

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
        """Scan roles/ directory + cache role rows (REQ-TUI-037).

        Proposal 003 PR-2 — caches the loaded rows in
        ``self._all_role_rows`` so the filter handler can re-populate
        the table without re-reading disk on every keystroke.  Calls
        ``_apply_filter()`` for the initial render.
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
            if self._selected_role and self._selected_role in (
                row[0] for row in self._all_role_rows
            ):
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

        # role.md surface.
        try:
            md_widget = self.query_one("#role-md-content", Markdown)
        except Exception:
            md_widget = None
        if md_widget is not None:
            md_text = _read_role_md(md_path, role_name)
            md_widget.update(md_text)

        # role.yaml surface — verbatim render under a Collapsible.
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
