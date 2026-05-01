"""ACC TUI data models — snapshots derived exclusively from NATS payloads.

No Redis or LanceDB access; all state is rebuilt from HEARTBEAT,
TASK_COMPLETE, ALERT_ESCALATE, and the 8 ACC-10 signal types observed on
the NATS bus.

ACC-10 additions: queue/backpressure/task-progress fields on AgentSnapshot,
                  PlanSnapshot dataclass, extended CollectiveSnapshot.
ACC-11 additions: domain_id, domain_drift_score on AgentSnapshot.
ACC-12 additions: compliance_health_score, owasp_violation_count,
                  oversight_pending_count on AgentSnapshot;
                  compliance_health_score, owasp_violation_log on
                  CollectiveSnapshot.
PR-telemetry:     CapabilityInvocationStats per (kind, target) on
                  CollectiveSnapshot; populated from the ``invocations``
                  field PR-B added to TASK_COMPLETE payloads.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Maximum number of items kept in FIFO-capped collections.
_MAX_KNOWLEDGE_FEED = 20
_MAX_EPISODE_NOMINEES = 20
_MAX_OWASP_LOG = 50
_MAX_INVOCATION_LOG = 50  # capability invocation tail (Performance screen)


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

    # ACC-10: queue / backpressure / task-progress (REQ-TUI-013)
    queue_depth: int = 0
    backpressure_state: str = "OPEN"   # OPEN | THROTTLE | CLOSED
    current_task_step: int = 0
    total_task_steps: int = 0
    task_progress_label: str = ""

    # ACC-11: domain identity and drift (REQ-TUI-014)
    domain_id: str = ""
    domain_drift_score: float = 0.0    # 0.0 (aligned) – 1.0 (drifted)

    # ACC-12: compliance health (REQ-TUI-015)
    compliance_health_score: float = 1.0   # 0.0 (critical) – 1.0 (clean)
    owasp_violation_count: int = 0
    oversight_pending_count: int = 0

    # LLM backend metadata (from HEARTBEAT.llm_backend — REQ-TUI-040)
    llm_backend: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_health: str = ""
    llm_p50_latency_ms: float = 0.0

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
    def queue_sparkbar(self) -> str:
        """Unicode block bar representing queue_depth (0–10+ buckets)."""
        blocks = " ▁▂▃▄▅▆▇█"
        # Clamp to 0–16 range for visual representation
        clamped = min(self.queue_depth, 16)
        idx = min(int(clamped / 16 * (len(blocks) - 1)), len(blocks) - 1)
        return blocks[idx] * 3

    @property
    def ladder_label(self) -> str:
        """Reprogramming level as a display label."""
        label = f"L{self.reprogramming_level}"
        if self.reprogramming_level > 0:
            label += " ⚠"
        return label

    @property
    def backpressure_css_class(self) -> str:
        """CSS class name for backpressure state colour coding."""
        return {
            "OPEN": "backpressure-open",
            "THROTTLE": "backpressure-throttle",
            "CLOSED": "backpressure-closed",
        }.get(self.backpressure_state, "backpressure-open")

    @property
    def compliance_css_class(self) -> str:
        """CSS class for compliance health score colour (REQ-TUI-019)."""
        if self.compliance_health_score >= 0.80:
            return "health-score-green"
        if self.compliance_health_score >= 0.50:
            return "health-score-amber"
        return "health-score-red"


@dataclass
class PlanSnapshot:
    """Snapshot of a single PLAN signal received from the arbiter (REQ-TUI-016)."""

    plan_id: str
    collective_id: str
    steps: list[dict] = field(default_factory=list)
    step_progress: dict[str, str] = field(default_factory=dict)
    # step_progress maps step_id → status ("PENDING"|"RUNNING"|"DONE"|"FAILED")
    received_ts: float = field(default_factory=time.time)


@dataclass
class CapabilityInvocationStats:
    """Per-(kind, target) running counters for the Performance screen.

    Populated from ``TASK_COMPLETE.invocations`` (the field PR-B added
    to the payload published in :func:`acc.agent.Agent._task_loop`).
    Each ``invocations`` entry is a dict with shape::

        {"kind": "skill"|"mcp", "target": "<id>" or "<server>.<tool>",
         "ok": bool, "error": str}

    Attributes:
        kind: ``"skill"`` or ``"mcp"`` — used to colour-code rows in
            the Performance screen and to dispatch on the prompt
            grammar reference (``[SKILL:...]`` vs ``[MCP:...]``).
        target: Skill id (e.g. ``"echo"``) or fully-qualified MCP
            server.tool name (e.g. ``"echo_server.echo"``).
        total: Every invocation seen, regardless of outcome.
        ok: Subset of *total* with ``ok=True``.
        last_error: Most recent non-empty ``error`` string — handy
            when an operator wants to know WHY a tool's success rate
            dropped without scrolling the audit log.
        last_seen_ts: UNIX seconds of the most recent observation;
            drives the "stale tools" hint in the panel header.
    """

    kind: str
    target: str
    total: int = 0
    ok: int = 0
    last_error: str = ""
    last_seen_ts: float = 0.0

    @property
    def fail(self) -> int:
        """Failures = total minus successes (Cat-A blocks, schema errors,
        adapter exceptions all collapse here — same shape as
        :class:`acc.capability_dispatch.InvocationOutcome`)."""
        return max(0, self.total - self.ok)

    @property
    def ok_rate(self) -> float:
        """0.0–1.0 success ratio.  Returns 1.0 when no invocations yet
        so a never-fired tool isn't shown as failing."""
        if self.total <= 0:
            return 1.0
        return self.ok / self.total


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

    # ACC-10: plan tracking (REQ-TUI-017)
    active_plans: dict[str, PlanSnapshot] = field(default_factory=dict)

    # ACC-10: knowledge share feed — FIFO-capped at _MAX_KNOWLEDGE_FEED (REQ-TUI-017)
    knowledge_feed: list[dict] = field(default_factory=list)

    # ACC-10: episode nomination queue — FIFO-capped at _MAX_EPISODE_NOMINEES (REQ-TUI-017)
    episode_nominees: list[dict] = field(default_factory=list)

    # ACC-12: collective-level compliance health (REQ-TUI-017)
    compliance_health_score: float = 1.0

    # ACC-12: OWASP violation log — FIFO-capped at _MAX_OWASP_LOG (REQ-TUI-017)
    owasp_violation_log: list[dict] = field(default_factory=list)

    # ACC-12: pending oversight items (REQ-TUI-025).  Populated from the
    # arbiter's HEARTBEAT — every other agent contributes nothing here.
    # Each dict carries the public surface of acc.oversight.OversightItem:
    #   oversight_id, task_id, agent_id, risk_level, summary,
    #   submitted_at_ms, status.
    oversight_pending_items: list[dict] = field(default_factory=list)

    # Signal flow log for CommunicationsScreen — FIFO-capped at 30
    signal_flow_log: list[dict] = field(default_factory=list)

    # PR-telemetry: capability-invocation aggregates rendered on the
    # Performance screen.  Keyed by ``f"{kind}:{target}"`` so skills and
    # MCP tools that happen to share a name don't collide.
    capability_stats: dict[str, "CapabilityInvocationStats"] = field(
        default_factory=dict
    )

    # FIFO tail of the most recent invocations so operators can scrub
    # failures even after the per-(kind, target) running totals have
    # absorbed them.  Each entry mirrors ``InvocationOutcome``'s wire
    # form: {kind, target, ok, error, agent_id, task_id, ts}.
    invocation_log: list[dict] = field(default_factory=list)

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

    def latency_percentiles(self) -> dict[str, float]:
        """Return p50/p90/p95/p99 latencies across all active agents (REQ-TUI-032)."""
        latencies = sorted(
            a.last_task_latency_ms
            for a in self.agents.values()
            if a.last_task_latency_ms > 0
        )
        if not latencies:
            return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}

        def _pct(p: float) -> float:
            idx = max(0, int(len(latencies) * p) - 1)
            return latencies[idx]

        return {
            "p50": _pct(0.50),
            "p90": _pct(0.90),
            "p95": _pct(0.95),
            "p99": _pct(0.99),
        }

    @property
    def blocked_task_count(self) -> int:
        return sum(a.cat_b_trigger_count for a in self.agents.values())

    # --- FIFO helpers (enforce caps without duplicating logic in client.py) ---

    def append_knowledge(self, entry: dict) -> None:
        """Append an entry to knowledge_feed, capping at _MAX_KNOWLEDGE_FEED (FIFO)."""
        self.knowledge_feed.append(entry)
        if len(self.knowledge_feed) > _MAX_KNOWLEDGE_FEED:
            self.knowledge_feed = self.knowledge_feed[-_MAX_KNOWLEDGE_FEED:]

    def append_episode_nominee(self, entry: dict) -> None:
        """Append to episode_nominees, capping at _MAX_EPISODE_NOMINEES (FIFO)."""
        self.episode_nominees.append(entry)
        if len(self.episode_nominees) > _MAX_EPISODE_NOMINEES:
            self.episode_nominees = self.episode_nominees[-_MAX_EPISODE_NOMINEES:]

    def append_owasp_violation(self, entry: dict) -> None:
        """Append to owasp_violation_log, capping at _MAX_OWASP_LOG (FIFO)."""
        self.owasp_violation_log.append(entry)
        if len(self.owasp_violation_log) > _MAX_OWASP_LOG:
            self.owasp_violation_log = self.owasp_violation_log[-_MAX_OWASP_LOG:]

    def append_signal_log(self, entry: dict) -> None:
        """Append to signal_flow_log, capping at 30 (FIFO)."""
        self.signal_flow_log.append(entry)
        if len(self.signal_flow_log) > 30:
            self.signal_flow_log = self.signal_flow_log[-30:]

    # ------------------------------------------------------------------
    # PR-telemetry — capability invocation aggregates
    # ------------------------------------------------------------------

    def record_invocation(
        self,
        invocation: dict,
        *,
        agent_id: str = "",
        task_id: str = "",
        ts: float | None = None,
    ) -> None:
        """Fold one entry from ``TASK_COMPLETE.invocations`` into the running stats.

        Looks up (or creates) the matching :class:`CapabilityInvocationStats`,
        bumps the totals, and pushes a copy of the invocation onto
        ``invocation_log`` (FIFO-capped at ``_MAX_INVOCATION_LOG``).

        Args:
            invocation: Dict with keys ``kind``, ``target``, ``ok``,
                ``error`` — the wire shape PR-B produces.  Entries with
                missing/invalid ``kind`` or ``target`` are dropped (we
                won't synthesise data the agent didn't send).
            agent_id: Sender id, taken from the enclosing TASK_COMPLETE
                payload.  Stored on the log entry so the panel can
                render "which agent fired this".
            task_id: Original task id (also from TASK_COMPLETE) — useful
                for cross-screen correlation with the Comms log.
            ts: Override timestamp.  Defaults to ``time.time()``.
        """
        kind = str(invocation.get("kind", "")).strip()
        target = str(invocation.get("target", "")).strip()
        if kind not in ("skill", "mcp") or not target:
            return  # malformed — drop silently, the agent's own logs cover it

        ok = bool(invocation.get("ok", False))
        error = str(invocation.get("error", "") or "")
        when = ts if ts is not None else time.time()

        key = f"{kind}:{target}"
        stats = self.capability_stats.get(key)
        if stats is None:
            stats = CapabilityInvocationStats(kind=kind, target=target)
            self.capability_stats[key] = stats
        stats.total += 1
        if ok:
            stats.ok += 1
        elif error:
            stats.last_error = error
        stats.last_seen_ts = when

        # Tail log for the panel's "recent failures" view.
        self.invocation_log.append({
            "kind": kind,
            "target": target,
            "ok": ok,
            "error": error,
            "agent_id": agent_id,
            "task_id": task_id,
            "ts": when,
        })
        if len(self.invocation_log) > _MAX_INVOCATION_LOG:
            self.invocation_log = self.invocation_log[-_MAX_INVOCATION_LOG:]
