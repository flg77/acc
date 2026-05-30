"""Operator identity — multi-user-ready single-user seam.

Proposal `20260530-assistant-agent-of-agents` Phase 5.

Today's deployments are single-operator: every prompt, every
proposal, every sleep/wake control signal lands with
``operator_id = "default"``.  This module is the **resolution
seam** so the flip to multi-user is one env var (or, later, one
config) instead of a refactor across every NATS publish site.

Sources (highest priority first):

1. **Explicit override** — caller passes ``operator_id`` directly
   into :func:`resolve_operator_id`.  Wins always (the TUI / webgui
   session knows who's typing).
2. **Env-var pinned** — ``ACC_OPERATOR_ID`` set in the agent's
   process environment.  Useful for headless / batch deployments
   where a single operator id is correct for the whole process.
3. **Source rule** — ``ACC_OPERATOR_ID_SOURCE`` env directs the
   resolver to a specific origin:
   - ``session`` *(Phase 5b — pending TUI auth proposal)* — read
     the authenticated session principal.  Falls back to "default"
     when no session is available so this is safe to enable in
     advance.
   - ``user`` — read ``$USER`` / ``$USERNAME`` (Windows).
   - ``default`` *(today's default)* — return ``"default"``.
4. **Fallback** — ``"default"``.

The resolver is **pure** + **sync** so it's safe to call from any
publish site without async plumbing.  Multiple operators can run
concurrently as soon as a session source lands; until then,
nothing about the current single-operator behaviour changes.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("acc.operator_identity")


DEFAULT_OPERATOR_ID = "default"


def resolve_operator_id(
    override: Optional[str] = None,
    *,
    source: Optional[str] = None,
) -> str:
    """Return the resolved operator id.

    Args:
        override: explicit value from the caller (TUI session, webgui
            principal, slash command flag).  Wins absolutely when
            non-empty.
        source: explicit source-rule override; falls back to
            ``ACC_OPERATOR_ID_SOURCE`` env when None.

    Returns:
        A non-empty operator-id string.  Empty / whitespace-only
        inputs are treated as missing.  The fallback is always
        ``"default"`` so callers don't have to handle empty.
    """
    # 1. Explicit override always wins.
    if override and override.strip():
        return override.strip()

    # 2. Env-pinned absolute value.
    env_pin = os.environ.get("ACC_OPERATOR_ID", "").strip()
    if env_pin:
        return env_pin

    # 3. Source-rule resolution.
    if source is None:
        source = os.environ.get(
            "ACC_OPERATOR_ID_SOURCE", DEFAULT_OPERATOR_ID,
        ).strip().lower()

    if source == "user":
        candidate = (
            os.environ.get("USER", "").strip()
            or os.environ.get("USERNAME", "").strip()
        )
        if candidate:
            return candidate
        logger.debug(
            "operator_identity: ACC_OPERATOR_ID_SOURCE=user but no USER/"
            "USERNAME env set — falling back to %r",
            DEFAULT_OPERATOR_ID,
        )
        return DEFAULT_OPERATOR_ID

    if source == "session":
        # Phase 5b — wire to the authenticated session principal when
        # the TUI / webgui auth proposal lands.  Today there's no
        # session yet, so fall back safely.
        logger.debug(
            "operator_identity: source=session not yet wired — "
            "falling back to %r (Phase 5b)", DEFAULT_OPERATOR_ID,
        )
        return DEFAULT_OPERATOR_ID

    # 4. Fallback / explicit ``default``.
    if source not in ("", DEFAULT_OPERATOR_ID):
        logger.warning(
            "operator_identity: unknown ACC_OPERATOR_ID_SOURCE=%r — "
            "falling back to %r", source, DEFAULT_OPERATOR_ID,
        )
    return DEFAULT_OPERATOR_ID


__all__ = [
    "DEFAULT_OPERATOR_ID",
    "resolve_operator_id",
]
