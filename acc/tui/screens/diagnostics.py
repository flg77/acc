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
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from acc.tui.widgets.nav_bar import NavigateTo, NavigationBar

logger = logging.getLogger("acc.tui.diagnostics")

# Proposal 033 WS-B — operating modes offered in the Form's Mode
# selector (mirrors acc.operating_modes: PLAN / ACCEPT_EDITS /
# ASK_PERMISSIONS / AUTO).
_OPERATING_MODES = ("PLAN", "ACCEPT_EDITS", "ASK_PERMISSIONS", "AUTO")


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
        # Proposal 033 WS-B — name of the prompt currently mirrored into
        # the Form tab (reused as the GoldenPrompt name when sending).
        self._current_name = ""

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
                # PR-Y-2 + proposal 033 WS-B — Form/MD subnav.  The
                # MD (YAML) tab is the authoritative editor (highlight a
                # row → its YAML loads here; Save validates + writes to
                # the writable store; New starts a template).  The Form
                # tab is a human-readable projection of the important
                # fields (role / agent / mode / timeout / prompt) with a
                # Send button that dispatches the (possibly retargeted)
                # prompt to a live agent.  Switching to Form re-derives
                # its fields from the YAML so the Form reflects the MD.
                yield Label("EDIT / SEND", classes="panel-label")
                with TabbedContent(id="golden-edit-tabs"):
                    with TabPane("Form", id="tab-golden-form"):
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
                        with Horizontal(id="form-actions"):
                            yield Button(
                                "Send", id="btn-golden-send",
                                variant="success",
                            )
                    with TabPane("MD (YAML)", id="tab-golden-md"):
                        yield TextArea(
                            "", id="golden-editor", language="yaml",
                        )
                with Horizontal(id="golden-editor-actions"):
                    yield Button("New", id="btn-golden-new", variant="default")
                    yield Button(
                        "Save", id="btn-golden-save", variant="primary",
                    )
                    # Proposal 044 O2 — in-TUI copy/paste affordances.
                    yield Button(
                        "Copy", id="btn-golden-copy", variant="default",
                    )
                    yield Button(
                        "Paste", id="btn-golden-paste", variant="default",
                    )
                    # Proposal G — restore a previous saved version.
                    yield Button(
                        "Versions", id="btn-golden-versions",
                        variant="default",
                    )
                    # Proposal G P3 — promote to a role's behavioral eval pack.
                    yield Button(
                        "→ Eval", id="btn-golden-promote-eval",
                        variant="default",
                    )
                with Horizontal(id="golden-attach-row"):
                    yield Input(
                        placeholder=(
                            "dir to watch / import / export, "
                            "e.g. /host-home/golden"
                        ),
                        id="golden-attach-input",
                    )
                    yield Button(
                        "+ Add", id="btn-golden-add-dir", variant="default",
                    )
                    # Proposal 044 O2 — durable backup: import COPIES a
                    # dir's prompts into the writable store; export writes
                    # the store out to the dir (survives a volume reset).
                    yield Button(
                        "Import", id="btn-golden-import", variant="default",
                    )
                    yield Button(
                        "Export", id="btn-golden-export", variant="default",
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
        self._load_into_editor(name)
        self._load_into_form(name)
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
        elif bid == "btn-golden-send":
            self.action_send()

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
            name=self._current_name or "form_send",
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

    def _editor_new(self) -> None:
        try:
            self._editor().text = self._EDITOR_TEMPLATE
        except Exception:
            pass
        self._set_status("[dim]New prompt template — edit + Save.[/dim]")

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
        """Export the writable store to the dir in the attach input."""
        from acc.golden_prompts import export_store  # noqa: PLC0415

        path = self._attach_input_path()
        if not path:
            self._set_status(
                "[yellow]Enter a target directory to export to.[/yellow]"
            )
            return
        try:
            n = export_store(path)
        except OSError as exc:
            self._set_status(f"[red]export failed: {exc}[/red]")
            return
        self._set_status(
            f"[green]✓ exported {n}[/green] [dim]→ {path}[/dim]"
        )

    def _import_dir(self) -> None:
        """Import (copy) prompts from the attach-input dir into the store."""
        from acc.golden_prompts import import_store  # noqa: PLC0415

        path = self._attach_input_path()
        if not path:
            self._set_status(
                "[yellow]Enter a source directory to import from.[/yellow]"
            )
            return
        try:
            n = import_store(path)
        except OSError as exc:
            self._set_status(f"[red]import failed: {exc}[/red]")
            return
        self._set_status(
            f"[green]✓ imported {n}[/green] [dim]from {path}[/dim]"
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
