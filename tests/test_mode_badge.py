"""Tests for the operator-mode (dev/prod) TUI badge helper — 033 WS-F."""

from __future__ import annotations

from acc.tui.mode_badge import (
    operator_mode_badge,
    operator_mode_hint,
    operator_mode_markup,
)


def test_dev_badge_is_loud():
    label, style = operator_mode_badge("dev")
    assert "DEV" in label
    # dev must be visually loud (warning palette) — it relaxes security floors.
    assert "yellow" in style


def test_prod_badge_is_affirmative():
    label, style = operator_mode_badge("prod")
    assert label == "PROD"
    assert "green" in style


def test_unknown_mode_falls_back_to_prod():
    # Never render an empty/misleading indicator — fail safe to prod.
    assert operator_mode_badge("bogus") == operator_mode_badge("prod")
    assert operator_mode_badge("") == operator_mode_badge("prod")


def test_hint_explains_the_relaxation():
    assert "optional" in operator_mode_hint("dev")
    assert "enforced" in operator_mode_hint("prod")
    # unknown -> prod hint (safe)
    assert operator_mode_hint("bogus") == operator_mode_hint("prod")


def test_markup_wraps_label_in_style():
    markup = operator_mode_markup("dev")
    assert markup.startswith("[")
    assert "DEV" in markup
    assert markup.endswith("[/]")
