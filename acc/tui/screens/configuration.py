"""ACC TUI — ConfigurationScreen: LLM endpoints + Skills + MCPs.

Proposal 003 PR-4 — absorbs three surfaces that had grown into the
Ecosystem screen and didn't structurally belong there:

* **LLM Endpoints tab** — shows the operator-configured LLM
  backend (read-only summary of ``ACCConfig.llm``) and the live
  per-agent backend table that previously lived as "ACTIVE LLM
  BACKENDS" on Ecosystem.  A "Test connection" button does an
  HTTP HEAD against the configured ``base_url`` and reports
  latency + status.
* **Skills tab** — moved verbatim from Ecosystem, including the
  "Upload skill" file-picker flow.
* **MCPs tab** — same shape; moved verbatim from Ecosystem.

The "Assign LLM endpoint to a role" + role.yaml writeback flow
called out in proposal 003 PR-4 §5 is deferred to a follow-up so
this PR can land within budget.  Operator-side role editing
already works via the external editor + the file-watcher landed
in PR-3.

This screen mounts pane 8 on the NavigationBar.  All other
screens' BINDINGS gain a ``("8", "navigate('configuration')",
"Configuration")`` entry so the operator can hop directly to it
with the ``8`` key.
"""

from __future__ import annotations

import logging
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from acc.tui.path_resolution import resolve_manifest_root
from acc.tui.widgets.file_picker import FilePickerModal
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

logger = logging.getLogger("acc.tui.screens.configuration")


def _skills_root() -> Path:
    """Resolve the skills/ directory.  Mirrors ecosystem.py's helper."""
    return resolve_manifest_root("ACC_SKILLS_ROOT", "skills")


def _mcps_root() -> Path:
    """Resolve the mcps/ directory.  Mirrors ecosystem.py's helper."""
    return resolve_manifest_root("ACC_MCPS_ROOT", "mcps")


def _risk_cell(risk_level: str) -> str:
    """Rich-formatted risk-level cell.  Same palette as ecosystem.py."""
    risk = (risk_level or "").upper()
    if risk == "LOW":
        return "[green]LOW[/green]"
    if risk == "MEDIUM":
        return "[yellow]MEDIUM[/yellow]"
    if risk == "HIGH":
        return "[red]HIGH[/red]"
    if risk == "CRITICAL":
        return "[bold red]CRITICAL[/bold red]"
    return risk or "—"


def _format_health(health: str) -> str:
    """Health value → Rich-coloured cell.  Used by the live LLM table."""
    h = (health or "").lower()
    if h in {"ok", "healthy", "ready"}:
        return f"[green]{health}[/green]"
    if h in {"degraded", "warn", "warning"}:
        return f"[yellow]{health}[/yellow]"
    if h in {"down", "error", "fail"}:
        return f"[red]{health}[/red]"
    return health or "—"


def _ping_endpoint(url: str, timeout_s: float = 5.0) -> tuple[bool, str, float]:
    """HEAD-ping an HTTP endpoint; return ``(ok, message, elapsed_ms)``.

    Stdlib-only.  Used by the Configuration screen's "Test connection"
    button to verify the configured LLM backend is reachable without
    pulling in an HTTP client dependency.  Errors collapse to a
    short human-readable string the operator can paste into a bug
    report.
    """
    if not url:
        return False, "no base_url configured", 0.0
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            status = resp.status
            elapsed_ms = (time.monotonic() - started) * 1000
            ok = 200 <= status < 500  # 5xx = backend not ready; 4xx OK (endpoint exists)
            return ok, f"HTTP {status}", elapsed_ms
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        # 4xx on HEAD often means "method not allowed but server is up".
        return True, f"HTTP {exc.code} ({exc.reason})", elapsed_ms
    except urllib.error.URLError as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        return False, f"unreachable: {exc.reason}", elapsed_ms
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.monotonic() - started) * 1000
        return False, f"error: {exc}", elapsed_ms


def _load_acc_config_summary() -> dict[str, str]:
    """Return the configured LLM backend summary as a dict.

    Reads ``ACCConfig.llm`` defaults — backend / model / base_url /
    request_timeout_s — and applies the env-var overrides that
    :mod:`acc.config` documents (`ACC_LLM_BACKEND`, `ACC_LLM_MODEL`,
    etc.).  Best-effort: a missing config module yields a placeholder
    summary so the screen never crashes on import.
    """
    try:
        from acc.config import ACCConfig, LLMConfig  # noqa: PLC0415
        cfg = LLMConfig()
        # Resolve role_source via the full ACCConfig so the deploy-mode
        # default (proposal 010) is applied.  Best-effort: if ACCConfig
        # validation fails (e.g. rhoai mode missing milvus_uri in a
        # smoke harness), fall back to "—".
        try:
            full = ACCConfig()
            role_source = full.role_sync.role_source
            deploy_mode = full.deploy_mode
            signing_mode = full.security.signing_mode
            spiffe_enabled = full.security.spiffe.enabled
            nkey_enabled = full.security.nkey.enabled
            nkey_role = full.security.nkey.role or "—"
        except Exception:
            role_source = "—"
            deploy_mode = "—"
            signing_mode = "—"
            spiffe_enabled = False
            nkey_enabled = False
            nkey_role = "—"
        return {
            "backend": str(cfg.backend),
            "model": getattr(cfg, "model", "—") or "—",
            "base_url": getattr(cfg, "base_url", "—") or "—",
            "request_timeout_s": str(getattr(cfg, "request_timeout_s", "—")),
            "role_source": role_source,
            "deploy_mode": deploy_mode,
            "signing_mode": signing_mode,
            "spiffe_enabled": "yes" if spiffe_enabled else "no",
            "nkey_enabled": "yes" if nkey_enabled else "no",
            "nkey_role": nkey_role,
        }
    except Exception:
        logger.exception("configuration: LLMConfig() failed")
        return {
            "backend": "—",
            "model": "—",
            "base_url": "—",
            "request_timeout_s": "—",
            "role_source": "—",
            "deploy_mode": "—",
            "signing_mode": "—",
            "spiffe_enabled": "—",
            "nkey_enabled": "—",
            "nkey_role": "—",
        }


class ConfigurationScreen(Screen):
    """Pane 8 — Configuration.

    Three tabs:

    * LLM Endpoints — configured backend + live per-agent backend
      table + test-connection button.
    * Skills — moved from Ecosystem (PR-A2 upload flow preserved).
    * MCPs — moved from Ecosystem (PR-A2 upload flow preserved).
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
    ]

    DEFAULT_CSS = """
    ConfigurationScreen {
        layout: vertical;
    }
    ConfigurationScreen #configuration-title {
        height: 1;
        padding: 0 1;
        color: $accent;
        text-style: bold;
    }
    ConfigurationScreen .panel-label {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
    }
    ConfigurationScreen #llm-config-summary {
        height: auto;
        padding: 0 1;
        background: $surface;
        border: round $primary;
        margin: 0 0 1 0;
    }
    ConfigurationScreen #llm-test-row {
        height: 3;
        padding: 0 1;
    }
    ConfigurationScreen #llm-test-result {
        height: auto;
        padding: 0 1;
        margin: 0 1 1 1;
        color: $text-muted;
    }
    ConfigurationScreen .panel-header-row {
        height: 1;
        margin: 1 0 0 0;
    }
    ConfigurationScreen .panel-header-button {
        margin: 0 0 0 1;
        min-width: 14;
    }
    """

    snapshot: reactive["CollectiveSnapshot | None"] = reactive(None, layout=True)

    def __init__(self, **kwargs) -> None:  # type: ignore[override]
        super().__init__(**kwargs)
        # PR-A2 — which upload kind is currently in-flight (mirrors the
        # ecosystem screen's pattern).  "" = no upload pending.
        self._pending_upload_kind: str = ""

    # ------------------------------------------------------------------
    # Compose / mount
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield NavigationBar(active_screen="configuration", id="nav")
        yield Label(
            "ACC Configuration — Operator-Tunable Surface",
            id="configuration-title",
        )

        with TabbedContent(initial="tab-llm"):
            with TabPane("LLM Endpoints", id="tab-llm"):
                yield from self._compose_llm_tab()
            with TabPane("Skills", id="tab-skills"):
                yield from self._compose_skills_tab()
            with TabPane("MCPs", id="tab-mcps"):
                yield from self._compose_mcps_tab()

        yield Footer()

    def _compose_llm_tab(self):
        """LLM Endpoints tab — configured summary + live table + test."""
        with ScrollableContainer():
            yield Label("CONFIGURED BACKEND", classes="panel-label")
            yield Static(
                "[dim]Reading ACCConfig.llm …[/dim]",
                id="llm-config-summary",
            )

            with Horizontal(id="llm-test-row"):
                yield Button(
                    "Test connection",
                    id="btn-llm-test",
                    variant="primary",
                )
                yield Label(
                    "[dim] HEAD-pings the configured base_url[/dim]",
                    id="llm-test-hint",
                )
            yield Static(
                "[dim]Press Test to ping the configured base_url.[/dim]",
                id="llm-test-result",
            )

            yield Label("LIVE BACKENDS (per agent)", classes="panel-label")
            yield DataTable(id="llm-live-table", show_cursor=False)

    def _compose_skills_tab(self):
        """Skills tab — table + Upload button.  Moved from Ecosystem."""
        with ScrollableContainer():
            with Horizontal(classes="panel-header-row"):
                yield Label("SKILLS", classes="panel-label")
                yield Button(
                    "Upload skill",
                    id="btn-upload-skill",
                    variant="default",
                    classes="panel-header-button",
                )
            yield DataTable(id="skills-table")

    def _compose_mcps_tab(self):
        """MCPs tab — table + Upload button.  Moved from Ecosystem."""
        with ScrollableContainer():
            with Horizontal(classes="panel-header-row"):
                yield Label("MCP SERVERS", classes="panel-label")
                yield Button(
                    "Upload MCP",
                    id="btn-upload-mcp",
                    variant="default",
                    classes="panel-header-button",
                )
            yield DataTable(id="mcps-table")

    def on_mount(self) -> None:
        """Populate tables + the LLM config summary."""
        live_table = self.query_one("#llm-live-table", DataTable)
        live_table.add_columns("Agent", "Backend", "Model", "Health", "p50ms")

        skills_table = self.query_one("#skills-table", DataTable)
        skills_table.add_columns("Skill", "Version", "Risk", "Requires")
        skills_table.cursor_type = "row"

        mcps_table = self.query_one("#mcps-table", DataTable)
        mcps_table.add_columns("Server", "Transport", "Risk", "Tools")
        mcps_table.cursor_type = "row"

        self._render_llm_summary()
        self._load_skills()
        self._load_mcps()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def on_navigate_to(self, event: NavigateTo) -> None:
        self.app.switch_screen(event.screen_name)

    def action_navigate(self, screen_name: str) -> None:
        self.app.switch_screen(screen_name)

    # ------------------------------------------------------------------
    # Snapshot watcher (LLM live table)
    # ------------------------------------------------------------------

    def watch_snapshot(self, snap: "CollectiveSnapshot | None") -> None:
        if snap is None:
            return
        try:
            self._render_llm_backends(snap)
        except Exception:
            logger.exception("configuration: LLM live render failed")

    def _render_llm_backends(self, snap: "CollectiveSnapshot") -> None:
        """Repopulate the LIVE BACKENDS table from the snapshot.

        Source fields per agent — same shape ecosystem used:
        ``llm_backend``, ``llm_model``, ``llm_health``,
        ``llm_p50_latency_ms``.  Missing fields fall back to "—".
        """
        table = self.query_one("#llm-live-table", DataTable)
        table.clear()
        agents = getattr(snap, "agents", {}) or {}
        for agent_id in sorted(agents.keys()):
            agent = agents[agent_id]
            backend = getattr(agent, "llm_backend", "") or "—"
            model = getattr(agent, "llm_model", "") or "—"
            health = getattr(agent, "llm_health", "") or "—"
            p50 = getattr(agent, "llm_p50_latency_ms", None)
            p50_str = f"{p50:.0f}" if isinstance(p50, (int, float)) else "—"
            table.add_row(
                agent_id[:24],
                str(backend)[:18],
                str(model)[:24],
                _format_health(str(health)),
                p50_str,
                key=agent_id,
            )

    # ------------------------------------------------------------------
    # LLM tab — config summary + test
    # ------------------------------------------------------------------

    def _render_llm_summary(self) -> None:
        """Render the configured-backend summary (top of LLM tab)."""
        summary = _load_acc_config_summary()
        content = (
            f"[bold]Backend:[/bold] {summary['backend']}\n"
            f"[bold]Model:[/bold] {summary['model']}\n"
            f"[bold]Base URL:[/bold] {summary['base_url']}\n"
            f"[bold]Timeout (s):[/bold] {summary['request_timeout_s']}\n"
            "\n"
            f"[bold]Role sync:[/bold] {summary['role_source']} "
            f"[dim](deploy_mode={summary['deploy_mode']}; "
            "proposal 010)[/dim]\n"
            f"[bold]Signing mode:[/bold] {summary['signing_mode']} "
            f"[dim](spiffe.enabled={summary['spiffe_enabled']}; "
            "proposal 011)[/dim]\n"
            f"[bold]NATS NKey auth:[/bold] {summary['nkey_enabled']} "
            f"[dim](role={summary['nkey_role']}; proposal 013)[/dim]\n"
            "\n[dim]Values reflect ACCConfig.llm + the documented "
            "ACC_LLM_* env-var overrides.  Read-only for now — "
            "edit the underlying acc-config.yaml or the env vars; "
            "writeback from the TUI is slated for a follow-up.[/dim]"
        )
        try:
            self.query_one("#llm-config-summary", Static).update(content)
        except Exception:
            logger.exception("configuration: render summary failed")

    def _on_test_button(self) -> None:
        """Handler for the Test connection button.

        Pure-stdlib HTTP HEAD against the configured ``base_url``.
        Writes the result into ``#llm-test-result``.  Runs on the
        UI thread; the timeout is short (5 s default) so it doesn't
        freeze the screen for the operator.
        """
        result_widget = self.query_one("#llm-test-result", Static)
        result_widget.update("[yellow]Pinging…[/yellow]")
        summary = _load_acc_config_summary()
        base_url = summary["base_url"]
        if base_url in ("", "—"):
            result_widget.update(
                "[red]No base_url configured for the active backend.[/red]"
            )
            return
        ok, message, elapsed_ms = _ping_endpoint(base_url)
        colour = "green" if ok else "red"
        result_widget.update(
            f"[{colour}]{message}[/{colour}] · "
            f"{elapsed_ms:.0f} ms · {base_url}"
        )

    # ------------------------------------------------------------------
    # Skills + MCPs (moved from Ecosystem)
    # ------------------------------------------------------------------

    def _load_skills(self) -> None:
        """Populate the Skills DataTable.  Moved verbatim from
        Ecosystem screen; idempotent (clears the table first)."""
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
        """Populate the MCP SERVERS DataTable.  Moved verbatim from
        Ecosystem screen; idempotent."""
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
                "[dim]no MCPs loaded — see docs/howto-mcps.md[/dim]",
                "—", "—", "—",
            )
            return

        for server_id in sorted(manifests.keys()):
            manifest = manifests[server_id]
            table.add_row(
                server_id,
                manifest.transport,
                _risk_cell(manifest.risk_level),
                ", ".join(manifest.allowed_tools) or "—",
                key=server_id,
            )

    # ------------------------------------------------------------------
    # Upload flow — copies of the PR-A2 ecosystem pattern
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "btn-llm-test":
            self._on_test_button()
            return
        if btn_id == "btn-upload-skill":
            self._pending_upload_kind = "skill"
            self.app.push_screen(
                FilePickerModal(
                    target_filename="skill.yaml",
                    title="Pick a skill.yaml to upload",
                )
            )
            return
        if btn_id == "btn-upload-mcp":
            self._pending_upload_kind = "mcp"
            self.app.push_screen(
                FilePickerModal(
                    target_filename="mcp.yaml",
                    title="Pick an mcp.yaml to upload",
                )
            )
            return

    def on_file_picker_modal_file_selected(
        self, message: "FilePickerModal.FileSelected",
    ) -> None:
        """Copy the picked file into skills/ or mcps/ + reload the
        target table.  Mirrors the ecosystem screen's PR-A2 pattern."""
        kind = self._pending_upload_kind
        self._pending_upload_kind = ""
        if not kind or not message.path:
            return
        target_root = _skills_root() if kind == "skill" else _mcps_root()
        target_dir = target_root / message.path.stem
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(message.path, target_dir / message.path.name)
        except Exception:
            logger.exception("configuration: upload copy failed")
            try:
                self.notify(
                    f"Upload failed: see logs",
                    severity="error",
                    timeout=4.0,
                )
            except Exception:
                pass
            return
        if kind == "skill":
            self._load_skills()
        else:
            self._load_mcps()
        try:
            self.notify(
                f"Uploaded {kind} manifest: {message.path.name}",
                severity="information",
                timeout=3.0,
            )
        except Exception:
            pass
