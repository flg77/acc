"""Learn from violations → Cat-C rule proposals (PR-Z3c).

Closes the adaptive-governance loop: when the same kind of violation
recurs (the OWASP violation log + rejected oversight items), cluster it
and, once a cluster crosses the ``pattern_min_cluster`` Cat-B setpoint,
emit a Cat-C :class:`acc.rule_proposals.RuleProposal` so a learned
defence can be reviewed (or auto-activated) and fed to the arbiter's
signed bundle pipeline.

This mirrors the existing EPISODE_NOMINATE → ICL consolidation idea but
runs over the *violation* signal so the system strengthens itself in
response to what actually went wrong.  Pure + testable; the proposal
store + promotion mode live in :mod:`acc.rule_proposals`.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ViolationCluster:
    code: str
    pattern: str
    count: int
    agent_ids: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.code}:{self.pattern}"


def cluster_violations(violations: list[dict]) -> list[ViolationCluster]:
    """Group violation-log entries by (code, pattern).

    Each entry mirrors the ``owasp_violation_log`` shape:
    ``{ts, code, agent_id, risk_level, pattern}``.  Returns clusters
    sorted by descending count."""
    buckets: dict[tuple[str, str], ViolationCluster] = {}
    for v in violations:
        code = str(v.get("code") or "").strip()
        pattern = str(v.get("pattern") or "").strip()
        if not code:
            continue
        key = (code, pattern)
        cluster = buckets.get(key)
        if cluster is None:
            cluster = ViolationCluster(code=code, pattern=pattern, count=0)
            buckets[key] = cluster
        cluster.count += 1
        agent = str(v.get("agent_id") or "").strip()
        if agent and agent not in cluster.agent_ids:
            cluster.agent_ids.append(agent)
    return sorted(buckets.values(), key=lambda c: c.count, reverse=True)


def _min_cluster_setpoint() -> int:
    """Read ``pattern_min_cluster`` from the Cat-B setpoints (default 5)."""
    env = os.environ.get("ACC_PATTERN_MIN_CLUSTER", "").strip()
    if env.isdigit():
        return max(1, int(env))
    try:
        from acc.governance_inventory import regulatory_root  # noqa: PLC0415
        data = json.loads(
            (regulatory_root() / "category_b" / "data_rhoai.json").read_text(
                encoding="utf-8",
            )
        )
        return int(data.get("setpoints", {}).get("pattern_min_cluster", 5))
    except Exception:
        return 5


def _rule_text(cluster: ViolationCluster) -> str:
    agents = ", ".join(cluster.agent_ids[:5]) or "(various)"
    return (
        f"# Proposed Cat-C learned rule from {cluster.count} repeated "
        f"{cluster.code} violations\n"
        f"# Pattern: {cluster.pattern or '(unspecified)'}\n"
        f"# Observed on agents: {agents}\n"
        f"# Intent: detect + mitigate this recurring pattern to protect "
        f"Cat-A integrity.\n"
        f"# TODO: encode as an OPA guard / setpoint and route through the "
        f"signed RULE_UPDATE path."
    )


def propose_from_violations(
    violations: list[dict],
    *,
    min_cluster: Optional[int] = None,
    root: Optional[Path] = None,
) -> list:
    """Cluster *violations* and create a Cat-C proposal per cluster that
    meets the ``pattern_min_cluster`` threshold.

    Honours :func:`acc.rule_proposals.promotion_mode` — ``auto``
    auto-approves into the overlay, ``propose`` leaves them PENDING.
    Returns the created RuleProposals."""
    from acc.rule_proposals import create_proposal, promotion_mode  # noqa: PLC0415

    threshold = min_cluster if min_cluster is not None else _min_cluster_setpoint()
    auto = promotion_mode() == "auto"
    created = []
    for cluster in cluster_violations(violations):
        if cluster.count < threshold:
            continue
        created.append(create_proposal(
            source="violation",
            category="C",
            rule_text=_rule_text(cluster),
            rationale=(
                f"Cluster of {cluster.count} {cluster.code} violations "
                f"(>= threshold {threshold}) sharing pattern "
                f"'{cluster.pattern or '(unspecified)'}' on "
                f"{len(cluster.agent_ids)} agent(s)."
            ),
            severity="HIGH" if cluster.count >= threshold * 2 else "MEDIUM",
            confidence=min(1.0, cluster.count / (threshold * 2)),
            refs=[cluster.key],
            root=root,
            auto_approve=auto,
        ))
    return created
