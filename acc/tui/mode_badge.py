"""Operator-mode (dev/prod) badge for TUI surfaces — 033 WS-F / proposal 034.

A pure helper so the Nucleus (dashboard), Prompt, and Configuration screens
render the security-floor mode consistently. ``dev`` is surfaced LOUDLY because
it relaxes the signing/auth/secret floors (proposal 034); ``prod`` is the quiet,
affirmative default. Kept free of Textual imports so it is trivially unit-tested
and reusable by the WebGUI banner copy as well.
"""

from __future__ import annotations

# (label, Rich/Textual style) per mode. dev = warning (loud); prod = success.
_BADGE: dict[str, tuple[str, str]] = {
    "dev": ("DEV — relaxed security floors", "bold black on yellow"),
    "prod": ("PROD", "bold white on green"),
}

# One-line explanation shown next to the badge so the operator knows what the
# mode actually changes (never a silent floor relaxation — proposal 034 G2).
_HINT: dict[str, str] = {
    "dev": "signatures optional, empty secrets tolerated — standalone/demo only",
    "prod": "signatures + secrets enforced",
}


def operator_mode_badge(mode: str) -> tuple[str, str]:
    """Return ``(label, style)`` for the operator-mode badge.

    Unknown values fall back to the ``prod`` (safe) badge so a surface never
    renders an empty or misleading mode indicator.
    """
    return _BADGE.get(str(mode), _BADGE["prod"])


def operator_mode_hint(mode: str) -> str:
    """Return the one-line explanation of what ``mode`` relaxes/enforces."""
    return _HINT.get(str(mode), _HINT["prod"])


def operator_mode_markup(mode: str) -> str:
    """Return a Rich/Textual markup string for inline rendering."""
    label, style = operator_mode_badge(mode)
    return f"[{style}] {label} [/]"
