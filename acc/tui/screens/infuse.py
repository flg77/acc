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

import logging
import os
import time
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("acc.tui.screens.infuse")

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


def _resolve_collective_path():
    """PR-D — resolve `./collective.yaml`.

    Precedence: ``ACC_COLLECTIVE_PATH`` env > ``/app/collective.yaml``
    (the canonical mount inside the acc-tui container, added by PR-C
    in ``container/production/podman-compose.yml``) >
    ``./collective.yaml`` (host-run fallback).  Same shape as
    ``EcosystemScreen._resolve_collective_path``.
    """
    from pathlib import Path  # noqa: PLC0415
    explicit = os.environ.get("ACC_COLLECTIVE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    container_path = Path("/app/collective.yaml")
    if container_path.is_file():
        return container_path
    return Path("collective.yaml")


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
        ("8", "navigate('configuration')", "Configuration"),
    ]

    history_rows: reactive[list[dict]] = reactive([], layout=True)
    status_text: reactive[str] = reactive("Ready")
    show_history: reactive[bool] = reactive(False)

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        self._dynamic_task_types: list[str] = []
        # Set by preload_from_role when InfuseScreen hasn't been mounted
        # yet (first 'i' press in a session — compose hasn't run, so
        # `query_one("#input-version")` raises NoMatches).  on_mount
        # replays the preload once the widget tree is real.
        self._pending_preload: str = ""
        # PR-D — spawn-on-Apply state.  `_apply_started_ts` filters
        # the heartbeat watcher so it only marks "agent registered"
        # for HEARTBEATs that arrive AFTER the operator hit Apply
        # (avoids matching an existing agent of the same role).
        # `_pending_apply` holds the `(role, cluster_id)` tuple we're
        # waiting for; cleared once an agent matching it heartbeats.
        self._apply_started_ts: float = 0.0
        self._pending_apply: tuple[str, str | None] | None = None
        # 033 WS-G Part 2 — lazily-built (skill_reg, mcp_reg) tuple for the
        # caps panel; None until first _capability_registries() call.
        self._caps_registries: tuple[Any, Any] | None = None

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

            # 033 WS-G Part 2 — Active LLM + caps overview for the selected
            # role.  The Active-LLM line shows the model the role is BOUND to
            # in collective.yaml (AgentSpec.model, resolved to a human label
            # via acc.models.get_model); "—" when unbound.  The two caps
            # tables show the allowed∩installed skills + MCPs (the
            # capabilities this role can actually use on this deploy).
            yield Static("Active LLM: —", id="active-llm-line", classes="status-bar")
            with Horizontal(id="row-caps"):
                with Container(id="caps-skills-box"):
                    yield Label("Skills (allowed ∩ installed)", classes="section-label")
                    yield DataTable(id="caps-skills-table")
                with Container(id="caps-mcps-box"):
                    yield Label("MCPs (allowed ∩ installed)", classes="section-label")
                    yield DataTable(id="caps-mcps-table")

            # PR-D — cluster_id surface.  Tags the agent the operator's
            # about to spawn with a cluster grouping the arbiter's
            # PlanExecutor uses for task fan-out.  Free-form string;
            # empty means "no cluster".
            with Horizontal(id="row-cluster-id"):
                yield Label("Cluster id:", classes="field-label")
                yield Input(
                    placeholder="e.g. backend, planner, …",
                    id="input-cluster-id",
                    classes="input-short",
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
        # 033 WS-G Part 2 — caps tables: one column each (the capability id).
        self.query_one("#caps-skills-table", DataTable).add_columns("skill")
        self.query_one("#caps-mcps-table", DataTable).add_columns("mcp")
        self._refresh_status()
        self._set_history_visible(False)
        self._load_dynamic_roles()
        # Replay any preload_from_role() call that arrived before compose
        # had created the widget tree (first 'i' press of the session).
        if self._pending_preload:
            pending, self._pending_preload = self._pending_preload, ""
            try:
                self.preload_from_role(pending)
            except Exception:
                logger.exception(
                    "infuse: replayed preload_from_role failed for %r",
                    pending,
                )

    def _load_dynamic_roles(self) -> None:
        """Scan roles/ and populate the Select widget (REQ-TUI-020)."""
        root = _roles_root()
        role_names = list_roles(root)
        if not role_names:
            return  # keep fallback options

        select = self.query_one("#select-role", Select)
        options = [(name, name) for name in role_names]
        select.set_options(options)
        self._scanned_roles = list(role_names)

        # Pre-populate task types for the first role
        if role_names:
            self._populate_task_types(role_names[0])
            self._refresh_role_caps(role_names[0])

    def on_screen_resume(self) -> None:
        """N9 — re-scan roles when Nucleus is re-shown, so an agentset infused
        from the Ecosystem pane appears in the dropdown without a TUI restart.

        The TUI screens are installed singletons (``on_mount`` runs once), so
        a role added after the first mount would otherwise never show up here —
        the 25.6.26 finding "TUI not updated according to the newly deployed
        agentset" (image 9).  Selection + form are preserved across the
        refresh (Select.Changed is suppressed so the detail form doesn't
        reload to a different role).
        """
        try:
            select = self.query_one("#select-role", Select)
        except Exception:
            return
        names = list_roles(_roles_root())
        if not names or names == getattr(self, "_scanned_roles", []):
            return  # nothing new
        current = select.value
        with select.prevent(Select.Changed):
            select.set_options([(n, n) for n in names])
            if current in names:
                select.value = current
        self._scanned_roles = list(names)
        self.status_text = f"Roles refreshed — {len(names)} available"

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

        Mount-safety: when InfuseScreen has not been pushed onto the stage
        yet (first 'i' press in a session), its `compose` hasn't run so
        none of the `#input-*` widgets exist, and `query_one` raises
        ``NoMatches``.  We stash the role name in ``_pending_preload`` and
        let ``on_mount`` (or the next compose pass) replay this method
        once the widget tree is real.
        """
        try:
            self.query_one("#input-version", Input)
        except Exception:
            # Widget tree not mounted yet — defer until on_mount fires.
            self._pending_preload = role_name
            return
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

        # 033 WS-G Part 2 — refresh the caps tables + Active-LLM line for the
        # role we just pre-filled.
        self._refresh_role_caps(role_name, role_def=role_def)

        # N8 — seed the History panel with this role's release tag when no
        # live arbiter audit has arrived, so it names the active release
        # instead of sitting empty (25.6.26 image 7).
        self._seed_history_from_role(role_def)

        self.status_text = f"Pre-filled from roles/{role_name}/ — review and Apply"

    def _seed_history_from_role(self, role_def) -> None:
        """N8 — show the selected role's current release in the History panel.

        A no-op once live role-audit rows have arrived from the arbiter
        (those carry the real promotion chain and always win).
        """
        if getattr(self, "_has_live_audit", False):
            return
        version = getattr(role_def, "version", "") or "0.1.0"
        self.history_rows = [
            {
                "new_version": version,
                "event_type": "current release",
                "ts": 0,
                "approver_id": "—",
            }
        ]

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Reload the WHOLE detail form when the role dropdown changes.

        Previously this only refreshed task_types + caps, so version /
        persona / seed_context / token_budget / rate_limit kept the prior
        role's values (or the 2048/0.1.0 compose defaults) — the
        "dropdown is static, budget never changes" finding (TUI test
        2026-06-25, images 4/7/8).  Re-use the full ``preload_from_role``
        path so every editable field reflects the selected role's
        role.yaml.  ``_loading_role`` guards against the re-entrant
        ``Select.Changed`` that ``preload_from_role`` itself can post when
        it re-asserts the Select value.
        """
        if event.select.id != "select-role":
            return
        if getattr(self, "_loading_role", False):
            return
        role_name = str(event.value) if event.value else ""
        if not role_name:
            return
        self._loading_role = True
        try:
            self.preload_from_role(role_name)
        finally:
            self._loading_role = False

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
            self._has_live_audit = True  # N8 — real arbiter history now wins
            self.history_rows = snapshot.role_audit_rows
        if "Awaiting" in self.status_text:
            submitted_ver = self.query_one("#input-version", Input).value.strip()
            if any(a.role_version == submitted_ver for a in snapshot.agents.values()):
                self.status_text = f"✓ Role {submitted_ver!r} applied"

        # PR-D — when Apply also requested a spawn, watch for a NEW
        # agent (registered AFTER `_apply_started_ts`) whose role +
        # cluster_id match the pending tuple.  Status flips to
        # "Agent <id> registered" the moment the reconcile lands and
        # the new agent heartbeats.  Bail out cleanly if the screen
        # has no pending apply or no started-ts.
        if self._pending_apply is None or not self._apply_started_ts:
            return
        want_role, want_cluster = self._pending_apply
        for agent_id, agent in snapshot.agents.items():
            if getattr(agent, "role", "") != want_role:
                continue
            # cluster_id propagates via REGISTER once PR-B-era agents
            # ingest ACC_CLUSTER_ID.  Match strictly when set; allow
            # any when None.
            agent_cid = getattr(agent, "cluster_id", None)
            if want_cluster is not None and agent_cid != want_cluster:
                continue
            registered_at = (
                getattr(agent, "registered_ts", None)
                or getattr(agent, "first_seen_ts", None)
                or 0.0
            )
            if registered_at and registered_at < self._apply_started_ts:
                continue
            # Match — clear the pending tuple so re-applies start a
            # fresh wait.
            self.status_text = (
                f"✓ Agent {agent_id} registered "
                f"(role={want_role}"
                f"{', cluster=' + want_cluster if want_cluster else ''})"
            )
            self._pending_apply = None
            self._apply_started_ts = 0.0
            return

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

        # Proposal 008 — parity with CLI infuse.
        # Load the role's full pydantic dump from disk (same path the
        # CLI uses) so the published role_definition is the SUPERSET
        # of what's on disk; the 9 visible form values overlay the
        # operator's per-infusion edits.  The arbiter previously
        # accepted the TUI's 9-field subset OK, but the divergence
        # was a latent regression risk surfaced by the proposal 003
        # PR-6 parity test.
        role_name = str(self.query_one("#select-role", Select).value or "")
        try:
            root = _roles_root()
            loaded = RoleLoader(root, role_name).load() if role_name else None
        except Exception:
            logger.exception("infuse: full-dump load failed for %r", role_name)
            loaded = None
        if loaded is not None and hasattr(loaded, "model_dump"):
            full_role_def: dict[str, Any] = loaded.model_dump()
        else:
            full_role_def = {}

        # Overlay the form's per-infusion edits.  We preserve any
        # disk-only fields (allowed_skills, max_skill_risk_level,
        # parent_role, …) through verbatim.
        form_overlay: dict[str, Any] = {
            "purpose": purpose,
            "persona": persona,
            "version": version,
            "task_types": task_types,
            "seed_context": seed,
            "allowed_actions": allowed_actions,
            "domain_id": domain_id,
            "domain_receptors": domain_receptors,
        }
        # category_b_overrides: keep the disk's keys but override
        # token_budget + rate_limit_rpm with the form's values.
        cat_b_overlay = dict(full_role_def.get("category_b_overrides", {}) or {})
        cat_b_overlay["token_budget"] = token_budget
        cat_b_overlay["rate_limit_rpm"] = rate_rpm
        form_overlay["category_b_overrides"] = cat_b_overlay

        merged_role_def = {**full_role_def, **form_overlay}

        payload = {
            "signal_type": "ROLE_UPDATE",
            "agent_id": "",
            "collective_id": collective_id,
            "ts": time.time(),
            "approver_id": "",
            "signature": "",
            "role_definition": merged_role_def,
        }

        self.app.post_message(_PublishMessage(subject_role_update(collective_id), payload))
        self.status_text = "Awaiting arbiter approval…"

        # PR-D — write the (role, cluster_id, purpose) tuple to
        # collective.yaml and ask the host-side apply-watcher to
        # reconcile, so Apply actually CREATES the agent (not just
        # updates the role on already-running ones).  Best-effort:
        # any failure logs + leaves the legacy ROLE_UPDATE published
        # above as the only effect.
        cluster_id = self.query_one("#input-cluster-id", Input).value.strip() or None
        self._apply_started_ts = time.time()
        self._pending_apply = (role, cluster_id)
        try:
            self._spawn_via_collective(
                role=role, cluster_id=cluster_id,
                purpose=purpose.strip() or None,
            )
        except Exception:
            logger.exception("infuse: collective spawn path failed; "
                              "legacy ROLE_UPDATE still published")

    def _spawn_via_collective(
        self,
        *,
        role: str,
        cluster_id: str | None,
        purpose: str | None,
    ) -> None:
        """PR-D — Apply's NEW semantic: upsert into collective.yaml and
        request a reconcile so a fresh agent actually comes up.

        Writes the agent entry through
        :func:`acc.collective.upsert_agent_entry` (idempotent — bumps
        replicas on a matching role+cluster_id, else appends).  Then
        touches ``./.acc-apply.request`` next to the spec; the
        host-side watcher (or the operator running
        ``./acc-deploy.sh apply`` by hand) reconciles podman state.

        No-op when no collective.yaml is reachable — the legacy
        ROLE_UPDATE path the caller already published remains the
        only effect.
        """
        from acc.collective import upsert_agent_entry  # noqa: PLC0415

        path = _resolve_collective_path()
        if not path.exists():
            logger.info(
                "infuse: collective.yaml not found at %s — "
                "spawn path skipped (legacy ROLE_UPDATE only)", path,
            )
            self.status_text = (
                "Awaiting arbiter approval (no collective.yaml — "
                "agent will NOT auto-spawn)"
            )
            return

        try:
            upsert_agent_entry(
                path, role,
                cluster_id=cluster_id,
                purpose=purpose,
                replicas=1,
            )
        except Exception:
            logger.exception("infuse: upsert_agent_entry failed for %s", path)
            return

        # Touch the apply-request marker.  Same convention as PR-C's
        # Ecosystem Agentset Apply: a host-side watcher picks it up
        # and runs `./acc-deploy.sh apply <spec>`.
        try:
            (path.parent / ".acc-apply.request").write_text(
                f"{path}\n", encoding="utf-8",
            )
        except OSError:
            logger.exception("infuse: .acc-apply.request touch failed")

        self.status_text = (
            "Awaiting reconcile… (role applied; agent spawn requested)"
        )

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

    # ------------------------------------------------------------------
    # 033 WS-G Part 2 — caps panel + Active-LLM line
    # ------------------------------------------------------------------

    def _capability_registries(self):
        """Build (once, cached) the skill + MCP registries used to compute
        the allowed∩installed overlap.  Best-effort: a registry that fails
        to load yields an empty one, so the caps tables just show nothing
        rather than raising on the render path."""
        cached = getattr(self, "_caps_registries", None)
        if cached is not None:
            return cached
        skill_reg = None
        mcp_reg = None
        try:
            from acc.skills.registry import SkillRegistry  # noqa: PLC0415
            skill_reg = SkillRegistry()
            skill_reg.load_from()
        except Exception:
            logger.exception("infuse: SkillRegistry load failed (caps panel)")
            skill_reg = None
        try:
            from acc.mcp.registry import MCPRegistry  # noqa: PLC0415
            mcp_reg = MCPRegistry()
            mcp_reg.load_from()
        except Exception:
            logger.exception("infuse: MCPRegistry load failed (caps panel)")
            mcp_reg = None
        self._caps_registries = (skill_reg, mcp_reg)
        return self._caps_registries

    def _refresh_role_caps(self, role_name: str, role_def: Any = None) -> None:
        """Repaint the caps tables + the Active-LLM line for *role_name*.

        Best-effort end-to-end: any failure logs + leaves the panel in its
        previous state (never breaks the Apply form)."""
        if role_def is None:
            try:
                root = _roles_root()
                role_def = RoleLoader(root, role_name).load()
            except Exception:
                logger.exception("infuse: role load failed for caps panel (%r)", role_name)
                role_def = None
        self._refresh_caps_tables(role_def)
        self._refresh_active_llm(role_name)

    def _refresh_caps_tables(self, role_def: Any) -> None:
        from acc.capability_index import (  # noqa: PLC0415
            get_allowed_installed_capabilities,
        )

        skill_reg, mcp_reg = self._capability_registries()
        try:
            skills, mcps = get_allowed_installed_capabilities(
                role_def, skill_reg, mcp_reg,
            )
        except Exception:
            logger.exception("infuse: capability intersection failed")
            skills, mcps = [], []

        skills_table = self.query_one("#caps-skills-table", DataTable)
        skills_table.clear()
        for sid in skills:
            skills_table.add_row(sid)
        if not skills:
            skills_table.add_row("—")

        mcps_table = self.query_one("#caps-mcps-table", DataTable)
        mcps_table.clear()
        for mid in mcps:
            mcps_table.add_row(mid)
        if not mcps:
            mcps_table.add_row("—")

    def _role_model_id(self, role_name: str) -> str:
        """Return the model_id this role is BOUND to in collective.yaml
        (``AgentSpec.model``), or "" when unbound / no spec is reachable."""
        try:
            from acc.collective import load_collective  # noqa: PLC0415
            path = _resolve_collective_path()
            if not path.exists():
                return ""
            spec = load_collective(path)
        except Exception:
            logger.exception("infuse: collective load failed for active-LLM")
            return ""
        for agent in getattr(spec, "agents", []) or []:
            if getattr(agent, "role", "") == role_name:
                return (getattr(agent, "model", None) or "").strip()
        return ""

    def _refresh_active_llm(self, role_name: str) -> None:
        """Paint the Active-LLM line from the role's bound model.

        Resolves ``AgentSpec.model`` (a model_id) to a human label via
        :func:`acc.models.get_model`; falls back to the raw model_id when
        the registry has no entry, and to "—" when the role is unbound."""
        line = self.query_one("#active-llm-line", Static)
        model_id = self._role_model_id(role_name)
        if not model_id:
            line.update("Active LLM: —")
            return
        label = model_id
        try:
            from acc.models import get_model  # noqa: PLC0415
            entry = get_model(model_id)
            if entry is not None:
                label = entry.display()
        except Exception:
            logger.exception("infuse: get_model failed for %r", model_id)
        line.update(f"Active LLM: {label}")


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
