"""ACC TUI — InfuseScreen: role definition composition and dispatch form.

Renders all RoleDefinitionConfig fields as editable Textual widgets.
Submitting the form publishes a ROLE_UPDATE signal on NATS; the TUI
does NOT sign the payload (arbiter countersign via ACC-6a RoleStore).

ACC-TUI-Evolution updates (REQ-TUI-020 – REQ-TUI-022):
  - Role Select populated dynamically from list_roles() at mount time
  - Task types populated from the selected role's task_types via RoleLoader
  - New fields: allowed_actions, domain_id, domain_receptors
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)
from textual.reactive import reactive

from acc.role_loader import RoleLoader, list_roles
from acc.signals import subject_role_update
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot


_PERSONAS = [
    ("concise", "concise"),
    ("formal", "formal"),
    ("exploratory", "exploratory"),
    ("analytical", "analytical"),
]

# Fallback role list when roles/ directory is unavailable at import time.
# on_mount replaces this with the live filesystem scan (REQ-TUI-020).
_FALLBACK_ROLES = [
    ("ingester", "ingester"),
    ("analyst", "analyst"),
    ("synthesizer", "synthesizer"),
    ("arbiter", "arbiter"),
    ("observer", "observer"),
]


def _roles_root() -> str:
    return os.environ.get("ACC_ROLES_ROOT", "roles")


class InfuseScreen(Screen):
    """Role infusion form — compose and apply role definitions to the collective."""

    BINDINGS = [
        ("ctrl+a", "apply", "Apply"),
        ("ctrl+l", "clear", "Clear"),
        ("ctrl+h", "toggle_history", "History"),
        ("q", "app.quit", "Quit"),
        ("1", "navigate('soma')", "Soma"),
        ("2", "navigate('nucleus')", "Nucleus"),
        ("3", "navigate('compliance')", "Compliance"),
        ("4", "navigate('comms')", "Comms"),
        ("5", "navigate('performance')", "Performance"),
        ("6", "navigate('ecosystem')", "Ecosystem"),
        ("7", "navigate('prompt')", "Prompt"),
    ]

    history_rows: reactive[list[dict]] = reactive([], layout=True)
    status_text: reactive[str] = reactive("Ready")
    show_history: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        self._dynamic_task_types: list[str] = []

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="nucleus", id="nav")
        yield Label("ACC Role Infusion — Nucleus", id="screen-title")

        with ScrollableContainer():
            with Horizontal(id="row-collective"):
                yield Label("Collective:", classes="field-label")
                yield Input(
                    placeholder="sol-01",
                    id="input-collective",
                    classes="input-short",
                )
                yield Label("Role:", classes="field-label")
                # Populated dynamically in on_mount (REQ-TUI-020)
                yield Select(
                    options=_FALLBACK_ROLES,
                    id="select-role",
                    value="ingester",
                    allow_blank=False,
                )

            yield Label("Purpose", classes="section-label")
            yield TextArea(id="textarea-purpose", classes="textarea-tall")

            with Horizontal(id="row-persona-version"):
                yield Label("Persona:", classes="field-label")
                yield Select(
                    options=_PERSONAS,
                    id="select-persona",
                    value="concise",
                    allow_blank=False,
                )
                yield Label("Version:", classes="field-label")
                yield Input(
                    value="0.1.0",
                    id="input-version",
                    classes="input-short",
                )

            yield Label("Task types (from role — comma-separated)", classes="section-label")
            yield Input(id="input-task-types", placeholder="TASK_ASSIGN, CODE_GENERATE …")

            yield Label("Allowed actions (comma-separated)", classes="section-label")
            yield Input(
                id="input-allowed-actions",
                placeholder="read_vector_db, write_working_memory …",
            )

            yield Label("Domain ID", classes="section-label")
            yield Input(id="input-domain-id", placeholder="software_engineering", classes="input-short")

            yield Label("Domain receptors (comma-separated)", classes="section-label")
            yield Input(
                id="input-domain-receptors",
                placeholder="software_engineering, it_security …",
            )

            yield Label("Seed context", classes="section-label")
            yield TextArea(id="textarea-seed", classes="textarea-medium")

            yield Label("Cat-B overrides", classes="section-label")
            with Horizontal(id="row-cat-b"):
                yield Label("token_budget:", classes="field-label")
                yield Input(value="2048", id="input-token-budget", classes="input-short")
                yield Label("rate_limit_rpm:", classes="field-label")
                yield Input(value="60", id="input-rate-rpm", classes="input-short")

            with Horizontal(id="row-actions"):
                yield Button("Apply ↵", id="btn-apply", variant="primary")
                yield Button("Clear", id="btn-clear")
                yield Button("History ▼", id="btn-history", variant="default")

            yield Static(id="status-bar", classes="status-bar")

            with Container(id="history-panel"):
                yield Label("── History ──────────────────────────────────", classes="section-label")
                yield DataTable(id="history-table")

        yield Footer()

    def on_mount(self) -> None:
        """Populate role Select dynamically from filesystem (REQ-TUI-020)."""
        table = self.query_one("#history-table", DataTable)
        table.add_columns("Version", "Timestamp", "Event", "Approver")
        self._refresh_status()
        self._set_history_visible(False)
        self._load_dynamic_roles()

    def _load_dynamic_roles(self) -> None:
        """Scan roles/ and populate the Select widget (REQ-TUI-020)."""
        root = _roles_root()
        role_names = list_roles(root)
        if not role_names:
            return  # keep fallback options

        select = self.query_one("#select-role", Select)
        options = [(name, name) for name in role_names]
        select.set_options(options)

        # Pre-populate task types for the first role
        if role_names:
            self._populate_task_types(role_names[0])

    def _populate_task_types(self, role_name: str) -> None:
        """Load task_types from the selected role and fill the input (REQ-TUI-021)."""
        root = _roles_root()
        loader = RoleLoader(root, role_name)
        role_def = loader.load()
        if role_def is None:
            return
        self._dynamic_task_types = list(role_def.task_types or [])
        task_input = self.query_one("#input-task-types", Input)
        task_input.value = ", ".join(self._dynamic_task_types)

        # Also populate domain_id and domain_receptors from role definition
        domain_id_input = self.query_one("#input-domain-id", Input)
        domain_id_input.value = getattr(role_def, "domain_id", "") or ""

        receptors = getattr(role_def, "domain_receptors", []) or []
        domain_rec_input = self.query_one("#input-domain-receptors", Input)
        domain_rec_input.value = ", ".join(receptors)

    def preload_from_role(self, role_name: str) -> None:
        """Pre-fill the entire form from a roles/<name>/role.yaml definition.

        Called by the App when the user clicks "Schedule infusion" in the
        Ecosystem screen.  Resolves the role via RoleLoader and populates
        every editable field — Select, Inputs, TextAreas — so the operator
        can review and Apply without re-typing.

        Falls back gracefully if the role does not exist or is malformed:
        the form keeps its current values and the status bar reports the
        problem.
        """
        root = _roles_root()
        loader = RoleLoader(root, role_name)
        role_def = loader.load()
        if role_def is None:
            self.status_text = f"⚠ Could not load role {role_name!r}"
            return

        # Switch the Select widget to the named role.  This will also
        # trigger on_select_changed → _populate_task_types, but we set the
        # remaining fields explicitly afterwards so partial data from the
        # previous role does not linger.
        try:
            self.query_one("#select-role", Select).value = role_name
        except Exception:
            pass

        # Persona dropdown — guard against custom personas not in _PERSONAS
        try:
            persona = role_def.persona or "concise"
            self.query_one("#select-persona", Select).value = persona
        except Exception:
            pass

        # Version
        self.query_one("#input-version", Input).value = role_def.version or "0.1.0"

        # Purpose + seed_context
        self.query_one("#textarea-purpose", TextArea).text = role_def.purpose or ""
        self.query_one("#textarea-seed", TextArea).text = role_def.seed_context or ""

        # Task types, allowed actions
        self._dynamic_task_types = list(role_def.task_types or [])
        self.query_one("#input-task-types", Input).value = ", ".join(self._dynamic_task_types)
        allowed = list(role_def.allowed_actions or [])
        self.query_one("#input-allowed-actions", Input).value = ", ".join(allowed)

        # Domain identity (ACC-11)
        self.query_one("#input-domain-id", Input).value = (
            getattr(role_def, "domain_id", "") or ""
        )
        receptors = list(getattr(role_def, "domain_receptors", []) or [])
        self.query_one("#input-domain-receptors", Input).value = ", ".join(receptors)

        # Cat-B overrides — coerce numeric values from the role's overrides dict
        overrides = role_def.category_b_overrides or {}
        token_budget = overrides.get("token_budget", 2048)
        rate_rpm = overrides.get("rate_limit_rpm", 60)
        self.query_one("#input-token-budget", Input).value = str(token_budget)
        self.query_one("#input-rate-rpm", Input).value = str(rate_rpm)

        self.status_text = f"Pre-filled from roles/{role_name}/ — review and Apply"

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Reload task types when role selection changes (REQ-TUI-021)."""
        if event.select.id == "select-role":
            role_name = str(event.value) if event.value else ""
            if role_name:
                self._populate_task_types(role_name)

    # ------------------------------------------------------------------
    # Reactive watchers
    # ------------------------------------------------------------------

    def watch_history_rows(self, rows: list[dict]) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear()
        for row in rows[:20]:
            ts = row.get("ts", 0)
            ts_str = _format_ts(ts) if ts else "—"
            table.add_row(
                row.get("new_version", "—"),
                ts_str,
                row.get("event_type", "—"),
                row.get("approver_id", "—") or "—",
            )

    def watch_status_text(self, text: str) -> None:
        self.query_one("#status-bar", Static).update(text)

    def watch_show_history(self, show: bool) -> None:
        self._set_history_visible(show)

    # ------------------------------------------------------------------
    # Snapshot update
    # ------------------------------------------------------------------

    def apply_snapshot(self, snapshot: "CollectiveSnapshot") -> None:
        """Update the history panel from the latest collective snapshot."""
        if snapshot.role_audit_rows:
            self.history_rows = snapshot.role_audit_rows
        if "Awaiting" in self.status_text:
            submitted_ver = self.query_one("#input-version", Input).value.strip()
            if any(a.role_version == submitted_ver for a in snapshot.agents.values()):
                self.status_text = f"✓ Role {submitted_ver!r} applied"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-apply":
            self.action_apply()
        elif event.button.id == "btn-clear":
            self.action_clear()
        elif event.button.id == "btn-history":
            self.action_toggle_history()

    def action_apply(self) -> None:
        """Build ROLE_UPDATE payload and publish to NATS (REQ-INF-003/004)."""
        collective_id = self.query_one("#input-collective", Input).value.strip() or "sol-01"
        role = str(self.query_one("#select-role", Select).value or "ingester")
        persona = str(self.query_one("#select-persona", Select).value or "concise")
        version = self.query_one("#input-version", Input).value.strip() or "0.1.0"
        purpose = self.query_one("#textarea-purpose", TextArea).text
        seed = self.query_one("#textarea-seed", TextArea).text

        # Dynamic task types from the input field (REQ-TUI-021)
        raw_tasks = self.query_one("#input-task-types", Input).value
        task_types = [t.strip() for t in raw_tasks.split(",") if t.strip()]

        # Allowed actions (REQ-TUI-022)
        raw_actions = self.query_one("#input-allowed-actions", Input).value
        allowed_actions = [a.strip() for a in raw_actions.split(",") if a.strip()]

        # Domain fields (REQ-TUI-022)
        domain_id = self.query_one("#input-domain-id", Input).value.strip()
        raw_receptors = self.query_one("#input-domain-receptors", Input).value
        domain_receptors = [r.strip() for r in raw_receptors.split(",") if r.strip()]

        try:
            token_budget = float(self.query_one("#input-token-budget", Input).value or "0")
            rate_rpm = float(self.query_one("#input-rate-rpm", Input).value or "0")
        except ValueError:
            self.status_text = "⚠ Invalid Cat-B override values"
            return

        payload = {
            "signal_type": "ROLE_UPDATE",
            "agent_id": "",
            "collective_id": collective_id,
            "ts": time.time(),
            "approver_id": "",
            "signature": "",
            "role_definition": {
                "purpose": purpose,
                "persona": persona,
                "version": version,
                "task_types": task_types,
                "seed_context": seed,
                "allowed_actions": allowed_actions,
                "domain_id": domain_id,
                "domain_receptors": domain_receptors,
                "category_b_overrides": {
                    "token_budget": token_budget,
                    "rate_limit_rpm": rate_rpm,
                },
            },
        }

        self.app.post_message(_PublishMessage(subject_role_update(collective_id), payload))
        self.status_text = "Awaiting arbiter approval…"

    def action_clear(self) -> None:
        """Reset all widgets to defaults."""
        self.query_one("#textarea-purpose", TextArea).clear()
        self.query_one("#textarea-seed", TextArea).clear()
        self.query_one("#input-version", Input).value = "0.1.0"
        self.query_one("#input-token-budget", Input).value = "2048"
        self.query_one("#input-rate-rpm", Input).value = "60"
        self.query_one("#input-task-types", Input).value = ""
        self.query_one("#input-allowed-actions", Input).value = ""
        self.query_one("#input-domain-id", Input).value = ""
        self.query_one("#input-domain-receptors", Input).value = ""
        self.status_text = "Cleared"

    def action_toggle_history(self) -> None:
        self.show_history = not self.show_history

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_history_visible(self, visible: bool) -> None:
        panel = self.query_one("#history-panel")
        panel.display = visible

    def _refresh_status(self) -> None:
        self.query_one("#status-bar", Static).update(self.status_text)


# ---------------------------------------------------------------------------
# Internal message for NATS publish
# ---------------------------------------------------------------------------

from textual.message import Message  # noqa: E402 (must be after screen definition)


class _PublishMessage(Message):
    """Internal message requesting NATSObserver.publish()."""

    def __init__(self, subject: str, payload: dict) -> None:
        super().__init__()
        self.subject = subject
        self.payload = payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_ts(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
