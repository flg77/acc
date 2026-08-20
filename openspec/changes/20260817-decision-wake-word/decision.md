# Decision: Always-listening wake word

**Change ID:** 20260817-decision-wake-word
**Date:** 2026-08-17
**Status:** **Declined** — recorded, not scheduled
**Author:** flg

---

## What was considered

A hands-free trigger phrase that keeps a microphone open and activates the
assistant when it hears its name, across the terminal interface and any desktop
surface.

## Decision

**Declined.** ACC will not implement an always-listening wake word. The existing voice
channel remains push-to-talk.

## Reasoning

The voice channel exists so an operator with occupied hands can work. That
need is met by push-to-talk. A wake word does not add capability; it changes the
microphone from *activated* to *always open*, and that is a data-handling change
rather than an ergonomic one.

For a runtime whose positioning is governed and auditable, an always-open
microphone raises questions — what is captured, where it goes, how consent is
recorded for anyone else in the room, what lands in a retained record — that cost
more to answer properly than the feature returns. Push-to-talk is the defensible
posture and it is already implemented.

## What we do instead

Keep push-to-talk. If activation friction is the real complaint, address it
with a better trigger (a hardware key, a foot switch, a shorter activation path)
rather than by leaving the microphone open.

## Conditions for reopening

Reopen only with a named operator whose work is genuinely blocked by
push-to-talk. Any reopened design must specify local-only wake detection with no
audio leaving the device, and must state what enters the durable record.

> This is a decision record, not a proposal. It exists so the absence is
> deliberate and traceable rather than an oversight, and so the same idea does
> not get re-raised without new information.
