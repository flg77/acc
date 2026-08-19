# Proposal: Brokered code execution that composes tools

**Change ID:** 20260817-brokered-code-execution
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

An agent can run code and can call tools, but cannot express a tool
composition *as* code. Every step of a repetitive operation costs a separate tool
call, and each call carries its own context and latency. A loop over fifty files
is fifty round trips.

The efficiency argument is straightforward. The governance argument runs the
other way and is the reason this needs specifying rather than building: a
discrete tool call with typed arguments is something Category A/B evaluation can
reason about; a program that calls tools is much harder to evaluate, because what
it will do is not visible in what was submitted.

Both arguments are real, which is why the shape of the answer matters more than
the feature.

## Current Behavior

Execution skills exist — shell, Python and SSH execution — and the
delegated-sandbox path runs code in a gateway-managed environment rather than in
the agent process. What none of them provide is a way for agent-authored code to
call ACC's own tools, so composition happens by issuing calls one at a time.

## Desired Behavior

Agent-authored code may call a **brokered** tool client: every call the code
makes goes through the same path, checks and logging as a directly issued tool
call, and is individually attributable.

Three properties make this acceptable rather than a hole:

* **No in-process execution.** Code runs in the existing delegated sandbox, never
  inside the agent.
* **The tool surface is injected, not imported.** The code receives a client
  restricted to the capabilities the role already holds; it cannot reach further
  than the agent could.
* **Every brokered call is logged individually.** The audit record must show the
  calls, not merely that a program ran.

If those cannot be met, the correct outcome is not to build this.

## Success Criteria

- Agent-authored code can call permitted tools and cannot call others.
- Every brokered call appears in the audit record as a distinct call.
- Code executes only in the sandbox; no path executes it in the agent process.
- A role's existing capability limits are enforced against the code's calls.
- Resource limits (wall clock, memory, call count) are enforced and reported.

## Scope

**In scope**

- A brokered tool client injected into sandboxed code.
- Capability enforcement matching the role's existing limits.
- Per-call audit records and resource limits.

**Out of scope**

- In-process execution of agent-authored code, under any flag.
- Widening what a role may do; this changes *how* calls are issued, never *which*.
- Long-running or background programs.

## Implementation options

**A. RPC back to the agent.** The sandbox calls out; the agent brokers each call
through its normal path. Strongest audit story, highest latency, and requires a
channel back from the sandbox.

**B. A pre-authorised token the sandbox uses against a broker service.** Removes
the agent from the hot path; introduces a credential and a service to protect.

**C. Restrict the composition to a declarative form** — a plan the agent submits
rather than arbitrary code — which is evaluable by construction but far less
expressive.

C deserves serious consideration precisely because it keeps Category evaluation
meaningful; it may satisfy the actual need (batch over many items) without the
governance cost of arbitrary code.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **How does Category A/B evaluation assess a program rather than an action?**
   This is the blocking question. If there is no good answer, option C is the
   design.
2. What are the resource limits, and what happens when a program exceeds them
   mid-composition — partial effects are the hard case.
3. Does a program that would trigger an oversight-gated action pause, fail, or
   pre-declare it? Pausing inside a sandboxed program is awkward.

## Assumptions

- The delegated sandbox is available and is the only execution surface.
- Role capability limits are enforceable at the broker.
