---
type: backlog-item
area: operator-release
id: OR-12
status: in-progress
size: L
depends: [OR-09]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-12 — Maturity after 0.3.0

**What.** The items that make the operator a product rather than a release:
- Roles / skills / mcps delivered from the agent image or from packs (`AccPackageInstall`), so the embed and `sync-manifests` go.
- A Helm chart of the operator (from `make build-installer`) on ArtifactHub — the adoption channel for Kubernetes clusters **without OLM**, which is most of them (business plan §3, §5 item 4).
- `stable` channel once the CRDs stop moving.
- `certified-operators` (Red Hat Connect, preflight with `--submit`) only when a customer asks; `docs/operator-certification.md` already describes the path.

**Done when.** Each sub-item has its own OR-1x note or is closed with a reason.

**2026-10-05 — started** (acc-spearhead-operator, branch `or-12-maturity`). The two code items that were ready landed together:
- **Scorecard `olm` suite blocks `e2e`.** It failed on two tests: `olm-crds-have-resources` (no `resources` on any owned CRD) and `olm-spec-descriptors` (no spec descriptors). Fixed at the source, with `+operator-sdk:csv:customresourcedefinitions` markers on the four API types (resources per kind; 44 spec descriptors with console hints: selects for `deployMode`/`tier`/`mode`, field groups for the nested specs, `advanced` for the rarely used) and by registering `AccCatalog`/`AccPackageInstall` in `PROJECT`, without which operator-sdk ignored their markers. The hand-written CRD descriptions moved from the CSV base into the Go doc comments, which is where the generator reads them now.
- **The AgentCollective webhook is registered** (defaulting: heartbeat, the assistant, replicas, embedding model; validation: typo-of-a-built-in role refused with suggestions, unknown role warned, reserved `extraEnv` names refused, scaling targets, LLM backend block present). Its markers were parked since OR-03 because nobody served it; the bundle now declares all four webhooks and the e2e applies a collective with role `arbitre` and expects the refusal to name `arbiter`.
- **The role catalogue is generated from the embedded tree.** `hack/gen-catalogue.go` read `<repo-root>/roles`, which the split removed, and looked for a directory named `operator`; it now reads `internal/reconcilers/manifests/data/roles` and `make sync-manifests` runs it, so `ci`'s generated-code check keeps it fresh. 12 roles (the 7 CONTROL roles plus `hub_curator`, `analyst`, `coding_agent_architect`, `devops_engineer`, `research_synthesizer`, which runtime 0.27.0 still ships in-tree); a unit test pins the catalogue to the tree. The semantics are now exactly "what the operator can deliver without a package".

The remaining sub-items have their own notes, as the done-when asked: [[OR-13 — Helm chart for clusters without OLM]] (M), [[OR-14 — Roles from the image, not the embed]] (L, D2), [[OR-15 — Stable channel and the certified path]] (S; the certified path closed with a reason).
