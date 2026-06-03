# OpenSpec — folder rename log

## Convention (effective 2026-06-04)

Two proposal shapes, distinguished by infix:

| Shape | Pattern | When to use |
|---|---|---|
| **Role-specific** | `YYYYMMDD-role-proposal-<role-or-focus>` | The proposal exists to design or modify ONE specific named role (e.g. assistant, orchestrator, dreamer, equity_analyst). |
| **Functional** | `YYYYMMDD-<topic>` (status quo) | The proposal designs a mechanism, surface, or platform capability that touches many roles or is structural (e.g. perception profiles, capability pool, MLflow telemetry). |

The infix lets the operator scan `openspec/changes/` and immediately
see which proposals are role-centric vs. platform-centric.

## Rename map (2026-06-04 batch)

Six proposals renamed for retroactive consistency:

| Old | New |
|---|---|
| `20260526-multiagent-reasoning-orchestrator` | `20260526-role-proposal-orchestrator-multiagent-reasoning` |
| `20260530-acc-dreaming-agent` | `20260530-role-proposal-dreamer-agent` |
| `20260530-assistant-agent-of-agents` | `20260530-role-proposal-assistant-agent-of-agents` |
| `20260531-assistant-action-loop` | `20260531-role-proposal-assistant-action-loop` |
| `20260531-orchestrator-repurpose-skills-mcp-specialist` | `20260531-role-proposal-orchestrator-skills-mcp-specialist` |
| `20260602-assistant-blindspots` | `20260602-role-proposal-assistant-blindspots` |

Cross-references in 54 files were updated in the same commit; tests
green post-rename. Git history is preserved via `git mv`.

## What stayed (functional)

All other proposals remain at their original names. Examples:

* `20260527-mlflow-otel-telemetry` — telemetry pipeline (platform)
* `20260530-acc-self-improvement-policy-gradient` — SIP substrate
* `20260531-role-perception-profiles` — perception substrate (touches
  every role but proposes a **mechanism**, not a role)
* `20260603-capability-pool` — skill+MCP pool

The word "role" appearing in a *functional* proposal name does not
make it role-specific. The test is whether the proposal exists to
design **one** role's behaviour.

## When in doubt

Prefer role-specific when:
* The proposal title would naturally start with a role name.
* Implementation lands almost entirely in `roles/<name>/role.yaml`
  (+ a small handler in `acc/`).
* You can imagine sibling proposals for *other* roles by the same
  shape.

Prefer functional when:
* The proposal lands a new module in `acc/`, a new model field, or
  a new TUI/webgui surface.
* It would compose with multiple role.yaml files.
* Removing the proposal would break a shared mechanism, not one
  role's behaviour.
