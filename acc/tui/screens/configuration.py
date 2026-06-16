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
from textual.css.query import NoMatches
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
)

from acc.models import ModelEntry, load_models
from acc.pkg.manifest import CORE_BASELINE_MCPS, CORE_BASELINE_SKILLS
from acc.tui.path_resolution import resolve_manifest_root
from acc.tui.widgets.file_picker import FilePickerModal
from acc.tui.widgets.nav_bar import NavigationBar, NavigateTo

if TYPE_CHECKING:
    from acc.tui.models import CollectiveSnapshot

logger = logging.getLogger("acc.tui.screens.configuration")

# Backends supported by the LLM Endpoints Save form.  Mirrors
# `LLMBackendChoice` in acc/config.py; kept as a local tuple so the
# Select widget can render without importing the Pydantic model at
# screen-import time.
_LLM_BACKEND_CHOICES = (
    "ollama", "openai_compat", "anthropic", "vllm", "llama_stack",
)

# The four hot-swappable LLM knobs the Save form writes to ./.env.
# Anything else (deploy_mode, NKey, SPIFFE) is file-edit + restart.
_LLM_EDITABLE_KEYS = (
    "ACC_LLM_BACKEND",
    "ACC_LLM_MODEL",
    "ACC_LLM_BASE_URL",
    "ACC_LLM_TIMEOUT_S",
)


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


def _capability_source(cap_id: str, baseline: "frozenset[str]") -> str:
    """Provenance cell for the Skills / MCP tabs (033 WS-D).

    ``core`` = a built-in baseline capability that ships with ACC (the
    trusted floor); ``pack`` = one an installed package added.  This is
    the trust distinction derivable today; full signer / signature /
    install-time provenance needs install-pipeline capture and stays a
    documented follow-up.
    """
    return "[dim]core[/dim]" if cap_id in baseline else "[cyan]pack[/cyan]"


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


def _probe_for(backend: str, base_url: str) -> tuple[str, str]:
    """Pick the right HTTP probe URL+method per LLM backend.

    The bare base_url is rarely the right thing to ping: vLLM /
    OpenAI-compat / Llama-Stack return ``HTTP 404`` on ``HEAD /v1``
    because ``/v1`` is not a handler — even though the server is
    healthy and serves ``GET /v1/models`` etc.  This helper resolves
    the canonical health endpoint for each backend.
    """
    b = (backend or "").lower()
    base = base_url.rstrip("/")
    if b in ("vllm", "openai_compat", "llama_stack"):
        return f"{base}/models", "GET"
    # Ollama answers on the root with a friendly string; Anthropic has
    # no public unauth health probe — fall through to a HEAD on the
    # base URL and let the response decide.
    return base_url, "HEAD"


def _ping_endpoint(
    url: str, method: str = "HEAD", timeout_s: float = 5.0,
) -> tuple[bool, str, float]:
    """Ping an HTTP endpoint; return ``(ok, message, elapsed_ms)``.

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
        req = urllib.request.Request(url, method=method)
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


def _resolve_env_writeback_path():
    """Resolve the .env path the TUI write-back persists to.

    Precedence: ``ACC_ENV_FILE`` env var > ``/app/.env`` (the
    rw mount used by the acc-tui compose service) > ``./.env``.
    Returns a :class:`pathlib.Path`.
    """
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    explicit = os.environ.get("ACC_ENV_FILE", "").strip()
    if explicit:
        return Path(explicit)
    container_path = Path("/app/.env")
    if container_path.exists() or container_path.parent.is_dir():
        # Use the container path when the canonical mount point is
        # reachable (the file may not exist yet — upsert_env creates it).
        if Path("/app").is_dir():
            return container_path
    return Path(".env")


def _resolve_acc_config_path() -> str:
    """Resolve the acc-config.yaml path for the TUI to read.

    Precedence: ``ACC_CONFIG_PATH`` env var > the canonical container
    mount ``/app/acc-config.yaml`` > the cwd fallback
    ``./acc-config.yaml``.  Mirrors what the compose file mounts on
    every ACC service (see ``container/production/podman-compose.yml``,
    the ``acc-config.yaml`` volume on each agent / TUI / webgui).
    """
    import os  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    explicit = os.environ.get("ACC_CONFIG_PATH", "").strip()
    if explicit:
        return explicit
    if Path("/app/acc-config.yaml").is_file():
        return "/app/acc-config.yaml"
    return "acc-config.yaml"


def _load_acc_config_summary() -> dict[str, str]:
    """Return the configured LLM backend summary as a dict.

    Resolves the live `ACCConfig` via :func:`acc.config.load_config` —
    so the panel reflects the YAML file the container actually mounted
    AND the documented env-var overrides (`ACC_LLM_BACKEND`,
    `ACC_LLM_MODEL`, etc., overlaid by `acc.config._apply_env`).

    Best-effort: a missing YAML / validation failure / import error
    yields a placeholder summary so the screen never crashes.
    """
    try:
        from acc.config import load_config  # noqa: PLC0415

        full = load_config(_resolve_acc_config_path())
        cfg = full.llm
        return {
            "backend": str(cfg.backend),
            "model": getattr(cfg, "model", "—") or "—",
            "base_url": getattr(cfg, "base_url", "—") or "—",
            "request_timeout_s": str(getattr(cfg, "request_timeout_s", "—")),
            "role_source": full.role_sync.role_source,
            "deploy_mode": full.deploy_mode,
            "signing_mode": full.security.signing_mode,
            "spiffe_enabled": "yes" if full.security.spiffe.enabled else "no",
            "nkey_enabled": "yes" if full.security.nkey.enabled else "no",
            "nkey_role": full.security.nkey.role or "—",
        }
    except Exception:
        logger.exception("configuration: load_config() failed")
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
    /* LLM Endpoints — editable Save form. */
    ConfigurationScreen #llm-edit-form {
        height: auto;
        margin: 0 1 1 1;
        padding: 0 1;
    }
    ConfigurationScreen .llm-edit-row {
        height: 3;
        margin: 0 0 0 0;
    }
    ConfigurationScreen .llm-edit-label {
        width: 14;
        content-align: left middle;
        padding: 1 0 0 0;
    }
    ConfigurationScreen .llm-edit-control {
        width: 60;
    }
    ConfigurationScreen #llm-save-result {
        height: auto;
        padding: 1 1;
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
        """LLM Endpoints tab — configured summary + live table + test.

        The four hot-swappable LLM knobs (Backend / Model / Base URL /
        Timeout) are editable; Save writes them to the canonical
        ``./.env`` and publishes a ``config.reload`` signal so running
        agents can pick the change up without a restart.  Everything
        else on this screen (deploy_mode, signing, NKey, role sync)
        is still file-edit + container restart.
        """
        with ScrollableContainer():
            yield Label("CONFIGURED BACKEND", classes="panel-label")
            yield Static(
                "[dim]Reading ACCConfig.llm …[/dim]",
                id="llm-config-summary",
            )

            # Editable LLM knobs.  Pre-populated by _render_llm_summary
            # on mount; saving routes through _on_save_llm_config().
            with Vertical(id="llm-edit-form"):
                with Horizontal(classes="llm-edit-row"):
                    yield Label("Backend:", classes="llm-edit-label")
                    yield Select(
                        [(b, b) for b in _LLM_BACKEND_CHOICES],
                        id="llm-edit-backend",
                        allow_blank=False,
                        classes="llm-edit-control",
                    )
                with Horizontal(classes="llm-edit-row"):
                    yield Label("Model:", classes="llm-edit-label")
                    yield Input(
                        id="llm-edit-model",
                        placeholder="e.g. claude-sonnet-4-5",
                        classes="llm-edit-control",
                    )
                with Horizontal(classes="llm-edit-row"):
                    yield Label("Base URL:", classes="llm-edit-label")
                    yield Input(
                        id="llm-edit-base-url",
                        placeholder="http://host:port/v1",
                        classes="llm-edit-control",
                    )
                with Horizontal(classes="llm-edit-row"):
                    yield Label("Timeout (s):", classes="llm-edit-label")
                    yield Input(
                        id="llm-edit-timeout",
                        placeholder="120",
                        type="integer",
                        classes="llm-edit-control",
                    )
                with Horizontal(classes="llm-edit-row"):
                    yield Button(
                        "Save & reload",
                        id="btn-llm-save",
                        variant="success",
                    )
                    yield Static(
                        "[dim] Writes to ./.env and broadcasts a "
                        "config.reload signal.[/dim]",
                        id="llm-save-result",
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

            # 033 WS-C — the "all configured LLM endpoints" overview the
            # 2026-06-16 TUI review asked for.  The registry (models.yaml)
            # is what an agentset assigns to roles via AgentSpec.model;
            # LIVE BACKENDS above shows what each running agent resolved
            # to.  Read-only here; assignment writeback stays a follow-up.
            yield Label("MODEL REGISTRY (models.yaml)", classes="panel-label")
            yield Static(
                "[dim]Endpoints an agentset can assign to roles "
                "(per-agent model = AgentSpec.model → a model_id here). "
                "Empty = agents use the configured default above.[/dim]",
                id="llm-registry-hint",
            )
            yield DataTable(id="llm-registry-table", show_cursor=False)

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

        registry_table = self.query_one("#llm-registry-table", DataTable)
        registry_table.add_columns("model_id", "Backend", "Model", "Base URL", "Label")

        skills_table = self.query_one("#skills-table", DataTable)
        skills_table.add_columns("Skill", "Version", "Risk", "Requires", "Source")
        skills_table.cursor_type = "row"

        mcps_table = self.query_one("#mcps-table", DataTable)
        mcps_table.add_columns("Server", "Transport", "Risk", "Tools", "Source")
        mcps_table.cursor_type = "row"

        self._render_llm_summary()
        self._render_model_registry()
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
        except NoMatches:
            # Commit-6 — expected lazy-mount race: the LIVE BACKENDS
            # table lives under the LLM Endpoints sub-tab of a
            # TabbedContent.  When the operator is on a different
            # sub-tab the table isn't in the DOM yet, and a full
            # traceback every snapshot tick (≈ once / second) floods
            # the log with thousands of useless entries.  Silently
            # skip — the next sub-tab activation rebuilds the DOM and
            # the watcher catches up.
            pass
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
    # LLM tab — model registry overview (033 WS-C)
    # ------------------------------------------------------------------

    @staticmethod
    def _model_registry_rows(
        entries: "list[ModelEntry]",
    ) -> list[tuple[str, str, str, str, str]]:
        """Display rows for the MODEL REGISTRY table (pure → testable).

        An empty registry yields one explanatory row rather than a blank
        table — the 2026-06-16 review flagged silent empty tables as
        confusing.
        """
        if not entries:
            return [(
                "—",
                "(no models.yaml)",
                "agents use the default backend above",
                "—",
                "—",
            )]
        rows: list[tuple[str, str, str, str, str]] = []
        for e in entries:
            rows.append((
                (e.model_id or "—")[:24],
                (e.backend or "—")[:14],
                (e.model or "—")[:28],
                (e.base_url or "—")[:32],
                (e.label or "—")[:40],
            ))
        return rows

    def _render_model_registry(self) -> None:
        """Populate the MODEL REGISTRY table from models.yaml (best-effort).

        ``load_models`` already folds a missing/invalid registry into an
        empty list, so this never raises on a fresh corpus.
        """
        try:
            entries = load_models()
        except Exception:
            logger.exception("configuration: model registry load failed")
            entries = []
        try:
            table = self.query_one("#llm-registry-table", DataTable)
            table.clear()
            for row in self._model_registry_rows(entries):
                table.add_row(*row)
        except Exception:
            logger.exception("configuration: model registry render failed")

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
            "ACC_LLM_* env-var overrides.  Edit the four LLM knobs "
            "below to update ./.env; deploy_mode / signing / NKey "
            "stay file-edit + restart.[/dim]"
        )
        try:
            self.query_one("#llm-config-summary", Static).update(content)
        except Exception:
            logger.exception("configuration: render summary failed")

        # Pre-populate the editable form with the current values.
        try:
            backend = summary["backend"]
            if backend in _LLM_BACKEND_CHOICES:
                self.query_one("#llm-edit-backend", Select).value = backend
            model = summary["model"] if summary["model"] != "—" else ""
            base_url = summary["base_url"] if summary["base_url"] != "—" else ""
            timeout = summary["request_timeout_s"] if summary["request_timeout_s"] != "—" else ""
            self.query_one("#llm-edit-model", Input).value = model
            self.query_one("#llm-edit-base-url", Input).value = base_url
            self.query_one("#llm-edit-timeout", Input).value = timeout
        except Exception:
            logger.exception("configuration: prefill edit form failed")

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
        backend = summary["backend"]
        base_url = summary["base_url"]
        if base_url in ("", "—"):
            result_widget.update(
                "[red]No base_url configured for the active backend.[/red]"
            )
            return
        probe_url, method = _probe_for(backend, base_url)
        ok, message, elapsed_ms = _ping_endpoint(probe_url, method)
        colour = "green" if ok else "red"
        result_widget.update(
            f"[{colour}]{message}[/{colour}] · {method} · "
            f"{elapsed_ms:.0f} ms · {probe_url}"
        )

    def _on_save_llm_config(self) -> None:
        """Persist the four LLM knobs to ./.env and broadcast a reload.

        The file write is atomic with a single-rotation .bak (see
        :mod:`acc.tui.env_writeback`).  After the write succeeds, a
        ``acc.<cid>.config.reload`` NATS signal is published so live
        agents can hot-swap their LLM client without a restart.  If
        the publish fails (NATS down), the file still persists and
        the operator is told to restart agents manually.
        """
        result = self.query_one("#llm-save-result", Static)
        try:
            backend = str(self.query_one("#llm-edit-backend", Select).value)
            model = self.query_one("#llm-edit-model", Input).value.strip()
            base_url = self.query_one("#llm-edit-base-url", Input).value.strip()
            timeout_raw = self.query_one("#llm-edit-timeout", Input).value.strip()
        except Exception:
            logger.exception("configuration: read edit form failed")
            result.update("[red]Could not read the form values.[/red]")
            return

        if backend not in _LLM_BACKEND_CHOICES:
            result.update(f"[red]Invalid backend: {backend!r}[/red]")
            return
        try:
            timeout = int(timeout_raw) if timeout_raw else 120
            if timeout <= 0:
                raise ValueError("must be positive")
        except ValueError:
            result.update(
                f"[red]Timeout must be a positive integer (got {timeout_raw!r}).[/red]"
            )
            return

        updates = {
            "ACC_LLM_BACKEND": backend,
            "ACC_LLM_MODEL": model,
            "ACC_LLM_BASE_URL": base_url,
            "ACC_LLM_TIMEOUT_S": str(timeout),
        }
        env_path = _resolve_env_writeback_path()

        try:
            from acc.tui.env_writeback import upsert_env  # noqa: PLC0415
            upsert_env(env_path, updates)
        except Exception as exc:
            logger.exception("configuration: .env writeback failed")
            result.update(f"[red]Save failed: {exc}[/red]")
            return

        # Publish a best-effort reload signal.  Wraps any error so a
        # NATS outage does NOT mask the successful file save.
        publish_msg = self._publish_config_reload(updates)

        # Refresh the read panel from the new file/env state so the
        # operator sees the saved values immediately.
        os_environ_update = updates if env_path.exists() else {}
        try:
            import os  # noqa: PLC0415
            for k, v in os_environ_update.items():
                os.environ[k] = v
        except Exception:
            pass
        self._render_llm_summary()
        result.update(
            f"[green]Saved to {env_path}[/green] · {publish_msg}"
        )

    def _publish_config_reload(self, changes: dict[str, str]) -> str:
        """Broadcast a ``config.reload`` signal on NATS, best-effort.

        Returns a status string to render next to the Save button.
        Never raises — failures (no observer, NATS down) are reported
        in the returned message, not via exceptions.
        """
        try:
            client = self._get_observer_client()
            if client is None:
                return "[yellow]reload not broadcast — no NATS client[/yellow]"
            cid = client.collective_id
            client.publish_config_reload(changes)
            return f"[dim]reload broadcast on acc.{cid}.config.reload[/dim]"
        except Exception as exc:
            logger.exception("configuration: publish_config_reload failed")
            return (
                f"[yellow]save persisted; reload broadcast failed "
                f"({exc}). Restart agents to apply.[/yellow]"
            )

    def _get_observer_client(self):
        """Return the running NATSObserver, or None if unavailable.

        The TUI app exposes the primary observer as the
        ``nats_observer`` property (read-only over ``_observers[0]``
        after the multi-collective refactor).  Earlier callers used
        ``self.app.observer`` which never existed and quietly returned
        None — meaning Save's CONFIG_RELOAD broadcast was silently
        dropped, and live agents kept pinging the old endpoint until
        an operator restart.
        """
        try:
            obs = getattr(self.app, "nats_observer", None)
            if obs is not None:
                return obs
            return getattr(self.app, "observer", None)
        except Exception:
            return None

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
            table.add_row("[dim]acc.skills not available[/dim]", "—", "—", "—", "—")
            return

        try:
            reg = SkillRegistry()
            reg.load_from(_skills_root())
        except Exception as exc:
            table.add_row(f"[red]load error: {exc}[/red]", "—", "—", "—", "—")
            return

        manifests = reg.manifests()
        if not manifests:
            table.add_row(
                "[dim]no skills loaded — see docs/howto-skills.md[/dim]",
                "—", "—", "—", "—",
            )
            return

        for skill_id in sorted(manifests.keys()):
            manifest = manifests[skill_id]
            table.add_row(
                skill_id,
                manifest.version,
                _risk_cell(manifest.risk_level),
                ", ".join(manifest.requires_actions) or "—",
                _capability_source(skill_id, CORE_BASELINE_SKILLS),
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
            table.add_row("[dim]acc.mcp not available[/dim]", "—", "—", "—", "—")
            return

        try:
            reg = MCPRegistry()
            reg.load_from(_mcps_root())
        except Exception as exc:
            table.add_row(f"[red]load error: {exc}[/red]", "—", "—", "—", "—")
            return

        manifests = reg.manifests()
        if not manifests:
            table.add_row(
                "[dim]no MCPs loaded — see docs/howto-mcps.md[/dim]",
                "—", "—", "—", "—",
            )
            return

        for server_id in sorted(manifests.keys()):
            manifest = manifests[server_id]
            table.add_row(
                server_id,
                manifest.transport,
                _risk_cell(manifest.risk_level),
                ", ".join(manifest.allowed_tools) or "—",
                _capability_source(server_id, CORE_BASELINE_MCPS),
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
        if btn_id == "btn-llm-save":
            self._on_save_llm_config()
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
