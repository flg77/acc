"""ACC TUI — DiagnosticsScreen: golden-prompt suite runner (PR-N, K-2).

Pane #9.  The TUI runner mode for the golden-prompt suite (D-005).
Consumes the same ``acc.golden_prompts`` loader + assertion engine
the CLI (``acc-cli e2e``) and the scheduled maintenance agent use,
so a green run here means green in CI and on cron too.

Layout::

    ┌─ GOLDEN PROMPTS ──────────────────┐ ┌─ RESULT ─────────────┐
    │ Name        Role        Last       │ │ <selected prompt's   │
    │ smoke…      analyst     —          │ │  definition + last   │
    │ coding…     coding…     PASS 412ms │ │  run detail>         │
    │ …                                  │ │                      │
    └────────────────────────────────────┘ └──────────────────────┘
    [Run selected]  [Run all]    status line

Each run drives a real :class:`acc.channels.TUIPromptChannel`
against the app's live NATSObserver — the same send/receive path
the operator's Prompt screen uses.  Results update the table's
"Last" column + the detail panel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    OptionList,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from textual.widgets.option_list import Option

from acc.tui.widgets.nav_bar import NavigateTo, NavigationBar

logger = logging.getLogger("acc.tui.diagnostics")

# Proposal 033 WS-B — operating modes offered in the Form's Mode
# selector (mirrors acc.operating_modes: PLAN / ACCEPT_EDITS /
# ASK_PERMISSIONS / AUTO).
_OPERATING_MODES = ("PLAN", "ACCEPT_EDITS", "ASK_PERMISSIONS", "AUTO")


class DiagnosticsScreen(Screen):
    """Pane #9 — run the golden-prompt suite against the live stack."""

    # 047 Slice 1 — ground-up layout.  THREE full-width areas stacked
    # vertically (① List · ② Workspace · ③ Form), replacing the cramped
    # 2-column grid the 2.6.26 findings flagged.  Focus-driven resize: the
    # focused area absorbs the height (≥80% via an 8fr weight); the other two
    # collapse toward their header + action bar.  Inside every area the CONTENT
    # is 1fr (shrinks first) and the ACTION BARS are auto (pinned) with a
    # per-area min-height, so the controls are ALWAYS on screen — the 045 G1
    # "eval-history controls reachable" invariant, now enforced per-area.
    DEFAULT_CSS = """
    DiagnosticsScreen #gp-stack { height: 1fr; }
    DiagnosticsScreen #gp-list { width: 100%; height: 1fr; min-height: 6; }
    DiagnosticsScreen #gp-workspace { width: 100%; height: 1fr; min-height: 6; }
    DiagnosticsScreen #gp-form { width: 100%; height: 1fr; min-height: 9; }
    DiagnosticsScreen.focus-list #gp-list { height: 8fr; }
    DiagnosticsScreen.focus-workspace #gp-workspace { height: 8fr; }
    DiagnosticsScreen.focus-form #gp-form { height: 8fr; }
    /* content flexes to nothing; action bars stay pinned (auto) */
    DiagnosticsScreen #golden-table { height: 1fr; min-height: 3; }
    DiagnosticsScreen #diagnostics-detail-container { height: 1fr; min-height: 0; }
    DiagnosticsScreen #golden-edit-tabs { height: 1fr; min-height: 0; }
    DiagnosticsScreen #gp-form-fields { height: 1fr; min-height: 0; }
    DiagnosticsScreen #gp-versions { height: auto; max-height: 5; display: none; }
    DiagnosticsScreen.show-versions #gp-versions { display: block; }
    /* 047 Slice 2 — View shows the rendered doc; Edit shows the YAML editor. */
    DiagnosticsScreen.ws-view #golden-edit-tabs { display: none; }
    DiagnosticsScreen.ws-edit #diagnostics-detail-container { display: none; }
    DiagnosticsScreen #gp-run { height: auto; }
    DiagnosticsScreen #golden-editor-actions { height: auto; }
    DiagnosticsScreen #form-actions { height: auto; }
    DiagnosticsScreen #golden-attach-row { height: auto; }
    DiagnosticsScreen #golden-editor-actions Button { width: auto; min-width: 6; margin-right: 1; }
    DiagnosticsScreen #form-actions Button { width: auto; min-width: 6; margin-right: 1; }
    DiagnosticsScreen #golden-attach-row Button { width: auto; min-width: 6; margin-right: 1; }
    DiagnosticsScreen #golden-attach-input { width: 1fr; min-width: 12; }
    DiagnosticsScreen .panel-label { text-style: bold; }
    """

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
        ("9", "navigate('diagnostics')", "Diagnostics"),
        Binding("r", "run_selected", "Run selected", priority=True),
        Binding("a", "run_all", "Run all", priority=True),
        # 047 Slice 1 — collapse the expanded area back to the list view.
        Binding("escape", "collapse_to_list", "Back to list"),
        # 047 Slice 2 — 'e' enters Edit on the Workspace (only when a text
        # widget isn't focused, so it never hijacks typing).
        Binding("e", "edit_mode", "Edit"),
    ]

    snapshot: reactive["Any | None"] = reactive(None, layout=True)

    # 047 Slice 1 — which of the three stacked areas is expanded to ~80%.
    focus_area: reactive[str] = reactive("list")

    # 047 Slice 2 — Workspace mode: 'view' (rendered, read-only) or 'edit'
    # (the YAML editor).  'e' / Edit → edit; the View button → view.
    ws_mode: reactive[str] = reactive("view")

    # 7-column table (No|Title|Description|Role|Mode|Version|Last): the
    # "Last" cell that a completed run rewrites is column index 6.
    _COL_LAST = 6

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Loaded golden prompts keyed by name; the table's row key is
        # the prompt name so result updates can target the right row.
        self._prompts: dict[str, Any] = {}
        # Last result per prompt name (GoldenResult); rendered in the
        # detail panel + the "Last" column.
        self._results: dict[str, Any] = {}
        self._running = False
        # Proposal 033 WS-B — name of the prompt currently mirrored into
        # the Form tab (reused as the GoldenPrompt name when sending).
        self._current_name = ""

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="diagnostics", id="nav")
        yield Label(
            "ACC Diagnostics — Golden Prompt Suite", id="diagnostics-title",
        )
        # 047 Slice 1 — three full-width areas stacked vertically.  Focus one
        # (Tab / click / Enter) and it grows to ~80%; the other two collapse
        # to their header + action bar.  Esc returns to the list.
        with Vertical(id="gp-stack"):
            # ── ① List ──────────────────────────────────────────────────
            with Vertical(id="gp-list"):
                yield Label("① GOLDEN PROMPTS", classes="panel-label")
                yield DataTable(id="golden-table")
                # Slice 2 turns Enter into a version picker here; hidden now.
                yield OptionList(id="gp-versions")
                with Horizontal(id="gp-run"):
                    yield Button(
                        "Run selected", id="btn-run-selected",
                        variant="primary",
                    )
                    yield Button("Run all", id="btn-run-all", variant="default")
                yield Static("", id="diagnostics-status")

            # ── ② Workspace (rendered detail + the MD/YAML editor) ───────
            with Vertical(id="gp-workspace"):
                yield Label("② WORKSPACE", classes="panel-label")
                with ScrollableContainer(id="diagnostics-detail-container"):
                    yield Static(
                        "[dim]Select a prompt to see its definition; "
                        "run it (r) or run all (a) to see pass/fail "
                        "detail.[/dim]",
                        id="diagnostics-detail",
                    )
                # The MD (YAML) tab is the power-user editor (operator-kept,
                # 047 §8): highlight a row → its YAML loads here; Save
                # validates + writes to the writable store; New starts a
                # template.  (Slice 2 adds View/Edit tabs alongside it.)
                with TabbedContent(id="golden-edit-tabs"):
                    with TabPane("MD (YAML)", id="tab-golden-md"):
                        yield TextArea(
                            "", id="golden-editor", language="yaml",
                        )
                # Eval-history controls (natural height, always on screen —
                # 045 G1).  Copy/Paste buttons dropped (047 G9 — copy/paste
                # is terminal-native: mark + Ctrl+Shift+C/V or middle-click).
                with Horizontal(id="golden-editor-actions"):
                    # 047 Slice 2 — View (rendered) / Edit (YAML) mode toggle.
                    yield Button("View", id="btn-ws-view", variant="default")
                    yield Button("Edit", id="btn-ws-edit", variant="default")
                    yield Button("New", id="btn-golden-new", variant="default")
                    yield Button("Save", id="btn-golden-save", variant="primary")
                    # Proposal G — restore a previous saved version.
                    yield Button(
                        "Versions", id="btn-golden-versions", variant="default",
                    )
                    # Proposal G P3 — promote to a role's behavioral eval pack.
                    yield Button(
                        "→ Eval", id="btn-golden-promote-eval",
                        variant="default",
                    )

            # ── ③ Form (always visible — Send lives here now) ────────────
            with Vertical(id="gp-form"):
                yield Label("③ FORM", classes="panel-label")
                with ScrollableContainer(id="gp-form-fields"):
                    # 047 Slice 2c — Title (required to save) + Description.
                    yield Label("Title (required to save)")
                    yield Input(placeholder="prompt title", id="form-title")
                    yield Label("Description")
                    yield Input(placeholder="optional", id="form-desc")
                    yield Label("Target role")
                    yield Select(
                        [], id="form-role", prompt="select role",
                        allow_blank=True,
                    )
                    yield Label("Target agent (optional)")
                    yield Input(
                        placeholder="agent_id or blank", id="form-agent",
                    )
                    yield Label("Mode")
                    yield Select(
                        [(m, m) for m in _OPERATING_MODES],
                        id="form-mode", value="AUTO", allow_blank=False,
                    )
                    yield Label("Timeout (s)")
                    yield Input(value="60", id="form-timeout")
                    yield Label("Prompt")
                    yield TextArea("", id="form-prompt")
                # 047 G7 — Send is its own always-visible bar (it used to hide
                # inside the Form tab → "Send disappeared" in the findings).
                with Horizontal(id="form-actions"):
                    # 047 Slice 2c — the Form's own [New · Export · Save · Send].
                    yield Button("New", id="btn-form-new", variant="default")
                    yield Button(
                        "Export", id="btn-form-export", variant="default",
                    )
                    yield Button(
                        "Save", id="btn-form-save", variant="primary",
                    )
                    yield Button(
                        "Send", id="btn-golden-send", variant="success",
                    )
                with Horizontal(id="golden-attach-row"):
                    yield Input(
                        placeholder=(
                            "dir (watch/import/export) · .csv / .json file · "
                            "@scope/name for → Pack"
                        ),
                        id="golden-attach-input",
                    )
                    yield Button(
                        "+ Add", id="btn-golden-add-dir", variant="default",
                    )
                    # Durable backup: import COPIES a dir's prompts into the
                    # store; export writes the store out (survives a reset).
                    yield Button(
                        "Import", id="btn-golden-import", variant="default",
                    )
                    yield Button(
                        "Export", id="btn-golden-export", variant="default",
                    )
                    # Gap #5 — export the store as a signed-able @scope/*
                    # .accpkg (kept in Slice 1; 048 supersedes with role packs).
                    yield Button(
                        "→ Pack", id="btn-golden-pack", variant="default",
                    )

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#golden-table", DataTable)
        # Row-cursor (not the default cell-cursor) so DataTable posts
        # RowHighlighted / RowSelected as the operator navigates — without
        # this the selection handlers never fire in the running TUI, so the
        # detail panel stayed blank and prompts could not be loaded into the
        # editor (the pilot test masked it by calling the handler directly).
        table.cursor_type = "row"
        # 047 G3 — full column set: No/Version/Last are system-assigned +
        # persisted; Title/Description/Role/Mode are the editable fields.
        table.add_columns(
            "No", "Title", "Description", "Role", "Mode", "Version", "Last",
        )
        self.add_class("focus-list")  # the list is the default work area
        self.add_class("ws-view")  # Workspace opens read-only; 'e'/Edit → YAML
        self._reload_prompts()
        self._populate_role_options()
        # PR-Y-2 — live-reload: poll the load roots' file mtimes every
        # 2s and refresh the table when a golden file changes (mirrors
        # the Ecosystem role watcher).  The editor is independent of the
        # table, so a reload never clobbers in-progress edits.
        self._files_sig = self._compute_files_sig()
        self.set_interval(2.0, self._poll_changes)

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)

    # ------------------------------------------------------------------
    # 047 Slice 1 — focus-driven resize (List / Workspace / Form)
    # ------------------------------------------------------------------

    _AREAS = ("list", "workspace", "form")

    def watch_focus_area(self, area: str) -> None:
        """Toggle the screen class that expands the focused area (TCSS
        sizes ``.focus-<area> #gp-<area>`` to 8fr; the others collapse)."""
        for a in self._AREAS:
            self.set_class(a == area, f"focus-{a}")

    def on_descendant_focus(self, event) -> None:
        """Grow whichever area now holds keyboard focus (Tab moves it)."""
        node = getattr(event, "widget", None)
        while node is not None:
            nid = getattr(node, "id", None)
            if nid in ("gp-list", "gp-workspace", "gp-form"):
                self.focus_area = nid.split("-", 1)[1]
                return
            node = getattr(node, "parent", None)

    def action_collapse_to_list(self) -> None:
        """Esc — close the version picker if open (G4), else collapse the
        expanded area back to the list view."""
        if self.has_class("show-versions"):
            self.remove_class("show-versions")
            self._set_status("[dim]version pick cancelled[/dim]")
            try:
                self.query_one("#golden-table", DataTable).focus()
            except Exception:
                pass
            return
        self.focus_area = "list"
        try:
            self.query_one("#golden-table", DataTable).focus()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 047 Slice 2 — Workspace View / Edit modes
    # ------------------------------------------------------------------

    def watch_ws_mode(self, mode: str) -> None:
        self.set_class(mode == "view", "ws-view")
        self.set_class(mode == "edit", "ws-edit")

    def action_edit_mode(self) -> None:
        """'e' / Edit — show the YAML editor + focus it (in the Workspace)."""
        self.ws_mode = "edit"
        self.focus_area = "workspace"
        try:
            self._editor().focus()
        except Exception:
            pass

    def action_view_mode(self) -> None:
        """View — show the read-only rendered doc."""
        self.ws_mode = "view"
        if self._current_name:
            self._render_detail(self._current_name)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _reload_prompts(self) -> None:
        """Populate the table from the merged golden-prompts library
        (shipped suite + writable store + attached watch dirs)."""
        from acc.golden_prompts import load_merged  # noqa: PLC0415

        table = self.query_one("#golden-table", DataTable)
        table.clear()
        self._prompts.clear()
        try:
            prompts = load_merged()
        except Exception:
            logger.exception("diagnostics: load_merged failed")
            prompts = []

        if not prompts:
            self._set_status(
                "[yellow]No golden prompts found — see "
                "examples/golden_prompts/, or write one in the editor "
                "→ Save.[/yellow]"
            )
            return

        for i, p in enumerate(prompts, start=1):
            self._prompts[p.name] = p
            table.add_row(
                str(i),                                   # No (system)
                p.name,                                   # Title (editable)
                (p.description or "").strip()[:40],       # Description
                p.target_role,                            # Role
                p.operating_mode,                         # Mode
                self._version_cell(p.name),               # Version (system)
                self._last_cell(p.name),                  # Last (system)
                key=p.name,
            )
        self._set_status(f"[dim]{len(prompts)} prompt(s) loaded.[/dim]")

    @staticmethod
    def _version_cell(name: str) -> str:
        """047 G3 — saved-version count for the Version column ('—' if none)."""
        try:
            from acc.golden_prompts import version_count  # noqa: PLC0415
            n = version_count(name)
            return str(n) if n else "—"
        except Exception:
            return "—"

    def _last_cell(self, name: str) -> str:
        """047 G3 — the Last column: an in-session result wins; else the most
        recent persisted run (this is what the 2.6.26 findings saw as '—')."""
        result = self._results.get(name)
        if result is not None:
            return self._format_last(result)
        try:
            from acc.golden_prompts import read_run_history  # noqa: PLC0415
            hist = read_run_history(name, limit=8)
            if hist:
                row = max(hist, key=lambda r: r.get("run_ts", 0) or 0)
                ok = (
                    "[green]PASS[/green]" if row.get("passed")
                    else "[red]FAIL[/red]"
                )
                return f"{ok} {self._fmt_ts(row.get('run_ts', 0))}"
        except Exception:
            pass
        return "—"

    @staticmethod
    def _format_last(result) -> str:
        if result is None:
            return "—"
        marker = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        return f"{marker} {result.elapsed_ms}ms"

    # ------------------------------------------------------------------
    # Selection → detail
    # ------------------------------------------------------------------

    def on_data_table_row_highlighted(self, event) -> None:
        # Moving the cursor updates the (read-only) detail panel AND the
        # Form quick-send fields (proposal 033 WS-B) so the operator can
        # highlight → Send without an extra select.  Loading into the
        # MD/YAML *editor* stays deferred to an EXPLICIT row select
        # (Enter / click) so a background file-reload — which resets the
        # cursor — can never clobber the operator's unsaved editor edits.
        if getattr(event, "data_table", None) is None:
            return
        if event.data_table.id != "golden-table":
            return
        name = self._row_key_value(event.row_key)
        self._render_detail(name)
        self._load_into_form(name)

    def on_data_table_row_selected(self, event) -> None:
        # Enter / click on a row: load it into the editor for tweaking.
        if getattr(event, "data_table", None) is None:
            return
        if event.data_table.id != "golden-table":
            return
        name = self._row_key_value(event.row_key)
        self._render_detail(name)
        self._load_into_form(name)
        # 047 G4 — Enter opens the version picker when saved versions exist
        # (max 3 visible + scroll; Enter loads one, Esc cancels).  With no
        # saved versions the Workspace just becomes the work window.
        if self._open_version_picker(name):
            return
        self._load_into_editor(name)
        self.ws_mode = "edit"
        self.focus_area = "workspace"
        if name in self._prompts:
            self._set_status(
                f"[dim]loaded[/dim] [b]{name}[/b] "
                f"[dim]into editor — tweak + Save (writes to your "
                f"writable store as a copy).[/dim]"
            )

    # ------------------------------------------------------------------
    # 047 Slice 2 — version picker (Enter → dropdown below the list)
    # ------------------------------------------------------------------

    def _open_version_picker(self, name: str) -> bool:
        """Populate + reveal the full-width version dropdown below the list
        (newest first).  Returns True when it opened (≥1 saved version)."""
        try:
            from acc.golden_prompts import list_versions  # noqa: PLC0415
            versions = list_versions(name)
        except Exception:
            versions = []
        if not versions:
            return False
        try:
            ol = self.query_one("#gp-versions", OptionList)
        except Exception:
            return False
        ol.clear_options()
        self._versions_for = name
        for v in sorted(versions, reverse=True):
            ol.add_option(Option(f"v{v}", id=str(v)))
        self.add_class("show-versions")
        try:
            ol.focus()
        except Exception:
            pass
        self._set_status(
            f"[dim]{len(versions)} version(s) of [b]{name}[/b] — "
            f"Enter to load, Esc to cancel[/dim]"
        )
        return True

    def on_option_list_option_selected(self, event) -> None:
        """Enter on a version → load that saved blob into the editor."""
        if getattr(event, "option_list", None) is None:
            return
        if event.option_list.id != "gp-versions":
            return
        from acc.golden_prompts import read_version  # noqa: PLC0415
        name = getattr(self, "_versions_for", "")
        try:
            ver = int(event.option.id)
        except (TypeError, ValueError):
            return
        try:
            content = read_version(name, ver)
        except Exception:
            self._set_status("[red]could not read that version[/red]")
            return
        try:
            self._editor().text = content
        except Exception:
            pass
        self._current_name = name
        self.remove_class("show-versions")
        self.ws_mode = "edit"
        # Move focus INTO the workspace (hiding the OptionList otherwise
        # auto-moves focus to the table → on_descendant_focus flips us to
        # 'list'); focusing the editor lands focus in the workspace last.
        try:
            self._editor().focus()
        except Exception:
            pass
        self.focus_area = "workspace"
        self._set_status(
            f"[green]✓ loaded v{ver}[/green] "
            f"[dim]{name} — edit + Save[/dim]"
        )

    @staticmethod
    def _row_key_value(row_key) -> str:
        return str(getattr(row_key, "value", None) or row_key or "")

    def _render_detail(self, name: str) -> None:
        panel = self.query_one("#diagnostics-detail", Static)
        prompt = self._prompts.get(name)
        if prompt is None:
            panel.update("[dim]—[/dim]")
            return
        result = self._results.get(name)
        lines = [
            f"[b]{prompt.name}[/b]",
            f"[dim]{prompt.description.strip()}[/dim]" if prompt.description else "",
            "",
            f"[b]target_role:[/b] {prompt.target_role}",
            f"[b]mode:[/b] {prompt.operating_mode}",
            f"[b]timeout_s:[/b] {prompt.timeout_s}",
            "",
            "[b]prompt[/b]",
            f"  {prompt.prompt.strip()[:400]}",
        ]
        if result is not None:
            lines.append("")
            verdict = (
                "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
            )
            lines.append(f"[b]last run:[/b] {verdict}  {result.elapsed_ms}ms")
            # Proposal G P2 — the per-task signals joined onto the run by
            # task_id: tokens, compliance, the model's self-verdict.
            lines.append(f"  {self._run_metrics_line(result)}")
            # Proposal G P3 — deep link to this run's trace in MLflow (DC
            # only; gated on ACC_MLFLOW_TRACKING_URI, absent on the edge).
            link = self._mlflow_trace_link(result)
            if link:
                lines.append(f"  [dim]trace →[/dim] {link}")
            if result.error:
                lines.append(f"  [red]ERROR: {result.error}[/red]")
            for f in result.failures:
                lines.append(f"  · {f}")
            if result.output_excerpt:
                lines.append("")
                lines.append("[b]reply excerpt[/b]")
                lines.append(f"  {result.output_excerpt}")
        # Proposal G P2 — "definition of good": the layered criteria this
        # prompt is judged by (deterministic expects + model self-verdict).
        lines.extend(self._definition_of_good(prompt, result))
        # Proposal G — per-prompt run history (outcomes of repeated runs) +
        # version control, both read from the writable store by prompt name.
        try:
            from acc.golden_prompts import (  # noqa: PLC0415
                list_versions, read_run_history,
            )
            history = read_run_history(name, limit=8)
            versions = list_versions(name)
        except Exception:
            history, versions = [], []
        if history:
            lines.append("")
            lines.append(f"[b]run history[/b] [dim](last {len(history)})[/dim]")
            for row in history:
                ok = "[green]✓[/green]" if row.get("passed") else "[red]✗[/red]"
                ms = row.get("elapsed_ms", 0)
                nfail = len(row.get("failures") or [])
                extra = f" · {nfail} failed" if nfail else ""
                vd = row.get("eval_verdict") or ""
                vtag = f"  [dim]{vd}[/dim]" if vd else ""
                lines.append(
                    f"  {ok} {self._fmt_ts(row.get('run_ts', 0))}  "
                    f"{ms}ms{extra}{vtag}"
                )
        if versions:
            prev = versions[-2] if len(versions) > 1 else versions[-1]
            lines.append("")
            lines.append(
                f"[b]versions[/b] {len(versions)} "
                f"[dim](latest v{versions[-1]}; Versions ↺ restores v{prev})[/dim]"
            )
        panel.update("\n".join(line for line in lines if line is not None))

    @staticmethod
    def _fmt_ts(ts) -> str:
        import datetime as _dt  # noqa: PLC0415
        try:
            return _dt.datetime.fromtimestamp(
                float(ts)
            ).strftime("%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError, TypeError):
            return "—"

    @staticmethod
    def _run_metrics_line(result) -> str:
        """Proposal G P2 — one line: tokens · compliance · self-verdict.
        Defensive (result may be a stub); compliance < 0 = unreported → "—"."""
        toks = int(getattr(result, "input_tokens", 0) or 0)
        cache = int(getattr(result, "cache_read_tokens", 0) or 0)
        comp = getattr(result, "compliance_health_score", -1.0)
        verdict = getattr(result, "eval_verdict", "") or ""
        parts = ["tokens in " + str(toks) + (f" · cache {cache}" if cache else "")]
        try:
            parts.append(
                f"compliance {comp:.2f}"
                if comp is not None and comp >= 0 else "compliance —"
            )
        except (TypeError, ValueError):
            parts.append("compliance —")
        if verdict:
            parts.append(f"verdict {verdict}")
        return "[dim]" + "  ·  ".join(parts) + "[/dim]"

    @staticmethod
    def _mlflow_trace_link(result) -> str:
        """Proposal G P3 — the MLflow trace URL for this run's task_id, or ""
        when MLflow isn't configured (edge / unset tracking URI)."""
        try:
            from acc.backends.mlflow_runs import mlflow_trace_url  # noqa: PLC0415
            return mlflow_trace_url(getattr(result, "task_id", "") or "") or ""
        except Exception:
            return ""

    def _definition_of_good(self, prompt, result) -> list[str]:
        """Proposal G P2 — the layered criteria a prompt is judged by:
        (1) the deterministic ``expects`` block; (2) the model's self-verdict.
        (The third layer — the role's consolidated ``memory_notes`` — is a
        DC/MLflow surface; the TUI is NATS-only, so it's not shown here.)"""
        crit: list[str] = []
        expects = getattr(prompt, "expects", None)
        if expects is not None:
            if getattr(expects, "reply_non_empty", False):
                crit.append("reply non-empty")
            if getattr(expects, "blocked", False):
                crit.append("expected blocked")
            lat = getattr(expects, "latency_max_ms", None)
            if lat:
                crit.append(f"latency ≤ {lat}ms")
            oc = list(getattr(expects, "output_contains", None) or [])
            if oc:
                crit.append("contains " + ", ".join(map(str, oc[:3])))
            rx = getattr(expects, "output_matches_regex", None)
            if rx:
                crit.append(f"matches /{rx}/")
            inv = list(getattr(expects, "invocations_kind_contains", None) or [])
            inv += list(getattr(expects, "invocations_target_contains", None) or [])
            if inv:
                crit.append("invokes " + ", ".join(map(str, inv[:3])))
        out = [
            "",
            "[b]definition of good[/b]",
            "  [dim]asserts:[/dim] "
            + ("; ".join(crit) if crit else "reply arrived"),
        ]
        verdict = getattr(result, "eval_verdict", "") if result is not None else ""
        if verdict:
            out.append(f"  [dim]model self-verdict:[/dim] {verdict}")
        return out

    def _promote_to_eval_pack(self) -> None:
        """Promote the editor's prompt into a role's behavioral eval suite
        (proposal G P3) — the role-testing on-ramp.  Adapts the GoldenPrompt
        to a BehaviorEval and writes it to the writable promoted-evals store,
        keyed by target_role, ready to fold into a signed @acc/*-roles pack."""
        import yaml  # noqa: PLC0415

        from acc.golden_prompts import GoldenPrompt, writable_root  # noqa: PLC0415
        from acc.pkg.evals import (  # noqa: PLC0415
            dump_behavior_eval, from_golden_prompt,
        )
        try:
            gp = GoldenPrompt.model_validate(
                yaml.safe_load(self._editor().text) or {}
            )
        except Exception as exc:
            self._set_status(f"[red]invalid prompt: {exc}[/red]")
            return
        be = from_golden_prompt(gp)
        role = gp.target_role or "_norole"
        # pkg-shaped layout (<root>/evals/behavior) so the promoted-evals/<role>
        # dir loads back via acc.pkg.evals.load_evals → folds into a signed pack.
        dest = writable_root() / "promoted-evals" / role / "evals" / "behavior"
        try:
            out = dump_behavior_eval(be, dest)
        except OSError as exc:
            self._set_status(f"[red]promote failed: {exc}[/red]")
            return
        self._set_status(
            f"[green]✓ promoted to {role} eval pack[/green] [dim]{out}[/dim]"
        )

    def _restore_previous_version(self) -> None:
        """Load the previous saved version of the highlighted prompt into
        the editor (proposal G — restore).  The detail panel lists every
        saved version; this recovers the one before the latest save so a
        bad edit can be rolled back, then re-Saved to keep."""
        from acc.golden_prompts import (  # noqa: PLC0415
            diff_versions, list_versions, read_version,
        )
        name = self._current_name
        if not name:
            self._set_status("[yellow]Select a prompt first.[/yellow]")
            return
        versions = list_versions(name)
        if len(versions) < 2:
            self._set_status(
                f"[yellow]no earlier version to restore "
                f"({len(versions)} saved)[/yellow]"
            )
            return
        prev, cur = versions[-2], versions[-1]
        try:
            content = read_version(name, prev)
            ndiff = sum(
                1 for ln in diff_versions(name, prev, cur).splitlines()
                if ln[:1] in "+-" and not ln.startswith(("+++", "---"))
            )
        except OSError as exc:
            self._set_status(f"[red]restore failed: {exc}[/red]")
            return
        try:
            self._editor().text = content
        except Exception:
            return
        self._set_status(
            f"[green]✓ restored v{prev}[/green] "
            f"[dim](was v{cur}; {ndiff} changed lines — Save to keep)[/dim]"
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-run-selected":
            self.action_run_selected()
        elif bid == "btn-run-all":
            self.action_run_all()
        elif bid == "btn-ws-view":
            self.action_view_mode()
        elif bid == "btn-ws-edit":
            self.action_edit_mode()
        elif bid == "btn-golden-new":
            self._editor_new()
        elif bid == "btn-golden-save":
            self._editor_save()
        elif bid == "btn-golden-add-dir":
            self._attach_dir()
        elif bid == "btn-golden-copy":
            self._editor_copy()
        elif bid == "btn-golden-paste":
            self._editor_paste()
        elif bid == "btn-golden-versions":
            self._restore_previous_version()
        elif bid == "btn-golden-promote-eval":
            self._promote_to_eval_pack()
        elif bid == "btn-golden-import":
            self._import_dir()
        elif bid == "btn-golden-export":
            self._export_dir()
        elif bid == "btn-golden-pack":
            self._export_as_pack()
        elif bid == "btn-golden-send":
            self.action_send()
        elif bid == "btn-form-new":
            self._form_new()
        elif bid == "btn-form-save":
            self._form_save()
        elif bid == "btn-form-export":
            self._form_export()

    # ------------------------------------------------------------------
    # PR-Y-2 — in-pane editor + attach/watch
    # ------------------------------------------------------------------

    _EDITOR_TEMPLATE = (
        "name: my_prompt\n"
        "description: \"\"\n"
        "prompt: |\n"
        "  Write a Python function that returns the nth Fibonacci number.\n"
        "target_role: coding_agent\n"
        "operating_mode: AUTO\n"
        "timeout_s: 60.0\n"
        "expects:\n"
        "  reply_non_empty: true\n"
    )

    def _editor(self) -> "TextArea":
        return self.query_one("#golden-editor", TextArea)

    def _load_into_editor(self, name: str) -> None:
        """Dump the highlighted prompt's definition into the editor so
        the operator can tweak + Save it."""
        import yaml  # noqa: PLC0415

        prompt = self._prompts.get(name)
        if prompt is None:
            return
        try:
            text = yaml.safe_dump(
                prompt.model_dump(), sort_keys=False, allow_unicode=True,
            )
            self._editor().text = text
        except Exception:
            logger.debug("diagnostics: editor load failed", exc_info=True)

    # ------------------------------------------------------------------
    # Proposal 033 WS-B — Form tab (role selector + Send) + Form/MD sync
    # ------------------------------------------------------------------

    def _available_role_names(self) -> list[str]:
        """Roles offered in the Form's role selector: the installed set
        (in-tree control roles + packaged roles) plus any role a loaded
        golden prompt targets."""
        names: set[str] = set()
        try:
            from acc.role_loader import list_roles  # noqa: PLC0415
            from acc.tui.path_resolution import (  # noqa: PLC0415
                resolve_manifest_root,
            )
            roots = str(resolve_manifest_root("ACC_ROLES_ROOT", "roles"))
            names.update(list_roles(roots))
        except Exception:
            logger.debug("diagnostics: list_roles failed", exc_info=True)
        for prompt in self._prompts.values():
            tr = getattr(prompt, "target_role", "")
            if tr:
                names.add(tr)
        return sorted(names)

    def _populate_role_options(self) -> None:
        try:
            sel = self.query_one("#form-role", Select)
        except Exception:
            return
        sel.set_options([(n, n) for n in self._available_role_names()])

    def _populate_form_fields(self, prompt) -> None:
        """Set the Form widgets from a GoldenPrompt (no side effects on
        the loaded library)."""
        try:
            self.query_one("#form-title", Input).value = prompt.name or ""
            self.query_one("#form-desc", Input).value = prompt.description or ""
            sel = self.query_one("#form-role", Select)
            options = self._available_role_names()
            if prompt.target_role and prompt.target_role not in options:
                options = sorted(set(options) | {prompt.target_role})
            sel.set_options([(n, n) for n in options])
            try:
                sel.value = prompt.target_role or Select.BLANK
            except Exception:
                sel.value = Select.BLANK
            self.query_one("#form-agent", Input).value = (
                prompt.target_agent_id or ""
            )
            mode_sel = self.query_one("#form-mode", Select)
            mode_sel.value = (
                prompt.operating_mode
                if prompt.operating_mode in _OPERATING_MODES
                else "AUTO"
            )
            self.query_one("#form-timeout", Input).value = str(prompt.timeout_s)
            self.query_one("#form-prompt", TextArea).text = prompt.prompt
        except Exception:
            logger.debug("diagnostics: form populate failed", exc_info=True)

    def _load_into_form(self, name: str) -> None:
        prompt = self._prompts.get(name)
        if prompt is None:
            return
        self._current_name = name
        self._populate_form_fields(prompt)

    def on_tabbed_content_tab_activated(self, event) -> None:
        """When the Form tab becomes active, re-derive its fields from
        the MD (YAML) editor so the Form reflects the authoritative MD
        view (proposal 033 WS-B)."""
        try:
            active = self.query_one("#golden-edit-tabs", TabbedContent).active
        except Exception:
            return
        if active == "tab-golden-form":
            self._derive_form_from_md()

    def _derive_form_from_md(self) -> None:
        import yaml  # noqa: PLC0415
        from acc.golden_prompts import GoldenPrompt  # noqa: PLC0415

        try:
            raw = self.query_one("#golden-editor", TextArea).text
        except Exception:
            return
        if not raw.strip():
            return
        try:
            prompt = GoldenPrompt.model_validate(yaml.safe_load(raw) or {})
        except Exception:
            return  # MD mid-edit / invalid — leave the Form untouched
        self._populate_form_fields(prompt)

    def _form_to_prompt(self):
        """Build a transient GoldenPrompt from the Form fields, or None
        when the required role + prompt are missing."""
        from acc.golden_prompts import GoldenPrompt  # noqa: PLC0415

        try:
            title = self.query_one("#form-title", Input).value.strip()
            desc = self.query_one("#form-desc", Input).value.strip()
            raw_role = self.query_one("#form-role", Select).value
            agent = self.query_one("#form-agent", Input).value.strip()
            mode = str(self.query_one("#form-mode", Select).value or "AUTO")
            timeout_raw = self.query_one("#form-timeout", Input).value.strip()
            text = self.query_one("#form-prompt", TextArea).text.strip()
        except Exception:
            return None
        if raw_role is None or raw_role == Select.BLANK:
            return None
        role = str(raw_role).strip()
        if not role or not text:
            return None
        try:
            timeout_s = float(timeout_raw) if timeout_raw else 60.0
        except ValueError:
            timeout_s = 60.0
        return GoldenPrompt(
            name=title or self._current_name or "form_send",
            description=desc,
            prompt=text,
            target_role=role,
            target_agent_id=agent,
            operating_mode=mode if mode in _OPERATING_MODES else "AUTO",
            timeout_s=timeout_s,
        )

    def action_send(self) -> None:
        """Send the Form's current values to the Prompt screen and fire
        it there so the reply streams on the Prompt pane (proposal 033
        WS-B).  Routing through the Prompt screen is what makes "going
        back to Prompt show the golden prompt we just sent" + its
        feedback — the Prompt pane owns the rich reasoning/reply view."""
        prompt = self._form_to_prompt()
        if prompt is None:
            self._set_status(
                "[yellow]Form needs a target role + a prompt.[/yellow]"
            )
            return
        from acc.tui.messages import PromptLoadMessage  # noqa: PLC0415

        self.post_message(PromptLoadMessage(
            prompt_text=prompt.prompt,
            target_role=prompt.target_role,
            target_agent_id=prompt.target_agent_id,
            operating_mode=prompt.operating_mode,
            auto_send=True,
        ))
        self._set_status(
            f"[b]→ Prompt[/b] sending to {prompt.target_role}…"
        )

    # ------------------------------------------------------------------
    # 047 Slice 2c — the Form's own New / Save / Export
    # ------------------------------------------------------------------

    def _form_new(self) -> None:
        """Blank the Form for a fresh prompt (Title required before Save)."""
        for wid in ("#form-title", "#form-desc", "#form-agent"):
            try:
                self.query_one(wid, Input).value = ""
            except Exception:
                pass
        try:
            self.query_one("#form-timeout", Input).value = "60"
        except Exception:
            pass
        try:
            self.query_one("#form-prompt", TextArea).text = ""
        except Exception:
            pass
        try:
            self.query_one("#form-role", Select).value = Select.BLANK
        except Exception:
            pass
        self._current_name = ""
        self.focus_area = "form"
        try:
            self.query_one("#form-title", Input).focus()
        except Exception:
            pass
        self._set_status(
            "[green]✓ new form[/green] "
            "[dim]— fill Title + Prompt, then Save[/dim]"
        )

    def _form_save(self) -> None:
        """Persist the Form as a golden prompt.  Title is the name and is
        REQUIRED (047 G6 — 'if not set not saveable')."""
        import yaml  # noqa: PLC0415
        from acc.golden_prompts import (  # noqa: PLC0415
            append_save_history, save_prompt,
        )
        try:
            title = self.query_one("#form-title", Input).value.strip()
        except Exception:
            title = ""
        if not title:
            self._set_status("[yellow]Title is required to save.[/yellow]")
            return
        prompt = self._form_to_prompt()
        if prompt is None:
            self._set_status(
                "[yellow]Form needs a target role + a prompt.[/yellow]"
            )
            return
        try:
            out = save_prompt(prompt)
        except OSError as exc:
            self._set_status(f"[red]save failed: {exc}[/red]")
            return
        ver = append_save_history(
            prompt.name,
            yaml.safe_dump(prompt.model_dump(), sort_keys=False),
        )
        self._current_name = prompt.name
        self._set_status(
            f"[green]✓ saved (v{ver})[/green] [dim]{out}[/dim]"
        )
        self._reload_prompts()
        self._files_sig = self._compute_files_sig()

    def _form_export(self) -> None:
        """Export the Form's prompt as a single YAML to the attach dir."""
        from pathlib import Path  # noqa: PLC0415
        from acc.golden_prompts import dump_prompt  # noqa: PLC0415
        prompt = self._form_to_prompt()
        if prompt is None:
            self._set_status(
                "[yellow]Form needs a target role + a prompt to export.[/yellow]"
            )
            return
        path = self._attach_input_path()
        if not path:
            self._set_status(
                "[yellow]Enter a target directory (row below) to export to.[/yellow]"
            )
            return
        try:
            out = dump_prompt(prompt, Path(path) / f"{prompt.name}.yaml")
        except OSError as exc:
            self._set_status(f"[red]export failed: {exc}[/red]")
            return
        self._set_status(f"[green]✓ exported[/green] [dim]→ {out}[/dim]")

    def _editor_new(self) -> None:
        try:
            self._editor().text = self._EDITOR_TEMPLATE
        except Exception:
            pass
        # 047 Slice 2 — New flips to Edit + focuses the editor so the operator
        # SEES the template (the 2.6.26 "New blinks, no new form" finding).
        self.ws_mode = "edit"
        self.focus_area = "workspace"
        try:
            self._editor().focus()
        except Exception:
            pass
        self._set_status(
            "[green]✓ new template[/green] [dim]— edit + Save[/dim]"
        )

    def _editor_save(self) -> None:
        """Validate the editor YAML and write it to the writable store.

        Each Save also appends a row to the save-history log so the
        operator sees a version count — "every save is a commit"
        (proposal 044 O2)."""
        import yaml  # noqa: PLC0415
        from acc.golden_prompts import (  # noqa: PLC0415
            GoldenPrompt, append_save_history, save_prompt,
        )

        try:
            raw = self._editor().text
        except Exception:
            return
        try:
            data = yaml.safe_load(raw) or {}
            prompt = GoldenPrompt.model_validate(data)
        except Exception as exc:
            self._set_status(f"[red]invalid prompt: {exc}[/red]")
            return
        try:
            out = save_prompt(prompt)
        except OSError as exc:
            self._set_status(f"[red]save failed: {exc}[/red]")
            return
        ver = append_save_history(prompt.name, raw)
        self._set_status(
            f"[green]✓ saved (v{ver})[/green] [dim]{out}[/dim]"
        )
        self._reload_prompts()
        self._files_sig = self._compute_files_sig()

    # ------------------------------------------------------------------
    # Proposal 044 O2 — copy/paste + durable import/export
    # ------------------------------------------------------------------

    def _editor_copy(self) -> None:
        """Copy the editor's YAML to the system clipboard (OSC52 out)."""
        try:
            text = self._editor().text
        except Exception:
            return
        try:
            self.app.copy_to_clipboard(text)
        except Exception:
            logger.debug("diagnostics: copy failed", exc_info=True)
            self._set_status(
                "[yellow]copy unavailable in this terminal[/yellow]"
            )
            return
        self._set_status(
            f"[green]✓ copied[/green] [dim]{len(text)} chars[/dim]"
        )

    def _editor_paste(self) -> None:
        """Insert the app clipboard at the editor cursor.

        Pastes text copied within the TUI (Copy / Ctrl+C).  Pasting from
        the HOST clipboard is the terminal's own paste (Ctrl+Shift+V /
        right-click) — bracketed paste lands in the focused TextArea."""
        try:
            clip = self.app.clipboard or ""
        except Exception:
            clip = ""
        if not clip:
            self._set_status(
                "[yellow]app clipboard empty — use the terminal's paste "
                "(Ctrl+Shift+V) for host text[/yellow]"
            )
            return
        try:
            self._editor().insert(clip)
        except Exception:
            logger.debug("diagnostics: paste failed", exc_info=True)
            return
        self._set_status(
            f"[green]✓ pasted[/green] [dim]{len(clip)} chars[/dim]"
        )

    def _attach_input_path(self) -> str:
        try:
            return self.query_one("#golden-attach-input", Input).value.strip()
        except Exception:
            return ""

    def _export_dir(self) -> None:
        """Export the store.  047 Slice 3 — a ``.csv`` path exports CSV
        (human/Excel-Sheets), ``.json`` exports JSON (agentic interchange),
        anything else is the dir md/yaml backup."""
        from acc.golden_prompts import (  # noqa: PLC0415
            export_store, export_store_csv, export_store_json,
        )
        path = self._attach_input_path()
        if not path:
            self._set_status(
                "[yellow]Enter a target directory (md/yaml) or a .csv / "
                ".json file to export to.[/yellow]"
            )
            return
        low = path.lower()
        try:
            if low.endswith(".csv"):
                n, fmt = export_store_csv(path), "CSV"
            elif low.endswith(".json"):
                n, fmt = export_store_json(path), "JSON"
            else:
                n, fmt = export_store(path), "md/yaml"
        except OSError as exc:
            self._set_status(f"[red]export failed: {exc}[/red]")
            return
        self._set_status(
            f"[green]✓ exported {n} ({fmt})[/green] [dim]→ {path}[/dim]"
        )

    def _export_as_pack(self) -> None:
        """Export the writable golden store as an ``@scope/*`` ``.accpkg``
        (gap #5 — ``acc-pkg golden-pack``).

        The attach input, when it starts with ``@``, is the pack name with
        an optional ``@version`` suffix (``@you/uc`` or ``@you/uc@0.2.0``);
        otherwise a default ``@local/<collective>-golden@0.1.0`` is derived.
        The built pack lands under the writable store's ``_packs/`` dir (a
        durable volume); publish it with ``acc-pkg publish``.  Installed on a
        corpus, its prompts auto-load at boot (golden_roots discovery)."""
        import re  # noqa: PLC0415

        from acc.golden_prompts import writable_root  # noqa: PLC0415
        from acc.pkg.golden_pack import build_golden_pack  # noqa: PLC0415

        raw = self._attach_input_path()
        if raw.startswith("@"):
            body = raw[1:]
            if "@" in body:
                nm, version = body.rsplit("@", 1)
                name = "@" + nm
            else:
                name, version = "@" + body, "0.1.0"
        else:
            cid = self._active_collective_id() or "local"
            slug = re.sub(r"[^a-z0-9-]+", "-", cid.lower()).strip("-") or "local"
            name, version = f"@local/{slug}-golden", "0.1.0"

        try:
            out_dir = writable_root() / "_packs"
            out_dir.mkdir(parents=True, exist_ok=True)
            pslug = name.lstrip("@").replace("/", "-")
            out = out_dir / f"{pslug}-{version}.accpkg"
            result = build_golden_pack(name, version, output_path=out)
        except ValueError as exc:  # empty store
            self._set_status(f"[yellow]{exc}[/yellow]")
            return
        except Exception as exc:  # invalid name/version, build error
            logger.debug("diagnostics: pack export failed", exc_info=True)
            self._set_status(f"[red]pack failed: {exc}[/red]")
            return
        self._set_status(
            f"[green]✓ packed {result.manifest.name}@"
            f"{result.manifest.version}[/green] "
            f"[dim]→ {out}   publish: acc-pkg publish {out} "
            f"--catalog-url …[/dim]"
        )

    def _import_dir(self) -> None:
        """Import prompts into the store.  047 Slice 3 — a ``.csv`` / ``.json``
        path imports that format; anything else is a dir of md/yaml."""
        from acc.golden_prompts import (  # noqa: PLC0415
            import_store, import_store_csv, import_store_json,
        )
        path = self._attach_input_path()
        if not path:
            self._set_status(
                "[yellow]Enter a source directory (md/yaml) or a .csv / "
                ".json file to import from.[/yellow]"
            )
            return
        low = path.lower()
        try:
            if low.endswith(".csv"):
                n, fmt = import_store_csv(path), "CSV"
            elif low.endswith(".json"):
                n, fmt = import_store_json(path), "JSON"
            else:
                n, fmt = import_store(path), "md/yaml"
        except OSError as exc:
            self._set_status(f"[red]import failed: {exc}[/red]")
            return
        self._set_status(
            f"[green]✓ imported {n} ({fmt})[/green] [dim]from {path}[/dim]"
        )
        self._reload_prompts()

    def _attach_dir(self) -> None:
        """Attach the directory in the input as a watched golden root."""
        from acc.golden_prompts import add_watch_dir  # noqa: PLC0415

        try:
            path = self.query_one("#golden-attach-input", Input).value.strip()
        except Exception:
            path = ""
        if not path:
            self._set_status("[yellow]Enter a directory to attach.[/yellow]")
            return
        try:
            add_watch_dir(path)
        except OSError as exc:
            self._set_status(f"[red]attach failed: {exc}[/red]")
            return
        self._set_status(f"[green]✓ watching[/green] [dim]{path}[/dim]")
        self._reload_prompts()
        self._files_sig = self._compute_files_sig()

    # ------------------------------------------------------------------
    # PR-Y-2 — live reload watcher
    # ------------------------------------------------------------------

    def _compute_files_sig(self) -> tuple:
        """A cheap signature of (path, mtime) across all load roots."""
        from acc.golden_prompts import golden_roots  # noqa: PLC0415
        sig: list[tuple[str, float]] = []
        try:
            for root in golden_roots():
                if not root or not root.is_dir():
                    continue
                for pat in ("*.yaml", "*.md"):
                    for f in root.glob(pat):
                        try:
                            sig.append((str(f), f.stat().st_mtime))
                        except OSError:
                            continue
        except Exception:
            logger.debug("diagnostics: files-sig failed", exc_info=True)
        return tuple(sorted(sig))

    def _poll_changes(self) -> None:
        if self._running:
            return  # don't reshuffle the table mid-run
        sig = self._compute_files_sig()
        if sig != getattr(self, "_files_sig", None):
            self._files_sig = sig
            self._reload_prompts()

    def action_run_selected(self) -> None:
        if self._running:
            return
        try:
            table = self.query_one("#golden-table", DataTable)
            row_key = table.coordinate_to_cell_key(
                table.cursor_coordinate,
            ).row_key
            name = self._row_key_value(row_key)
        except Exception:
            self._set_status("[yellow]No prompt selected.[/yellow]")
            return
        if name not in self._prompts:
            self._set_status("[yellow]No prompt selected.[/yellow]")
            return
        self.run_worker(
            self._run_prompts([name]),
            exclusive=True, group="diagnostics-run",
        )

    def action_run_all(self) -> None:
        if self._running:
            return
        names = list(self._prompts.keys())
        if not names:
            self._set_status("[yellow]No prompts to run.[/yellow]")
            return
        self.run_worker(
            self._run_prompts(names),
            exclusive=True, group="diagnostics-run",
        )

    async def _run_prompts(self, names: list[str]) -> None:
        """Run *names* sequentially against the live stack."""
        from acc.golden_prompts import run_one  # noqa: PLC0415

        observer = self._active_observer()
        if observer is None:
            self._set_status(
                "[red]No NATS connection — cannot run.[/red]"
            )
            return
        cid = self._active_collective_id()

        self._running = True
        passed = 0
        try:
            for i, name in enumerate(names, start=1):
                prompt = self._prompts.get(name)
                if prompt is None:
                    continue
                self._set_status(
                    f"[yellow]Running {name} ({i}/{len(names)})…[/yellow]"
                )
                try:
                    result = await run_one(
                        prompt, observer=observer, collective_id=cid,
                    )
                except Exception as exc:
                    logger.exception("diagnostics: run_one failed for %s", name)
                    self._set_status(f"[red]{name}: {exc}[/red]")
                    continue
                self._results[name] = result
                # Proposal G — record this run to the per-prompt history
                # (carries task_id for later enrichment). Best-effort.
                try:
                    from acc.golden_prompts import (  # noqa: PLC0415
                        append_run_record,
                    )
                    append_run_record(result, collective_id=cid)
                except Exception:
                    logger.debug(
                        "diagnostics: run-record append failed", exc_info=True,
                    )
                if result.passed:
                    passed += 1
                self._update_row(name, result)
                self._render_detail(name)
            self._set_status(
                f"[b]{passed}/{len(names)} passed[/b]"
            )
            # Log the whole suite execution as one MLFlow run — no-op unless
            # ACC_MLFLOW_TRACKING_URI is set + acc[mlflow] installed. The TUI
            # keeps its own per-prompt history via append_run_record above, so
            # path=None: this adds only the experiment run + the `trace →`
            # deep links the detail panel already renders.
            try:
                from acc.backends.mlflow_runs import (  # noqa: PLC0415
                    base_run_meta,
                )
                from acc.golden_prompts import (  # noqa: PLC0415
                    persist_results,
                )
                persist_results(
                    [self._results[n] for n in names if n in self._results],
                    None,
                    run_meta=base_run_meta(
                        collective_id=cid, source="tui-diagnostics",
                    ),
                )
            except Exception:
                logger.debug(
                    "diagnostics: mlflow suite-log skipped", exc_info=True,
                )
        finally:
            self._running = False

    def _update_row(self, name: str, result) -> None:
        try:
            from textual.coordinate import Coordinate  # noqa: PLC0415
            table = self.query_one("#golden-table", DataTable)
            rows = list(table.rows.keys())
            for idx, rk in enumerate(rows):
                if self._row_key_value(rk) == name:
                    table.update_cell_at(
                        Coordinate(idx, self._COL_LAST),
                        self._format_last(result),
                    )
                    break
        except Exception:
            logger.debug("diagnostics: row update failed", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, markup: str) -> None:
        try:
            self.query_one("#diagnostics-status", Static).update(markup)
        except Exception:
            pass
        self._maybe_toast(markup)

    def _maybe_toast(self, markup: str) -> None:
        """047 G8 — fire a Textual toast for a discrete OUTCOME so New/Save/
        Send visibly do something (the 2.6.26 "blinks, no proof" finding).
        Transient/dim status ('running…', 'loaded…') stays status-only."""
        import re as _re  # noqa: PLC0415
        if "✓" in markup:
            severity = "information"
        elif markup.startswith("[red]"):
            severity = "error"
        elif markup.startswith("[yellow]"):
            severity = "warning"
        else:
            return
        plain = _re.sub(r"\[/?[^\]]*\]", "", markup).strip()
        if not plain:
            return
        try:
            self.notify(plain, severity=severity, timeout=4)
        except Exception:
            pass

    def _active_observer(self):
        observers = getattr(self.app, "_observers", None)
        idx = getattr(self.app, "_active_collective_idx", 0)
        if not observers:
            return None
        try:
            return observers[idx]
        except IndexError:
            return None

    def _active_collective_id(self) -> str:
        cid = getattr(self.app, "_active_collective_id", None)
        if callable(cid):
            cid = cid()
        if isinstance(cid, str) and cid:
            return cid
        ids = getattr(self.app, "_collective_ids", None)
        if ids:
            return ids[0]
        return "sol-01"
