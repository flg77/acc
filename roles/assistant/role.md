# ACC Assistant

The **ACC Assistant** is your guide to the Agentic Cell Corpus. It connects you
to the system from day 0 — onboarding, explaining the interface, and helping you
compose and spawn agentsets that succeed.

> **It is a guide, not a worker.** In v1 (the concierge phase) the Assistant
> answers questions and points you at the right surface. It holds no skills, no
> MCP servers, and no workspace access — and it is bound by the same Cat-A/B/C
> governance as every other agent.

## What it helps with

- **Onboarding** — what each of the 9 TUI screens is for, the keyboard
  shortcuts, the CLI, and command mode.
- **Agentsets** — how to compose a dedicated cell in `collective.yaml`, spawn it
  with `./acc-deploy.sh apply`, swap teams, and pick per-agent models. See
  [`docs/howto-agentsets.md`](../../docs/howto-agentsets.md).
- **Roles** — where roles live and how to author a new one
  ([`docs/role-authoring.md`](../../docs/role-authoring.md)).
- **Governance** — when an action needs human oversight, and (when it hits a
  permission wall) *naming the exact setting* you'd need to change.

With `reasoning_trace` on, the Assistant shows **how** it reached an answer
(what it knows → options → recommendation) in the Prompt screen's reasoning
stream.

## Scenario-optional footprint

The Assistant is **not** part of the edge/baseline agentset — edge and factory
deployments stay minimal. Activate it on demand for maintenance:

```bash
./acc-deploy.sh apply assistant   # spin it up
# … use it via the Prompt screen (target role: assistant) or Slack …
./acc-deploy.sh apply collective.yaml             # swap it back out
```

Nothing in a collective depends on the Assistant being present; its only
persistent state is per-user memory in the shared store (a later phase).

## Roadmap (separately reviewed phases)

Acting within a sandbox (spawn sub-agents, apply changes via the existing signed
paths), per-user memory, and a gated local-research capability are planned but
**out of scope for v1** — each is governance-bound and gated behind its own
review. See the ACC Assistant OpenSpec note.
