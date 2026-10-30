"""Who a capability call serves, at what ceiling, in which collective.

Skills receive only their arguments, and the arguments come from the model.
A skill that must act *as the requester* — read the knowledge store at the
task's ceiling (RDF-05), write the requester's private graph — cannot take
those from its arguments: a model could claim any ceiling.  So
:func:`acc.capability_dispatch.dispatch_invocations` sets them here, from the
task the governance layer admitted, for the duration of each call.

A ContextVar, not an attribute: an agent runs tasks concurrently (the same
reason as :func:`acc.credentials.live.serving`).
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class CallContext:
    requester: str = ""
    #: The task's ceiling (D-014).  Empty = no call in progress.
    ceiling: str = ""
    collective_id: str = ""
    task_id: str = ""


_CURRENT: ContextVar[CallContext] = ContextVar("acc_call_context", default=CallContext())


@contextlib.contextmanager
def calling(ctx: CallContext) -> Iterator[None]:
    token = _CURRENT.set(ctx)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current() -> CallContext:
    """The call in progress; an empty context outside one."""
    return _CURRENT.get()
