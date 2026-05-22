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
from textual.widgets import Button, DataTable, Footer, Label, Static

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

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#golden-table", DataTable)
        table.add_columns("Name", "Role", "Mode", "Last")
        self._reload_prompts()

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _reload_prompts(self) -> None:
        """Populate the table from the golden-prompts library."""
        from acc.golden_prompts import load_all  # noqa: PLC0415

        table = self.query_one("#golden-table", DataTable)
        table.clear()
        self._prompts.clear()
        try:
            prompts = load_all()
        except Exception:
            logger.exception("diagnostics: load_all failed")
            prompts = []

        if not prompts:
            self._set_status(
                "[yellow]No golden prompts found — see "
                "examples/golden_prompts/.[/yellow]"
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
        if getattr(event, "data_table", None) is None:
            return
        if event.data_table.id != "golden-table":
            return
        name = self._row_key_value(event.row_key)
        self._render_detail(name)

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
