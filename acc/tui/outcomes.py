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
#: Why a parked hand-off was not sent (acc.handover.DROP_*).
HANDOVER_DROP_TEXT: dict[str, str] = {
    "deadline": "the role did not come up in time",
    "no_signing_key": "the arbiter has no signing key (ACC_ARBITER_SIGNING_KEY)",
    "scale_refused": "no worker could be started",
}
#: Operator-facing words for each lifecycle refusal (acc.lifecycle.REASON_*).
LIFECYCLE_REASON_TEXT: dict[str, str] = {
    "no_signing_key": "the arbiter has no signing key (ACC_ARBITER_SIGNING_KEY)",
    "unknown_role": "no such role is installed",
    "unknown_action": "not a lifecycle action (scale, start, stop, pause, resume)",
    "bad_signature": "the broker could not verify the arbiter's signature",
    "stale_request": "the request was too old when it arrived",
    "replayed_request": "the request had already been handled",
    "wrong_collective": "the request was for another collective",
    "rate_limited": "too many lifecycle actions in a short time; try again shortly",
    "pool_exhausted": "every pool worker is already running — add workers to the pool",
    "no_worker_holds_role": "no pool worker holds that role",
    "worker_busy": "the worker is in the middle of a task",
    "execution_failed": "the container runtime or the cluster refused the operation",
    "protected_role": "that role is part of the control plane and is never stopped or scaled",
    "role_not_declared": "the role is not in the AgentCollective; adding a role is an operator edit",
    "autoscaled": "the collective is autoscaled (KEDA owns the replica counts)",
}
NO_SIGNING_KEY_HINT = (
    "the arbiter has no signing key, so it cannot assign a worker — set "
    "ACC_ARBITER_SIGNING_KEY (the pair of ACC_ARBITER_VERIFY_KEY) for the "
    "arbiter and restart it"
)
WORKER_POOL_HINT_CLUSTER = (
    "no dormant worker — add an agent of this role (or a worker pool) to the "
    "AgentCollective; the ACC operator starts it"
)


def worker_pool_hint() -> str:
    from acc.deploy import environment  # noqa: PLC0415

    return WORKER_POOL_HINT_CLUSTER if environment().cluster else WORKER_POOL_HINT


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
    if trigger.startswith("review_"):
        role = str(outcome.get("role") or "")
        if trigger == "review_accepted":
            return [f"✓ reviewed {role}'s answer — accepted"]
        if trigger == "review_refine":
            how = " (asked in the console)" if outcome.get("dispatch") == "queue" else ""
            return [f"↻ sent {role}'s answer back for round {outcome.get('next_round', '?')}{how}: "
                    f"{str(outcome.get('critique') or '')[:160]}"]
        if trigger == "review_escalated":
            return [f"⚑ review of {role}'s answer needs your decision: {str(outcome.get('reason') or '')[:200]}"]
        return []
    if trigger.startswith("handover_"):
        role = str(outcome.get("role") or "")
        if trigger == "handover_parked":
            return [f"· hand-off to {role} waits until {role} is up"]
        if trigger == "handover_released":
            how = "asked in the console" if outcome.get("dispatch") == "queue" else "sent"
            return [f"✓ hand-off to {role} {how} — {role} is up"]
        if trigger == "handover_waiting":
            return [f"· no free worker for {role}; starting one (PROPOSE_LIFECYCLE:scale)"]
        if trigger == "handover_dropped":
            reason = str(outcome.get("reason") or "")
            return [f"✗ hand-off to {role} not sent: {HANDOVER_DROP_TEXT.get(reason.split(':')[0], reason)}"
                    + (f" ({reason.split(':', 1)[1]})" if ":" in reason and reason.split(":", 1)[1] else "")]
        return []
    if trigger == "lifecycle_result":
        action = str(outcome.get("action") or "lifecycle")
        role = str(outcome.get("role") or "")
        if outcome.get("ok"):
            done = ", ".join(
                f"{o.get('op', '')} {o.get('container', '')}"
                for o in (outcome.get("ops") or []) if isinstance(o, dict)
            ) or "nothing to do"
            line = f"✓ {action} {role}: {done}"
            busy = outcome.get("skipped_busy") or []
            if busy:
                line += f" (left busy: {', '.join(busy)})"
            return [line]
        why = LIFECYCLE_REASON_TEXT.get(str(outcome.get("reason") or ""), "")
        detail = str(outcome.get("detail") or "")
        return [f"✗ {action} {role}: {why or outcome.get('reason') or 'refused'}"
                + (f" ({detail})" if detail and not why else "")]
    if trigger == "reconcile_result":
        lines: list[str] = []
        for a in outcome.get("assigned") or []:
            if isinstance(a, dict):
                lines.append(
                    f"✓ spawned {a.get('role', '')} → {a.get('target_agent_id', '')}"
                )
        from acc.worker_reconcile import REASON_NO_SIGNING_KEY  # noqa: PLC0415
        hint = (
            NO_SIGNING_KEY_HINT
            if outcome.get("reason") == REASON_NO_SIGNING_KEY
            else worker_pool_hint()
        )
        for role in outcome.get("unmet") or []:
            lines.append(f"✗ spawn {role}: {hint}")
        if not lines and outcome.get("role"):
            lines.append(
                f"· {outcome['role']}: already active "
                f"({int(outcome.get('already_active') or 0)} running)"
            )
        return lines
    return []
