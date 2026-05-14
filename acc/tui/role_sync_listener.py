"""ACC TUI role-sync event listener (proposal 010 PR-5).

Subscribes to ``acc.role.sync.>`` and maintains an in-memory map of
recent conflict / applied events.  Ecosystem screen reads this state
to render a small badge in the role detail header.

Why a separate subscription (not folded into the existing
collective-scoped subscriber)?  The role-sync subject is *global* —
``acc.role.sync.{applied,conflict}`` doesn't carry a collective_id —
so a per-collective subscription wouldn't see it.  Keeping the
listener standalone also avoids touching the proven
``CollectiveObserver`` while we get the role-sync flow validated.

Test posture: the listener accepts a generic
``message_handler(subject, payload)`` callable, so unit tests drive
synthetic events without a live NATS connection.  The
``connect_and_subscribe()`` helper is the only NATS-dependent surface
and is exercised by integration tests on acc1.

Proposal reference
------------------
``010 - Bi-directional file-CRD sync for role definitions.md`` §5 PR-5.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("acc.tui.role_sync_listener")


@dataclass
class RoleSyncState:
    """Per-role view of recent sync events.

    ``last_conflict_ts`` is wall-clock seconds (``time.time()``);
    ``last_winner_source`` / ``last_loser_source`` describe the last
    observed conflict; ``last_applied_ts`` records the most recent
    successful projection regardless of side.  All Optional fields
    are ``None`` when no event has been observed yet.
    """
    role_id: str
    last_conflict_ts: Optional[float] = None
    last_winner_source: Optional[str] = None
    last_loser_source: Optional[str] = None
    last_loser_snippet: Optional[str] = None
    last_applied_ts: Optional[float] = None
    last_applied_source: Optional[str] = None
    # Lifetime counters for /metrics integration (future).
    conflict_count: int = 0
    applied_count: int = 0


class RoleSyncListener:
    """Maintains :class:`RoleSyncState` per role from NATS events.

    Args:
        badge_window_s: A conflict is considered "fresh" (and the
            Ecosystem badge highlights it) for this many seconds.
            Defaults to 300 (5 minutes).  After the window the badge
            fades to "last conflict at HH:MM" without the urgent
            colour.
    """

    SUBJECT_PREFIX = "acc.role.sync"
    SUFFIX_APPLIED = "applied"
    SUFFIX_CONFLICT = "conflict"

    def __init__(self, badge_window_s: float = 300.0) -> None:
        self._badge_window_s = badge_window_s
        self._state: dict[str, RoleSyncState] = {}

    # ----- public state accessors --------------------------------------

    def state(self, role_id: str) -> Optional[RoleSyncState]:
        """Return the recorded state for *role_id*, or ``None``."""
        return self._state.get(role_id)

    def has_fresh_conflict(self, role_id: str) -> bool:
        """True if *role_id* had a conflict within ``badge_window_s``."""
        st = self._state.get(role_id)
        if st is None or st.last_conflict_ts is None:
            return False
        return (time.time() - st.last_conflict_ts) <= self._badge_window_s

    def all_roles(self) -> list[str]:
        """List role_ids that have any recorded state.  Useful for
        bulk-rendering badges in a list view."""
        return sorted(self._state.keys())

    # ----- event ingestion ---------------------------------------------

    def handle_event(self, subject: str, payload: bytes) -> None:
        """Process one NATS message.

        Designed for direct invocation from a NATS ``Msg.cb`` style
        callback or from unit tests; never raises — malformed payloads
        are logged and dropped.
        """
        try:
            body = json.loads(payload.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("role-sync: malformed payload on %s: %s", subject, exc)
            return

        role_id = body.get("role_id")
        if not role_id:
            logger.warning("role-sync: payload on %s missing role_id", subject)
            return

        st = self._state.setdefault(role_id, RoleSyncState(role_id=role_id))

        if subject.endswith(f".{self.SUFFIX_CONFLICT}"):
            st.last_conflict_ts = float(body.get("ts", time.time()))
            st.last_winner_source = body.get("winner_source")
            st.last_loser_source = body.get("loser_source")
            st.last_loser_snippet = body.get("loser_snippet")
            st.conflict_count += 1
            logger.info(
                "role-sync conflict: %s (winner=%s loser=%s)",
                role_id, st.last_winner_source, st.last_loser_source,
            )
        elif subject.endswith(f".{self.SUFFIX_APPLIED}"):
            st.last_applied_ts = time.time()
            st.last_applied_source = body.get("source")
            st.applied_count += 1
        else:
            logger.debug("role-sync: ignoring subject %s", subject)

    def render_badge(self, role_id: str) -> str:
        """Return a Rich-markup string suitable for a Static widget.

        Three states:

        * No events: empty string (caller hides the widget).
        * Fresh conflict: ``[bold red]⚠ Sync conflict…[/bold red]``.
        * Aged conflict / applied only: ``[dim]Last sync: …[/dim]``.
        """
        st = self._state.get(role_id)
        if st is None:
            return ""

        if self.has_fresh_conflict(role_id):
            winner = st.last_winner_source or "?"
            return (
                f"[bold red]⚠ Sync conflict[/bold red]  "
                f"winner=[bold]{winner}[/bold]  "
                f"loser={st.last_loser_source or '?'}  "
                f"[dim](within {int(self._badge_window_s)}s)[/dim]"
            )

        if st.last_conflict_ts is not None:
            age_min = int((time.time() - st.last_conflict_ts) / 60)
            return (
                f"[dim]Last conflict {age_min} min ago "
                f"(winner={st.last_winner_source or '?'})[/dim]"
            )

        if st.last_applied_ts is not None:
            return (
                f"[dim]Sync: applied "
                f"(source={st.last_applied_source or '?'})[/dim]"
            )

        return ""
