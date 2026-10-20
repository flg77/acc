---
type: backlog-item
area: operator-release
id: OR-09
status: in-progress
size: M
depends: [OR-06, OR-07, OR-08]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-09 — Release 0.3.0 and the two catalog PRs

**What.** Tag `v0.3.0` on `acc-spearhead-operator`; curated squash to `acc-operator`; CI publishes. Verify `operator-sdk run bundle` on kind and on OpenShift 4.1x. Then:
1. Fork `k8s-operatorhub/community-operators`; add `operators/acc-operator/0.3.0/{manifests,metadata}` and `operators/acc-operator/ci.yaml` (`updateGraph: semver-mode`, `reviewers`); PR titled `operator acc-operator (0.3.0)`.
2. Fork `redhat-openshift-ecosystem/community-operators-prod`; same layout; follow its current contributing guide for `ci.yaml` (`fbc.enabled`, catalog templates per OCP version if required — the guide was unreachable from the drafting session, read it first); PR.
3. Iterate on both CIs until merged.

**Done when.** `operatorhub.io/operator/acc-operator` shows 0.3.0 and an OCP console with the community catalog shows the tile.

**2026-10-05 — tooling and runbook done; the release itself is maintainer steps** (acc-spearhead-operator, branch `or-09-release-0.3.0`, stacked on OR-08). `VERSION` is 0.3.0 and the committed bundle is `acc-operator.v0.3.0`. `make catalog-submissions` lays out both PR trees from the committed bundle (`dist/catalog-submissions/`, also attached to the GitHub release by `release.yml`): operatorhub.io with `updateGraph: semver-mode`; OpenShift in FBC mode — `ci.yaml` maps the `olm.semver` template to the catalogs `v4.14`…`v4.22` (`OCP_CATALOGS`), `0.3.0/release-config.yaml` has the pipeline add the built bundle to the **Candidate** channel itself (rendered `candidate-v0`, `candidate-v0.3`); no template is shipped because an empty `olm.semver` template does not render and the pipeline creates it (read in operator-pipelines `add_bundle_to_fbc`). Both `ci.yaml` list `flg77` as reviewer, so the PRs merge on green. The tree satisfies the pipeline's static tests as read from source (ci/release-config schemas, semver channel names, version directory = `spec.version`, no NetworkPolicy, required CSV fields, `operator-sdk bundle validate` operatorframework + operatorhub at k8s 1.30). `docs/RELEASING.md` is the runbook. The k8s catalog's docs site was unreachable from this session; the layout follows the repo's existing operators.

**Maintainer steps, in order (none scriptable from here):**
1. Merge the stack #5 → #6 → #7 → #8 → #9 → #10 → #11 into `main` (or squash the lot).
2. OR-06's quay.io / secrets steps; `RUNTIME_E2E=true` once `acc_images` is public.
3. Release commit on `main`: `make bundle BUNDLE_CREATED_AT=now`, CHANGELOG date, `git commit -s`; wait for `ci` + `e2e`.
4. `git tag v0.3.0 && git push origin v0.3.0` → `release.yml`; verify `cosign verify` and `operator-sdk run bundle quay.io/flg77/acc-operator-bundle:0.3.0` on kind and on an OCP 4.14+ cluster; the standalone sample reaches Ready.
5. Curated squash to `flg77/acc-operator` (`acc-promote`, `docs/MIRROR-WITHHELD.md`), tag it `v0.3.0`.
6. Fork both catalogs; copy `dist/catalog-submissions/<org>/<repo>/operators/acc-operator` into each; `git commit -s`; PRs titled `operator acc-operator (0.3.0)`; iterate on the pipelines (`/pipeline restart operator-hosted-pipeline`).
