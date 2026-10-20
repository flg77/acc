# 20261004-operator-release-split — tasks

Order matters: OR-04 (the group rename) lands before OR-09 (the public bundle).
Vault items: `20-backlog/operator-release/OR-01 … OR-15`. `summary.md` (2026-10-06)
is the release plan summary and the maintainer sequence that remains.

## OR-01 — decisions (S) — done 2026-10-04
- [x] `docs/DECISIONS.md` D-032 (2026-10-04) recording A3 (two repos, public `flg77/acc-operator`), B2 (group rename, the chosen group), C2 (one bundle, plugin by discovery), D1 (pin and fetch), E (public Quay org), F (GitHub Actions), G (0.3.0, `alpha`, semver-mode)
- [x] the group: `acc.ic3networks.com` (chosen 2026-10-04)

## OR-02 — the split (M) — done 2026-10-04 (acc-promote pending)
- [ ] `git subtree split -P operator` → `flg77/acc-spearhead-operator` (private); `console-plugin/` and the two parity tests follow (as `console-plugin/` and `test/parity/`)
- [x] operator `docs/MIRROR-WITHHELD.md` (+ `docs/howto-operator-ops.md`, a lab-ops doc that links the private runbooks): `hack/build-remote.sh`, `hack/deploy-private-catalog.sh`, `hack/acc-operator-ops.sh`, `config/private-catalog/`, `docs/private-quay-to-ocp-catalog-deploy.md`, `docs/rhoai-e2e-agentset-runbook.md`, `config/samples/loan-origination/`
- [x] delete `bundle_tmp*`, `index_build_tmp*`, `index.Dockerfile*`; `.gitignore` them (the operator repo gets its own `.gitignore`)
- [x] `flg77/acc-spearhead-operator` (private) and `flg77/acc-operator` (public, empty until OR-04) created by the maintainer 2026-10-04; `main` pushed. (Earlier note: `create_repository` returned 403 from the session's GitHub App; create both by hand, then `git push git@github.com:flg77/acc-spearhead-operator.git split/acc-spearhead-operator:main`
- [ ] `acc-promote` (workstation skill, not in this repo) handles the second spearhead → mirror pair

## OR-03 — standalone build (M) — done 2026-10-04 (acc-spearhead-operator, branch `or-03-standalone-build`)
- [x] `ACC_RUNTIME_VERSION` = 0.27.0 (the public tag whose trees and permission matrix equal spearhead main); `make sync-manifests` shallow-clones the tag (a tarball download is blocked by some proxies, git is not) and copies `roles/ skills/ mcps/` + `acc/nats_permissions.yaml`; `ACC_SRC=<dir>` for the lab
- [x] `test/unit/nats_permissions_sync_test.go` compares against the fetched copy; `make sync-permissions` re-vendors
- [x] `KUSTOMIZE` tool target; `config/manifests/` + `config/scorecard/` + `config/console-plugin/`; `make bundle` regenerates `bundle/`, is idempotent, validates; `make bundle-check` for CI
- [x] Go pinned by the `go 1.25.0` line only — no `toolchain` directive, because the UBI go-toolset builder runs GOTOOLCHAIN=local and would refuse a newer directive; go-toolset:10.1 matches
- [x] `make build`, `make test` (all packages), `pytest test/parity` from a clone with no sibling directory
- [x] found on the way: a CEL rule gofmt corrupts (`''` → `”` in doc comments), the AgentCollective webhook declared but never registered (kustomize installs failed every collective create), the AccCatalog/AccPackageInstall CRDs stale against the Go types, the CSV missing the NetworkPolicy/SPIRE grants `role.yaml` had, the ConsolePlugin CR not a valid bundle object (operator-sdk refuses it → OR-05 is the fix)

## OR-04 — de-brand and group rename (L) — code done 2026-10-04 (operator: acc-spearhead-operator `or-04-group-rename`; runtime: the acc-spearhead plan branch); lab migration open
- [x] module path → `github.com/flg77/acc-operator`; `PROJECT` repo/domain `ic3networks.com`; Containerfile `vendor="Agentic Cell Corpus (community)"`, `version` from the build
- [x] API group `acc.ic3networks.com`: Go types + markers, webhook paths, `config/crd` (regenerated, new file names), `config/rbac`, `config/webhook`, `config/samples`, CSV base, bundle; controller-gen pinned to `./api ./cmd ./internal` (its loader walked into the pinned runtime checkout under `bin/`)
- [x] console plugin: `src/models.ts`, `console-extensions.json`, pages
- [x] runtime: `acc/api_group.py` is the one constant; the seven modules read it; Falco/Tetragon rules, `tools/mode_ab/rhoai/`, `gitops/argocd/applications/*`, tests and docs renamed (89 tests pass; 4 pre-existing failures need the `textual`/`fastapi` extras)
- [x] docs: `docs/operator-*.md`, `docs/howto-rhoai.md`, `docs/security-hardening.md`, `docs/kagenti-discovery.md`, … (frozen `operator/` and `console-plugin/` trees in this repo left as they are until OR-11)
- [x] migration note in both CHANGELOGs (runtime Unreleased; operator `CHANGELOG.md` 0.3.0)
- [x] migration tooling and runbooks 2026-10-07 (acc-spearhead-operator PR #39): `hack/migrate_api_group.py` export/rewrite/apply/verify/rollback, `docs/migrate-from-0.2.md`, the lab sequence in `docs/howto-operator-ops.md`, `test/migration` in CI
- [ ] the run itself on bb3 and lighthouse (export → delete CRs → uninstall 0.2.x → drop old CRDs → install 0.3.0 → apply → verify) — after runtime v0.28.0 and the 0.3.0 bundle exist (summary §5 steps 2–5); bb3 first, then lighthouse

## OR-05 — one bundle for Kubernetes and OpenShift (M) — done 2026-10-04 (acc-spearhead-operator, branch `or-05-one-bundle`, stacked on OR-03)
- [x] `internal/consoleplugin/`: a leader-only runnable creates the ConsolePlugin, its Deployment and Service (serving-cert annotation included) in the operator's namespace on `console.openshift.io/v1` discovery, re-applied every 10 min; no-op elsewhere; RBAC for `consoleplugins`; `RELATED_IMAGE_CONSOLE_PLUGIN` + `OPERATOR_NAMESPACE` on the manager
- [x] bundle = CRDs + CSV (+ the webhook Service operator-sdk emits); second CSV deployment and the plugin Service gone; `console.openshift.io/plugins` kept
- [ ] kind + OLM: install succeeds, no ConsolePlugin created; CRC: plugin served and enabled — **not run yet** (no cluster in the drafting session; OR-07 brings kind + OLM to CI, CRC/lab for the OpenShift half). Unit tests cover the no-op, the skips and the rendered objects.

## OR-06 — public registry (S) — code done 2026-10-05 (acc-spearhead-operator PR #9, `ci` + `e2e` green on the first run); the quay.io steps are the maintainer's
- [ ] **maintainer:** on quay.io create the public repositories `flg77/acc-operator`, `flg77/acc-operator-bundle`, `flg77/acc-console-plugin` (quay auto-creates repositories on first push as *private*, so create them public first or flip them after), make `flg77/acc_images` public (the runtime images), create a robot account with write on the four, and set `REGISTRY_USERNAME` / `REGISTRY_PASSWORD` as repository secrets on `acc-spearhead-operator` (later on `acc-operator`). No new organisation: the personal namespace is enough for the community catalogs; `IMAGE_ORG` moves everything later if wanted.
- [x] cosign keyless (GitHub OIDC) in `release.yml` (OR-07); verify with `cosign verify --certificate-identity-regexp 'github.com/flg77/acc-operator' --certificate-oidc-issuer https://token.actions.githubusercontent.com <image>@<digest>`
- [x] CSV `containerImage`, `relatedImages` and the manager image repointed to `$(IMAGE_ORG)/acc-operator*` by `make bundle`; the CRD's `imagePullSecrets` default removed (field stays), `imageRepository` comment says public; manager carries no pull secret; samples drop the legacy registry. Also: `spec.version` defaults to the embedded runtime (`ACC_RUNTIME_VERSION` through the linker), not `0.1.0`.

## OR-07 — CI (M) — done 2026-10-05 (acc-spearhead-operator PR #8, branch `or-07-ci`, stacked on OR-04): `ci` and `e2e` green on the PR
- [x] `ci.yml`: gofmt, `make build`, `make test` (envtest), generated code committed, `make bundle-check`, optional validators (blocking since OR-08), `pytest test/parity`, console plugin `npm run build`
- [x] `e2e.yml`: kind + local registry + OLM, `operator-sdk run bundle` with the run's own images, CSV Succeeded, CRDs established, defaulting webhook + CRD validation checked, console plugin no-op (OR-05), scorecard basic blocking / olm advisory; the Ready wait is gated on the repository variable `RUNTIME_E2E=true` until the runtime images are public (OR-06)
- [x] `release.yml` on `v*`: refuses unless the committed bundle is this version; buildx amd64+arm64 for operator and console plugin (Containerfile cross-compiles per TARGETARCH), bundle image, cosign keyless by digest, preflight advisory with artifact, GitHub release with bundle tarball + CHANGELOG section; inputs `IMAGE_REPO` variable + `REGISTRY_USERNAME`/`REGISTRY_PASSWORD` secrets (OR-06)
- [ ] branch protection on `main` of the public repo (maintainer, in the GitHub UI: require `ci` and `e2e`)
- [x] first green run (2026-10-05, after three fixes the runner found: controller-tools v0.16.1 does not compile under Go 1.25 → v0.16.5; docker needs `-f Containerfile`; `operator-sdk run bundle --use-http` for the plain-HTTP kind registry). `e2e` installs the bundle through OLM on kind, the webhooks answer, the console plugin is a no-op, the scorecard basic suite passes.

## OR-08 — CSV hygiene (S) — done 2026-10-05 (acc-spearhead-operator, branch `or-08-csv-hygiene`, stacked on OR-06)
- [x] `createdAt` kept by `make bundle`, stamped at release with `BUNDLE_CREATED_AT=now` (OR-09); one operator-sdk version (1.36.0) in `builder` and `annotations.yaml`
- [x] `repository: https://github.com/flg77/acc-operator`; `links` (operator, runtime, ecosystem), `provider.url`, `maintainers`; `support` honest; the description opens with govern / gate / audit and closes with the disclaimer; keywords governance, audit, policy, oversight, mcp, a2a
- [x] `com.redhat.openshift.versions: v4.14` (open-ended: 4.14 and later, no catalog PR per OCP minor); `minKubeVersion` 1.27.0 matches
- [x] alm-examples: standalone `AgentCorpus` + `AgentCollective` of the core CONTROL roles (assistant, ingester, arbiter, observer) on Ollama, `AccCatalog` `acc-ecosystem` at its real URL with the keyless signing identity, `AccPackageInstall` `@acc/research-roles`; no namespaces; `e2e` applies all four from the installed CSV
- [x] no `replaces`, no `skips` (since OR-06); `ci.yaml` `updateGraph: semver-mode` is written in OR-09
- [x] optional validators block `ci` (operatorframework suite + community; the OR-06 selector `operatorhub/v2` was invalid and the step had failed silently as advisory); CSV declares `linux/amd64` + `linux/arm64`; no `---` in the CSV (`make bundle` guard; `operator-sdk run bundle` splits the catalog on it); `e2e` diagnostics cover the OLM registry pod after one unreproduced crash-loop of it on the first run

## OR-09 — release 0.3.0 (M) — tooling done 2026-10-05 (acc-spearhead-operator, branch `or-09-release-0.3.0`, stacked on OR-08); the release is maintainer steps
- [x] `VERSION` 0.3.0, bundle `acc-operator.v0.3.0`; `make catalog-submissions` builds both PR trees (operatorhub.io semver-mode; OpenShift FBC: `ci.yaml` catalog mapping `v4.14`…`v4.22`, `release-config.yaml` → Candidate, no shipped template — the pipeline creates it); `release.yml` attaches the tree; `docs/RELEASING.md` runbook
- [ ] maintainer: merge the stack; OR-06 quay.io + secrets; release commit (`make bundle BUNDLE_CREATED_AT=now`, CHANGELOG date); tag `v0.3.0` → `release.yml`; `cosign verify`
- [ ] `operator-sdk run bundle quay.io/flg77/acc-operator-bundle:0.3.0` on kind and on OpenShift 4.14+ (CRC or lab); standalone sample Ready on both
- [ ] curated squash to `acc-operator` (`acc-promote`, MIRROR-WITHHELD), tag `v0.3.0` there
- [ ] fork `k8s-operatorhub/community-operators`; copy `dist/catalog-submissions/k8s-operatorhub/community-operators/operators/acc-operator`; `git commit -s`; PR `operator acc-operator (0.3.0)`
- [ ] fork `redhat-openshift-ecosystem/community-operators-prod`; copy the other tree; `git commit -s`; PR; the pipeline's auto-release opens the catalog PR
- [ ] iterate on both CIs; listing visible on operatorhub.io and in an OCP console with the community catalog

## OR-10 — docs for a stranger (M) — done 2026-10-05 (acc-spearhead-operator, branch `or-10-docs`, stacked on OR-09)
- [x] `README.md` of `acc-operator`: one sentence, the compatibility table, install from OperatorHub, the 15-minute kind quickstart; spearhead-only status moved to withheld `docs/SPEARHEAD.md`
- [x] the four docs moved as `docs/install.md`, `catalogs-and-certification.md`, `agentcollective-guide.md`, `standalone-parity.md`, rewritten against 0.3.0; spearhead keeps `docs/operator.md`; stale `WS-A` runbook deleted
- [x] `SECURITY.md` (private reporting, contact, supported versions, what to verify); `CONTRIBUTING.md` (ten-minute setup, rules, local e2e); `LICENSE` added (Apache-2.0 was missing)
- [ ] a stranger installs on kind from the README without help (after the 0.3.0 tag publishes the images)

## OR-11 — spearhead cleanup (S) — done 2026-10-05 (acc-spearhead #534)
- [x] remove `operator/`, `console-plugin/`, the two parity tests; `pyproject.toml` had no references; README badges, quick starts, tree and related-repositories table; `howto-edge`, `howto-rhoai`, `howto-tui`, `webgui`, design-doc code pointers
- [x] `CHANGELOG.md` "Upgrade notes" pair the runtime with operator 0.3.0; a "Removed" entry records the trees' departure

## Merged 2026-10-05
- acc-spearhead-operator: #5 → #6 → #7 → #8 → #9 → #10 → #11 → #12 into `main` in order (merge commits), plus the Dependabot console-plugin bumps; `main` carries OR-03…OR-10 and runs `ci`/`e2e` on push

## OR-12 — maturity (L, after 0.3.0) — started 2026-10-05 (acc-spearhead-operator #28, merged)
- [x] scorecard `olm` suite blocks `e2e`: `resources` and spec descriptors from `+operator-sdk:csv` markers on the four types; `AccCatalog`/`AccPackageInstall` registered in `PROJECT`
- [x] AgentCollective webhook registered and declared in the bundle; e2e refuses a typo role
- [x] `internal/rolecatalogue/known_roles.txt` generated from the embedded roles tree by `make sync-manifests` (`hack/gen-catalogue.go` read a path the split removed); unit test pins it
- [x] OR-13 Helm chart: `charts/acc-operator-chart` generated from `config/` (`make helm-sync`/`helm-check`), cert-manager for the webhooks, kind e2e job without OLM, pushed as `oci://quay.io/flg77/acc-operator-chart` by the release (acc-spearhead-operator #29, `ci` + `e2e` green)
- [ ] OR-13 maintainer: public quay.io repository `acc-operator-chart`; add the OCI repo on ArtifactHub after the first release
- [x] OR-14 runtime side (D-033): both agent images bake `roles/ skills/ mcps/`, set `ACC_*_ROOT`, and bake `python3 -m acc.roles_index` output at `$ACC_ROLES_INDEX`; `acc/roles_index.py` is the contract the operator reads (acc-spearhead, this PR)
- [x] OR-14 operator side code done: embed, `sync-manifests` of the trees, the catalogue and the three ConfigMaps go; `{corpus}-role-index` Job records the image's index in `AgentCorpus.status.roleIndex`; the collective webhook validates against it; floor 0.28.0; pin 0.28.0 (acc-spearhead-operator branch `or-14-roles-from-image`)
- [ ] OR-14 maintainer: tag runtime v0.28.0 and publish `acc-agent-core-0.28.0`, then merge the operator PR; `RUNTIME_E2E=true` proves the index is read
- [x] OR-15 done as "why not yet": `hack/channel-gate.sh` (run by `make bundle`/`catalog-submissions`) refuses Fast/Stable while maturity, CRD versions, conversion and the 30-day soak say no; promotion is `maturity:` + `CHANNELS`/`DEFAULT_CHANNEL`/`OCP_CHANNELS`; the certified path is recorded as closed until a customer asks (acc-spearhead-operator branch `or-15-channels`)
