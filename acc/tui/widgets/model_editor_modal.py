"""Add / Edit a models.yaml registry entry — the Configuration pane's model CRUD.

Dismisses with a validated :class:`acc.models.ModelEntry` on Save, or ``None``
on Cancel.  The caller (ConfigurationScreen) persists it via
``acc.models.upsert_model`` and broadcasts a ``config.reload`` so running agents
pick the change up.  ``api_key_env`` names the env var holding the key — the key
value itself never lives in models.yaml (or here).
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select

from acc.models import ModelEntry

_BACKENDS = ["anthropic", "ollama", "vllm", "openai_compat", "llama_stack"]


class ModelEditorModal(ModalScreen[Optional[ModelEntry]]):
    """Modal form for one models.yaml entry (add or edit)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    DEFAULT_CSS = """
    ModelEditorModal {
        align: center middle;
    }
    ModelEditorModal #model-editor-box {
        width: 72;
        max-width: 90%;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    ModelEditorModal #model-editor-title {
        text-style: bold;
        color: $accent;
        margin: 0 0 1 0;
    }
    ModelEditorModal .model-editor-row {
        height: auto;
        margin: 0 0 1 0;
    }
    ModelEditorModal .model-editor-label {
        width: 14;
        padding: 1 1 0 0;
    }
    ModelEditorModal .model-editor-control {
        width: 1fr;
    }
    ModelEditorModal #model-editor-error {
        color: $error;
        height: auto;
    }
    ModelEditorModal #model-editor-actions {
        height: auto;
        margin: 1 0 0 0;
    }
    ModelEditorModal #model-editor-actions Button {
        margin: 0 1 0 0;
    }
    """

    def __init__(self, entry: Optional[ModelEntry] = None) -> None:
        super().__init__()
        self._entry = entry          # None → Add; else Edit
        self._editing = entry is not None

    def compose(self) -> ComposeResult:
        e = self._entry
        with Vertical(id="model-editor-box"):
            yield Label(
                "Edit model" if self._editing else "Add model",
                id="model-editor-title",
            )
            with Horizontal(classes="model-editor-row"):
                yield Label("model_id:", classes="model-editor-label")
                yield Input(
                    value=(e.model_id if e else ""),
                    placeholder="e.g. maas-qwen3-14b",
                    id="model-editor-id",
                    classes="model-editor-control",
                    # model_id is the immutable key on edit (rename = delete+add).
                    disabled=self._editing,
                )
            with Horizontal(classes="model-editor-row"):
                yield Label("backend:", classes="model-editor-label")
                # Value set in on_mount (passing Select.BLANK at construction
                # trips _init_selected_option on some Textual builds).
                yield Select(
                    [(b, b) for b in _BACKENDS],
                    id="model-editor-backend",
                    classes="model-editor-control",
                    allow_blank=True,
                )
            with Horizontal(classes="model-editor-row"):
                yield Label("model:", classes="model-editor-label")
                yield Input(
                    value=(e.model if e else ""),
                    placeholder="provider model name, e.g. Qwen/Qwen3-14B",
                    id="model-editor-model",
                    classes="model-editor-control",
                )
            with Horizontal(classes="model-editor-row"):
                yield Label("base_url:", classes="model-editor-label")
                yield Input(
                    value=(e.base_url if e else ""),
                    placeholder="http://host:port/v1  (blank for hosted APIs)",
                    id="model-editor-base-url",
                    classes="model-editor-control",
                )
            with Horizontal(classes="model-editor-row"):
                yield Label("api_key_env:", classes="model-editor-label")
                yield Input(
                    value=(e.api_key_env if e else ""),
                    placeholder="env var NAME holding the key (never the key)",
                    id="model-editor-key-env",
                    classes="model-editor-control",
                )
            with Horizontal(classes="model-editor-row"):
                yield Label("label:", classes="model-editor-label")
                yield Input(
                    value=(e.label if e else ""),
                    placeholder="human label, e.g. Qwen3 14B (worker)",
                    id="model-editor-label",
                    classes="model-editor-control",
                )
            yield Label("", id="model-editor-error")
            with Horizontal(id="model-editor-actions"):
                yield Button("Save", id="model-editor-save", variant="success")
                yield Button("Cancel", id="model-editor-cancel")

    def on_mount(self) -> None:
        # Preselect the backend on Edit (construction-time value= is avoided).
        if self._entry and self._entry.backend in _BACKENDS:
            self.query_one("#model-editor-backend", Select).value = self._entry.backend

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "model-editor-save":
            self._save()
        elif event.button.id == "model-editor-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _save(self) -> None:
        model_id = self.query_one("#model-editor-id", Input).value.strip()
        backend = self.query_one("#model-editor-backend", Select).value
        if not model_id:
            self._error("model_id is required")
            return
        if not backend or backend is Select.BLANK:
            self._error("pick a backend")
            return
        try:
            entry = ModelEntry(
                model_id=model_id,
                backend=str(backend),
                model=self.query_one("#model-editor-model", Input).value.strip(),
                base_url=self.query_one("#model-editor-base-url", Input).value.strip(),
                api_key_env=self.query_one("#model-editor-key-env", Input).value.strip(),
                label=self.query_one("#model-editor-label", Input).value.strip(),
            )
        except Exception as exc:  # noqa: BLE001 — surface pydantic errors inline
            self._error(str(exc))
            return
        self.dismiss(entry)

    def _error(self, message: str) -> None:
        self.query_one("#model-editor-error", Label).update(f"[red]{message}[/red]")
