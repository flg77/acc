---
type: backlog-item
area: operator-release
id: OR-14
status: in-progress
size: L
depends: [OR-09]
created: 2026-10-05
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-14 — Roles from the image, not the embed (decision D2)

**Why.** The operator embeds the pinned runtime's `roles/ skills/ mcps/` trees (`make sync-manifests`, `//go:embed`) and delivers them as ConfigMaps. Every runtime release therefore needs an operator release to carry its roles, and the operator's build needs the runtime's source. The agent image already bakes the control roles; the pack ecosystem moves the rest out of core.

**What.** Decision D2 of the proposal: the agent image is the source of roles, skills and MCP servers. The runtime exposes them at a known path (`ACC_ROLES_ROOT`, already honoured by the dual-source loader) and the operator stops embedding: the manifest delivery (`internal/reconcilers/manifests`) becomes a mount of what the image ships, plus the ConfigMaps that `AccPackageInstall` adds. `make sync-manifests`, the embed and `hack/gen-catalogue.go` go; the role catalogue the webhook validates against is then read from the running corpus (a ConfigMap the runtime publishes at start) or dropped to warn-only.

**Runtime side first.** A runtime change: the image publishes its role list (names + versions) at `/etc/acc/roles.index` or similar, and `acc-agent-core` serves the trees read-only. Then the operator change. Order: runtime release N publishes the index; operator release N+1 reads it and stops embedding; the compatibility table gains a floor.

**Size.** L: runtime change, operator change, and the webhook's catalogue semantics (hybrid validation today relies on a compile-time list).

**Done when.** `ACC_RUNTIME_VERSION` is only a default for `spec.version`; the operator image contains no role content; a runtime release that adds a role needs no operator release.

---

## Progress

**2026-10-05 — runtime side done** (acc-spearhead, this PR; decision D-033). Findings first: the source-built `Containerfile.agent-core` baked `skills/` and `mcps/` but **not** `roles/` (the compose mounted a checkout's tree), while `Containerfile.agent-core-rpm` baked all three under `/usr/share/acc` — the two pipelines produce the same image tag, so "the image already bakes the control roles" was true of one flavour only. Change: both images bake the three trees and set `ACC_ROLES_ROOT`; new `acc/roles_index.py` (`python3 -m acc.roles_index`) prints a versioned JSON index of what the image serves — runtime version, the three roots, every role with `version`/`control`/`source` (`in-tree` or `<pack>@<version>`, installed packages under `ACC_PACKAGES_ROOT` included), skill and MCP ids — and the builds bake it at `$ACC_ROLES_INDEX` (`/app/roles.index`, `/usr/share/acc/roles.index`), so a tree that does not parse fails the build. **The command is the contract, not a path**: the two flavours keep their layouts. Decided against the note's "runtime publishes a ConfigMap at start": agents would need `configmaps` write RBAC and the webhook needs the list before any agent runs; the operator will instead run the image once per corpus (an introspection Job, the same pattern as its `regulatory-layer` init container) and record the index in `AgentCorpus.status`. Compatible with operator 0.3: its ConfigMap mount and `ACC_ROLES_ROOT` override the baked tree. Tests: `tests/test_roles_index.py` (index, both Containerfiles); `tests/test_hub_curator.py` no longer reads the removed `operator/.../known_roles.txt`.

**2026-10-05 — operator side code done** (acc-spearhead-operator, branch `or-14-roles-from-image`; merges after runtime 0.28.0 is tagged and its image published, or into 0.3.0 if that has not shipped). The embed, `make sync-manifests`, `hack/gen-catalogue.go` and `known_roles.txt` are gone; `make build` needs no runtime source (`sync-permissions`/`check-permissions` fetch the pinned release for `acc/nats_permissions.yaml` only). No manifest ConfigMaps, no `ACC_*_ROOT` on agent or UI pods; a 0.3 operator's ConfigMaps are removed; `manifestDelivery` deprecated and ignored. A Job `{corpus}-role-index` runs the corpus's agent image with `python3 -m acc.roles_index --no-packages` once per image; `AgentCorpus.status.roleIndex` records image, state (Pending/Known/Failed), runtime, schema, roles; condition `RoleIndexKnown`; failures retried after 30 min and on a version change; RBAC gains `batch/jobs` and `pods/log`. The `AgentCollective` webhook is a client-backed CustomValidator: the corpus's index (via `spec.corpusRef`, else the one corpus listing the collective) is the catalogue; the API's declared role constants stand in while it is unknown, with a warning — so the e2e typo check (`arbitre` → `arbiter`) holds without the runtime image and no status seeding is needed. Floor: `MinRuntimeVersion` 0.28.0; the corpus webhook refuses `spec.version` below it; a corpus found on an older runtime is stopped with `roleIndex.state: Failed` and phase Error **before** its ConfigMaps are deleted (role index runs ahead of the cleanup in the chain). Pin `ACC_RUNTIME_VERSION` → 0.28.0. README table gains the floor column; CHANGELOG has the upgrade notes; e2e checks the floor refusal, the absence of `ci-corpus-acc-roles`, the index Job's image and command, and `Known` + `arbiter` in the roles when `RUNTIME_E2E`. Unit tests: role index reconciler (Job created, completed → Known, failed → Failed with retry, image change, unknown schema, below floor), catalogue, webhook with a fake-client corpus, floor parsing.

**Open.** Runtime v0.28.0 tag + image (maintainer; the index is read from `acc_images:acc-agent-core-0.28.0`); merge order: acc-spearhead #538 → release → operator PR; `RUNTIME_E2E=true` then also proves `Known`. Compatibility table row for the release that carries this.
