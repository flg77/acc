"""ACC TUI — HelpScreen: per-screen markdown documentation overlay.

Pressing ``?`` on any screen pushes a HelpScreen instance with the matching
markdown loaded from ``acc/tui/help/{screen_id}.md``.  Pressing ``escape``
or ``?`` again dismisses it.

The markdown files are bundled with the package via ``[tool.setuptools.package-data]``
in ``pyproject.toml`` (``"acc.tui": ["*.tcss", "help/*.md"]``).

Biological framing: each screen is one organ system of the cell — soma is
the cell body, nucleus contains the DNA, the immune layer is the compliance
plane, and so on.  The help text leads with that framing so operators
internalise the metaphor while learning the controls.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Markdown, Static


_HELP_PACKAGE = "acc.tui.help"
_FALLBACK_TEXT = (
    "# No help available\n\n"
    "This screen does not yet have a help document.\n\n"
    "Contribute one at `acc/tui/help/{screen_id}.md` and rebuild "
    "the package."
)


def _load_help_markdown(screen_id: str) -> str:
    """Load the markdown body for ``screen_id``.

    Resolution order:

    1. Package resource ``acc.tui.help/{screen_id}.md`` — the canonical
       location once the package is installed.
    2. Filesystem path ``acc/tui/help/{screen_id}.md`` relative to the
       editable repo root — for development without a re-install.
    3. ``_FALLBACK_TEXT`` — guarantees the modal always renders something.

    Returns:
        Raw markdown text.  Never raises.
    """
    filename = f"{screen_id}.md"

    # 1. Installed package resource
    try:
        return (
            resources.files(_HELP_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        pass

    # 2. Repo-relative fallback (handy when running from a checkout
    #    without a fresh `pip install -e .`).
    here = Path(__file__).resolve().parent.parent / "help" / filename
    if here.exists():
        try:
            return here.read_text(encoding="utf-8")
        except OSError:
            pass

    return _FALLBACK_TEXT


class HelpScreen(ModalScreen[None]):
    """Modal overlay rendering screen-specific markdown help."""

    BINDINGS = [
        Binding("escape", "dismiss_help", "Close", show=True),
        Binding("question_mark", "dismiss_help", "Close"),
        Binding("q", "dismiss_help", "Close"),
    ]

    def __init__(self, screen_id: str) -> None:
        super().__init__()
        self._screen_id = screen_id

    def compose(self) -> ComposeResult:
        body = _load_help_markdown(self._screen_id)
        with Container(id="help-modal-container"):
            with Vertical(id="help-modal-body"):
                yield Static(
                    f"[b]ACC Help — {self._screen_id}[/b]   "
                    "[dim](Esc / ? / q to close)[/dim]",
                    id="help-modal-title",
                )
                yield Markdown(body, id="help-modal-markdown")
        yield Footer()

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
