# 20260603-acc-pkg-pilot — proposal

## Why

The role-ecosystem brainstorm (`<vault>\ACC Openspec\ACC Role
Ecosystem\Ecosystem split — brainstorm.md`) sequences a four-stage
rollout to split the 44 movable roles out of `acc/` into a separate
`acc-ecosystem` repo. The brainstorm answers WHY / WHAT / WHEN; it
also surfaces **12 Phase A blockers** that don't exist in code yet.

**Stage 0** is the smallest viable proof: a minimal `acc-pkg` CLI +
the `.accpkg` manifest schema + a skill/MCP tier-classification
script + a pilot build of **one** role (`coding_agent`) built **in
place** from `acc/roles/coding_agent/`, published to an internal
catalog hub running on the team's `acc1` Kubernetes cluster, and
proof that the role re-installs back into a vanilla `acc` core
and passes its golden prompts.

**No repo split happens in Stage 0.** Roles stay in `acc/roles/`
through Stages 0 + 1. The Stage 2 extraction to `flg77/acc-ecosystem`
(public, Apache 2.0 + CLA — independent sibling of `acc`, `acc-web`,
and `acc-podman-desktop`) happens after the format + trust chain
have proven themselves on the internal hub.

No trust chain yet (that's Stage 1). No public hub (Stage 2). No
bootc bundler (Stage 3). No `PROPOSE_INFUSE` runtime path
(Stage 1). The goal of Stage 0 is to **derisk the format and the
extraction discipline** before any policy decisions land.

If Stage 0 surfaces a blocker — e.g. the manifest schema can't
capture skill scoping, or the role's perception_profile resolution
breaks once the role moves — we discover it now, not three stages
deep.

## What changes

### Phase 1 (this ship — v0.3.54)

* **`acc/pkg/` (new module)** — Pydantic models + minimal CLI:
  * `acc/pkg/manifest.py` — `AccPkgManifest` schema v1 (name,
    version, depends_on with semver, role refs, skill refs, MCP refs)
  * `acc/pkg/build.py` — pack a source tree → `.accpkg` (tarball with
    deterministic ordering, sha256 over content)
  * `acc/pkg/install.py` — unpack to `/var/lib/acc/packages/<scope>/<name>-<version>/`
    + write registry entry + topological dependency sort
  * `acc/pkg/verify.py` — **real cosign verify** against the
    resolving catalog's `required_signer:` (sha256 + manifest
    schema + signature). Per brainstorm Q3b: the signing floor is
    non-negotiable for every tier; deferring it to Stage 1 would
    let the pilot establish "unsigned is acceptable" as precedent.
    What stays deferred: full Enterprise Contract policy depth
    (eval attestations + Cat-A/B/C smoke verification).
  * `acc/pkg/catalog.py` — layered catalog loader + resolver
    (`/etc/acc/catalogs.yaml` → `~/.acc/catalogs.yaml` →
    `<workspace>/.acc/catalogs.yaml`). Supports `mode: https` and
    `mode: file`. Returns `ResolvedPackage(catalog_id, tier, url|path,
    required_signer, policy_tier)`.
  * `acc/pkg/cli.py` — `python -m acc.pkg {build|install|verify|inspect|list}`
    * **Automation-friendly from day 1** (per brainstorm Q3
      infusion matrix): deterministic exit codes, `--quiet`,
      `--json` machine-readable output, idempotent re-runs
      (re-install of same version is a no-op + exit 0), no
      interactive prompts. The CLI is the universal seam for
      both Assistant-initiated and Manual paths across Edge + DC.
  * `acc/pkg/registry.py` — flock-protected JSON index at
    `/var/lib/acc/packages/registry.json`
* **`tools/classify_skills_mcps.py` (new script)** — analyzes the
  44 movable roles, emits `tools/skill_mcp_tiers.yaml` classifying
  each referenced skill + MCP as `core_baseline | bundle_in_role |
  own_pack`. One-time analysis; output is review-ready (human
  edits the YAML if the heuristic miscategorizes).
* **Pilot in-place build — `coding_agent` only:**
  * `tools/build_pilot_pkg.py coding_agent` — assembles a build
    tree from `acc/roles/coding_agent/role.yaml` + bundled
    skills/MCPs (per the tier classification) **without moving
    files**, then invokes `acc-pkg build` to produce
    `acc-coding-agent-0.1.0.accpkg`. Source stays in
    `acc/roles/coding_agent/`; the build tree is ephemeral in
    `build/pilot/`.
* **Internal `acc1` Kubernetes hub:**
  * Operator-side step (documented, not automated): deploy a
    minimal HTTPS endpoint on the team's `acc1` k8s cluster that
    serves `index.json` + `.accpkg` blobs + cosign signatures.
    Static nginx + ConfigMap is enough; ingress at e.g.
    `https://acc-hub.acc1.internal/`.
  * Stage 0 publishes the pilot package to this endpoint manually
    (`acc-pkg publish` is Stage 1; for Stage 0 a one-liner
    `kubectl cp` + index.json edit is the bootstrap).
  * Test catalog config (`examples/catalogs.dev.yaml`) declares
    this hub as a `trusted` tier catalog with the team's
    development cosign identity as `required_signer:`.
* **Cache layout — `/var/lib/acc/packages/`** with flock + simple
  GC stub (`acc-pkg gc` removes versions with zero references in
  registry.json). Stub level: counts references, doesn't yet
  observe live ROLE_ASSIGNs (Stage 1).
* **Round-trip verification:**
  * Extract `coding_agent` from `acc/` → spearhead.
  * `acc-pkg build` produces `acc-coding-agent-0.1.0.accpkg`.
  * `acc-pkg install` unpacks it into a vanilla `acc` test container
    that has the role REMOVED from `roles/`.
  * Vanilla container runs the golden prompts for `coding_agent`
    (PR-K suite) and passes.

### Phase 2 (deferred — Stage 1: trust chain)

* Real `acc-pkg verify` — cosign + Enterprise Contract policy.
* Behavioral + safety eval YAML format + runner (extends `acc-bench`).
* `PROPOSE_INFUSE` marker handler in `acc/assistant_proposal.py`.
* Konflux pipeline templates for `acc-ecosystem`.

### Phase 3 (deferred — Stage 2: extraction + repo split + public hub)

* Extract `coding_agent`'s 5 variants → `@acc/workspace-roles` pack.
* Extract research_* family → `@acc/research-roles`.
* Extract business roles → `@acc/business-roles`.
* `flg77/acc-ecosystem` public repo (independent sibling of `acc`,
  `acc-web`, `acc-podman-desktop`); Apache 2.0 + CLA.
* Public hub MVP (`acc-roles.dev`) promoted from the internal
  `acc1` endpoint.
* `docs/CONTRIBUTING-ROLE.md`.

### Phase 4 (deferred — Stage 3: edge)

* Bootc bundler (`acc-pkg bundle --base {hummingbird|rhel-bootc|microshift|<custom>}`).

## Impact

* **Affected code (Phase 1):**
  * NEW `acc/pkg/` module (~600 LOC estimate: manifest models +
    build/install/verify-stub + CLI + registry)
  * NEW `tools/classify_skills_mcps.py`, `tools/extract_role.py`,
    `tools/skill_mcp_tiers.yaml`
  * NEW `tests/pkg/` — manifest schema tests, build/install
    roundtrip, registry flock concurrency, tier-classification
    coverage of all 44 movable roles
  * Existing `roles/coding_agent/role.yaml` — unchanged in
    Phase 1 (extraction is to spearhead; vanilla container test
    REMOVES it locally to prove install works)
* **New env knobs:** `ACC_PACKAGES_ROOT` (defaults to
  `/var/lib/acc/packages`; tests override to tmpdir).
* **Tests:** target ~40 new tests across schema validation,
  build/install roundtrip, registry concurrency, dependency
  resolution, tier-classification coverage. Full sweep stays at
  2577+ passing.
* **Backward compatibility:** purely additive. `roles/` tree is
  unchanged. Existing collectives continue to load roles from
  `roles/` until Stage 2 extraction lands.

## What stays open after Phase 1

* Cosign signing + Enterprise Contract policy (Stage 1).
* Eval format + curated-LLM panel resolver (Stage 1; brainstorm Q7).
* `PROPOSE_INFUSE` runtime path (Stage 1).
* Multi-role extraction at family scale (Stage 2).
* Public hub + CLA + community contribution path (Stage 2).
* Bootc bundler + Hummingbird/rhel-bootc/microshift base selector
  (Stage 3; brainstorm Q5).
* Hub outage / offline-install bundle file (Stage 2; brainstorm Q6 #7).

Each deferred bullet warrants its own OpenSpec sibling proposal
when the operator engages that stage.

## Risk mitigations (Phase 1)

* **Signing floor lands now, not Stage 1.** Per brainstorm Q3b,
  `acc-pkg verify` performs real cosign signature verification
  against the resolving catalog's `required_signer:`. Stage 1
  adds attestation-depth checks (Enterprise Contract policy);
  Stage 0 ensures unsigned packages are refused day 1 so the
  pilot doesn't establish "unsigned is acceptable" as precedent.
  CLI prints a Stage-1 warning when attestation depth is
  unavailable; audit-logged.
* **Spearhead is private + behind dual-repo discipline.** No
  community contribution surface exists yet; nothing leaks to a
  public hub until Stage 2.
* **Pilot extraction is one role.** If the manifest or extractor
  miscategorizes `coding_agent`, the blast radius is one role's
  golden-prompt failure in a test container — not 44 roles +
  production.
* **Tier classification is YAML-reviewable.** The auto-generated
  `tools/skill_mcp_tiers.yaml` is checked in for human review
  before any extraction uses it.

## References

* Brainstorm (parent):
  `C:\Users\micro\Documents\Notes\Notes\Development\AgenticCellCorpus\ACC Openspec\ACC Role Ecosystem\Ecosystem split — brainstorm.md`
  (Q3a — Skills + MCPs tier classification; Implementation readiness section)
* Strategy proposal: `openspec/changes/20260604-role-ecosystem-strategy/proposal.md`
* Package format proposal: `openspec/changes/20260531-acc-role-package-format/proposal.md`
* Naming convention: `openspec/RENAMES.md` (this proposal is
  functional — `acc-pkg` is a CLI / module / mechanism, not a role).
