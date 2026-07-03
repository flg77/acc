"""Global command palette providers (proposal 050, Slice 2 — G1).

Registered on :class:`ACCTUIApp` via ``COMMANDS`` so ``ctrl+p`` opens a
fuzzy-searchable palette that reaches **any screen** and **any action on the
active screen** from anywhere — the "don't lose time hunting for the right
per-screen key" surface.

Two providers:

- :class:`ScreenCommands` — "Go to <Screen>" for every registered screen
  (the 9 nav-strip panes + Marketplace + Catalogs, which have no number-key
  slot). Selecting one ``switch_screen``s to it.
- :class:`ScreenActionCommands` — the *active* screen's own key-bound actions
  (Approve, Run all, Apply, …). Navigation + quit are excluded (the palette's
  ScreenCommands + the built-in system Quit already cover those), so this lists
  only what's specific to where you are.

Both implement ``discover`` (shown when the palette opens with no query) and
``search`` (fuzzy). Kept in its own module so it imports no screen class except
the lazy nav registry (mirrors the ``REQ-TUI-051`` no-cross-import discipline).
"""

from __future__ import annotations

from functools import partial

from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider

from acc.tui.widgets.nav_bar import _SCREENS

# Every jump target: the 9 nav-strip screens + the two hub-reached panes
# (Marketplace/Catalogs have no number key — the strip is full at 1–9).
_JUMP_TARGETS: list[tuple[str, str]] = [
    (name, label.split(" ", 1)[1]) for _key, name, label in _SCREENS
] + [("marketplace", "Marketplace"), ("catalogs", "Catalogs")]

# Actions ScreenActionCommands never surfaces (covered elsewhere / not useful).
_SKIP_ACTION_PREFIXES = ("navigate(", "app.quit", "command_palette")


def _binding_parts(b: object) -> tuple[str, str, str]:
    """``(key, action, description)`` from a ``Binding`` or a bindings tuple,
    tolerant of 1/2/3-element tuples."""
    if isinstance(b, Binding):
        return (b.key or "", b.action or "", b.description or "")
    if isinstance(b, tuple):
        key = b[0] if len(b) > 0 else ""
        action = b[1] if len(b) > 1 else ""
        desc = b[2] if len(b) > 2 else ""
        return (str(key), str(action), str(desc))
    return ("", "", "")


class ScreenCommands(Provider):
    """Jump to any ACC screen from the command palette."""

    def _rows(self):
        for name, label in _JUMP_TARGETS:
            yield f"Go to {label}", name, f"Switch to the {label} screen"

    async def discover(self) -> Hits:
        for text, name, help_ in self._rows():
            yield DiscoveryHit(text, partial(self._go, name), help=help_)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for text, name, help_ in self._rows():
            score = matcher.match(text)
            if score > 0:
                yield Hit(
                    score, matcher.highlight(text),
                    partial(self._go, name), help=help_,
                )

    def _go(self, name: str) -> None:
        self.app.switch_screen(name)


class ScreenActionCommands(Provider):
    """Run one of the *active* screen's own key-bound actions."""

    def _target_screen(self):
        """The screen the palette was opened over.  ``self.screen`` is that
        screen in normal use; guard against it being the palette itself."""
        from textual.command import CommandPalette  # noqa: PLC0415

        scr = self.screen
        if isinstance(scr, CommandPalette):
            for s in reversed(self.app.screen_stack):
                if not isinstance(s, CommandPalette):
                    return s
        return scr

    def _rows(self):
        screen = self._target_screen()
        label = type(screen).__name__.removesuffix("Screen") or "Screen"
        seen: set[str] = set()
        for klass in type(screen).__mro__:
            for b in getattr(klass, "BINDINGS", []):
                key, action, desc = _binding_parts(b)
                if not action or action in seen:
                    continue
                if any(action.startswith(p) for p in _SKIP_ACTION_PREFIXES):
                    continue
                seen.add(action)
                text = f"{label}: {desc or action}"
                help_ = (f"[{key}] " if key else "") + (desc or action)
                yield text, screen, action, help_

    async def discover(self) -> Hits:
        for text, screen, action, help_ in self._rows():
            yield DiscoveryHit(text, partial(self._run, screen, action), help=help_)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for text, screen, action, help_ in self._rows():
            score = matcher.match(text)
            if score > 0:
                yield Hit(
                    score, matcher.highlight(text),
                    partial(self._run, screen, action), help=help_,
                )

    def _run(self, screen, action: str) -> None:
        # Runs after the palette dismisses; the target screen is active again.
        # call_later awaits the coroutine that run_action returns, handling
        # both sync and async action_* methods.
        self.app.call_later(screen.run_action, action)


__all__ = ["ScreenCommands", "ScreenActionCommands"]
