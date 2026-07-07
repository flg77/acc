"""which-key style leader menu for the Nucleus/Infuse screen.

``Ctrl+A`` on Nucleus pops this small overlay listing the follow-up keys
(``s`` Skills · ``m`` MCPs · ``e`` Config · ``a`` Apply).  The operator presses
one key and this dismisses with it; ``Esc`` (or any unlisted key) cancels.

Why a modal and not a bare "leader then key" chord: Textual's ``Input`` binds
``ctrl+a → home`` and swallows printable keys, so a plain leader-then-letter is
eaten by whichever form field has focus.  The screen fires ``Ctrl+A`` as a
*priority* binding (which beats the focused widget) to open THIS modal, and the
modal has no text field — so the single follow-up key is captured reliably from
anywhere on the form, and the menu doubles as a discoverable hint.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label


class LeaderMenuModal(ModalScreen[str]):
    """Show a which-key hint panel; dismiss with the pressed key (lowercased)
    when it is one of *entries*, else ``""`` (cancel).

    Args:
        title: Heading shown above the key list.
        entries: ``[(key, label), …]`` — the follow-up keys and what they do.
    """

    DEFAULT_CSS = """
    LeaderMenuModal {
        align: center middle;
    }
    LeaderMenuModal #leader-menu-box {
        width: auto;
        max-width: 60;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    LeaderMenuModal #leader-menu-title {
        text-style: bold;
        color: $accent;
        margin: 0 0 1 0;
    }
    LeaderMenuModal .leader-menu-row {
        height: 1;
    }
    LeaderMenuModal #leader-menu-hint {
        color: $text-muted;
        margin: 1 0 0 0;
    }
    """

    def __init__(self, title: str, entries: list[tuple[str, str]]) -> None:
        super().__init__()
        self._title = title
        self._entries = entries
        self._valid = {key for key, _label in entries}

    def compose(self) -> ComposeResult:
        with Vertical(id="leader-menu-box"):
            yield Label(self._title, id="leader-menu-title")
            for key, label in self._entries:
                yield Label(
                    f"  [b]{key}[/b]   {label}", classes="leader-menu-row"
                )
            yield Label("Esc  cancel", id="leader-menu-hint")

    def on_key(self, event: events.Key) -> None:
        # Nothing focusable lives in this modal, so the screen sees every key.
        event.stop()
        key = event.key
        if key in self._valid:
            self.dismiss(key)
        else:  # Esc, Ctrl+A again, or any unlisted key → cancel.
            self.dismiss("")
