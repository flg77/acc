# Proposal: Deployment backup and restore

**Change ID:** 20260817-deployment-backup-restore
**Date:** 2026-08-17
**Status:** Implemented
**Author:** flg

---

## Problem Statement

There is no way to capture a working ACC deployment, move it, or return to it
after a bad change.

This has already cost real work. The v0.7.0 release moved the five per-host
configuration files from tracked to gitignored, which meant the upgrade *deleted*
the tracked originals on any host that had them; only a hand-made copy preserved
one edge node's live model mapping. The same class of loss applies to everything
else a deployment accumulates — installed packages and their registry, the vector
store, working memory, and the session tracelog — none of which has a capture
path today.

It is also the honest answer to a question the profiles work raises directly: "can
I try this configuration and get back?" Currently that guarantee exists for one
file, produced by an ansible role, and nothing else.

## Current Behavior

Nothing in the product. The `acc-profiles` ansible role writes a single `.bak`
of `models.yaml` before it edits. Package installs are recorded in a registry on a
shared volume with no snapshot. `acc-deploy.sh down` deliberately preserves
volumes, which is not the same as being able to restore them elsewhere.

## Desired Behavior

Two commands, with an explicit and conservative position on secrets:

    acc-cli backup [-o <path>] [--label <name>] [--include <set>]
    acc-cli restore <archive> [--dry-run]

A backup captures: the configuration surfaces, the package registry and installed
package trees, the session tracelog, and enough metadata to identify the source
deployment and ACC version. It **excludes secret values by default**; the archive
records which secret *names* the deployment needs so a restore can say precisely
what must be provisioned out of band, and fails loudly rather than silently
restoring an unusable deployment.

`restore` must refuse to overwrite a running deployment without an explicit
acknowledgement, and `--dry-run` must report exactly what it would replace.

## Success Criteria

- A backup taken before a configuration change restores the previous state,
  verified by comparing resolved configuration and installed packages.
- A restore onto a host with no secrets provisioned reports which names are
  missing and does not leave a half-configured deployment.
- The archive contains no secret values — asserted by a test that scans it.
- Restoring across ACC versions either works or refuses with a clear reason.

## Scope

**In scope**

- Configuration, package registry and trees, session tracelog, deployment
  metadata (version, host, collective ids).
- Secret **names** required, never values.
- `--dry-run`, and refusal to clobber a running deployment unacknowledged.

**Out of scope**

- Secret material. Explicitly excluded; a backup that contains credentials is a
  credential artifact and needs handling this change is not going to invent.
- Live migration between hosts without downtime.
- Backing up the vector store in v1 if its size makes that impractical — decide
  during evaluation (see open questions).

## Implementation options

**A. A single archive with a manifest.** Simple, portable, easy to inspect.
Struggles when the vector store is large.

**B. Tiered sets** — `--include config` (small, fast, the common case),
`--include state` (adds registry and tracelog), `--include all`. Keeps the
frequent operation cheap and makes the expensive one explicit.

**C. Delegate to volume snapshots.** Cheapest to build and least portable; the
archive would not move between a podman edge node and a cluster.

B is the recommendation: the case that actually recurs is "capture the
configuration before I change it", and that should not cost a vector-store copy.

> This section is deliberately open. The contract above is what must hold; the
> mechanism below is a starting point, not a decision. Anyone implementing this
> is expected to improve on it.

## Open questions

1. What exactly constitutes ACC state? Configuration and registry are clear;
   the vector store and working memory are large and arguably reconstructible.
2. Should a backup be restorable onto a *different* host, and if so how are
   host-specific values (paths, endpoints) handled?
3. Does a restore of installed packages re-verify signatures, or trust the
   archive? Re-verifying is slower and much easier to defend.

## Assumptions

- Backups are taken by an operator with host access; this is not a scheduled
  service in v1.
- The archive format is inspectable without ACC installed.
