"""ACC signal type constants, NATS subject helpers, and Redis key helpers.

All NATS subjects follow the pattern ``acc.{collective_id}.{signal_type_lower}``.
All Redis keys follow the pattern ``acc:{collective_id}:{agent_id}:{resource}``.

Cross-collective bridge subjects (ACC-9) follow the pattern:
  ``acc.bridge.{from_cid}.{to_cid}.delegate``   — task delegation request
  ``acc.bridge.{to_cid}.{from_cid}.result``      — result returned to originator
  ``acc.bridge.{collective_id}.pending``          — local JetStream queue (edge)

Usage::

    from acc.signals import SIG_HEARTBEAT, subject_heartbeat, redis_role_key

    subject = subject_heartbeat("sol-01")
    # → "acc.sol-01.heartbeat"

    key = redis_role_key("sol-01", "analyst-9c1d")
    # → "acc:sol-01:analyst-9c1d:role"

    bridge = subject_bridge_delegate("sol-01", "sol-02")
    # → "acc.bridge.sol-01.sol-02.delegate"
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Signal type constants
# ---------------------------------------------------------------------------

SIG_REGISTER = "REGISTER"
SIG_HEARTBEAT = "HEARTBEAT"
SIG_TASK_ASSIGN = "TASK_ASSIGN"
SIG_TASK_COMPLETE = "TASK_COMPLETE"
SIG_ROLE_UPDATE = "ROLE_UPDATE"
SIG_ROLE_APPROVAL = "ROLE_APPROVAL"
SIG_ALERT_ESCALATE = "ALERT_ESCALATE"

# Cross-collective bridge signals (ACC-9)
SIG_BRIDGE_DELEGATE = "BRIDGE_DELEGATE"
SIG_BRIDGE_RESULT = "BRIDGE_RESULT"

# ---------------------------------------------------------------------------
# NATS subject helpers
# ---------------------------------------------------------------------------


def subject_register(collective_id: str) -> str:
    """Return the NATS subject for REGISTER signals."""
    return f"acc.{collective_id}.register"


def subject_heartbeat(collective_id: str) -> str:
    """Return the NATS subject for HEARTBEAT signals."""
    return f"acc.{collective_id}.heartbeat"


def subject_task(collective_id: str) -> str:
    """Return the NATS subject for TASK_ASSIGN / TASK_COMPLETE signals."""
    return f"acc.{collective_id}.task"


def subject_role_update(collective_id: str) -> str:
    """Return the NATS subject for ROLE_UPDATE signals."""
    return f"acc.{collective_id}.role_update"


def subject_role_approval(collective_id: str) -> str:
    """Return the NATS subject for ROLE_APPROVAL signals."""
    return f"acc.{collective_id}.role_approval"


def subject_alert(collective_id: str) -> str:
    """Return the NATS subject for ALERT_ESCALATE signals."""
    return f"acc.{collective_id}.alert"


# ---------------------------------------------------------------------------
# Cross-collective bridge subject helpers (ACC-9)
# ---------------------------------------------------------------------------


def subject_bridge_delegate(from_cid: str, to_cid: str) -> str:
    """Return the NATS subject for cross-collective task delegation.

    The originating collective publishes here; the target collective subscribes.

    Example::

        subject_bridge_delegate("sol-01", "sol-02")
        # → "acc.bridge.sol-01.sol-02.delegate"
    """
    return f"acc.bridge.{from_cid}.{to_cid}.delegate"


def subject_bridge_result(from_cid: str, to_cid: str) -> str:
    """Return the NATS subject for cross-collective task results.

    The target collective (``to_cid``) publishes results here after processing;
    the originating collective (``from_cid``) subscribes to receive them.

    Note: the subject is keyed by ``to_cid`` first so that a single wildcard
    subscription ``acc.bridge.sol-02.*.result`` collects all results produced
    by collective ``sol-02``.

    Example::

        subject_bridge_result("sol-01", "sol-02")
        # → "acc.bridge.sol-02.sol-01.result"
    """
    return f"acc.bridge.{to_cid}.{from_cid}.result"


def subject_bridge_pending(collective_id: str) -> str:
    """Return the NATS/JetStream subject for the local bridge pending queue.

    Used by edge agents to enqueue delegations while the leaf node is
    disconnected.  The JetStream stream ``acc.bridge.{cid}.pending`` buffers
    messages until the leaf reconnects and they can be forwarded to the hub.

    Example::

        subject_bridge_pending("sol-edge-01")
        # → "acc.bridge.sol-edge-01.pending"
    """
    return f"acc.bridge.{collective_id}.pending"


# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------


def redis_role_key(collective_id: str, agent_id: str) -> str:
    """Return the Redis key for the active role definition of an agent."""
    return f"acc:{collective_id}:{agent_id}:role"


def redis_centroid_key(collective_id: str, agent_id: str) -> str:
    """Return the Redis key for the role embedding centroid of an agent."""
    return f"acc:{collective_id}:{agent_id}:centroid"


def redis_stress_key(collective_id: str, agent_id: str) -> str:
    """Return the Redis key for the StressIndicators snapshot of an agent."""
    return f"acc:{collective_id}:{agent_id}:stress"


def redis_collective_key(collective_id: str) -> str:
    """Return the Redis key for the collective registry (arbiter agent_id etc.)."""
    return f"acc:{collective_id}:registry"
