# 20261003-assistant-orchestrated-infusion — proposal

Experiment, scaffolded 2026-10-03 from a live failure on the workstation stack
(session `20261003-212631-1`). Fleet thread 24. Design: `design.md`.

- **Date:** 2026-10-03
- **Branch:** `feat/assistant-orchestrated-infusion` (acc-spearhead)
- **Owner:** flg / Claude Code session (workstation)

## Why

The operator asked the assistant to *"check the status of our security settings on
the local server and create a full report."* The assistant chose the right
specialist (`devops_engineer`) and said so, and nothing else happened:

1. `PROPOSE_SPAWN:devops_engineer` was auto-approved and dispatched. The arbiter had
   no `ACC_ARBITER_SIGNING_KEY` and returned without a word. It would not have
   helped if it had one: a spawn only promotes an **already running dormant
   worker**, and the stack had none. Nothing in ACC can start a container on
   request.
2. The hand-off only happened after the operator typed "confirmed". The assistant
   cannot wait for a role to come up and then hand over, so a spawn and its hand-off
   are two turns, and the operator has to be the clock.
3. The route went to a role no agent held. Even when a specialist does run, **its
   result never returns to the assistant**: the route carries a `handover_id`, and
   nothing reads it. The assistant's own prompt promises "when the specialist
   replies, summarise", and no code path delivers that reply.
4. A promoted worker's memory lives at `/app/data/lancedb/worker-N`: it belongs to
   the container, not the role. Stop a worker and re-infuse the role elsewhere, and
   the role starts with no memory.

The expected workflow, in the operator's words: *the infusion and the task handover
from the assistant are created automatically. All infused roles keep their own
memories and provide notes to the assistant, who then judges and refines the
results when necessary. This workflow is confirmed from the console where
necessary.* The operator also decided that **the assistant has the power to start,
stop and pause containers to infuse roles.**

## Hypothesis

Giving the assistant governed container lifecycle authority (start / stop / pause /
resume / scale the specialist pool, through a broker that enforces an allow-list
and the operating mode), a **spawn-then-hand-over** step that waits for the role to
report ACTIVE, and a **review turn** that brings the specialist's result back to
the assistant will make a prompt that needs a not-yet-running specialist end in a
reviewed answer **without an operator re-prompt**: from 0 of 5 golden prompts today
to 5 of 5. Governance tests stay green, and in `ASK_PERMISSIONS` every lifecycle
action still waits for a console decision.

## Baseline (before)

Measured on the workstation stack, `0.26.3-3-g69146ff`, assistant on
`claude-sonnet-4-6`, AUTO mode. Trace: `logs/sessions/20261003-212631-1.jsonl`
(tasks `de9436b8`, `77664624`); arbiter log 21:55:30.

```
prompt: "check the status of our security settings on the local server and create a full report."
specialist chosen correctly        yes (devops_engineer)
container/worker started           no   (no signing key; no dormant pool; no start path)
operator re-prompts needed         1    ("confirmed" before the route)
hand-off delivered to an agent     no   (no agent held the role)
specialist result back to assistant n/a (no consumer of handover_id exists)
failure visible to operator        no   (arbiter returned silently)
end-to-end: reviewed answer        0/1
```

The golden set for the after-measurement (`tasks.md` §0) is five prompts that each
need a specialist the base stack does not run. Baseline for all five is expected to
be 0/5 for the same structural reasons. Capture it before any implementation lands.

The reasoning-trace depth score (`acc-reasoning-trace`) is **not** this experiment's
metric: the failure is in orchestration, not deliberation. It is recorded once for
the assistant's routing turn so a reasoning regression would still show.

## Change

See `design.md`. In one line each:

- **Lifecycle authority.** New proposal kind `PROPOSE_LIFECYCLE:<action>:<role>:<reason>`
  (`start | stop | pause | resume | scale`), executed by a **lifecycle broker**: a
  host-side daemon on podman (the existing lifecycle-watcher, generalised) and the
  ACC operator on a cluster. The broker acts only on signed requests, only on
  specialist workers, and never on the control plane.
- **Spawn, then hand over.** `PROPOSE_SPAWN` takes an optional hand-off brief. The
  assistant parks the brief and dispatches the route when the role reports ACTIVE,
  or reports why it cannot.
- **Review turn.** A specialist's TASK_COMPLETE carrying a `handover_id` becomes a
  follow-up task for the assistant: accept, refine (send back with a critique,
  bounded rounds), or escalate to the console.
- **Role-keyed memory.** A promoted worker opens the role's memory, not the
  container's, so a role keeps its memory across stop, start and re-infusion.

Already shipped toward this, separately: `fix/reconcile-result-says-why` (the
arbiter reports a spawn it could not do, and why).

## Result (after)

_To fill in: same five golden prompts, same model, same stack._

## Outcome

- [ ] kept   - [ ] reverted   - [ ] needs iteration

If kept and vetted, promote via `acc-promote`.
