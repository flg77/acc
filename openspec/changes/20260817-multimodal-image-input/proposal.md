# Proposal: Image input for prompts and evidence

**Change ID:** 20260817-multimodal-image-input
**Date:** 2026-08-17
**Status:** Draft
**Author:** flg

---

## Problem Statement

ACC is text-only. There is no path for an image to reach an agent from any
surface, even though several models already in use are multimodal.

Two concrete cases motivate it. An operator diagnosing a problem usually has a
screenshot and must instead transcribe what it shows — during recent debugging
sessions, screenshots were exactly how problems were reported, and the content had
to be retyped. And a compliance or review role asked to assess a dashboard, a
chart or a rendered document currently cannot see the artifact it is judging.

This is a modest capability with a clear boundary, which is why it is worth
specifying narrowly rather than as "multimodal support".

## Current Behavior

Backends send text content only. Neither the terminal interface nor the web
interface accepts an image, and the durable record has no representation for one.

## Desired Behavior

An image may be attached to a prompt and reaches a multimodal backend as image
content, with a defined behaviour when the backend cannot accept it.

The web interface is the natural first surface — a terminal cannot display an
image meaningfully, so attaching one there is possible but reviewing it is not.

The question this change must answer, and the reason it is not trivial, is **what
enters the audit record**. An image is large, may contain sensitive material, and
the durable record is retained. Storing full images inflates the record; storing
only a hash makes the record unverifiable against the image later.

## Success Criteria

- An image attached in the web interface reaches a multimodal backend and
  influences the response.
- A backend that cannot accept images fails with a clear message rather than
  silently dropping the attachment.
- The audit record's treatment of image content is explicit and documented.
- Size limits are enforced before dispatch.

## Scope

**In scope**

- Attachment on the web interface, transport to the backend, and multimodal
  request formation for backends that support it.
- Size limits and explicit failure on unsupported backends.
- A decided, documented position on what the durable record holds.

**Out of scope**

- Image generation.
- Terminal-interface image display.
- Video or audio input; the voice path is separate and already exists.

## Implementation options

**A. Store the image in the record.** Complete and verifiable; inflates a
retained record and puts potentially sensitive pixels in the audit trail.

**B. Store a hash and a reference.** Compact, and the record can prove *which*
image was used only if the image is still retrievable elsewhere.

**C. Store a hash plus a thumbnail.** A compromise that keeps the record legible
without carrying full resolution.

The choice is a retention and sensitivity question rather than a technical one,
and should be decided with whatever answer the session-retention work reaches.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. What does the durable record hold — full image, hash, or hash plus
   thumbnail? Ties directly to the session-retention decision.
2. Should image input be role-restricted? Not every role has a reason to receive
   pixels, and restricting reduces the surface.
3. What is the size limit, and is it per image or per task?

## Assumptions

- At least one configured backend accepts image content.
- The web interface is the first and possibly only surface in v1.
