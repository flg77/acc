"""Which ssh host the dispatcher approved for the call now running (vault 060).

``ssh_exec`` used to trust only the static ``ACC_SSH_HOST_ALLOWLIST``.  Hosts
are now granted once by a person (a ``host:`` standing grant), by an approved
plan (059 ``Plan.hosts``) or for one call.  The dispatcher decides; the adapter
must not take the model's word for it, so the decision travels in a context
variable the dispatcher sets around the one invocation -- never in the call's
arguments, which the model writes.
"""

from __future__ import annotations

import contextvars
import os

LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

_APPROVED: contextvars.ContextVar[str] = contextvars.ContextVar("acc_approved_host", default="")


def env_allowlist() -> set[str]:
    """The operator's static seed list (``ACC_SSH_HOST_ALLOWLIST``)."""
    raw = os.environ.get("ACC_SSH_HOST_ALLOWLIST", "")
    return {h.strip() for h in raw.split(",") if h.strip()}


def pre_allowed(host: str) -> bool:
    """Allowed without asking anyone: local, or on the static seed list."""
    return host in LOCAL_HOSTS or host in env_allowlist()


def approve(host: str) -> contextvars.Token:
    """Mark *host* approved for the invocation about to run."""
    return _APPROVED.set(host)


def reset(token: contextvars.Token) -> None:
    _APPROVED.reset(token)


def allowed(host: str) -> bool:
    """The adapter's check: pre-allowed, or approved by the dispatcher for this call."""
    return pre_allowed(host) or (bool(host) and _APPROVED.get() == host)
