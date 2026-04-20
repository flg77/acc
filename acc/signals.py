"""ACC signal type constants, NATS subject helpers, and Redis key helpers.

All NATS subjects follow the pattern ``acc.{collective_id}.{signal_type_lower}``.
All Redis keys follow the pattern ``acc:{collective_id}:{agent_id}:{resource}``.

Usage::

    from acc.signals import SIG_HEARTBEAT, subject_heartbeat, redis_role_key

    subject = subject_heartbeat("sol-01")
    # → "acc.sol-01.heartbeat"

    key = redis_role_key("sol-01", "analyst-9c1d")
    # → "acc:sol-01:analyst-9c1d:role"
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
