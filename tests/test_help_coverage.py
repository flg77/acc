"""Proposal 050 Slice 6 — every navigable screen has a `?` help doc.

`?` maps the active screen to a logical id and loads `acc/tui/help/{id}.md`
(falling back to a placeholder on a miss). Configuration / Diagnostics /
Marketplace / Catalogs had no doc — and Prompt had one that wasn't mapped —
so `?` silently showed Soma's help on them. This guards that every registered
screen resolves to its own real doc.
"""

from __future__ import annotations

import pytest

from acc.tui.app import ACCTUIApp
from acc.tui.screens.help import _FALLBACK_TEXT, _load_help_markdown

# The logical help ids == the primary ACCTUIApp.SCREENS keys (minus aliases).
HELP_IDS = sorted(set(ACCTUIApp.SCREENS) - {"dashboard", "infuse"})


@pytest.mark.parametrize("sid", HELP_IDS)
def test_help_doc_exists_and_is_real(sid):
    body = _load_help_markdown(sid)
    assert body != _FALLBACK_TEXT, f"{sid}: no help doc — `?` would fall back"
    assert body.lstrip().startswith("# "), f"{sid}: not a markdown help doc"


def test_all_navigable_screens_have_help():
    """Structural guard: adding a screen without a help doc fails here."""
    missing = [
        sid for sid in HELP_IDS
        if _load_help_markdown(sid) == _FALLBACK_TEXT
    ]
    assert not missing, f"registered screens with no help doc: {missing}"


def test_previously_missing_screens_now_covered():
    for sid in ("configuration", "diagnostics", "marketplace", "catalogs", "prompt"):
        assert _load_help_markdown(sid) != _FALLBACK_TEXT
