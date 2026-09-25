"""Lessons that travel — the typed envelope a learning rides between agents.

OpenSpec ``20260923-lessons-that-travel`` Phase 1.  ACC has two ways to keep
a learning (a reflected memory note; a signed role / skill / rule change) and,
until this module, no way to hand one from agent to agent as a message.
``KNOWLEDGE_SHARE`` was defined, permissioned and drawn in
``docs/SUBAGENT_COMMUNICATION.md`` ("Pattern B") but nothing published it.
This is the payload that subject now carries.

Three rules, in order of how often they will be argued about:

* **Provenance travels with the lesson.**  ``scope``, ``ceiling``,
  ``source_requesters`` and ``evidence`` are the same fields RP-01 put on
  the note (`20260823-attributed-memory`); a reader below the ceiling never
  sees it, a reader in another scope never sees it.  Putting the note on the
  wire changes where it can be *read*, not who may read it.
* **Ephemeral by default.**  A peer lesson is rendered into the reader's
  *next* prompt and then consumed.  It never enters the reader's hot cache or
  episode store, so the retrieval filters RP-01 Phase 3 built stay exact.
  Durable adoption is a later phase and a role decision.
* **The wire is typed, and a bad envelope is dropped, not guessed at.**
  ``Lesson.model_validate`` is the only way in; unknown fields are ignored so
  a newer producer does not break an older consumer.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from acc.identity import exceeds_ceiling
from acc.memory_scope import LOCAL_SCOPE
from acc.signals import SIG_KNOWLEDGE_SHARE

logger = logging.getLogger("acc.lessons")

#: Schema revision of the envelope.  Bump when a *required* field changes.
SCHEMA_REV = 1

#: Kinds a lesson may carry.  Phase 1 renders ``note`` only; the other kinds
#: are accepted on the wire so producers can start emitting them and the
#: ledger (Phase 2) can record them.
LessonKind = Literal["note", "role_patch", "skill_patch", "rule"]

#: Ring defaults.  Small on purpose: a peer lesson is a hint for the next
#: turn, not a second memory.
DEFAULT_RING_SIZE = 32
DEFAULT_TTL_S = 3600.0

PEER_LESSONS_HEADING = (
    "PEER_LESSONS (lessons other agents in this collective just learned; "
    "unverified -- weigh them, do not repeat them as fact):"
)


class LessonEvidence(BaseModel):
    """What the lesson rests on.  Ids, never content: the content is in the
    producer's episode store under its own scope and ceiling."""

    model_config = ConfigDict(extra="ignore")

    source_episode_ids: list[str] = Field(default_factory=list)
    dissent: str = ""
    tracelog_refs: list[str] = Field(default_factory=list)


class Lesson(BaseModel):
    """One learning, on the wire.

    Carried on ``acc.{cid}.knowledge.{tag}`` (``SIG_KNOWLEDGE_SHARE``,
    PARACRINE -- receptor-filtered) and, when ``target_agent_id`` is set,
    meant for exactly one agent on the same subject (every other agent drops
    it).  No new subjects: the ``SUBAGENT_COMMUNICATION`` rule stands.
    """

    model_config = ConfigDict(extra="ignore")

    signal_type: Literal["KNOWLEDGE_SHARE"] = SIG_KNOWLEDGE_SHARE
    schema_rev: int = SCHEMA_REV
    lesson_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = Field(default_factory=time.time)
    collective_id: str = ""
    from_agent: str
    role_label: str = ""
    kind: LessonKind = "note"
    #: The ligand.  Empty = universal (every receptor accepts it).
    domain_tag: str = ""
    #: Where it was learned, and how far it may be read (RP-01 fields).
    scope: str = LOCAL_SCOPE
    ceiling: str = ""
    source_requesters: list[str] = Field(default_factory=list)
    #: What happened, what was learned, what should change.
    trigger: str = ""
    summary: str = Field(min_length=1, max_length=2000)
    evidence: LessonEvidence = Field(default_factory=LessonEvidence)
    expected_outcome: str = ""
    #: For ``role_patch`` / ``skill_patch`` / ``rule``: the proposed change.
    #: Never applied by a consumer; it becomes a ``PROPOSE_*`` (Phase 2).
    patch: dict[str, Any] | None = None
    probation_s: float = 0.0
    confidence: float = 0.0
    #: Direct address on the shared subject.  Empty = broadcast.
    target_agent_id: str = ""

    def visible_to(self, *, reader_ceiling: str, reader_scope: str) -> bool:
        """The information rule, applied to a lesson: same scope, and not
        above the reader's ceiling.  An unset lesson ceiling reads as
        CRITICAL (the note rule), so an unlabelled lesson reaches only the
        operator."""
        if str(self.scope or LOCAL_SCOPE) != str(reader_scope or LOCAL_SCOPE):
            return False
        return not exceeds_ceiling(self.ceiling or "CRITICAL", reader_ceiling)

    def render(self) -> str:
        """One prompt line.  Role, not agent id: the reader reasons about
        *who kind of* learned it, and an agent id is a secret-shaped token."""
        who = self.role_label or "peer"
        line = f"- [{who}] {self.summary.strip()}"
        if self.evidence.dissent:
            line += f" (dissent: {self.evidence.dissent.strip()})"
        return line


def lesson_from_note(
    note: Any,
    *,
    collective_id: str,
    domain_tag: str = "",
    trigger: str = "reflection",
) -> Lesson:
    """Lift a reflected :class:`acc.memory_reflection.MemoryNote` onto the
    wire.  The note keeps its id: a lesson *is* the note, travelling."""
    return Lesson(
        lesson_id=str(getattr(note, "note_id", "") or uuid.uuid4().hex),
        ts=float(getattr(note, "ts", 0.0) or time.time()),
        collective_id=collective_id,
        from_agent=str(getattr(note, "agent_id", "") or ""),
        role_label=str(getattr(note, "role_label", "") or ""),
        kind="note",
        domain_tag=domain_tag or "",
        scope=str(getattr(note, "scope", LOCAL_SCOPE) or LOCAL_SCOPE),
        ceiling=str(getattr(note, "ceiling", "") or ""),
        source_requesters=list(getattr(note, "source_requesters", []) or []),
        trigger=trigger,
        summary=str(getattr(note, "summary", "") or "").strip() or "(empty)",
        evidence=LessonEvidence(
            source_episode_ids=list(getattr(note, "source_ids", []) or []),
            dissent=str(getattr(note, "dissent", "") or ""),
        ),
        confidence=float(getattr(note, "confidence", 0.0) or 0.0),
    )


def parse_lesson(payload: Any) -> Lesson | None:
    """Validate an inbound payload.  ``None`` (and a WARNING naming the
    reason) for anything that is not a lesson -- a malformed envelope is
    dropped, never partially read."""
    if not isinstance(payload, dict):
        logger.warning("lessons: dropped non-dict payload (%s)", type(payload).__name__)
        return None
    try:
        return Lesson.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 -- pydantic ValidationError and friends
        logger.warning("lessons: dropped invalid envelope: %s", str(exc).splitlines()[0])
        return None


class PeerLessonRing:
    """The lessons an agent has heard and not yet used.

    Bounded (oldest evicted), time-limited, and **consumed on read**: a
    lesson rendered into one prompt is gone.  Lessons the current reader may
    not see (ceiling, scope) stay for a later reader that may.
    """

    def __init__(self, *, maxlen: int = DEFAULT_RING_SIZE, ttl_s: float = DEFAULT_TTL_S) -> None:
        self._ring: deque[Lesson] = deque(maxlen=max(1, int(maxlen)))
        self._ttl_s = float(ttl_s)

    def __len__(self) -> int:
        return len(self._ring)

    def offer(self, lesson: Lesson, *, now: float | None = None) -> bool:
        """Accept a lesson the transport already filtered (receptor, sender,
        target).  Duplicates by id are ignored."""
        self._expire(now)
        if any(existing.lesson_id == lesson.lesson_id for existing in self._ring):
            return False
        self._ring.append(lesson)
        return True

    def take(
        self,
        *,
        reader_ceiling: str,
        reader_scope: str,
        limit: int,
        now: float | None = None,
    ) -> list[Lesson]:
        """The lessons this reader may see, newest first, at most *limit*;
        each one is removed from the ring."""
        self._expire(now)
        if limit <= 0:
            return []
        picked: list[Lesson] = []
        for lesson in reversed(self._ring):
            if lesson.kind != "note":
                continue
            if not lesson.visible_to(reader_ceiling=reader_ceiling, reader_scope=reader_scope):
                continue
            picked.append(lesson)
            if len(picked) >= limit:
                break
        if picked:
            gone = {p.lesson_id for p in picked}
            self._ring = deque(
                (item for item in self._ring if item.lesson_id not in gone),
                maxlen=self._ring.maxlen,
            )
        return picked

    def peek(self) -> list[Lesson]:
        """Everything currently held, oldest first.  For the CLI and tests."""
        return list(self._ring)

    def _expire(self, now: float | None) -> None:
        if self._ttl_s <= 0:
            return
        cutoff = (now if now is not None else time.time()) - self._ttl_s
        if any(item.ts < cutoff for item in self._ring):
            self._ring = deque(
                (item for item in self._ring if item.ts >= cutoff),
                maxlen=self._ring.maxlen,
            )


def peer_lessons_parts(lessons: list[Lesson] | None) -> tuple[str, list[str]]:
    """``(heading, items)`` for the context packer -- the same shape the
    notes and episode blocks use, so eviction can be per item."""
    items = [lesson.render() for lesson in (lessons or [])]
    if not items:
        return "", []
    return PEER_LESSONS_HEADING, items


def render_peer_lessons_block(lessons: list[Lesson] | None) -> str:
    heading, items = peer_lessons_parts(lessons)
    return "\n".join([heading, *items]) if items else ""
