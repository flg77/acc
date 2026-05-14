"""ACC role-sync conflict detection (proposal 010 PR-4).

Enables ``role_sync.role_source: mirror`` — both file-side and CRD-side
projections active simultaneously, with last-writer-wins reconciliation
and conflict observability via NATS.

Architecture
------------

In ``mirror`` mode three writes can compete for the same logical role:

1. **Operator** edits ``roles/<id>/role.yaml`` directly.
2. **CRD projector** (PR-3) writes the same file after observing a CRD
   patch on the K8s API.
3. **Operator-side controller** (PR-2, Go) patches the CRD after
   observing the file edit.

The convergence danger is **echo confusion** — when the CRD projector
writes a file, the file-watcher inside the Go operator should NOT treat
that as a fresh edit and re-patch the CRD.  We solve this with two
correlated mechanisms:

* **File-side**: every CRD-driven write carries a sentinel comment
  header (PR-3 already emits this).  The Go-side parser strips comments
  and compares content with ``RoleDefinitionsEqual`` — so an echo
  becomes a no-op patch by construction.
* **Time-window**: this module records every CRD-driven file write
  along with its body hash.  When the file watcher fires within
  ``conflict_window_s`` of one of our writes, we check whether the
  current content matches our recorded body.  If yes, it's our echo
  (silent).  If no, an operator wrote *concurrently* — that's a real
  conflict.

Conflict resolution: **last writer wins**.  We do not attempt
three-way merge.  Operators get a NATS event describing the loser side
and can re-edit if needed.

NATS subjects
-------------

All published on the prefix ``role_sync.events_subject`` (default
``acc.role.sync``):

* ``acc.role.sync.applied`` — successful projection (either direction).
  Operators / dashboards can use this for an "out-of-sync" badge.
* ``acc.role.sync.conflict`` — concurrent writes detected.  Loser's
  payload is preserved in the event body so an audit log can
  reconstruct what was overwritten.

Out of scope for PR-4
---------------------

* The TUI badge that surfaces the conflict to the operator — PR-5.
* Cross-process correlation between the Go operator's writes and our
  Python observation (we only see file changes, not the originating
  process).  In practice the sentinel-header strip on the Go side
  prevents the echo loop; PR-4's conflict detector handles the case
  where an external editor races with our projection.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol

logger = logging.getLogger("acc.role_sync_conflict")


# ---------------------------------------------------------------------------
# Publisher Protocol — minimal NATS-publish surface
# ---------------------------------------------------------------------------


class EventPublisher(Protocol):
    """Just enough of :class:`acc.backends.SignalingBackend` for tests.

    Production wires this to ``signaling_backend.publish``; tests inject
    a recording fake.
    """

    async def publish(self, subject: str, payload: bytes) -> None: ...


# ---------------------------------------------------------------------------
# Recorded write — what we remember to detect conflicts
# ---------------------------------------------------------------------------


@dataclass
class RecordedWrite:
    """One CRD-driven file write that this module performed.

    Stored per role_id so the next file-watcher event can be classified:

    * ``ts`` < ``conflict_window_s`` ago AND ``body_hash`` matches the
      file we just wrote → echo, silent.
    * ``ts`` < ``conflict_window_s`` ago AND ``body_hash`` differs →
      concurrent write → conflict.
    * ``ts`` ≥ ``conflict_window_s`` ago → operator edit; emit
      ``applied`` (the file-side controller will project this back to
      CRD via PR-2).
    """
    ts: float
    body_hash: str
    body_snippet: str  # first ~200 chars for the conflict event payload


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ConflictDetector — the main object
# ---------------------------------------------------------------------------


class ConflictDetector:
    """Classifies file changes as echoes vs. real edits, emits events.

    Used by :class:`acc.role_crd_loader.RoleCRDProjector` in mirror mode.
    All file-watcher events flow through :meth:`classify_file_change`;
    the projector also calls :meth:`record_our_write` after every
    CRD-driven file write.

    Args:
        publisher: NATS-publish surface.  ``None`` disables event
            publication (used in tests that don't care about side
            effects).
        events_subject: Subject *prefix* — ``.applied`` /
            ``.conflict`` suffixes are appended per event type.
        conflict_window_s: A file change within this window of a
            recorded write is correlated.  Outside the window the file
            change is treated as an operator edit.
        now: Optional time source (defaults to ``time.monotonic``);
            injected by tests to drive deterministic scenarios.
    """

    APPLIED_SUFFIX = "applied"
    CONFLICT_SUFFIX = "conflict"

    def __init__(
        self,
        publisher: Optional[EventPublisher] = None,
        events_subject: str = "acc.role.sync",
        conflict_window_s: float = 2.0,
        now: Optional[Any] = None,
    ) -> None:
        self._publisher = publisher
        self._subject = events_subject.rstrip(".")
        self._window_s = conflict_window_s
        self._now = now if now is not None else time.monotonic
        self._writes: dict[str, RecordedWrite] = {}
        # Stats — exposed for /metrics integration in a follow-up PR.
        self.applied_count = 0
        self.conflict_count = 0
        self.echo_count = 0

    # ----- recording the projector's own writes ------------------------

    def record_our_write(self, role_id: str, body: str) -> None:
        """Remember that we (the CRD projector) just wrote *body* for
        *role_id*.  The next file-watcher event for this role is
        classified against this record."""
        self._writes[role_id] = RecordedWrite(
            ts=self._now(),
            body_hash=_hash(body),
            body_snippet=body[:200],
        )

    # ----- classifying file watcher events ------------------------------

    def classify_file_change(
        self, role_id: str, file_body: str,
    ) -> str:
        """Classify a file-watcher event for *role_id*.

        Returns one of:

        * ``"echo"``    — file matches our last CRD-driven write within
                          the conflict window.  Caller should ignore.
        * ``"applied"`` — genuine operator edit (no recent CRD write,
                          or window expired).  Caller should propagate.
        * ``"conflict"``— file changed within the window but content
                          differs from our last write.  Caller resolves
                          via LWW; we emit the loser event.
        """
        recorded = self._writes.get(role_id)
        body_hash = _hash(file_body)

        if recorded is None:
            self.applied_count += 1
            return "applied"

        age_s = self._now() - recorded.ts
        if age_s > self._window_s:
            # Outside the window — assume operator edit.  Drop the
            # record so the next event doesn't false-positive.
            self._writes.pop(role_id, None)
            self.applied_count += 1
            return "applied"

        if body_hash == recorded.body_hash:
            self.echo_count += 1
            return "echo"

        # Same role, within window, content differs — a real conflict.
        self.conflict_count += 1
        return "conflict"

    # ----- event publication --------------------------------------------

    async def publish_applied(self, role_id: str, source: str) -> None:
        """Emit ``<subject>.applied``.

        Args:
            role_id: The role whose definition was successfully
                projected.
            source: ``"file"`` or ``"crd"`` — which side originated
                the write.
        """
        await self._publish(
            self.APPLIED_SUFFIX,
            {"role_id": role_id, "source": source},
        )

    async def publish_conflict(
        self,
        role_id: str,
        winner_source: str,
        loser_source: str,
        loser_body: str,
    ) -> None:
        """Emit ``<subject>.conflict`` with enough payload for an
        audit log + the TUI badge (PR-5)."""
        await self._publish(
            self.CONFLICT_SUFFIX,
            {
                "role_id": role_id,
                "winner_source": winner_source,
                "loser_source": loser_source,
                "loser_snippet": loser_body[:200],
                "ts": time.time(),
            },
        )

    async def _publish(self, suffix: str, body: dict[str, Any]) -> None:
        if self._publisher is None:
            return
        subject = f"{self._subject}.{suffix}"
        try:
            payload = json.dumps(body, sort_keys=True).encode("utf-8")
            await self._publisher.publish(subject, payload)
        except Exception as exc:  # noqa: BLE001
            # NATS hiccup must not break the projector's hot loop.
            logger.warning("role-sync: publish %s failed: %s", subject, exc)
