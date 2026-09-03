"""Proposal outcomes as thread lines (pure).

`20260902-assistant-autonomy-prompt-pane-approvals` 1.5.  The agents publish
what became of a proposal on ``acc.<cid>.assistant_proposal`` (stamped
``ASSISTANT_PROPOSAL_OUTCOME``): an infuse installed, a dispatch refused, a
reconcile that assigned a worker — or could not.  Until now those were log
lines in a container; the Prompt pane renders them as ``system`` lines in
the thread, and this module decides the words.
"""

from __future__ import annotations

WORKER_POOL_HINT = (
    "no dormant worker — raise `worker_pool` in collective.yaml or run "
    "`./acc-deploy.sh apply worker-pool`"
)


def outcome_key(outcome: dict) -> tuple:
    """Identity for "render once": trigger + proposal + timestamp."""
    return (
        str(outcome.get("trigger") or ""),
        str(outcome.get("proposal_id") or outcome.get("oversight_id") or ""),
        str(outcome.get("ts") or ""),
    )


def outcome_lines(outcome: dict) -> list[str]:
    """Transcript lines for one outcome payload; ``[]`` for one we don't
    narrate (unknown trigger, or a reconcile that had nothing to say)."""
    trigger = str(outcome.get("trigger") or "")
    if trigger == "infuse_completed":
        name = str(outcome.get("name") or "")
        version = str(outcome.get("version") or "")
        ref = f"{name}@{version}" if version else name
        suffix = " (already installed)" if outcome.get("was_already_installed") else ""
        return [f"✓ installed {ref}{suffix}"]
    if trigger == "proposal_dispatch_failed":
        what = str(outcome.get("spec") or outcome.get("kind") or "proposal")
        oid = str(outcome.get("oversight_id") or "")
        where = f" ({oid[:12]})" if oid and not outcome.get("spec") else ""
        return [f"✗ {what}{where}: {outcome.get('reason', '') or 'dispatch failed'}"]
    if trigger == "reconcile_result":
        lines: list[str] = []
        for a in outcome.get("assigned") or []:
            if isinstance(a, dict):
                lines.append(
                    f"✓ spawned {a.get('role', '')} → {a.get('target_agent_id', '')}"
                )
        for role in outcome.get("unmet") or []:
            lines.append(f"✗ spawn {role}: {WORKER_POOL_HINT}")
        if not lines and outcome.get("role"):
            lines.append(
                f"· {outcome['role']}: already active "
                f"({int(outcome.get('already_active') or 0)} running)"
            )
        return lines
    return []
