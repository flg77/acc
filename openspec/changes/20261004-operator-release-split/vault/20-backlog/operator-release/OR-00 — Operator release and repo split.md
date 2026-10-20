---
type: backlog-index
area: operator-release
status: in-progress
created: 2026-10-04
owner: flg
source: "[[acc-spearhead]] openspec/changes/20261004-operator-release-split"
tags: [backlog, operator, operatorhub, release, repo-split]
---

# OR-00 — Operator release and repo split

> **Goal.** One ACC operator release the maintainer can cut with no lab host in the path, published to **operatorhub.io** (`k8s-operatorhub/community-operators`) **and** the OpenShift embedded OperatorHub (`redhat-openshift-ecosystem/community-operators-prod`). Kubernetes and OpenShift both.

## 0. Where it stands (2026-10-04)

| | State |
|---|---|
| Operator | `acc-spearhead/operator/` + `console-plugin/`, Go, OLM level 3, **0.2.28** private, runtime at 0.26.4 |
| Build | workstation → ssh `acc1` (fallback `bb3`) → go-toolset container → hand push to **private** `quay.io/flg77/acc_images` → `opm index` → private CatalogSource. No `.github/`, no CI. |
| Coupling | `//go:embed` of the spearhead root `roles/ skills/ mcps/` (`make sync-manifests`); unit test reads `../../../acc/nats_permissions.yaml`. Cannot build from its own directory. |
| Bundle | CSV hand-maintained (`make bundle` not runnable: no `config/manifests/`, no `kustomize` target); `opm` scratch dirs committed; ships a `ConsolePlugin` + service-ca Service → **fails on vanilla Kubernetes** |
| Naming | group `acc.redhat.io`, module `github.com/redhat-ai-dev/…`, label `vendor="Red Hat"`, CSV says "NOT a Red Hat product" — [[acc-business plan]] §9 risk |

## 1. Decisions (settle first — OR-01)

| | Decision | Recommendation |
|---|---|---|
| A | Repo topology | **Two repos**: private `flg77/acc-spearhead-operator` (subtree split, history kept) + public **`flg77/acc-operator`** (curated squash per release, like `flg77/acc`). Public name is `acc-operator`, not `acc-mirror-operator`: it is what the CSV links and the listing show. Remote name stays `mirror`. |
| B | API group | **Rename now to `acc.ic3networks.com`** (decided 2026-10-04, D-032). Only cheap moment is before anyone outside has a CR. Mechanical, ~3 days, lab clusters re-create CRs once. |
| C | K8s + OpenShift | **One bundle.** Console plugin created by the operator on `console.openshift.io/v1` discovery (as `consolelink.go` does). Bundle = CRDs + CSV. |
| D | Runtime coupling | **Pin and fetch** for 0.3.0: `ACC_RUNTIME_VERSION` → tarball of the public `flg77/acc` tag. Roles-from-image is the maturity path (OR-12). |
| E | Images | Public Quay organisation, one repo per operator image, cosign keyless. CSV drops `imagePullSecrets` default. |
| F | Build infra | GitHub Actions on `ubuntu-latest`: build/test, bundle regenerate-and-diff, validate, scorecard + `run bundle` on kind, buildx amd64+arm64, push+sign on tag. |
| G | Versioning | Operator version independent of runtime. First public **0.3.0**, channel `alpha`, `ci.yaml updateGraph: semver-mode`, no `replaces`/`skips`. Lab reinstalls once. |

## 2. Items

| Item | Size | Depends | Status |
|---|---|---|---|
| [[OR-01 — Record the decisions]] | S | — | done 2026-10-04 (D-032) |
| [[OR-02 — Split the repositories]] | M | OR-01 | done 2026-10-04 — `flg77/acc-spearhead-operator` main pushed; `acc-promote` still to teach |
| [[OR-03 — Build standalone]] | M | OR-02 | done 2026-10-04; merged to `main` 2026-10-05 (#5) |
| [[OR-04 — De-brand and rename the API group]] | L | OR-01 | code done 2026-10-04, merged (#7); runtime half in acc-spearhead #534; migration tool and runbooks in #39; the lab run after the 0.3.0 tag |
| [[OR-05 — One bundle for Kubernetes and OpenShift]] | M | OR-02 | done 2026-10-04, merged (#6); OpenShift check after the tag |
| [[OR-06 — Public registry and signing]] | S | OR-01 | code done, merged (#9); quay.io repos + robot + secrets are the maintainer's |
| [[OR-07 — CI in GitHub Actions]] | M | OR-03, OR-05 | done, merged (#8); `release.yml` runs first on the 0.3.0 tag |
| [[OR-08 — CSV hygiene]] | S | OR-04, OR-05 | done, merged (#10) |
| [[OR-09 — Release 0.3.0 and the two catalog PRs]] | M | OR-06..08 | tooling + runbook merged (#11); the release is maintainer steps (quay.io, tag, forks) |
| [[OR-10 — Docs for a stranger]] | M | OR-09 | done, merged (#12) |
| [[OR-11 — Spearhead cleanup]] | S | OR-02, OR-10 | done 2026-10-05 (acc-spearhead #534) |
| [[OR-12 — Maturity after 0.3.0]] | L | OR-09 | started 2026-10-05: olm scorecard blocking, collective webhook registered, role catalogue generated; the rest split out below |
| [[OR-13 — Helm chart for clusters without OLM]] | M | OR-09 | code done 2026-10-05 (chart, ci/e2e/release wiring); quay.io repo + ArtifactHub are the maintainer's |
| [[OR-14 — Roles from the image, not the embed]] | L | OR-09 | code done 2026-10-05 on both sides (runtime D-033: images bake the trees + `acc.roles_index`; operator: no embed, index Job, webhook on the index, floor 0.28.0); merge order runtime release → operator PR |
| [[OR-15 — Stable channel and the certified path]] | S | OR-09 | done 2026-10-05 as "why not yet": channel gate + one-commit promotion; Stable opens at v1beta1 + a month's soak; certified path closed until a customer asks |

S ≈ ½–1 d · M ≈ 2–3 d · L ≈ 4–5 d (one maintainer).

## 3. Effort

* **To a published 0.3.0: 20–28 engineer-days**, ~5–6 weeks alongside runtime work, plus catalog review queues (k8s: hours–days; OpenShift repo: up to a week). Skipping the group rename saves ~3 d and keeps the §9 risk forever.
* **Steady state:** ½–1 d per operator release (tag → CI → two catalog PRs); one PR per new OCP minor; a runtime-pin review whenever a runtime release changes a CRD-visible field.

## 4. Acceptance of ACC — our view

* **The listing is the enterprise door**, not the adoption channel. The [[acc-business plan]] names Red Hat the enterprise channel and Ubuntu / upstream K8s the adoption channel. operatorhub.io reaches only clusters that run **OLM**; most upstream clusters do not. The adoption path stays `pip install` + Helm (plan §5 items 1, 4). OR-12 adds a Helm chart of the operator at near-zero cost.
* **What the listing buys:** three-click install for an OpenShift admin; the precondition for the Red Hat channel-partner row (§6) and for any OpenShift design partner; a credibility signal (public repo, CI, signed images, catalog entry) the project has none of today.
* **What still blocks acceptance and is outside the operator:** public runtime images (IN-11 phase 2 must publish), a keyless model path (alm-example = standalone + Ollama, not RHOAI + MaaS), positioning (CSV description leads with *govern, gate, audit*, then the disclaimer; "biological cell architecture" moves down), the 106 KB `AgentCorpus` CRD (the sample CR is what a visitor reads, keep it tiny).
* **Effort it creates:** strangers' issues across two catalogs × N cluster versions, a security contact once images are public, the compatibility table kept true. Budget **10–20 % of the maintainer's week** once live. Bus factor 1 → keep `maturity: alpha`, channel `alpha`, "Community (best-effort; no SLA)" until a second maintainer (plan §7, month 12).

## 5. Verification (done when)

1. Clean clone of `flg77/acc-operator` → `make test` passes with no sibling directory.
2. `make bundle` on a clean tree → no diff (CI asserts).
3. `bundle validate` + `scorecard` green in CI.
4. `operator-sdk run bundle` installs on **kind (OLM)** and on **CRC**; sample `AgentCorpus` Ready on both with Ollama.
5. `git grep -nE "redhat\.io|redhat-ai-dev|vendor=\"Red Hat\""` on the public repo → nothing; no lab hostnames or IPs.
6. `operatorhub.io/operator/acc-operator` lists 0.3.0; the OCP console shows the tile with the community catalog.

## 6. Open questions

- ~~Which group/domain?~~ → `acc.ic3networks.com` (2026-10-04).
- Quay organisation name (`acc-project`? `agenticcell`?) — must be free on quay.io.
- ~~Does `community-operators-prod` now require `fbc.enabled: true` + `catalog-templates/` for new operators?~~ Answered in OR-09 from the pipeline source: FBC mode is recommended (a warning otherwise), `ci.yaml` maps a semver template, `release-config.yaml` has the pipeline create the template and add the bundle itself; no template is shipped on the first PR.

## Related

`summary.md` beside the proposal (2026-10-06: the release plan summary — before/after, what each item delivered, the maintainer sequence that remains, compatibility, effort, the acceptance view updated) · [[IN-11]] (image release pipeline) · `docs/operator-certification.md` (0.1.0-era roadmap, partly superseded) · `docs/MIRROR-WITHHELD.md` · [[acc-business plan]] §3 §6 §9
