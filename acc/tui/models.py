"""ACC TUI data models — snapshots derived exclusively from NATS payloads.

No Redis or LanceDB access; all state is rebuilt from HEARTBEAT,
TASK_COMPLETE, and ALERT_ESCALATE signals observed on the bus.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class AgentSnapshot:
    """Live state snapshot for one agent, rebuilt from NATS payloads."""

    agent_id: str
    role: str = ""
    state: str = "REGISTERING"         # REGISTERING | ACTIVE | DRAINING
    last_heartbeat_ts: float = 0.0

    # StressIndicators (from HEARTBEAT payload — ACC-6a)
    drift_score: float = 0.0
    cat_b_deviation_score: float = 0.0
    token_budget_utilization: float = 0.0
    reprogramming_level: int = 0
    task_count: int = 0
    last_task_latency_ms: float = 0.0
    cat_a_trigger_count: int = 0       # accumulated from ALERT_ESCALATE payloads
    cat_b_trigger_count: int = 0

    # Role metadata
    role_version: str = "0.1.0"
    cat_c_rule_count: int = 0

    def is_stale(self, heartbeat_interval_s: float = 30.0) -> bool:
        """Return True when this agent has missed two consecutive heartbeat intervals.

        Args:
            heartbeat_interval_s: Expected heartbeat interval in seconds.

        Returns:
            True if ``last_heartbeat_ts`` is older than ``2 × heartbeat_interval_s``.
        """
        if self.last_heartbeat_ts == 0.0:
            # Never received a heartbeat — treat as stale only if agent_id is known
            return True
        return (time.time() - self.last_heartbeat_ts) > (2 * heartbeat_interval_s)

    @property
    def display_state(self) -> str:
        """State string for display, accounting for staleness."""
        if self.is_stale():
            return "STALE"
        return self.state

    @property
    def drift_sparkbar(self) -> str:
        """Compact spark representation of drift_score (0.0–1.0) as block chars."""
        bars = " ▁▂▃▄▅▆▇█"
        idx = min(int(self.drift_score * (len(bars) - 1)), len(bars) - 1)
        return bars[idx] * 3

    @property
    def ladder_label(self) -> str:
        """Reprogramming level as a display label."""
        label = f"L{self.reprogramming_level}"
        if self.reprogramming_level > 0:
            label += " ⚠"
        return label


@dataclass
class CollectiveSnapshot:
    """Aggregate state snapshot for one collective, built from NATS observations."""

    collective_id: str
    agents: dict[str, AgentSnapshot] = field(default_factory=dict)
    icl_episode_count: int = 0          # sum from TASK_COMPLETE payloads
    pattern_count: int = 0
    last_updated_ts: float = 0.0

    # Role audit history (populated if ACC-6a is running on target agents)
    role_audit_rows: list[dict] = field(default_factory=list)

    # --- Computed governance aggregates ---

    @property
    def total_cat_a_triggers(self) -> int:
        return sum(a.cat_a_trigger_count for a in self.agents.values())

    @property
    def total_cat_b_deviations(self) -> int:
        """Count of agents that have recorded any Cat-B deviation."""
        return sum(1 for a in self.agents.values() if a.cat_b_deviation_score > 0)

    @property
    def total_cat_c_rules(self) -> int:
        return sum(a.cat_c_rule_count for a in self.agents.values())

    @property
    def avg_token_utilization(self) -> float:
        active = [a for a in self.agents.values() if not a.is_stale()]
        if not active:
            return 0.0
        return sum(a.token_budget_utilization for a in active) / len(active)

    @property
    def p95_latency_ms(self) -> float:
        """Approximate p95 latency across all active agents."""
        latencies = sorted(
            a.last_task_latency_ms
            for a in self.agents.values()
            if a.last_task_latency_ms > 0
        )
        if not latencies:
            return 0.0
        idx = max(0, int(len(latencies) * 0.95) - 1)
        return latencies[idx]

    @property
    def blocked_task_count(self) -> int:
        return sum(a.cat_b_trigger_count for a in self.agents.values())
