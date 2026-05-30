"""Direct prompt pane — chat-style operator → agent channel.

Layout (top → bottom)::

    ┌─────────────────────────────────────────────────┐
    │ NavigationBar (1–7)                              │
    ├─────────────────────────────────────────────────┤
    │ Target: <select role>  Agent id: <input>         │  compact, fixed height
    ├─────────────────────────────────────────────────┤
    │                                                  │
    │   TRANSCRIPT (operator + agent + traces)         │  1fr — flex, scrollable
    │                                                  │
    │                                                  │
    ├─────────────────────────────────────────────────┤
    │ [Type your prompt …                  ]  [Send]   │  fixed-height input row
    │ Status: idle                                     │
    └─────────────────────────────────────────────────┘

The transcript shows three categories of entries:

* **operator** — your prompt as you submitted it (cyan header).
* **agent** — the agent's reply (green when ok, red when blocked /
  timed out).  Body is rendered verbatim.
* **trace** — one line per ``invocations[]`` entry on the matching
  TASK_COMPLETE, showing which skills + MCP tools the agent fired
  while answering.  ``→ skill:echo OK`` / ``✗ mcp:fs.read FAILED — ...``
  Lets the operator see what the agent *did*, not just what it said.

History is FIFO-capped at 200 entries (5× the v1 cap; chat panes get
busy quickly).

Bindings:

* ``Ctrl+S`` — Send (priority).
* ``Ctrl+L`` — Clear transcript.
* ``Ctrl+J`` — Insert newline in the prompt (Enter alone in TextArea
  inserts a newline by default; ``Ctrl+S`` is the explicit submit).
* ``1–7`` — Navigate to other screens.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button, DataTable, Footer, Input, Label, Select, Static, TextArea,
)

from acc.channels import TUIPromptChannel
from acc.tui.widgets.cluster_panel import ClusterPanel
from acc.tui.widgets.invocation_detail_modal import InvocationDetailModal
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

logger = logging.getLogger("acc.tui.screens.prompt")


# Default target-role options.  Operator can pin a specific role from
# the dropdown; future PR could populate this dynamically from the
# loaded role registry.
#
# Proposal 20260530-assistant-agent-of-agents Phase 1 — the Assistant
# is now FIRST + default.  He's the gatekeeper that owns the operator's
# intent; specialist roles are still selectable but the default flow
# routes through him.  Operators who want direct-target driving simply
# pick the specialist from the dropdown or put the Assistant to sleep
# via Ctrl+Z (Knative-style dormant-watcher).
_TARGET_ROLES: list[tuple[str, str]] = [
    # The ACC Assistant — agent-of-agents gatekeeper (proposal
    # 20260530-assistant-agent-of-agents).  Default target on first
    # open of the Prompt screen.
    ("assistant", "assistant"),
    ("coding_agent", "coding_agent"),
    ("analyst", "analyst"),
    ("synthesizer", "synthesizer"),
    ("ingester", "ingester"),
    # PR-V6 (2c) — route through the orchestrator: it deliberates over which
    # role should handle the task and re-dispatches; its routing reasoning
    # surfaces in the stream, then the chosen role answers.
    ("orchestrator", "orchestrator"),
]

# Per-task wait cap.  Long-running tasks should split via PLAN, not
# block the prompt pane.
#
# Default raised from 60 → 180 s for slow local backends (vLLM /
# llama.cpp on consumer hardware).  Operators on faster
# infrastructure can lower this via the ``ACC_PROMPT_TIMEOUT_S``
# environment variable, read at screen mount.
_RECEIVE_TIMEOUT_S: float = 180.0
_RECEIVE_TIMEOUT_ENV: str = "ACC_PROMPT_TIMEOUT_S"


def _resolve_timeout() -> float:
    """Return the configured prompt timeout in seconds.

    Reads ``ACC_PROMPT_TIMEOUT_S`` from the environment when set;
    falls back to ``_RECEIVE_TIMEOUT_S``.  Malformed values are
    logged and ignored.
    """
    import os  # noqa: PLC0415
    raw = os.environ.get(_RECEIVE_TIMEOUT_ENV, "")
    if not raw:
        return _RECEIVE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "prompt: %s=%r is not a number; using default %.0fs",
            _RECEIVE_TIMEOUT_ENV, raw, _RECEIVE_TIMEOUT_S,
        )
        return _RECEIVE_TIMEOUT_S
    if value <= 0:
        logger.warning(
            "prompt: %s=%.1f must be > 0; using default %.0fs",
            _RECEIVE_TIMEOUT_ENV, value, _RECEIVE_TIMEOUT_S,
        )
        return _RECEIVE_TIMEOUT_S
    return value

# History FIFO cap.  Chat panes accumulate fast; 200 entries gives
# ~50 prompt round-trips with traces before the oldest fall off.
_MAX_HISTORY: int = 200

# PR-V3 — braille spinner frames for the live activity line.  The
# screen advances one frame per ticker tick while a task is in flight so
# the operator always sees motion, even between the agent's sparse
# TASK_PROGRESS step-boundary events.
_SPINNER: str = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# PR-F — cap on rows shown in the invocation-waterfall DataTable.
# Operator typically wants to see the most recent task's tool fires;
# 50 rows survives a 10-step chain plus a few retries without being
# noisy.  Older rows fall off the head.
_WATERFALL_CAP: int = 50


class _PromptInput(TextArea):
    """Prompt textarea where **Enter sends** (no Send button) and
    **Shift+Enter** inserts a newline for multi-line prompts.

    TextArea normally inserts a newline on Enter; we intercept it in
    ``_on_key`` (async, runs before TextArea's own insertion) and post
    a :class:`PromptSubmitted` message the screen turns into a send."""

    class PromptSubmitted(Message):
        pass

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.PromptSubmitted())
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        await super()._on_key(event)


class PromptScreen(Screen):
    """Chat-style direct-prompt screen.

    Three regions, top → bottom:

    * Target row — compact ``role`` selector + optional ``agent_id``.
    * Transcript — scrollable, flex-grow centre.
    * Prompt input row — TextArea + Send button + status line.
    """

    BINDINGS = [
        Binding("ctrl+s", "send", "Send", priority=True),
        # PR-V2 — Enter sends (handled by _PromptInput); shift+tab cycles
        # the operating mode; ctrl+shift+'+' (and ctrl+'+') open the
        # working-directory picker.
        Binding("shift+tab", "cycle_mode", "Mode", priority=True),
        Binding("ctrl+shift+plus", "select_workspace", "Workspace", priority=True),
        Binding("ctrl+plus", "select_workspace", "Workspace", priority=True),
        Binding("ctrl+l", "clear_transcript", "Clear"),
        # PR-V4 — reasoning stream: Ctrl+O expand/collapse the one-liners;
        # Ctrl+R hide/show the reasoning stream entirely (default shown).
        Binding("ctrl+o", "toggle_reasoning", "Reasoning ±", priority=True),
        Binding("ctrl+r", "toggle_reasoning_visible", "Reasoning on/off"),
        # Proposal 20260530-assistant-agent-of-agents Phase 1 —
        # Ctrl+Z toggles the Assistant's dormant-watcher mode.  The
        # action publishes on subject_assistant_control and the
        # banner re-renders from the heartbeat-carried `dormant` flag.
        Binding("ctrl+z", "toggle_assistant_dormant", "💤 Assistant", priority=True),
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

    DEFAULT_CSS = """
    PromptScreen {
        layout: vertical;
    }
    PromptScreen #prompt-target-row {
        height: 3;
        padding: 0 1;
        background: $surface;
        border-bottom: solid $primary;
    }
    PromptScreen #prompt-target-row Label {
        width: auto;
        margin: 0 1 0 0;
        color: $text-muted;
    }
    PromptScreen #select-target-role {
        width: 28;
        margin: 0 2 0 0;
    }
    PromptScreen #input-target-agent-id {
        width: 1fr;
    }

    PromptScreen #prompt-cluster-panel {
        height: auto;
        max-height: 14;
        margin: 0 1 0 1;
    }
    PromptScreen #prompt-transcript-container {
        height: 1fr;
        border: round $primary;
        padding: 0 1;
        background: $background;
    }
    PromptScreen #prompt-transcript {
        width: 100%;
    }

    PromptScreen #prompt-input-row {
        height: 7;
        padding: 0 1;
        background: $surface;
        border-top: solid $primary;
    }
    PromptScreen #prompt-mode-hint {
        width: 16;
        content-align: center middle;
        margin: 1 1 0 0;
    }
    PromptScreen #btn-select-workspace {
        height: 3;
        width: 5;
        min-width: 5;
        margin: 1 1 0 0;
    }
    PromptScreen #prompt-textarea {
        height: 5;
        width: 1fr;
        margin: 1 0 0 0;
    }
    PromptScreen #task-progress-line {
        height: 1;
        color: $warning;
        background: $surface;
    }
    PromptScreen #prompt-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }
    """

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    # Chat history list.  Each entry is a dict with shape:
    #   {role: "operator|agent|trace|system", task_id, text, ts,
    #    blocked?, target_role?, target_agent_id?, agent_id?,
    #    latency_ms?, invocations?}
    history: reactive[list[dict[str, Any]]] = reactive([], layout=True)

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        # Track in-flight workers so screen unmount cancels them.
        self._workers: set[asyncio.Task] = set()
        # PR-U2b — selected trusted workspace, as a path RELATIVE to the
        # workspace mount root (e.g. "myproject").  None = no workspace
        # selected; agents then have no file-write target for the task.
        self._workspace_project: str | None = None
        # PR-V2 — operating mode is now internal state (the dropdown was
        # replaced by a shift+tab cycle + a tiny hint).  AUTO default;
        # role selection prefills it (on_select_changed).
        self._operating_mode: str = "AUTO"
        # Task ids the screen has already cancelled (timeout path).
        # Cap at 256 entries so the set can't grow unboundedly under
        # a flood of timeouts; oldest entries fall off via FIFO eviction
        # in _mark_cancelled().  Used by the late-TASK_COMPLETE path
        # (proposal 003 PR-1 §6 risk row 1) to suppress replies that
        # arrive after the operator has moved on.
        self._cancelled_task_ids: list[str] = []
        # PR-F — full invocation record per row key in the
        # `#invocation-waterfall` DataTable.  Click-handler reads from
        # here to populate the InvocationDetailModal so the full
        # record (not just the table cells) is preserved.
        self._waterfall_records: dict[str, dict[str, Any]] = {}
        # PR-V3 — live activity state for the floating "sign of activity"
        # line.  ``None`` ⇒ idle (line blank).  While a task is in flight
        # this holds the latest known {task_id, started, step, total,
        # label, conf, trend, tokens}; a 1 s ticker repaints it so the
        # elapsed clock + spinner advance even when the agent emits no new
        # TASK_PROGRESS between step boundaries.
        self._active_progress: dict[str, Any] | None = None
        self._spinner_i: int = 0
        # PR-V4 — reasoning stream display state.  Collapsed (one-liner per
        # block) by default; Ctrl+O expands/collapses, Ctrl+R hides/shows the
        # whole stream (for operators who feel overwhelmed).  Shown by default.
        self._reasoning_collapsed: bool = True
        self._reasoning_hidden: bool = False
        # PR-V5 (2b) — (task_id, agent_id) pairs whose reasoning we've already
        # shown, so the primary agent isn't rendered twice (its reasoning
        # arrives BOTH on its TASK_PROGRESS fan-in AND the final TASK_COMPLETE).
        self._reasoning_seen: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Compose / mount / lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="prompt", id="nav")

        # ── Target row + cluster panel (compact) ────────────────────
        with Horizontal(id="prompt-target-row"):
            yield Label("Target role:")
            yield Select(
                _TARGET_ROLES,
                id="select-target-role",
                # Proposal 20260530-assistant-agent-of-agents Phase 1 —
                # default target is the Assistant (gatekeeper).
                value="assistant",
                allow_blank=False,
            )
            yield Label("Agent id (optional):")
            yield Input(
                placeholder="e.g. coding_agent-deadbeef",
                id="input-target-agent-id",
            )

        # PR-4 — collapsible cluster topology panel.  Rendered above
        # the transcript so the operator can see active sub-agent
        # clusters without leaving the prompt pane.  The watcher on
        # ``snapshot`` (below) feeds it on every snapshot tick.
        yield ClusterPanel(id="prompt-cluster-panel")

        # PR-F — capability-invocation waterfall.  Promoted from the
        # transcript's line-by-line `→ skill:echo OK` entries to a
        # sortable DataTable.  Click a row to drill into the
        # invocation's full record via InvocationDetailModal.  The
        # table holds the LAST `_WATERFALL_CAP` invocations across all
        # tasks; older rows scroll off when the cap is exceeded.
        yield DataTable(
            id="invocation-waterfall",
            cursor_type="row",
            show_cursor=True,
            zebra_stripes=True,
        )

        # ── Transcript (centre, flex) ───────────────────────────────
        with ScrollableContainer(id="prompt-transcript-container"):
            yield Static(id="prompt-transcript")

        # PR-V2 — live activity line, floated at the lower end of the
        # transcript (just above the input).  Shows the agent's current
        # reasoning step while a task is in flight:
        #   ⏳ processing [step/tokens] … <what the agent is doing>
        yield Static("", id="task-progress-line")

        # ── Prompt input row (bottom) ───────────────────────────────
        # PR-V — the bottom row carries, left-to-right: the operating-
        # mode picker, a compact "+" workspace button, the textarea,
        # then Send.  PR-L's Mode picker moved here (was top row) so the
        # two per-prompt controls (mode + workspace) sit together beside
        # the input.  The "+" opens WorkspaceSelectModal (PR-U2b); the
        # chosen path shows in #prompt-workspace-path below.
        with Horizontal(id="prompt-input-row"):
            # PR-V2 — operating mode is a tiny hint (no dropdown).
            # shift+tab cycles AUTO → PLAN → ACCEPT_EDITS →
            # ASK_PERMISSIONS.  All respect Cat-A unconditionally.
            yield Static(
                "[b]AUTO[/b]\n[dim]shift+tab[/dim]",
                id="prompt-mode-hint",
            )
            # "+" (or ctrl+shift+'+') opens the working-directory picker.
            ws_btn = Button("+", id="btn-select-workspace", variant="default")
            ws_btn.tooltip = (
                "Select working directory (ctrl+shift+'+') — "
                "where the agent is trusted to write files"
            )
            yield ws_btn
            # Enter sends; shift+enter = newline.  No Send button.
            yield _PromptInput(id="prompt-textarea")

        yield Static(
            "[dim]Workspace: (none — '+' to pick a host dir the agent may "
            "write in)[/dim]",
            id="prompt-workspace-path",
        )
        yield Static("[dim]Idle. Type a prompt and press Enter.[/dim]",
                     id="prompt-status")
        yield Footer()

    def on_mount(self) -> None:
        """Render the empty transcript once at mount."""
        self._render_transcript()
        # PR-F — initialise the invocation waterfall column layout.
        try:
            wf = self.query_one("#invocation-waterfall", DataTable)
            wf.add_columns(
                "ts", "task", "agent", "kind:target", "ok", "error",
            )
        except Exception:
            logger.exception("prompt: invocation-waterfall init failed")
        # Focus the textarea so the operator can type immediately.
        try:
            self.query_one("#prompt-textarea", TextArea).focus()
        except Exception:
            logger.exception("prompt: textarea focus failed")
        # PR-V3 — heartbeat ticker for the live activity line.  Repaints
        # the floating progress line once a second while a task is in
        # flight (no-op when idle), so the elapsed clock + spinner always
        # tick — the operator never stares at a frozen pane.
        try:
            self.set_interval(1.0, self._tick_activity)
        except Exception:
            logger.exception("prompt: activity ticker init failed")

    def on_unmount(self) -> None:
        """Cancel any in-flight workers so screen-switch is clean."""
        for task in self._workers:
            if not task.done():
                task.cancel()
        self._workers.clear()

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        """Push cluster topology to the cluster panel on every tick.

        PR-4 — keeps the panel rendering-side stateless so unit tests
        can drive it just by feeding a snapshot dict.

        We assign to ``panel.snapshot`` and then call ``render_now``
        explicitly (NOT a reactive watcher on the panel — see
        :meth:`ClusterPanel.render_now` for the textual>=0.80 trap).
        """
        if snap is None:
            return
        try:
            panel = self.query_one("#prompt-cluster-panel", ClusterPanel)
        except Exception:
            return  # panel not mounted yet
        panel.snapshot = dict(getattr(snap, "cluster_topology", {}) or {})
        try:
            panel.render_now()
        except Exception:
            logger.exception("prompt: cluster panel render failed")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "btn-select-workspace":
            self._open_workspace_select()

    def on__prompt_input_prompt_submitted(
        self, event: "_PromptInput.PromptSubmitted",
    ) -> None:
        """PR-V2 — Enter in the prompt input sends (no Send button)."""
        self.action_send()

    def action_cycle_mode(self) -> None:
        """PR-V2 — shift+tab cycles the operating mode + updates the hint."""
        order = ["AUTO", "PLAN", "ACCEPT_EDITS", "ASK_PERMISSIONS"]
        try:
            i = order.index(self._operating_mode)
        except ValueError:
            i = 0
        self._operating_mode = order[(i + 1) % len(order)]
        self._set_mode_hint()

    def action_select_workspace(self) -> None:
        """PR-V2 — ctrl+shift+'+' opens the working-directory picker."""
        self._open_workspace_select()

    def _set_mode_hint(self) -> None:
        # Proposal 20260530-assistant-agent-of-agents Phase 6 — when
        # the operating mode is AUTO, the SIP-P2 bandit is frozen
        # (rail 6: no learning while no human is in the loop).  The
        # hint surfaces this so operators promoting to AUTO see they
        # get a stable behaviour snapshot, not a moving target.
        suffix = ""
        if self._operating_mode == "AUTO":
            suffix = "\n[dim yellow]💤 policy frozen[/dim yellow]"
        try:
            self.query_one("#prompt-mode-hint", Static).update(
                f"[b]{self._operating_mode}[/b]\n"
                f"[dim]shift+tab[/dim]{suffix}"
            )
        except Exception:
            pass

    def _open_workspace_select(self) -> None:
        """PR-X — open the directory picker.  The modal browses the
        read-only host mount and, on confirm, writes an apply request;
        the host-side watcher then recreates the agents onto the chosen
        directory (which becomes ``/workspace``).  We surface the chosen
        host path with an "applying" hint — tasks then write to the
        ``/workspace`` root, so no per-task subpath is threaded."""
        from acc.tui.widgets.workspace_select_modal import (  # noqa: PLC0415
            WorkspaceSelectModal,
            base_host_path,
        )

        # Host-mapped mode writes an apply request (agents restart onto the
        # path); local mode returns a directly-usable path with no restart.
        host_mapped = bool(base_host_path())

        def _on_pick(chosen) -> None:
            if not chosen:
                return
            # The selected dir IS the agents' /workspace, so clear any
            # per-task subpath.
            self._workspace_project = None
            hint = (
                "[yellow](applying — agents restarting, ~a few seconds)[/yellow]"
                if host_mapped
                else ""
            )
            try:
                self.query_one("#prompt-workspace-path", Static).update(
                    f"[green]Workspace:[/green] {chosen}  {hint}".rstrip()
                )
            except Exception:
                pass

        self.app.push_screen(WorkspaceSelectModal(), _on_pick)

    def action_send(self) -> None:
        """Read the form, dispatch a task, await the reply in a worker.

        PR-5 — input starting with ``/`` is parsed as a slash command
        and dispatched without an LLM round-trip.  Empty / whitespace
        and non-slash inputs follow the legacy prompt path unchanged.
        """
        prompt = self.query_one("#prompt-textarea", TextArea).text.strip()
        if not prompt:
            self.notify(
                "Type a prompt first",
                severity="warning",
                timeout=4.0,
            )
            return

        # PR-5 — slash command branch.
        if prompt.startswith("/"):
            self._dispatch_slash(prompt)
            self.query_one("#prompt-textarea", TextArea).clear()
            return

        target_role = str(
            self.query_one("#select-target-role", Select).value or ""
        ).strip()
        if not target_role:
            self.notify(
                "Pick a target role first",
                severity="warning",
                timeout=4.0,
            )
            return

        target_aid_raw = self.query_one(
            "#input-target-agent-id", Input,
        ).value.strip()
        target_aid = target_aid_raw or None

        # PR-L (D-003) — per-session operating mode selector.  Empty /
        # missing defaults to AUTO; the agent's task_loop normalises
        # unknown values back to AUTO so a missing selector can't
        # accidentally weaken the gate.
        operating_mode = self._operating_mode or "AUTO"

        observer = self._active_observer()
        if observer is None:
            self.notify(
                "No active NATS connection — cannot send",
                severity="error",
                timeout=6.0,
            )
            return

        # Liveness pre-flight — without an ACTIVE agent for the target
        # role the TASK_ASSIGN sits unconsumed until the 180 s
        # TASK_CANCEL timeout fires.  Operators reported this as a
        # silent "prompt did not reach vLLM" regression: the connection
        # tested fine but no worker of role X was registered (workspace
        # re-apply was still restarting agents, or — more commonly —
        # collective.yaml's worker_pool simply did not include that role
        # so no dormant container exists for the arbiter to dispatch to).
        #
        # v0.3.23: the warning was non-blocking and didn't enumerate
        # what IS live, so operators sent anyway and watched the spinner
        # run out.  Now we BLOCK the send and show the live-role list
        # so the next click can target a role that actually exists.
        snap = self.snapshot
        if snap is not None:
            agents = (snap.agents or {})
            try:
                live_for_role = [
                    a for a in agents.values()
                    if a.role == target_role and a.state == "ACTIVE"
                ]
                live_roles = sorted({
                    a.role for a in agents.values()
                    if a.state == "ACTIVE" and a.role
                })
            except Exception:
                live_for_role = []
                live_roles = []
            if not live_for_role:
                live_list = ", ".join(live_roles) if live_roles else "(none)"
                self.notify(
                    f"No ACTIVE '{target_role}' agent in this collective. "
                    f"Live roles: {live_list}. "
                    f"Either pick one of those, or add '{target_role}' to "
                    f"collective.yaml's worker_pool and re-apply. "
                    f"Send refused — would time out at 180 s.",
                    severity="error",
                    timeout=14.0,
                )
                # BLOCK — empirically the warning-only path led operators
                # to wait out the 180 s timeout.  An operator who really
                # knows an agent is seconds away from registering can
                # re-press Send the moment the heartbeat lands.
                return

        cid = self._active_collective_id()
        worker = asyncio.create_task(
            self._dispatch_and_await(
                observer=observer,
                collective_id=cid,
                prompt=prompt,
                target_role=target_role,
                target_agent_id=target_aid,
                operating_mode=operating_mode,
                workspace=self._workspace_project,
            )
        )
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)

    def action_clear_transcript(self) -> None:
        self.history = []
        self._reasoning_seen.clear()
        self._render_transcript()
        self.query_one("#prompt-status", Static).update("[dim]Cleared.[/dim]")

    def action_toggle_reasoning(self) -> None:
        """PR-V4 — Ctrl+O expand/collapse the reasoning one-liners."""
        self._reasoning_collapsed = not self._reasoning_collapsed
        self._render_transcript()

    def action_toggle_reasoning_visible(self) -> None:
        """PR-V4 — Ctrl+R hide/show the whole reasoning stream."""
        self._reasoning_hidden = not self._reasoning_hidden
        self._render_transcript()

    def _append_reasoning(self, task_id: str, agent_id: str, text: str) -> None:
        """Append a collapsible reasoning entry for one agent, deduped.

        PR-V5 (2b) — both the per-agent TASK_PROGRESS fan-in and the final
        TASK_COMPLETE reply carry the primary agent's reasoning; show it once.
        Other agents on a multi-agent task only arrive via the fan-in, so each
        still gets its own line.
        """
        key = (task_id or "", agent_id or "")
        if key in self._reasoning_seen:
            return
        self._reasoning_seen.add(key)
        self._append_history({
            "role": "reasoning",
            "task_id": task_id,
            "agent_id": agent_id,
            "text": text,
            "ts": time.time(),
        })

    @staticmethod
    def _reasoning_summary(text: str) -> str:
        """One-line 'main objective' for a collapsed reasoning block.

        Prefers the Plan section (the agent's committed approach); falls back
        to the first non-heading line.  Trimmed to a single readable line.
        """
        lines = [ln.strip(" #*-") for ln in (text or "").splitlines()]
        lines = [ln for ln in lines if ln]
        plan = ""
        for i, ln in enumerate(lines):
            low = ln.lower()
            if low.startswith("plan"):
                # "Plan: do X" → "do X"; or the next line if the heading is bare
                after = ln.split(":", 1)[1].strip() if ":" in ln else ""
                plan = after or (lines[i + 1] if i + 1 < len(lines) else "")
                break
        gist = plan or (lines[0] if lines else "")
        gist = " ".join(gist.split())
        return (gist[:100] + "…") if len(gist) > 100 else gist

    def on_select_changed(self, event: "Select.Changed") -> None:
        """PR-P (L-2) — when the operator picks a different target
        role, prefill the Mode dropdown from that role's
        ``default_operating_mode`` (role.yaml).  The operator can
        still override it per task; this just makes the role's
        preferred mode the starting point.

        Best-effort: a missing role / loader error leaves the Mode
        selector untouched (whatever the operator last chose stays).
        Only reacts to the target-role Select — the Mode Select's own
        Changed events are ignored to avoid a feedback loop.
        """
        try:
            if event.select.id != "select-target-role":
                return
        except Exception:
            return
        role_name = str(event.value or "").strip()
        if not role_name:
            return
        try:
            from acc.role_loader import RoleLoader  # noqa: PLC0415
            from acc.tui.path_resolution import resolve_manifest_root  # noqa: PLC0415
            from acc.operating_modes import normalise  # noqa: PLC0415
            roots = str(resolve_manifest_root("ACC_ROLES_ROOT", "roles"))
            rd = RoleLoader(roots, role_name).load()
            if rd is None:
                return
            mode = normalise(getattr(rd, "default_operating_mode", "AUTO"))
            self._operating_mode = mode
            self._set_mode_hint()
        except Exception:
            logger.debug(
                "prompt: mode prefill failed for role=%r", role_name,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # PR-5 — slash command dispatch
    # ------------------------------------------------------------------

    def _dispatch_slash(self, raw_input: str) -> None:
        """Parse + dispatch a ``/`` command.

        Implementation is intentionally light — the parser
        (:func:`acc.slash_commands.parse`) is pure; this method picks
        the side-effect (publish CANCEL, render help text, …) for each
        intent kind.  Unknown verbs append a system entry so operators
        learn from typos without leaving the screen.
        """
        from acc import slash_commands as _sc  # noqa: PLC0415

        intent = _sc.parse(raw_input)

        def _system(text: str, *, blocked: bool = False) -> None:
            self._append_history({
                "role": "system",
                "task_id": "",
                "text": text,
                "ts": time.time(),
                "blocked": blocked,
            })

        if intent.kind == _sc.KIND_HELP:
            _system(_sc.HELP_TEXT)
            return
        if intent.kind == _sc.KIND_INVALID:
            _system(intent.error, blocked=True)
            return
        if intent.kind == _sc.KIND_UNKNOWN:
            _system(intent.error, blocked=True)
            return

        if intent.kind == _sc.KIND_CANCEL:
            self._publish_cancel(task_id=intent.args["task_id"])
            _system(
                f"cancel requested for task_id={intent.args['task_id'][:14]}"
            )
            return

        if intent.kind == _sc.KIND_CLUSTER_KILL:
            cid = intent.args.get("cluster_id", "")
            if not cid:
                _system("cluster id required", blocked=True)
                return
            self._publish_cancel(cluster_id=cid)
            _system(f"cancel requested for cluster {cid[:10]}")
            return

        if intent.kind == _sc.KIND_CLUSTER_SHOW:
            self._render_cluster_show(intent.args.get("cluster_id", ""))
            return

        if intent.kind == _sc.KIND_ROLE_LIST:
            self._render_role_list()
            return

        if intent.kind == _sc.KIND_SKILLS:
            self._render_skills_summary()
            return

        if intent.kind in (
            _sc.KIND_OVERSIGHT_PENDING,
            _sc.KIND_OVERSIGHT_APPROVE,
            _sc.KIND_OVERSIGHT_REJECT,
        ):
            _system(
                "oversight slash commands are wired in a follow-up — "
                "use Compliance screen for now",
            )
            return

        # Proposal 20260530-assistant-agent-of-agents Phase 1.
        if intent.kind == _sc.KIND_ASSISTANT_CONTROL:
            action = str(intent.args.get("action", "") or "").lower()
            if action not in ("sleep", "wake"):
                _system(f"unknown assistant action: {action}", blocked=True)
                return
            self._publish_assistant_control(action)
            _system(
                f"Assistant → {action} (heartbeat keeps flowing; "
                f"OODA loop intact)",
            )
            return

        _system(f"unhandled intent: {intent.kind}", blocked=True)

    def _publish_cancel(
        self,
        *,
        task_id: str = "",
        cluster_id: str = "",
    ) -> None:
        """Publish a TASK_CANCEL signal on ``acc.{cid}.task.cancel``.

        Best-effort: failures are logged + reflected in the transcript
        via the caller.  We don't surface a Future here — the cancel
        is fire-and-forget; the agent's TASK_COMPLETE with
        ``blocked=True, block_reason='cancelled'`` is what the operator
        ultimately observes via the existing prompt-channel listener.
        """
        observer = self._active_observer()
        if observer is None:
            self.notify(
                "No NATS connection — cannot send cancel",
                severity="error",
            )
            return
        cid = self._active_collective_id()
        from acc.signals import (  # noqa: PLC0415
            SIG_TASK_CANCEL,
            subject_task_cancel,
        )
        payload = {
            "signal_type": SIG_TASK_CANCEL,
            "collective_id": cid,
            "ts": time.time(),
        }
        if task_id:
            payload["task_id"] = task_id
        if cluster_id:
            payload["cluster_id"] = cluster_id

        async def _do_publish() -> None:
            try:
                await observer.publish(subject_task_cancel(cid), payload)
            except Exception:
                logger.exception("prompt: cancel publish failed")

        worker = asyncio.create_task(_do_publish())
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)

    def _publish_assistant_control(self, action: str) -> None:
        """Publish a sleep / wake control signal on the Assistant's
        control subject.  Fire-and-forget — operators see the flag
        flip via the next heartbeat tick (banner updates from
        ``snapshot.agents[assistant].dormant``).

        Proposal 20260530-assistant-agent-of-agents Phase 1.
        """
        observer = self._active_observer()
        if observer is None:
            self.notify(
                "No NATS connection — cannot send /sleep|/wake",
                severity="error",
            )
            return
        cid = self._active_collective_id()
        from acc.signals import subject_assistant_control  # noqa: PLC0415
        payload = {
            "action": action,
            "operator_id": "default",  # Phase 5 will route the real id
            "ts": time.time(),
        }

        async def _do_publish() -> None:
            try:
                await observer.publish(
                    subject_assistant_control(cid), payload,
                )
            except Exception:
                logger.exception("prompt: assistant_control publish failed")

        worker = asyncio.create_task(_do_publish())
        self._workers.add(worker)
        worker.add_done_callback(self._workers.discard)

    def action_toggle_assistant_dormant(self) -> None:
        """Ctrl+Z — toggle the Assistant's dormant-watcher mode.

        Reads the current snapshot to decide which action to send
        (sleep when currently awake, wake when currently dormant).
        When the snapshot is unavailable, defaults to /sleep — Ctrl+Z
        most often means "leave me alone for now".

        Proposal 20260530-assistant-agent-of-agents Phase 1.
        """
        snap = self.snapshot
        currently_dormant = False
        if snap is not None:
            try:
                for a in (snap.agents or {}).values():
                    if a.role == "assistant" and getattr(a, "dormant", False):
                        currently_dormant = True
                        break
            except Exception:
                pass
        action = "wake" if currently_dormant else "sleep"
        self._publish_assistant_control(action)
        self.notify(
            f"Assistant → {action} (heartbeat keeps flowing)",
            severity="information",
            timeout=4.0,
        )

    def _mark_cancelled(self, task_id: str) -> None:
        """Record a task_id as cancelled-on-timeout.

        FIFO-capped at 256 entries.  Used by the late-TASK_COMPLETE
        suppression path (proposal 003 PR-1 §6 risk row 1): if a
        reply arrives after the timeout fired, downstream renderers
        can check this list and refuse to surface a stale answer
        the operator no longer expects.
        """
        if not task_id:
            return
        try:
            self._cancelled_task_ids.append(task_id)
            if len(self._cancelled_task_ids) > 256:
                # Drop the oldest entry; keep the cap stable.
                del self._cancelled_task_ids[:32]
        except Exception:
            logger.exception("prompt: _mark_cancelled failed")

    def _is_cancelled(self, task_id: str) -> bool:
        """Return True iff this task_id was cancelled-on-timeout."""
        return bool(task_id) and task_id in self._cancelled_task_ids

    def _render_cluster_show(self, cluster_id: str) -> None:
        """Append a system entry summarising current cluster topology."""
        snap = self.snapshot
        topology = dict(getattr(snap, "cluster_topology", {}) or {})
        if cluster_id:
            topology = {k: v for k, v in topology.items() if k == cluster_id}
        if not topology:
            self._append_history({
                "role": "system",
                "task_id": "",
                "text": "no clusters" + (
                    f" matching {cluster_id[:10]}" if cluster_id else ""
                ),
                "ts": time.time(),
            })
            return
        lines: list[str] = []
        for cid, row in topology.items():
            members = row.get("members", {}) or {}
            lines.append(
                f"cluster {cid[:10]} · {row.get('target_role', '?')} · "
                f"{len(members)}/{row.get('subagent_count', 0)} agents"
            )
            for aid, m in members.items():
                lines.append(
                    f"  - {aid[:14]} · skill:{m.get('skill_in_use', '?') or '?'}"
                    f" · {m.get('status', 'running')}"
                )
        self._append_history({
            "role": "system",
            "task_id": "",
            "text": "\n".join(lines),
            "ts": time.time(),
        })

    def _render_role_list(self) -> None:
        """Append a system entry listing roles in the local registry."""
        try:
            from acc.role_loader import list_roles  # noqa: PLC0415
            from acc.tui.path_resolution import resolve_manifest_root  # noqa: PLC0415
            roots = str(resolve_manifest_root("ACC_ROLES_ROOT", "roles"))
            names = list_roles(roots)
            text = (
                "roles:\n  " + "\n  ".join(names)
                if names else "(no roles found)"
            )
        except Exception as exc:
            text = f"role list failed: {exc}"
        self._append_history({
            "role": "system", "task_id": "", "text": text, "ts": time.time(),
        })

    def _render_skills_summary(self) -> None:
        target_role = str(
            self.query_one("#select-target-role", Select).value or ""
        )
        try:
            from acc.role_loader import RoleLoader  # noqa: PLC0415
            from acc.tui.path_resolution import resolve_manifest_root  # noqa: PLC0415
            roots = str(resolve_manifest_root("ACC_ROLES_ROOT", "roles"))
            rd = RoleLoader(roots, target_role).load()
            if rd is None:
                text = f"role {target_role!r} not found"
            else:
                allowed = ", ".join(getattr(rd, "allowed_skills", []) or []) or "(none)"
                default = ", ".join(getattr(rd, "default_skills", []) or []) or "(none)"
                text = (
                    f"skills for {target_role}:\n"
                    f"  allowed: {allowed}\n"
                    f"  default: {default}"
                )
        except Exception as exc:
            text = f"skills lookup failed: {exc}"
        self._append_history({
            "role": "system", "task_id": "", "text": text, "ts": time.time(),
        })

    # ------------------------------------------------------------------
    # Worker — send + receive + transcript rendering
    # ------------------------------------------------------------------

    async def _dispatch_and_await(
        self,
        *,
        observer: Any,
        collective_id: str,
        prompt: str,
        target_role: str,
        target_agent_id: str | None,
        operating_mode: str = "AUTO",
        workspace: str | None = None,
    ) -> None:
        """Background worker.  One per Send click."""
        channel = TUIPromptChannel(observer, collective_id=collective_id)

        # Progress callback fires from the observer's NATS routing
        # path (sync).  We just append an entry to history; the
        # reactive watcher re-renders on the next event-loop tick.
        # ``_active_task_id`` is captured in the closure so the
        # callback knows which task_id its events belong to without
        # dispatching by ourselves.
        def _on_progress(payload: dict) -> None:
            try:
                self._append_progress_entry(payload)
            except Exception:
                logger.exception("prompt: on_progress render failed")

        try:
            task_id = await channel.send(
                prompt=prompt,
                target_role=target_role,
                target_agent_id=target_agent_id,
                on_progress=_on_progress,
                operating_mode=operating_mode,
                workspace=workspace,
            )
        except Exception as exc:
            logger.exception("prompt: send failed")
            self._append_history({
                "role": "system",
                "task_id": "",
                "text": f"Send failed: {exc}",
                "ts": time.time(),
                "blocked": True,
            })
            return

        # Operator-side echo lands immediately.
        self._append_history({
            "role": "operator",
            "task_id": task_id,
            "text": prompt,
            "ts": time.time(),
            "blocked": False,
            "target_role": target_role,
            "target_agent_id": target_agent_id or "",
        })
        self.query_one("#prompt-status", Static).update(
            f"[yellow]Sent task_id={task_id[:12]} — awaiting reply…[/yellow]"
        )
        # PR-V3 — start the live activity line immediately so the operator
        # sees motion before the agent's first TASK_PROGRESS arrives.
        self._begin_activity(task_id)
        # Clear the prompt textarea so the operator can start the next one.
        self.query_one("#prompt-textarea", TextArea).clear()

        timeout_s = _resolve_timeout()
        try:
            reply = await channel.receive(task_id, timeout=timeout_s)
        except asyncio.TimeoutError:
            # Publish TASK_CANCEL so the agent stops generating.
            # Without this the LLM backend (vLLM, llama.cpp, …) keeps
            # running, finishes long after the operator gave up, and
            # the late TASK_COMPLETE lands on a screen the operator
            # has moved past.  See proposal 003 PR-1 in the operator's
            # Obsidian vault for the full rationale.
            self._publish_cancel(task_id=task_id)
            self._mark_cancelled(task_id)
            self._append_history({
                "role": "system",
                "task_id": task_id,
                "text": (
                    f"(cancelled after {timeout_s:.0f}s — no reply; "
                    "TASK_CANCEL published)"
                ),
                "ts": time.time(),
                "blocked": True,
            })
            self.query_one("#prompt-status", Static).update(
                f"[red]Cancelled after {timeout_s:.0f}s — no reply received.[/red]"
            )
            return
        except Exception as exc:
            logger.exception("prompt: receive failed")
            self._append_history({
                "role": "system",
                "task_id": task_id,
                "text": f"Receive failed: {exc}",
                "ts": time.time(),
                "blocked": True,
            })
            return
        finally:
            await channel.close()
            # PR-V3 — reply (or timeout) landed: stop the activity line.
            self._end_activity(task_id)

        # PR-V3b — surface the agent's externalized reasoning (role flag
        # reasoning_trace) ABOVE the answer so the operator sees the "why":
        # prior learnings, options considered, evaluation, plan, review.
        reasoning = getattr(reply, "reasoning", "") or ""
        if reasoning.strip():
            # PR-V5 (2b) — deduped: if this agent's reasoning already arrived on
            # its TASK_PROGRESS fan-in, don't render it again.
            self._append_reasoning(task_id, reply.agent_id, reasoning)

        # Append one trace line per invocation BEFORE the agent's reply
        # so the transcript reads chronologically: operator → traces →
        # agent.  Cat-A blocks (ok=False) get an ✗ marker; successes ✓.
        for inv in reply.invocations or []:
            kind = str(inv.get("kind", ""))
            target = str(inv.get("target", ""))
            if not kind or not target:
                continue
            self._append_history({
                "role": "trace",
                "task_id": task_id,
                "agent_id": reply.agent_id,
                "ts": time.time(),
                "kind": kind,
                "target": target,
                "ok": bool(inv.get("ok", False)),
                "error": str(inv.get("error", "") or ""),
            })

        text = reply.output or "(empty response)"
        if reply.blocked:
            text = f"[BLOCKED] {reply.block_reason}\n{text}"
        self._append_history({
            "role": "agent",
            "task_id": task_id,
            "agent_id": reply.agent_id,
            "text": text,
            "ts": time.time(),
            "blocked": reply.blocked,
            "latency_ms": reply.latency_ms,
        })
        status = "[red]blocked[/red]" if reply.blocked else "[green]ok[/green]"
        self.query_one("#prompt-status", Static).update(
            f"[dim]Reply received {status} — "
            f"agent={reply.agent_id[:14]} latency={reply.latency_ms:.0f}ms[/dim]"
        )

        # PR-Y-2c — capture the executed prompt as a golden candidate so
        # it shows up in the Diagnostics pane for review + persistence.
        # Only successful, non-empty replies; deduped by prompt text so
        # re-running the same prompt doesn't flood the store.  Strictly
        # best-effort — a capture failure must never affect the reply.
        if not reply.blocked and (reply.output or "").strip():
            self._capture_golden_candidate(prompt, target_role, operating_mode)

    def _capture_golden_candidate(
        self, prompt: str, target_role: str, operating_mode: str,
    ) -> None:
        try:
            from acc.golden_prompts import (  # noqa: PLC0415
                load_merged, save_candidate,
            )
            normalised = (prompt or "").strip()
            if not normalised:
                return
            # Dedup: skip if an identical prompt is already in the suite.
            for existing in load_merged():
                if existing.prompt.strip() == normalised:
                    return
            save_candidate(
                normalised, target_role, operating_mode=operating_mode,
            )
        except Exception:
            logger.debug("prompt: golden capture failed", exc_info=True)

    # ------------------------------------------------------------------
    # Transcript render
    # ------------------------------------------------------------------

    def _append_history(self, entry: dict) -> None:
        """Append to history (FIFO-capped at _MAX_HISTORY) + re-render +
        scroll to bottom so the latest entry is visible.

        PR-F — also feeds the dedicated widgets above the transcript:
        ``trace`` entries land in the invocation waterfall DataTable;
        ``progress`` entries refresh the task-progress bar; a new
        ``operator`` entry resets the progress bar for the new task.
        """
        self.history = (self.history + [entry])[-_MAX_HISTORY:]
        self._render_transcript()
        # PR-F — keep the structured widgets in sync with the transcript.
        role = entry.get("role", "")
        if role == "trace":
            self._waterfall_add_row(entry)
        elif role == "progress":
            self._render_task_progress_line(entry)
        elif role == "operator":
            # New task starts — clear the progress bar so a stale value
            # from the previous task doesn't sit there until the agent
            # emits its first TASK_PROGRESS.
            self._render_task_progress_line(None)
        try:
            container = self.query_one(
                "#prompt-transcript-container", ScrollableContainer,
            )
            container.scroll_end(animate=False)
        except Exception:
            # Container not mounted yet (tests).  Render still happened.
            pass

    # ------------------------------------------------------------------
    # PR-F — structured trace widgets (progress bar + invocation waterfall)
    # ------------------------------------------------------------------

    def _render_task_progress_line(self, entry: dict | None) -> None:
        """Fold one TASK_PROGRESS entry into the live activity state.

        Pass ``None`` to reset to idle (blank line).  Otherwise merge the
        entry's step / label / confidence / tokens into ``_active_progress``
        (preserving the in-flight ``started`` clock) and repaint.  The
        actual drawing lives in :meth:`_paint_activity` so the 1 s ticker
        and the step-boundary events share one renderer + one format.
        """
        if not entry:
            self._active_progress = None
            self._paint_activity()
            return
        prev = self._active_progress or {}
        self._active_progress = {
            "task_id": entry.get("task_id", "") or prev.get("task_id", ""),
            "started": prev.get("started") or time.time(),
            "step": int(entry.get("current_step", 0) or 0),
            "total": int(entry.get("total_steps", 0) or 0),
            "label": (entry.get("step_label", "") or "") or prev.get("label", ""),
            "conf": float(entry.get("confidence", 0.0) or 0.0),
            "trend": entry.get("confidence_trend", "") or "",
            "tokens": int(
                entry.get("tokens", entry.get("tokens_used", prev.get("tokens", 0)))
                or 0
            ),
        }
        self._paint_activity()

    # PR-V3 — live "sign of activity" line.
    #
    # The agent emits TASK_PROGRESS only at step boundaries, which for a
    # single LLM call can be seconds apart (or absent on pre-progress
    # backends).  To guarantee a continuous signal, ``_begin_activity``
    # seeds the state on Send, a 1 s ticker advances the spinner +
    # elapsed clock, step events refine it, and ``_end_activity`` clears
    # it when the reply (or timeout) lands.

    def _begin_activity(self, task_id: str) -> None:
        """Start the activity line for a freshly-dispatched task."""
        self._active_progress = {
            "task_id": task_id,
            "started": time.time(),
            "step": 0,
            "total": 0,
            "label": "sent — waiting for the agent",
            "conf": 0.0,
            "trend": "",
            "tokens": 0,
        }
        self._spinner_i = 0
        self._paint_activity()

    def _end_activity(self, task_id: str | None = None) -> None:
        """Clear the activity line.

        When ``task_id`` is given, only clears if it matches the task
        currently shown — so a slow task finishing doesn't wipe the
        activity line of a newer one the operator already fired.
        """
        ap = self._active_progress
        if ap is not None and task_id and ap.get("task_id") != task_id:
            return
        self._active_progress = None
        self._paint_activity()

    def _tick_activity(self) -> None:
        """Ticker callback — repaint while a task is in flight (else no-op)."""
        if self._active_progress is None:
            return
        self._spinner_i += 1
        self._paint_activity()

    def _paint_activity(self) -> None:
        """Draw ``#task-progress-line`` from ``_active_progress``.

        Format (the operator's "process [token] … message" ask)::

            ⠹ processing [2/6 · 1536 tok · 12s]  ████░░░ 33% ↑80% … <label>
        """
        try:
            line = self.query_one("#task-progress-line", Static)
        except Exception:
            return
        ap = self._active_progress
        if not ap:
            line.update("")  # blank when idle — only shows during activity
            return
        frame = _SPINNER[self._spinner_i % len(_SPINNER)]
        step = ap.get("step", 0) or 0
        total = ap.get("total", 0) or 0
        bar_w = 20
        if total > 0:
            ratio = min(1.0, step / total)
            filled = int(ratio * bar_w)
            bar = "█" * filled + "░" * (bar_w - filled)
            step_str = f"{step}/{total}"
            pct = f"{ratio:.0%}"
        else:
            bar = "░" * bar_w
            step_str = f"{step}/?" if step else "?"
            pct = "—"
        elapsed = max(0, int(time.time() - (ap.get("started") or time.time())))
        tokens = ap.get("tokens", 0) or 0
        tok_str = f" · {tokens} tok" if tokens else ""
        conf = ap.get("conf", 0.0) or 0.0
        trend = ap.get("trend", "")
        trend_arrow = (
            "↑" if trend == "RISING"
            else "↓" if trend == "FALLING"
            else "→"
        )
        conf_str = f" {trend_arrow}{conf:.0%}" if conf > 0 else ""
        doing = ap.get("label") or "working"
        line.update(
            f"[blue]{frame}[/blue] [bold]processing[/bold] "
            f"[{step_str}{tok_str} · {elapsed}s]  "
            f"[blue]{bar}[/blue] [dim]{pct}{conf_str}[/dim] … {doing}"
        )

    def _waterfall_add_row(self, entry: dict) -> None:
        """Append one row to ``#invocation-waterfall`` for a trace entry.

        Caps the table at ``_WATERFALL_CAP`` rows by dropping the
        oldest.  Stashes the full entry on ``_waterfall_records`` so
        the row-click handler can pop an :class:`InvocationDetailModal`
        with the complete record (the table cells are pre-truncated).
        """
        try:
            wf = self.query_one("#invocation-waterfall", DataTable)
        except Exception:
            return
        ts = time.strftime("%H:%M:%S", time.localtime(entry.get("ts", 0)))
        task = (entry.get("task_id", "") or "")[:8]
        agent = (entry.get("agent_id", "") or "")[:14]
        kind = entry.get("kind", "?")
        target = entry.get("target", "?")
        ok = bool(entry.get("ok", False))
        ok_cell = "[green]✓[/green]" if ok else "[red]✗[/red]"
        err = entry.get("error", "") or ""
        err_short = (err[:60] + "…") if len(err) > 60 else err
        key = f"inv-{int(time.time() * 1000)}-{wf.row_count}"
        try:
            wf.add_row(
                ts, task, agent, f"{kind}:{target}", ok_cell, err_short,
                key=key,
            )
        except Exception:
            logger.exception("prompt: waterfall add_row failed")
            return
        self._waterfall_records[key] = dict(entry)
        # Cap.  Textual returns row keys as `RowKey` objects; the
        # actual string we passed lives on `.value`.
        while wf.row_count > _WATERFALL_CAP:
            oldest = next(iter(wf.rows.keys()), None)
            if oldest is None:
                break
            old_value = getattr(oldest, "value", oldest)
            try:
                wf.remove_row(oldest)
            except Exception:
                break
            self._waterfall_records.pop(str(old_value), None)

    def on_data_table_row_selected(
        self, event: DataTable.RowSelected,
    ) -> None:
        """Click on an invocation row → push the detail modal."""
        if event.data_table.id != "invocation-waterfall":
            return
        key = ""
        try:
            row_key = event.row_key
            key = str(row_key.value if hasattr(row_key, "value") else row_key)
        except Exception:
            pass
        rec = self._waterfall_records.get(key)
        if rec is None:
            return
        try:
            self.app.push_screen(InvocationDetailModal(rec))
        except Exception:
            logger.exception("prompt: push InvocationDetailModal failed")

    def _append_progress_entry(self, payload: dict) -> None:
        """Convert one TASK_PROGRESS payload to a transcript entry.

        Translates the agent's ``progress`` nested struct
        (current_step / total_steps_estimated / step_label / confidence /
        confidence_trend) into a flat history entry the transcript
        renderer knows how to draw as a ``progress`` line.

        Defensive against missing keys — pre-progress agents may emit
        partial payloads.  Empty step_label is fine; the line still
        shows the step counter so the operator sees forward motion.
        """
        progress = payload.get("progress", {}) or {}
        # Some legacy emitters put fields at the top level instead of
        # nested under ``progress`` — accept both shapes.
        current_step = int(
            progress.get("current_step", payload.get("current_step", 0)) or 0
        )
        total_steps = int(
            progress.get(
                "total_steps_estimated",
                payload.get("total_steps", payload.get("total_steps_estimated", 0)),
            ) or 0
        )
        step_label = str(
            progress.get("step_label", payload.get("step_label", "")) or ""
        )
        confidence = float(
            progress.get("confidence", payload.get("confidence", 0.0)) or 0.0
        )
        trend = str(
            progress.get("confidence_trend", payload.get("confidence_trend", ""))
            or ""
        )
        # PR-V3 — running token tally (in + out) so the activity line can
        # show "[… · N tok …]".  Accept both the nested progress struct and
        # any top-level fallback from legacy emitters.
        tokens_in = int(
            progress.get("tokens_in_so_far", payload.get("tokens_in_so_far", 0)) or 0
        )
        tokens_out = int(
            progress.get("tokens_out_so_far", payload.get("tokens_out_so_far", 0)) or 0
        )
        task_id = payload.get("task_id", "")
        agent_id = payload.get("agent_id", "")
        self._append_history({
            "role": "progress",
            "task_id": task_id,
            "agent_id": agent_id,
            "current_step": current_step,
            "total_steps": total_steps,
            "step_label": step_label,
            "confidence": confidence,
            "confidence_trend": trend,
            "tokens": tokens_in + tokens_out,
            "ts": time.time(),
        })
        # PR-V5 (2b) — per-agent reasoning from the fan-in.  Every participating
        # agent (cluster member / PLAN step / critic) emits its own
        # TASK_PROGRESS, so this surfaces the WHOLE collective's deliberation,
        # not just the one reply receive() resolves on.  Deduped by
        # (task_id, agent_id) so the primary agent — whose reasoning also
        # arrives on the final TASK_COMPLETE — isn't shown twice.
        reasoning = str(progress.get("reasoning", payload.get("reasoning", "")) or "")
        if reasoning.strip():
            self._append_reasoning(task_id, agent_id, reasoning)

    def _render_transcript(self) -> None:
        """Re-render the transcript Static from ``self.history``.

        Per-entry block layout::

            <ts>  <role-tag>  <metadata>
              <body>

        Empty history shows a grey placeholder hint.
        """
        if not self.history:
            self.query_one("#prompt-transcript", Static).update(
                "[dim]No prompts yet.\n"
                "Type a prompt below and press [b]Enter[/b] "
                "([b]Shift+Enter[/b] for a newline).\n"
                "Replies + tool traces will land here.[/dim]"
            )
            return

        lines: list[str] = []
        for entry in self.history:
            ts = time.strftime("%H:%M:%S", time.localtime(entry.get("ts", 0)))
            role = entry.get("role", "?")
            tid = entry.get("task_id", "")[:8]

            if role == "operator":
                target = entry.get("target_role", "")
                aid = entry.get("target_agent_id", "")
                target_str = target + (f" / {aid[:14]}" if aid else "")
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold cyan]operator → {target_str}[/bold cyan]  "
                    f"[dim]task={tid}[/dim]"
                )
                lines.append(header)
                for body_line in entry.get("text", "").splitlines() or [""]:
                    lines.append(f"  {body_line}")

            elif role == "reasoning":
                # PR-V3b/V4 — the agent's externalized deliberation. Hidden
                # entirely when the operator toggled it off (Ctrl+R); otherwise
                # a collapsible one-liner (Ctrl+O) showing the main objective,
                # expanding to the full trace.
                if self._reasoning_hidden:
                    continue
                aid = entry.get("agent_id", "")[:14]
                body = entry.get("text", "") or ""
                marker = "▸" if self._reasoning_collapsed else "▾"
                summary = self._reasoning_summary(body)
                lines.append(
                    f"[dim]{ts}[/dim]  [bold magenta]{marker} 🧠 {aid} reasoning"
                    f"[/bold magenta]  [dim italic]{summary}[/dim italic]  "
                    f"[dim](Ctrl+O)[/dim]"
                )
                if not self._reasoning_collapsed:
                    for body_line in body.splitlines() or [""]:
                        lines.append(f"    [dim italic]{body_line}[/dim italic]")

            elif role == "agent":
                aid = entry.get("agent_id", "")[:14]
                blocked = entry.get("blocked", False)
                lat = entry.get("latency_ms", 0.0)
                col = "red" if blocked else "green"
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold {col}]{aid}[/bold {col}]  "
                    f"[dim]task={tid} latency={lat:.0f}ms[/dim]"
                )
                lines.append(header)
                for body_line in entry.get("text", "").splitlines() or [""]:
                    lines.append(f"  {body_line}")

            elif role == "trace":
                # One-line summary of one capability invocation.
                ok = entry.get("ok", False)
                kind = entry.get("kind", "?")
                target = entry.get("target", "?")
                err = entry.get("error", "")
                kind_colour = "cyan" if kind == "skill" else "magenta"
                if ok:
                    lines.append(
                        f"  [green]✓[/green] "
                        f"[{kind_colour}]{kind}[/{kind_colour}]:[bold]{target}[/bold]"
                    )
                else:
                    err_short = (err[:80] + "…") if len(err) > 80 else err
                    lines.append(
                        f"  [red]✗[/red] "
                        f"[{kind_colour}]{kind}[/{kind_colour}]:[bold]{target}[/bold]  "
                        f"[red]{err_short}[/red]"
                    )

            elif role == "progress":
                # Live "agent thinking" line.  Renders as a single dim
                # blue → entry between operator + agent so the operator
                # sees forward motion.  Confidence trend gets a tiny
                # arrow marker:  ↑ rising  → stable  ↓ falling.
                cur = entry.get("current_step", 0)
                tot = entry.get("total_steps", 0)
                label = entry.get("step_label", "") or ""
                conf = entry.get("confidence", 0.0) or 0.0
                trend = entry.get("confidence_trend", "")
                trend_arrow = (
                    "↑" if trend == "RISING"
                    else "↓" if trend == "FALLING"
                    else "→"
                )
                step_str = (
                    f"step {cur}/{tot}" if tot > 0
                    else f"step {cur}" if cur > 0
                    else "thinking"
                )
                conf_str = (
                    f"  [dim]{trend_arrow} {conf:.0%}[/dim]"
                    if conf > 0 else ""
                )
                tok = entry.get("tokens", 0) or 0
                tok_str = f" [dim]· {tok} tok[/dim]" if tok else ""
                label_str = f" — {label}" if label else ""
                lines.append(
                    f"  [blue]→[/blue] [dim]{step_str}[/dim]"
                    f"{label_str}"
                    f"{tok_str}"
                    f"{conf_str}"
                )

            else:  # system
                header = (
                    f"[dim]{ts}[/dim]  "
                    f"[bold yellow]system[/bold yellow]  "
                    f"[dim]task={tid}[/dim]"
                )
                lines.append(header)
                for body_line in entry.get("text", "").splitlines() or [""]:
                    lines.append(f"  {body_line}")

            lines.append("")  # blank separator between entries

        self.query_one("#prompt-transcript", Static).update("\n".join(lines))

    # ------------------------------------------------------------------
    # App glue
    # ------------------------------------------------------------------

    def _active_observer(self):
        """Return the App's observer for the active collective.

        Returns ``None`` if the App hasn't built any observers yet
        (test harness without a connected NATS stack).  Caller treats
        ``None`` as a hard error and surfaces a notification.
        """
        observers = getattr(self.app, "_observers", None)
        idx = getattr(self.app, "_active_collective_idx", 0)
        if not observers:
            return None
        try:
            return observers[idx]
        except IndexError:
            return None

    def _active_collective_id(self) -> str:
        """Return the App's active collective_id, or a safe fallback."""
        cid = getattr(self.app, "_active_collective_id", None)
        if callable(cid):  # property descriptor
            cid = cid()
        if isinstance(cid, str) and cid:
            return cid
        ids = getattr(self.app, "_collective_ids", None)
        if ids:
            return ids[0]
        return "sol-01"
