"""The one place a channel message becomes a task.

Every inbound channel — chat, voice, webhook, whatever is added next — passes
through :func:`admit_request` before anything is dispatched. That is the whole
design: authorisation is decided **once**, not re-implemented per adapter.

Adding a platform should mean writing a transport. If each adapter decided for
itself who may ask for work, the newest one would be the weakest, and the
weakest is the one that gets found.

Three rules the shared point enforces:

* **Default deny.** An identity nothing has admitted cannot cause a task to run,
  and the refusal says why rather than dropping the message.
* **Scope matters.** A direct message and a shared channel are different
  contexts. A grant for one does not carry to the other.
* **Attribution is not optional.** The requester is stamped onto the task and
  the audit record, so "who asked for this" is answerable afterwards.

:func:`conformance_report` exists so a future adapter can be checked against
this contract rather than trusted to have followed it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acc.identity import AccessError, Principal, Tier, require, resolve_external

logger = logging.getLogger("acc.channel_access")

#: Every admission decision this process made, for the audit record.
_JOURNAL: list[dict[str, Any]] = []


def journal() -> list[dict[str, Any]]:
    return list(_JOURNAL)


def clear_journal() -> None:
    _JOURNAL.clear()


@dataclass
class InboundRequest:
    """A message that wants to become a task."""

    channel: str            # "slack", "voice", "webhook", ...
    subject: str            # the requester, as the channel names them
    text: str = ""
    scope: str = ""         # "direct", a channel id, ...
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Admission:
    """The decision, and what to stamp on the task if it proceeds."""

    allowed: bool
    principal: Principal
    request: InboundRequest
    reason: str = ""
    at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "channel": self.request.channel,
            "subject": self.request.subject,
            "scope": self.request.scope,
            "tier": self.principal.tier,
            "reason": self.reason,
            "at": self.at,
        }

    def task_attribution(self) -> dict[str, Any]:
        """What goes onto the task payload.

        Carried explicitly rather than left to each adapter: an unattributed
        task is one nobody can answer questions about later.
        """
        return {
            "requested_by": self.principal.attribution(),
            "requester_subject": self.principal.subject,
            "requester_source": self.principal.source,
            "requester_tier": self.principal.tier,
            "requester_channel": self.request.channel,
            "requester_scope": self.request.scope,
        }


def admit_request(
    request: InboundRequest,
    *,
    need: str = Tier.REQUESTER,
    repo_root: Path | None = None,
) -> Admission:
    """Decide whether *request* may become a task. Never raises.

    Every channel calls this. The decision is journalled either way, because a
    refusal nobody can see is indistinguishable from a message that never
    arrived.
    """
    principal = resolve_external(
        request.subject, request.channel, scope=request.scope, repo_root=repo_root
    )
    try:
        require(principal, need)
        admission = Admission(True, principal, request, "", time.time())
    except AccessError as exc:
        admission = Admission(False, principal, request, str(exc), time.time())
        logger.warning(
            "channel_access: DENIED %s on %s (%s)",
            request.subject, request.channel, exc,
        )

    _JOURNAL.append(admission.as_dict())
    return admission


def refusal_message(admission: Admission) -> str:
    """What to send back to the requester.

    Says the request was refused and that a human can change that. It does not
    describe the access model or name who the operators are — a refusal is not
    an opportunity to enumerate the deployment for whoever knocked.
    """
    return (
        "This request was not accepted: you are not currently permitted to ask "
        "this collective for work. An operator can admit you."
    )


# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------

#: What any channel adapter must do. Checked rather than assumed, because the
#: newest adapter is the one most likely to have skipped a step.
CONTRACT = (
    "calls admit_request before dispatching",
    "drops the request when admission is refused",
    "stamps task_attribution onto the task payload",
    "passes the scope it received",
)


def conformance_report(
    *,
    calls_admit: bool,
    drops_on_refusal: bool,
    stamps_attribution: bool,
    passes_scope: bool,
) -> dict[str, Any]:
    """Score an adapter against the contract.

    A helper for an adapter's own test suite: a future channel proves it
    inherits the model instead of asserting that it does in a comment.
    """
    results = {
        CONTRACT[0]: calls_admit,
        CONTRACT[1]: drops_on_refusal,
        CONTRACT[2]: stamps_attribution,
        CONTRACT[3]: passes_scope,
    }
    missing = [name for name, ok in results.items() if not ok]
    return {"conformant": not missing, "results": results, "missing": missing}
