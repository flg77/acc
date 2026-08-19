# Proposal: Editor integration for the coding role

**Change ID:** 20260817-editor-integration
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC has a coding role, and the place coding work happens is an editor. Today
that role is reachable only through the terminal interface, the web interface, or
a chat channel — none of which is where the files are.

This is deliberately filed as a question about product intent rather than a
straightforward gap. If the coding role is meant to be genuinely used, the editor
is its natural surface and its absence is a real limitation. If it is primarily a
demonstration that the collective can hold such a role, then building an editor
integration spends significant effort on the wrong thing.

The spec therefore starts by asking that, and only then describes the work.

## Current Behavior

No editor surface. The coding role is invoked like any other role.

## Desired Behavior

An editor can drive the coding role over a standard agent-editor protocol,
with the editor's workspace bound to a trusted workspace and edits subject to the
same authorisation and reversibility as any other agent write.

Two ACC-specific constraints shape this. The workspace-trust boundary applies —
an editor session must not become a way to write outside it. And an editor
expects synchronous, incremental responses, which sits badly with an oversight
queue; the same design question as the standard endpoint applies here and should
be answered the same way.

## Success Criteria

- The coding role can be invoked from an editor and can read and modify files
  within a trusted workspace.
- Writes go through the same authorisation, recording and (once available)
  checkpoint path as any other agent write.
- A gated action behaves as documented rather than hanging the editor.
- The integration is optional and absent by default.

## Scope

**In scope**

- A protocol server exposing the coding role to editors.
- Workspace binding that respects the trust boundary.
- Documented behaviour for gated actions.

**Out of scope**

- Editor-specific plugins; the protocol is the surface.
- Exposing every role to editors.
- Bypassing workspace trust or oversight.

## Implementation options

**A. Implement the agent-editor protocol directly.** Native fit for editors that
already speak it; a protocol to track.

**B. Reuse the standard chat endpoint** and let editors treat ACC as a model
provider. Much less work, much less capable — no file operations, no workspace
awareness.

**C. Defer until the coding role's product position is settled.** The honest
option if the answer to the open question below is "demonstration".

Sequence B after the endpoint change lands, and treat A as conditional on intent.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. **Is the coding role a first-class product surface or a demonstration?**
   This change is only justified by the former, and answering it may close the
   item.
2. How does an editor session interact with the oversight queue? An editor cannot
   usefully block for a human approval that arrives minutes later.
3. Does an editor session get its own workspace, or bind to an existing one?

## Assumptions

- The trusted-workspace boundary applies unchanged to editor-driven writes.
- Editor integration is optional and off by default.
