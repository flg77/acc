"""Interactive slash-command palette for the Prompt pane (proposal 039).

Renders the matching slash commands (from :func:`acc.slash_commands.complete`)
as an inline, non-focusable dropdown above the prompt input.  It appears the
moment the buffer starts with ``/`` and hides otherwise; rows are **alphabetical**
(``complete`` guarantees the order).  ``Tab`` in the input completes the top
match (see ``_PromptInput._on_key`` in the prompt screen).

The data path (:func:`palette_rows`, :func:`top_match`) is pure and unit-tested
without a running app; the widget + its wiring are exercised by a Pilot test and
live in the TUI.
"""

from __future__ import annotations

from textual.widgets import OptionList
from textual.widgets.option_list import Option

from acc.slash_commands import complete


def palette_rows(buffer: str) -> list[tuple[str, str]]:
    """``(command_name, display_label)`` for the typed ``buffer``, alphabetical.

    ``label`` = ``/name  <hint>  — summary``.  For sub-form verbs (cluster,
    oversight) the hint is the ``" | "``-joined sub-signatures.  Returns ``[]``
    for non-slash input (empty buffer, plain prose) — only a ``/``-line opens
    the palette.  Pure — drives the palette + is unit-tested.
    """
    if not buffer.lstrip().startswith("/"):
        return []
    rows: list[tuple[str, str]] = []
    for c in complete(buffer):
        hint = c.arg_hint or " | ".join(sig for sig, _ in c.subforms)
        label = f"/{c.name}" + (f"  {hint}" if hint else "") + f"  — {c.summary}"
        rows.append((c.name, label))
    return rows


def top_match(buffer: str) -> str | None:
    """The verb to ``Tab``-complete to — the first alphabetical match — or
    ``None`` when nothing matches."""
    rows = palette_rows(buffer)
    return rows[0][0] if rows else None


class SlashPalette(OptionList):
    """Inline, non-focusable dropdown of the matching slash verbs."""

    can_focus = False

    DEFAULT_CSS = """
    SlashPalette {
        height: auto;
        max-height: 8;
        margin: 0 1;
        border: round $accent;
        background: $surface;
        display: none;
    }
    """

    def update_for(self, buffer: str) -> int:
        """Repopulate from ``buffer``; show iff it's a single-line slash line
        with matches.  Returns the match count."""
        self.clear_options()
        is_slash = buffer.lstrip().startswith("/") and "\n" not in buffer
        if not is_slash:
            self.display = False
            return 0
        rows = palette_rows(buffer.strip())
        for name, label in rows:
            self.add_option(Option(label, id=name))
        self.display = bool(rows)
        if rows:
            self.highlighted = 0
        return len(rows)

    def dismiss_palette(self) -> None:
        """Hide + empty the palette."""
        self.display = False
        self.clear_options()
