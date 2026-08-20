# Proposal: Headless collective status

**Change ID:** 20260817-collective-status-command
**Date:** 2026-08-17
**Status:** Implemented
**Author:** flg

---

## Problem Statement

The health of a running collective is only observable interactively. The TUI
dashboard renders it well, but an edge node is normally reached over SSH without
a TTY, and monitoring cannot drive a Textual app.

The practical result is that answering "is this deployment healthy?" currently
means either launching a TUI or running several unrelated probes by hand —
container state from podman, resolved model per agent by executing into each
container, endpoint reachability with curl, and key presence by grepping the
environment. That sequence has been performed repeatedly during live debugging and
is slow, error-prone and unscriptable.

## Current Behavior

`acc-cli llm` smoke-tests one configured backend. The TUI dashboard shows
live collective state. There is no single, headless, scriptable command that
reports the state of every agent at once, and no exit code a monitor could use.

## Desired Behavior

One command, safe to run on any cadence, that reports per-agent state and
exits non-zero when the collective is not healthy:

    acc-cli status [--json] [--role <role>]

Per agent it should report: role, running/absent, resolved backend and model, key
presence **by name**, and last-seen heartbeat. Collective-wide it should report
the signalling bus, working memory, and the oversight queue depth.

Two details matter and are easy to get wrong. The resolved model must be read
from the **backend-appropriate variable** — an `anthropic` binding sets a
different variable from an `openai_compat` one, and reading the wrong one makes a
correct mapping look broken. And a role that is mapped but not deployed must be
reported distinctly from a role that is deployed and unhealthy.

## Success Criteria

- On a healthy six-agent collective, one command shows all six with their
  resolved models and exits 0.
- On a collective with one agent down, the output names it and the exit code is
  non-zero.
- A role mapped in configuration but with no running agent is reported as
  *not deployed*, not as *failed*.
- Works over SSH with no TTY, and inside a container.

## Scope

**In scope**

- Per-agent and collective-wide state, read-only.
- `--json` output; documented exit-code semantics.
- Correct per-backend resolution of the running model.

**Out of scope**

- Configuration validation — that is the preflight change; `status` reports what
  *is*, preflight reports what is *wrong*.
- Historical or trend data.
- Any mutation, including restarts.

## Implementation options

**A. Query the signalling bus.** Agents already emit heartbeats; subscribing
briefly gives live state without container access, and works identically for
podman and Kubernetes deployments. Requires the bus to be reachable.

**B. Inspect containers.** Direct and dependency-free on an edge box, but
podman-specific and useless on OpenShift.

**C. Both, with a documented preference.** Bus first, container inspection as a
fallback when the bus is unreachable — which is itself a useful signal.

C is the recommendation precisely because "the bus is down" is one of the answers
the command exists to give.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Exit-code semantics: non-zero on any degraded agent, or only when the
   collective as a whole is unusable? Monitoring wants the former, humans the
   latter — possibly `--strict` selects.
2. Does `status` require the deployment host, or should it work remotely against
   a reachable bus? The latter is more useful and harder.
3. How is "last seen" defined when an agent is idle? Heartbeat interval must not
   be confused with liveness.

## Assumptions

- Agents continue to emit heartbeats on the bus.
- Reporting key presence by name only, never values, is a hard rule.
