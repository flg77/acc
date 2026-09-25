"""Direct messages to one agent — the inbox (OpenSpec
``20260923-lessons-that-travel`` Phase 6, vault PA-02).

Until now the only words that could reach a running agent were *cancel* and
*do it again* (``PLAN_STEP_CONTROL``, ``TASK_CANCEL``).  This adds one
addressed subject, ``acc.{cid}.agent.{agent_id}.inbox`` (SYNAPTIC), and one
typed envelope, :class:`AgentMessage`, with two deliveries:

* ``steer`` — the message lands in the prompt of the task in flight, as an
  ``OPERATOR_STEERING`` block right before the task, evicted last.  ACC's task
  is one LLM call, so "in flight" means "the prompt has not been built yet":
  a steer that arrives when nothing is in flight is delivered as a follow-up
  and the receipt says so.
* ``follow_up`` — the message becomes a new task for this agent, handled by
  the same task loop as a ``TASK_ASSIGN`` addressed to it, carrying the
  sender's attribution (tier and ceiling included).  No bus round-trip: a
  worker may not publish ``task.assign``, and does not need to.

Who may publish to an inbox: the arbiter, the TUI/CLI (the operator) — the
NKey matrix (``acc/nats_permissions.yaml``).  Not workers: this is the honest
v1 the gap item named — the arbiter relays, the matrix stays static.  Sender
identity is what the matrix let through, never a field in the payload the
receiver trusts on its own.

Receipts live in Redis (``acc:{cid}:message:{id}``, ``acc:{cid}:agent:{id}:
messages``) and in the receiver's ``messages-<agent_id>`` tracelog journal,
so ``acc-cli msg tail`` needs no new subject.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from acc.signals import SIG_AGENT_MESSAGE

logger = logging.getLogger("acc.agent_messages")

SCHEMA_REV = 1
Delivery = Literal["steer", "follow_up", "auto"]
MAX_BODY_CHARS = 4000

STEERING_HEADING = (
    "OPERATOR_STEERING (messages addressed to you while this task was in "
    "flight, from your operator or arbiter -- act on them):"
)

#: Keys copied from a message onto the follow-up task so the task runs as the
#: sender (HG-40.1b item 4: attribution inherits, ceiling included).
ATTRIBUTION_KEYS = (
    "requested_by", "requester_subject", "requester_source", "requester_tier",
    "requester_ceiling", "requester_channel", "requester_scope",
)


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    signal_type: Literal["AGENT_MESSAGE"] = SIG_AGENT_MESSAGE
    schema_rev: int = SCHEMA_REV
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = Field(default_factory=time.time)
    collective_id: str = ""
    #: Who sent it, as the matrix knows them: an agent id, or a surface
    #: (``acc-cli``, ``tui``).  Informational; the matrix is the authority.
    from_agent: str = ""
    to_agent: str
    delivery: Delivery = "auto"
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    #: The task this message is about, when the sender knows it.
    task_id: str = ""
    #: The sender's principal, copied onto a follow-up task verbatim.
    attribution: dict[str, Any] = Field(default_factory=dict)

    def render(self) -> str:
        who = self.from_agent or "operator"
        return f"- [{who}] {self.body.strip()}"

    def follow_up_task(self, *, target_role: str) -> dict[str, Any]:
        """The ``TASK_ASSIGN``-shaped payload the receiver hands its own task
        loop.  Attribution comes from the message, never from the receiver."""
        task: dict[str, Any] = {
            "signal_type": "TASK_ASSIGN",
            "task_id": f"msg-{self.message_id[:12]}",
            "collective_id": self.collective_id,
            "from_agent": self.from_agent,
            "target_agent_id": self.to_agent,
            "target_role": target_role,
            "task_type": "follow_up",
            "content": self.body,
            "parent_task_id": self.task_id,
            "message_id": self.message_id,
            "ts": time.time(),
        }
        for key in ATTRIBUTION_KEYS:
            if key in self.attribution and self.attribution[key] not in (None, ""):
                task[key] = self.attribution[key]
        return task


def parse_message(payload: Any) -> AgentMessage | None:
    if not isinstance(payload, dict):
        logger.warning("agent_messages: dropped non-dict payload")
        return None
    try:
        return AgentMessage.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_messages: dropped invalid envelope: %s", str(exc).splitlines()[0])
        return None


class SteerRing:
    """Steer messages waiting for the next prompt build.  Bounded, consumed on
    read, no filtering: the transport already checked the address, and a
    steer is never hearsay — it comes from a principal the matrix admitted."""

    def __init__(self, *, maxlen: int = 16) -> None:
        self._ring: deque[AgentMessage] = deque(maxlen=max(1, int(maxlen)))

    def __len__(self) -> int:
        return len(self._ring)

    def offer(self, message: AgentMessage) -> bool:
        if any(m.message_id == message.message_id for m in self._ring):
            return False
        self._ring.append(message)
        return True

    def take(self) -> list[AgentMessage]:
        out = list(self._ring)
        self._ring.clear()
        return out

    def peek(self) -> list[AgentMessage]:
        return list(self._ring)


def steering_parts(messages: list[AgentMessage] | None) -> tuple[str, list[str]]:
    items = [m.render() for m in (messages or [])]
    return (STEERING_HEADING, items) if items else ("", [])


def render_steering_block(messages: list[AgentMessage] | None) -> str:
    heading, items = steering_parts(messages)
    return "\n".join([heading, *items]) if items else ""
