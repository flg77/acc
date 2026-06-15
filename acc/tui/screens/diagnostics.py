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
    Static,
    TextArea,
)

from acc.tui.widgets.nav_bar import NavigateTo, NavigationBar

logger = logging.getLogger("acc.tui.diagnostics")


class DiagnosticsScreen(Screen):
    """Pane #9 — run the golden-prompt suite against the live stack."""

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
    ]

    snapshot: reactive["Any | None"] = reactive(None, layout=True)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # Loaded golden prompts keyed by name; the table's row key is
        # the prompt name so result updates can target the right row.
        self._prompts: dict[str, Any] = {}
        # Last result per prompt name (GoldenResult); rendered in the
        # detail panel + the "Last" column.
        self._results: dict[str, Any] = {}
        self._running = False

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="diagnostics", id="nav")
        yield Label(
            "ACC Diagnostics — Golden Prompt Suite", id="diagnostics-title",
        )

        with Horizontal(id="diagnostics-main"):
            with Vertical(id="diagnostics-left"):
                yield Label("GOLDEN PROMPTS", classes="panel-label")
                yield DataTable(id="golden-table")
                with Horizontal(id="diagnostics-actions"):
                    yield Button(
                        "Run selected", id="btn-run-selected",
                        variant="primary",
                    )
                    yield Button("Run all", id="btn-run-all", variant="default")
                yield Static("", id="diagnostics-status")

            with Vertical(id="diagnostics-right"):
                yield Label("RESULT DETAIL", classes="panel-label")
                with ScrollableContainer(id="diagnostics-detail-container"):
                    yield Static(
                        "[dim]Select a prompt to see its definition; "
                        "run it (r) or run all (a) to see pass/fail "
                        "detail.[/dim]",
                        id="diagnostics-detail",
                    )
                # PR-Y-2 — in-pane editor: write/edit golden prompts.
                # Highlighting a row loads its YAML here; Save validates
                # + writes to the writable store; New starts a blank
                # template.  "+ Add" attaches a directory (e.g. of
                # markdown prompts) that the pane watches + reloads.
                yield Label(
                    "EDIT / NEW (YAML) — Enter/click a row to load it",
                    classes="panel-label",
                )
                yield TextArea("", id="golden-editor", language="yaml")
                with Horizontal(id="golden-editor-actions"):
                    yield Button("New", id="btn-golden-new", variant="default")
                    yield Button(
                        "Save", id="btn-golden-save", variant="primary",
                    )
                with Horizontal(id="golden-attach-row"):
                    yield Input(
                        placeholder="dir to watch, e.g. /host-home/golden",
                        id="golden-attach-input",
                    )
                    yield Button(
                        "+ Add", id="btn-golden-add-dir", variant="default",
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
        table.add_columns("Name", "Role", "Mode", "Last")
        self._reload_prompts()
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

        for p in prompts:
            self._prompts[p.name] = p
            last = self._results.get(p.name)
            last_cell = self._format_last(last)
            table.add_row(
                p.name, p.target_role, p.operating_mode, last_cell,
                key=p.name,
            )
        self._set_status(f"[dim]{len(prompts)} prompt(s) loaded.[/dim]")

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
        # Moving the cursor only updates the (read-only) detail panel.
        # Loading into the editor is deferred to an EXPLICIT row select
        # (Enter / click) so a background file-reload — which resets the
        # cursor — can never clobber the operator's unsaved editor edits.
        if getattr(event, "data_table", None) is None:
            return
        if event.data_table.id != "golden-table":
            return
        self._render_detail(self._row_key_value(event.row_key))

    def on_data_table_row_selected(self, event) -> None:
        # Enter / click on a row: load it into the editor for tweaking.
        if getattr(event, "data_table", None) is None:
            return
        if event.data_table.id != "golden-table":
            return
        name = self._row_key_value(event.row_key)
        self._render_detail(name)
        self._load_into_editor(name)
        if name in self._prompts:
            self._set_status(
                f"[dim]loaded[/dim] [b]{name}[/b] "
                f"[dim]into editor — tweak + Save (writes to your "
                f"writable store as a copy).[/dim]"
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
            if result.error:
                lines.append(f"  [red]ERROR: {result.error}[/red]")
            for f in result.failures:
                lines.append(f"  · {f}")
            if result.output_excerpt:
                lines.append("")
                lines.append("[b]reply excerpt[/b]")
                lines.append(f"  {result.output_excerpt}")
        panel.update("\n".join(line for line in lines if line is not None))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-run-selected":
            self.action_run_selected()
        elif bid == "btn-run-all":
            self.action_run_all()
        elif bid == "btn-golden-new":
            self._editor_new()
        elif bid == "btn-golden-save":
            self._editor_save()
        elif bid == "btn-golden-add-dir":
            self._attach_dir()

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

    def _editor_new(self) -> None:
        try:
            self._editor().text = self._EDITOR_TEMPLATE
        except Exception:
            pass
        self._set_status("[dim]New prompt template — edit + Save.[/dim]")

    def _editor_save(self) -> None:
        """Validate the editor YAML and write it to the writable store."""
        import yaml  # noqa: PLC0415
        from acc.golden_prompts import GoldenPrompt, save_prompt  # noqa: PLC0415

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
        self._set_status(f"[green]✓ saved[/green] [dim]{out}[/dim]")
        self._reload_prompts()
        self._files_sig = self._compute_files_sig()

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
                if result.passed:
                    passed += 1
                self._update_row(name, result)
                self._render_detail(name)
            self._set_status(
                f"[b]{passed}/{len(names)} passed[/b]"
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
                        Coordinate(idx, 3), self._format_last(result),
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
