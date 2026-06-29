"""Pure tests for the slash palette's row/completion logic (proposal 039).

The widget rendering + prompt wiring are exercised live in the TUI (a Pilot
test belongs there); these cover the pure data path that feeds the palette,
runnable without a Textual app.
"""

from __future__ import annotations

from acc.tui.widgets.slash_palette import palette_rows, top_match


def test_rows_bare_slash_lists_all_alphabetical():
    rows = palette_rows("/")
    names = [n for n, _ in rows]
    assert names == sorted(names)
    assert names[0] == "allow"  # 044 B8 — /allow now sorts first alphabetically
    assert "oversight" in names and "wake" in names
    assert any("— Cancel a task" in label for _, label in rows)


def test_rows_prefix_filters():
    assert [n for n, _ in palette_rows("/ov")] == ["oversight"]
    assert [n for n, _ in palette_rows("/c")] == ["cancel", "catalog", "clear", "cluster"]


def test_rows_subform_hint_in_label():
    label = dict(palette_rows("/oversight"))["oversight"]
    assert "approve <id>" in label  # built from the sub-form signatures


def test_rows_non_slash_is_empty():
    assert palette_rows("hello world") == []
    assert palette_rows("") == []


def test_top_match_is_first_alphabetical():
    assert top_match("/c") == "cancel"
    assert top_match("/oversight") == "oversight"
    assert top_match("/zzz") is None
