# Proposal: Standard-protocol chat endpoint in front of a governed collective

**Change ID:** 20260817-openai-compatible-endpoint
**Date:** 2026-08-17
**Status:** Implemented (handler; HTTP route + dispatcher wiring outstanding)
**Author:** flg

---

## Problem Statement

Everything that wants to talk to ACC must learn ACC's own API. The web
interface exposes ACC-shaped routes — prompt, infuse, oversight, governance — and
nothing speaks the widely-implemented chat-completions shape that most tooling,
editors and internal portals already emit.

That is a reach problem with an unusually good answer available: a standard
endpoint in front of a governed collective means existing clients get governance,
budgets and an audit trail without changing a line. The pitch is precise — the
same call shape, but every request is evaluated, budgeted and recorded.

It also has a genuine design problem that must be solved rather than glossed: a
chat-completions client expects a synchronous answer, and a governed collective
may need to pause for human approval. Pretending otherwise produces an endpoint
that hangs or lies.

## Current Behavior

ACC-specific REST and WebSocket routes only. No standard-protocol surface.

## Desired Behavior

An endpoint that accepts the standard chat-completions request shape, routes
the work into a collective, and returns a response — with an explicit, documented
behaviour when the work cannot complete synchronously.

The gating case is the design. Options are: refuse work that would require
approval, return a pending handle the client can poll, or block until the
oversight timeout. Each implies a different client, and the choice should be
made deliberately and stated in the endpoint's documentation rather than
discovered at runtime.

Authentication and attribution matter more here than on the internal routes: a
standard endpoint is the surface most likely to be pointed at by something the
operator did not write.

## Success Criteria

- An unmodified standard client can send a request and receive a valid response.
- Work that triggers a gated action behaves as documented — never silently
  dropped, never indefinitely hung.
- Every request is attributed, budgeted and recorded like any other task.
- Streaming either works or is explicitly unsupported with a clear error.

## Scope

**In scope**

- The request/response shape, model naming (a collective or role presented as a
  model), authentication and attribution.
- A documented, deliberate behaviour for gated work.
- Budget and audit parity with internal task paths.

**Out of scope**

- Emulating provider-specific extensions beyond the core shape.
- Acting as a proxy to upstream providers.
- Bypassing oversight, budgets or Category evaluation under any flag.

## Implementation options

**A. Refuse gated work.** The endpoint serves only tasks that can complete
without approval; anything else returns a structured error naming the reason.
Honest, simple, and limits the endpoint to a subset of what ACC can do.

**B. Pending handle.** Return an identifier immediately; the client polls or
subscribes. Fully expressive, and no standard client speaks it — so it works only
with clients written for ACC, which undercuts the reason for the endpoint.

**C. Block until the oversight timeout**, then fail. Works with unmodified
clients for the common case and behaves badly for long approvals.

A is the recommendation for v1, with the error carrying enough detail that a
purpose-built client could then follow up through the native API.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. What does the endpoint do when a task needs approval? This is the whole
   design and should be settled before implementation.
2. What is a "model" in the request — a role, a collective, or a profile? Each
   reading is defensible and they are not interchangeable.
3. Is streaming in scope? Streaming a response whose governance verdict is not
   yet final is a question in itself.

## Assumptions

- Requests through this endpoint are subject to the same Category evaluation,
  budgets and recording as any other task.
- Authentication is required; this is not an unauthenticated surface.
