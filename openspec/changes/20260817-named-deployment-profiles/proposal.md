# Proposal: Named deployment profiles as a product capability

**Change ID:** 20260817-named-deployment-profiles
**Date:** 2026-08-17
**Status:** Implemented (signing question still open)
**Author:** flg

---

## Problem Statement

A host runs one configuration, and changing it means editing files in place.
There is no way to name a configuration, switch between named configurations, or
say what a deployment is currently running.

This has already been worked through in depth. A design specification exists
covering what a profile contains, how portable intent separates from
target-specific bindings, and why a profile switch is a governed action rather
than a file edit. External tooling implements the semantics today and is in use.
What does not exist is the capability *inside the product* — so the behaviour is
available to whoever has the external tooling and to nobody else, and the
deployment itself still cannot answer "what profile am I running?".

One element the existing design does not cover is worth adding here:
**distributable profiles**. A profile that can be exported, handed to another site
and installed is how a working configuration ships to a customer — and it changes
the trust question, because a profile names a catalog, a signer identity and a
signing floor.

## Current Behavior

Configuration is per-host and unnamed. A design specification exists; external
tooling implements profile CRUD against a deployment. The product has no profile
concept, and nothing records which configuration is active.

## Desired Behavior

Profiles as a first-class capability:

    acc-cli profile list|show|apply|diff|validate
    acc-cli profile export <name> / import <archive>

with the deployment able to report its active profile, and a profile switch
following the governed path the design specification sets out — validated before
application, with posture changes treated as the consequential mutations they are.

Distribution adds two requirements the local case does not have: an exported
profile must be verifiable by the receiving site, and it must be explicit about
what it does *not* carry (credentials, host-specific paths), failing loudly rather
than appearing to work.

## Success Criteria

- A deployment can report its active profile.
- A profile can be validated before it is applied, and application is refused if
  validation fails.
- Switching profiles is recorded and reversible.
- An exported profile can be installed at another site, with a clear report of
  what must be provisioned separately.
- The external tooling can be retired in favour of the product capability, or
  becomes a thin caller of it.

## Scope

**In scope**

- Profile representation, validation, application and diff.
- Active-profile reporting.
- Export and import, with verification of imported profiles.
- The governed application path per the existing design.

**Out of scope**

- Carrying credentials inside a profile.
- Re-deciding what a profile contains; the design specification covers that.
- Cross-target concerns already settled in that design.

## Implementation options

**A. Implement to the existing design specification directly**, with the
external tooling reduced to a caller. Most coherent; largest single step.

**B. Ship the local capability first** (list/show/apply/validate) and add
distribution later. Smaller, and defers the signing question that distribution
raises.

**C. Ship validation and reporting first**, leaving application to existing
tooling. Delivers the "what am I running, and is it valid?" half quickly and
leaves the important half undone.

B is the recommendation: local profiles are the demand today, and distribution
brings a trust question that deserves its own attention rather than being carried
along.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. Does a profile isolate **state** (memory, registry, sessions) or only
   configuration? The existing design assumes configuration; stronger isolation
   may be more useful on shared hosts, and is a much larger change.
2. Do distributable profiles need signing? The design specification identified
   distribution as the trigger for that question; this change makes it real.
3. What is the relationship between a profile and the external tooling once this
   lands — retire it, or make it a caller?

## Assumptions

- The existing design specification is the reference for content and semantics.
- Credentials are never carried in a profile.
