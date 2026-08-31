"""Model-visible means logged — the prompt corpus as tamper-evident evidence.

ACC's audit chain records what the runtime **decided**: the Cat-A verdict,
the guardrail violations, the outcome, the oversight id.  It did not record
what the model was **shown**.  Those are different questions, and only the
first one appears on a compliance checklist — which is why only the first
one got built.

An auditor asking "why did this agent produce that output" is asking for the
model-visible corpus.  Under EU AI Act Art. 12 (record-keeping) and Art. 13
(transparency), a decision log answers a related but different question.
Without this module, two runs of the same collective against the same prompt
could legitimately diverge — different retrieval hits, a changed overlay, a
role file edited between runs — and nothing in the audit trail could say
which.

The invariant
-------------

**Every request that reaches a model backend produces a durable record of
the exact corpus that model was shown.**

It is enforced at **construction**, not by discipline at each call site:
:func:`recording_backend` wraps the backend as it is built.  ACC has exactly
two places that instantiate a concrete LLM backend, and both wrap:

* ``acc.config.build_llm_backend`` — the main factory.
  ``llm_failover.backend_for_entry`` deliberately routes through it, so
  failover clients are wrapped too, and a failover that reaches a second
  model produces a second record because a second model saw the corpus.
* ``acc.cli.llm_cmd._build_llm_only`` — a deliberate duplicate that mirrors
  the factory branch-by-branch to keep LanceDB and pymilvus out of the CLI
  image.  It does not inherit the factory's enforcement, so it applies the
  wrapper itself.

``tests/test_prompt_record.py`` pins that set: a third construction site
fails the build rather than silently reopening the hole.

Enforcing at construction rather than at call sites is the whole point.  When
this was written, three paths reached a model with **no audit record at
all** — ``acc.llm_failover``, ``acc.memory_reflection`` and the operator
CLI.  Patching those three would have closed the instances and left the
fourth caller, not yet written, free to bypass again.

Retention
---------

Digests are always recorded and are cheap.  The **full text** is retained
only when ``ACC_PROMPT_RECORD_FULL`` is truthy, because a byte-faithful
record of everything a model saw is also a durable record of everything it
was *given*: PII the guardrails would have redacted downstream, documents
above the reader's clearance, secrets that leaked into a prompt.  The
artifact must be governed as carefully as the actions it explains, so the
default is digest-only and full retention is a deliberate operator act.

Digest-only still answers the question that matters most: *given a candidate
reconstruction of the corpus, was it the one the model saw?*  That is enough
to prove or disprove a specific claim about a specific run.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Deque, Iterator

logger = logging.getLogger("acc.prompt_record")

__all__ = [
    "PromptRecord",
    "corpus_sha256",
    "record",
    "drain",
    "snapshot",
    "source",
    "full_retention_enabled",
    "recording_backend",
    "unwrap",
]

# Bounded so an unattended process that never drains (the operator CLI, a
# memory-reflection pass with no audit broker) cannot grow without limit.
_MAX_LEDGER = 256

_FULL_RETENTION_ENV = "ACC_PROMPT_RECORD_FULL"

# Framing version — bump if the canonicalisation below ever changes, so a
# digest can never be silently compared across two different definitions.
_FRAMING = b"acc-prompt-corpus/v1"


def full_retention_enabled() -> bool:
    """True when the operator opted into retaining full prompt text."""
    return os.environ.get(_FULL_RETENTION_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@dataclass
class PromptRecord:
    """One model-visible corpus, as evidence.

    Stored inside :class:`acc.audit.AuditRecord`, so it is covered by that
    record's ``evidence_hash`` and by the HMAC chain linking records — the
    corpus digest is tamper-evident on the same terms as everything else in
    the audit trail.
    """

    seq: int = 0
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    source: str = ""
    """Which path reached the model.

    Set from the :func:`source` context of the CALLING code —
    ``cognitive_core``, ``memory_reflection``, ``cli`` — falling back to
    the wrapper's construction label (``unattributed``) outside any.
    """

    model: str = ""

    system_sha256: str = ""
    user_sha256: str = ""
    corpus_sha256: str = ""
    """Digest over BOTH parts under a length-prefixed framing (see :func:`corpus_sha256`)."""

    system_chars: int = 0
    user_chars: int = 0

    # Populated only under ACC_PROMPT_RECORD_FULL.
    system_text: str = ""
    user_text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def corpus_sha256(system: str, user: str) -> str:
    """Digest of the (system, user) pair under a length-prefixed framing.

    Length-prefixing is not decoration.  Concatenating the two parts would
    make ``("ab", "c")`` and ``("a", "bc")`` collide — two genuinely
    different corpora with one digest, in a record whose entire purpose is
    telling corpora apart.
    """
    h = hashlib.sha256()
    h.update(_FRAMING)
    for part in (system, user):
        raw = part.encode("utf-8")
        h.update(b"\x00")
        h.update(str(len(raw)).encode("ascii"))
        h.update(b"\x00")
        h.update(raw)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_ledger: Deque[PromptRecord] = deque(maxlen=_MAX_LEDGER)
_seq = 0

# Which call path is currently talking to a model.  A ContextVar rather than
# a construction-time label because the SAME backend object serves several
# paths: the agent's task loop and its out-of-band reflection loop share one
# client.  Those run as separate asyncio tasks, so their contexts are
# isolated and each records under its own source — which is what keeps the
# reflection loop's calls out of the next task's audit record.
_source_var: ContextVar[str] = ContextVar("acc_prompt_source", default="")


@contextlib.contextmanager
def source(name: str) -> Iterator[None]:
    """Tag every model call made in this context with *name*."""
    token = _source_var.set(name)
    try:
        yield
    finally:
        _source_var.reset(token)


def record(system: str, user: str, *, source: str, model: str = "") -> PromptRecord:
    """Record one model-visible corpus and return the record.

    In-process and cannot fail for I/O reasons, which is what lets the
    invariant hold: the record exists before the request is dispatched.
    """
    global _seq
    full = full_retention_enabled()
    with _lock:
        _seq += 1
        rec = PromptRecord(
            seq=_seq,
            source=source,
            model=model,
            system_sha256=_sha256(system),
            user_sha256=_sha256(user),
            corpus_sha256=corpus_sha256(system, user),
            system_chars=len(system),
            user_chars=len(user),
            system_text=system if full else "",
            user_text=user if full else "",
        )
        _ledger.append(rec)
    return rec


def drain(source_name: str | None = None) -> list[PromptRecord]:
    """Take pending records, removing them from the ledger.

    Args:
        source_name: when given, take only records tagged with this source
            and leave the rest pending.  The task loop uses it so the
            agent's out-of-band reflection loop — which shares the same
            backend object and runs concurrently — cannot have its calls
            attributed to whichever task happens to write an audit record
            next.
    """
    with _lock:
        if source_name is None:
            out = list(_ledger)
            _ledger.clear()
            return out
        out = [r for r in _ledger if r.source == source_name]
        keep = [r for r in _ledger if r.source != source_name]
        _ledger.clear()
        _ledger.extend(keep)
    return out


def snapshot() -> list[PromptRecord]:
    """Read pending records without clearing (tests, diagnostics)."""
    with _lock:
        return list(_ledger)


def reset() -> None:
    """Clear the ledger and the sequence counter (tests only)."""
    global _seq
    with _lock:
        _ledger.clear()
        _seq = 0


# ---------------------------------------------------------------------------
# The enforcement point
# ---------------------------------------------------------------------------


class _RecordingLLMBackend:
    """Wraps an ``LLMBackend`` so every completion is recorded first.

    Attribute access falls through to the wrapped backend, so this stays a
    drop-in for anything the concrete backends expose (``embed``, model
    metadata, backend-specific helpers) without enumerating it here.

    ``complete`` deliberately takes ``*args, **kwargs`` rather than mirroring
    the signature: ``CognitiveCore._call_llm`` probes for the ``cache_prefix``
    kwarg and falls back on ``TypeError``.  Re-declaring the signature here
    would absorb that probe and silently disable prompt caching.
    """

    __slots__ = ("_base", "_source")

    def __init__(self, base: Any, *, source: str) -> None:
        self._base = base
        self._source = source

    async def complete(self, system: str, user: str, *args: Any, **kwargs: Any) -> Any:
        record(
            system, user,
            # The call path wins over the construction-time label: one
            # backend object serves the task loop and the reflection loop.
            source=_source_var.get() or self._source,
            model=str(getattr(self._base, "model", "") or ""),
        )
        return await self._base.complete(system, user, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"<recording {self._base!r} source={self._source}>"


def unwrap(backend: Any) -> Any:
    """Return the concrete backend behind a recording wrapper.

    For callers that need the real object rather than the recording proxy —
    diagnostics, and assertions about which backend the factory selected.
    Returns *backend* unchanged when it is not wrapped.
    """
    if isinstance(backend, _RecordingLLMBackend):
        return backend._base
    return backend


def recording_backend(base: Any, *, source: str = "unattributed") -> Any:
    """Wrap *base* so every completion lands in the ledger first.

    Idempotent: wrapping an already-wrapped backend returns it unchanged, so
    a caller that builds through the factory and then wraps again (or a
    failover chain re-wrapping its clients) cannot double-record one call.
    """
    if isinstance(base, _RecordingLLMBackend):
        return base
    if base is None:
        return base
    return _RecordingLLMBackend(base, source=source)
