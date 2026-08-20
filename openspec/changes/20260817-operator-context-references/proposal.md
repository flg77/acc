# Proposal: Operator-supplied context references in a prompt

**Change ID:** 20260817-operator-context-references
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

An operator cannot point an agent at something. Context reaches an agent
through its role's seed context, through retrieval, or because the agent chose to
call a tool — never because the human said "look at this".

The practical effect is that the most natural instruction in an investigation —
"read this file and tell me what is wrong" — has no expression. The operator must
instead hope retrieval surfaces it, or ask the agent to go and find it, which is
slower, less reliable, and burns tool calls on something the human already knew.

There is a governance argument for this feature as well as an ergonomic one. An
operator-supplied reference is **explicit, attributable intent**, recorded in the
prompt. It is a cleaner provenance story than an agent deciding on its own to
read a path.

## Current Behavior

No mechanism. Prompts are plain text; there is no reference syntax, no
picker, and no injection path.

## Desired Behavior

A reference syntax in the prompt that resolves to content before the task is
dispatched:

    @<path>          a file
    @<path>/         a folder listing, or contents within a bound
    @diff            the working-tree diff of a bound repository
    @<url>           a fetched document

with completion in the TUI so references can be chosen rather than typed.

Two boundaries are not negotiable. A reference must be **refused** if it resolves
outside the trusted workspace — the write boundary and the read boundary should
not disagree. And resolved content enters the prompt as **data**, subject to the
same treatment as tool output; an instruction inside a referenced file is not an
instruction to the agent.

## Success Criteria

- A prompt containing `@<file>` reaches the agent with that file's content
  attached, attributed to the operator.
- A reference outside the trusted workspace is refused with a clear message, not
  silently read.
- Referenced content is recorded in the durable session record.
- Oversize references are bounded rather than silently truncated mid-file.
- The TUI offers completion for references.

## Scope

**In scope**

- Reference syntax, resolution, and size bounds.
- Enforcement of the trusted-workspace boundary on reads.
- Treating resolved content as untrusted data.
- TUI completion.

**Out of scope**

- Agent-initiated reads; those already exist as tools.
- Automatic inclusion of anything the operator did not name.
- Editing referenced files.

## Implementation options

**A. Resolve in the client (TUI/CLI) before publishing the task.** The agent
receives plain content and needs no new capability. Keeps the boundary check on
the operator's side, and makes the session record complete by construction.

**B. Resolve in the agent.** Smaller payloads on the bus and the agent can decide
what to read, at the cost of moving a trust decision into the component with the
least reason to be trusted with it.

**C. Resolve in the client, but pass a reference plus a content hash** so the
record proves what was read without duplicating large content on the bus.

A is the recommendation for v1; C is the better long-term shape if payload size
becomes a problem.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. What is the size bound, and what happens at it — refuse, truncate with a
   marker, or summarise? Silent truncation is the one unacceptable answer.
2. Does `@<url>` belong in v1? It crosses a network boundary and turns the
   operator's prompt into a fetch, which has different risk from a local read.
3. Should referenced content count against the role's token budget as ordinary
   input, or be accounted separately so operators can see what references cost?

## Assumptions

- The trusted-workspace boundary is available to the resolving client.
- The durable session record can hold, or reference, the resolved content.
