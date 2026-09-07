"""Who the TUI acts as.

Until `20260906-acc-instance` the TUI stamped ``tui:anonymous`` on every
decision and every board action (D-011's open item), and its prompts carried no
requester at all. A personal instance's TUI must carry its owner: the principal
is resolved once from the substrate (``identity.current()`` — Kubernetes, then
the OS user) and stamped as the actor, the approver and the requester.

The memory scope keeps ``requester_source = "tui"`` (pooled by policy); the
person is in ``requested_by``, which is what quorum and attribution count.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("acc.tui.actor")

ANONYMOUS = "tui:anonymous"

_principal: Any = None
_resolved = False


def tui_principal():
    """The principal this TUI acts as, resolved once; ``None`` when nothing
    could be resolved (the stamps fall back to anonymous)."""
    global _principal, _resolved
    if not _resolved:
        _resolved = True
        try:
            from acc.identity import current  # noqa: PLC0415
            _principal = current()
        except Exception:  # noqa: BLE001 -- an unattributed TUI still works
            logger.debug("tui.actor: could not resolve a principal", exc_info=True)
            _principal = None
    return _principal


def reset() -> None:
    """Forget the cached principal (tests)."""
    global _principal, _resolved
    _principal, _resolved = None, False


def tui_actor() -> str:
    """``source:subject`` of the person at the keyboard, or ``tui:anonymous``."""
    p = tui_principal()
    try:
        return p.attribution() if p is not None else ANONYMOUS
    except Exception:  # noqa: BLE001
        return ANONYMOUS


def tui_actor_tier() -> str:
    """The tier of the person at the keyboard ("" when nobody resolved)."""
    p = tui_principal()
    return str(getattr(p, "tier", "") or "") if p is not None else ""


def tui_attribution() -> dict[str, Any]:
    """What a prompt from this TUI carries (the shape ``channel_access``
    stamps for a channel). Empty when nobody could be resolved."""
    p = tui_principal()
    if p is None:
        return {}
    try:
        return {
            "requested_by": p.attribution(),
            "requester_subject": p.subject,
            "requester_source": "tui",
            "requester_tier": p.tier,
            "requester_ceiling": p.effective_ceiling,
            "requester_channel": "tui",
            "requester_scope": "direct",
        }
    except Exception:  # noqa: BLE001
        return {}
