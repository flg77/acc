# 20261004-operator-release-split — release plan summary

**Date:** 2026-10-06 · **Status:** all fifteen backlog items done or recorded; the
release itself is the maintainer's sequence below · **Companion:** `proposal.md`
(the options and the decisions), `tasks.md` (item by item), the vault notes
`vault/20-backlog/operator-release/OR-00 … OR-15`.

## 1. Outcome in one paragraph

The ACC operator is a repository of its own, builds and tests from a clean
clone with no lab host and no runtime checkout, runs CI and e2e on GitHub
Actions, ships one OLM bundle that installs on vanilla Kubernetes and on
OpenShift, a Helm chart for clusters without OLM, signed multi-arch images, the
two community-catalog submissions laid out by one command, and documentation a
stranger can follow. It carries no copy of the runtime's roles: the agent image
is their source and the operator asks the image what it serves. It is honest
about its maturity: `alpha` / `candidate-v0`, with a gate that refuses a
promotion the CRDs have not earned. What remains is a maintainer's checklist
of accounts, a runtime tag, a release tag and two pull requests to foreign
repositories.

## 2. Before and after

| | 2026-10-04 | 2026-10-06 |
|---|---|---|
| Where the code lives | `acc-spearhead/operator/` + `console-plugin/`, no CI | private `flg77/acc-spearhead-operator` (history kept), public `flg77/acc-operator` (curated squash per release, empty until the first) |
| Build | workstation → ssh lab host → go-toolset container → hand push to a private registry | `make build` on any machine; GitHub Actions `ci` (build, envtest, bundle-check, parity, chart, console plugin) and `e2e` (kind + OLM, kind + Helm) on every PR; `release.yml` on a tag |
| Runtime coupling | `//go:embed` of the spearhead root trees; tests read `../../../acc` | none: the agent image bakes `roles/ skills/ mcps/` and publishes an index; the operator vendors one file (the NATS permission matrix) from the pinned public release |
| Bundle | hand-maintained CSV, ships a `ConsolePlugin` → fails on Kubernetes | generated, idempotent, validated (operatorframework suite + community), one bundle; the console plugin is created by the operator on OpenShift only |
| Naming | `acc.redhat.io`, `redhat-ai-dev`, `vendor="Red Hat"` | `acc.ic3networks.com`, `github.com/flg77/acc-operator`, `vendor="Agentic Cell Corpus (community)"` (D-032) |
| Images | private `quay.io/flg77/acc_images`, pull secret in the CSV | public `quay.io/flg77/{acc-operator,acc-operator-bundle,acc-console-plugin,acc-operator-chart}`, linux/amd64 + arm64, cosign keyless; no pull-secret default |
| Install paths | private CatalogSource | OperatorHub (k8s + OpenShift), `operator-sdk run bundle`, kustomize, Helm (OCI chart) |
| Role validation | compile-time list copied from the tree | the corpus image's own index (`status.roleIndex`), hybrid admission; a floor on the runtime version |
| Channels | n/a | `alpha` / `candidate-v0`; Fast and Stable gated by `hack/channel-gate.sh` |

## 3. What was built, item by item

| Item | Delivered | Where |
|---|---|---|
| OR-01 decisions | D-032: two repos, group rename, one bundle, pin and fetch, public Quay, GitHub Actions, 0.3.0 on `alpha` | `docs/DECISIONS.md` |
| OR-02 split | subtree split with history; `MIRROR-WITHHELD.md` for the operator; scratch dirs gone | operator repo `main` |
| OR-03 standalone build | `make bundle` runnable and idempotent; fetch of the pinned public runtime; found and fixed five latent bugs (a CEL rule gofmt corrupted, the collective webhook never registered, stale CRDs, missing CSV grants, an invalid bundle object) | #5 |
| OR-04 de-brand | module path, PROJECT domain, labels, API group across operator, console plugin and the seven runtime modules; migration note in both CHANGELOGs; `hack/migrate_api_group.py` + `docs/migrate-from-0.2.md` for the one-time CR re-creation; the lab run itself waits on 0.3.0 | #7, #39; acc-spearhead #534 |
| OR-05 one bundle | console plugin by API discovery (leader-only runnable); bundle = CRDs + CSV | #6 |
| OR-06 public registry | CSV and manager repointed to `$(IMAGE_ORG)`; cosign keyless; `spec.version` defaults to the pin | #9 |
| OR-07 CI | `ci.yml`, `e2e.yml`, `release.yml`; first green run after three runner-found fixes | #8 |
| OR-08 CSV hygiene | required fields, honest description (govern / gate / audit first), four alm-examples, `v4.14` open-ended, both architectures, validators blocking | #10 |
| OR-09 release tooling | `VERSION` 0.3.0; `make catalog-submissions` (semver-mode for operatorhub.io, FBC + `release-config.yaml` for OpenShift); `docs/RELEASING.md` | #11 |
| OR-10 docs | README with compatibility table and kind quickstart; install, collective guide, parity, catalogs; CONTRIBUTING, SECURITY, LICENSE | #12 |
| OR-11 spearhead cleanup | `operator/`, `console-plugin/`, two parity tests removed; pointer doc; CHANGELOG pairing | acc-spearhead #534 |
| OR-12 maturity | scorecard `olm` suite blocking; collective webhook live with CSV descriptors and owned resources | #28 |
| OR-13 Helm chart | `charts/acc-operator-chart` generated from `config/`; cert-manager for the webhooks; kind e2e without OLM; pushed and signed by the release; ArtifactHub annotations | #29 |
| OR-14 roles from the image | runtime D-033: both agent images bake the trees and ship `python3 -m acc.roles_index`; operator: no embed, a Job per corpus reads the index into `status.roleIndex`, the webhook validates against it, floor 0.28.0 | acc-spearhead #538; #31 |
| OR-15 channels | channel gate run by `make bundle`; `OCP_CHANNELS`; the "why not yet" and the certified path closed until a customer asks | #32 |

Operator PR numbers are `flg77/acc-spearhead-operator`; `main` there is at the
merge of #32 and green. Plan records: acc-spearhead #536, #537, #540.

## 4. The two decisions that shaped it

- **D-032 — the operator leaves the spearhead, the group becomes
  `acc.ic3networks.com`.** A CRD group cannot change in place, so the only cheap
  moment was before anyone outside had a CR. Consequence: the private 0.2.x
  line is reinstalled, not upgraded; lab clusters re-create their CRs once.
- **D-033 — the agent image is the source of roles.** Found on the way: the
  source-built agent image never baked `roles/` (the compose mounted a
  checkout), so "the image already has the control roles" was true of the RPM
  flavour only. Both images now bake the trees and answer with an index; the
  operator reads it with a Job rather than the runtime publishing a ConfigMap,
  so agents need no write RBAC and the webhook has an answer before any agent
  runs. Consequence: a runtime release that adds a role needs no operator
  release, and the operator needs runtime 0.28.0 or later.

## 5. The release sequence that remains (maintainer)

In order; each step's detail is in `docs/RELEASING.md` of the operator, and
`release-howto.md` beside this file has every command with its checks.

1. **Accounts (OR-06, OR-13).** On quay.io create the public repositories
   `flg77/acc-operator`, `acc-operator-bundle`, `acc-console-plugin`,
   `acc-operator-chart`; make `flg77/acc_images` public; a robot account with
   write on all; `REGISTRY_USERNAME` / `REGISTRY_PASSWORD` as repository
   secrets. The personal namespace is enough; `IMAGE_ORG` moves everything later.
2. **Runtime v0.28.0 (OR-14).** Tag it from acc-spearhead `main` (it carries
   D-033 and the group rename) and run the image pipelines; both agent-core
   Containerfiles now run `python3 -m acc.roles_index` at build time, so a
   roles tree that does not parse fails the build there. Publish
   `acc_images:acc-agent-core-0.28.0`. This is the operator's floor; without it
   every `AgentCorpus` the operator deploys is refused or stopped.
3. **Decide the 0.3.0 scope.** Operator `main` already carries OR-14 and OR-15,
   so the first public release ships with the image-sourced roles and the
   floor. The CHANGELOG holds them in an "Unreleased — after 0.3.0" section;
   fold it into the 0.3.0 section (recommended: one upgrade note, one
   compatibility row), or cut 0.3.0 from the commit before #31 and release the
   rest as 0.4.0. Fold is simpler and the 0.2.x line is reinstalled anyway.
4. **Release commit and tag.** `make bundle BUNDLE_CREATED_AT=now`, CHANGELOG
   date, README table row; commit; tag `v0.3.0` → `release.yml` builds and
   signs the images and the chart, attaches the bundle tarball, the chart
   package and the catalog-submission tree to the GitHub release. Set the
   repository variable `RUNTIME_E2E=true` so `e2e` also proves a corpus
   reaches Ready and its role index `Known`.
5. **Verify.** `cosign verify` on the four images; `operator-sdk run bundle
   quay.io/flg77/acc-operator-bundle:0.3.0` on kind and on OpenShift 4.14+
   (CRC or lab) with the standalone sample Ready; the lab migration of bb3 and
   lighthouse with `hack/migrate_api_group.py` (export → rewrite → delete the
   CRs through the old operator → uninstall 0.2.x and its CRDs → install
   0.3.0 → apply → verify; `docs/migrate-from-0.2.md`, lab specifics in the
   withheld ops guide). bb3 first, lighthouse on another day; the NATS and
   Redis volumes survive and `verify` says so.
6. **Public repository.** Curated squash to `flg77/acc-operator` (`acc-promote`
   learns the second spearhead → mirror pair; `MIRROR-WITHHELD.md` lists what
   stays private), tag `v0.3.0` there, branch protection requiring `ci` and
   `e2e`. The cosign identity regexp already covers both repositories.
7. **The two catalog PRs (OR-09).** From the release's
   `dist/catalog-submissions/`: fork `k8s-operatorhub/community-operators` and
   `redhat-openshift-ecosystem/community-operators-prod`, copy the
   `operators/acc-operator` trees, `git commit -s`, PR `operator acc-operator
   (0.3.0)`. The OpenShift pipeline creates the semver template and opens the
   catalog PR itself. Review queues: hours to days, up to a week.
8. **ArtifactHub.** Add `oci://quay.io/flg77/acc-operator-chart` as a Helm
   repository once; every later tag is indexed.
9. **Afterwards.** Dependabot's console-plugin PRs on the operator repo; a
   stranger installs on kind from the README without help (OR-10's last box);
   OpenShift check of the console plugin (OR-05's last box).

Done when (unchanged from OR-00 §5): `operatorhub.io/operator/acc-operator`
lists 0.3.0; an OpenShift console with the community catalog shows the tile and
installs from `candidate-v0`; `helm install` of the chart on kind yields the
same Ready corpus; `git grep` of the public repository finds no Red Hat
identifier and no lab host.

## 6. Compatibility, as the README will state it

| Operator | Runtime deployed by default | Oldest runtime it runs | Kubernetes | OpenShift | OLM | Channels |
|---|---|---|---|---|---|---|
| 0.3.0 (with OR-14) | 0.28.0 (`ACC_RUNTIME_VERSION`) | 0.28.0 (`MinRuntimeVersion`: the image is the source of roles) | 1.27+ (CI: 1.31 on kind) | 4.14+ | 0.28+ (CI: 0.31) | `alpha` / `candidate-v0` |

Promotion to Fast or Stable is one commit (`maturity:` plus three Makefile
defaults) that `make bundle` refuses until every CRD is `v1beta1` or later, a
conversion webhook covers a still-served alpha version, and thirty days have
passed since the last CRD change; `make channel-report` prints today's verdict.

## 7. Effort: planned, spent, ahead

- **Planned (proposal):** 20–28 engineer-days to a published 0.3.0, about
  five to six weeks of one maintainer alongside runtime work, plus the catalog
  queues.
- **Spent on the engineering:** the fifteen items were implemented, reviewed
  against CI and merged between 2026-10-04 and 2026-10-06 in agent sessions
  driven by the maintainer, roughly three calendar days; CI ran green on every
  merge, with one runner-availability incident (queued jobs cancelled without a
  runner) that cost an hour and changed nothing in the code.
- **Ahead:** the maintainer sequence in §5 is one to two working days of
  hands-on time (accounts, two tags, verification on two clusters, the lab
  migration, the catalog forks), then the catalog queues. The steady state the
  proposal estimated stands: half a day to a day per operator release, one PR
  when Red Hat adds an OCP minor, and 10–20 % of the maintainer's week for
  strangers' issues across two catalogs once the listing is live.

## 8. Acceptance of ACC — the view, updated

- **The listing is the enterprise door, not the adoption channel.** That held.
  What changed: the adoption channel is now served too, because OR-13 ships
  the Helm chart on an OCI registry with ArtifactHub metadata, so a Kubernetes
  cluster without OLM installs the operator in one command.
- **What the listing buys** is unchanged: a three-click install for an
  OpenShift admin, the precondition for a Red Hat channel-partner conversation
  or an OpenShift design partner, and the credibility signals the project had
  none of (public repo, CI, signed images, a catalog entry, a security policy).
- **What still blocks acceptance, outside the operator:** the runtime images
  must be public (step 1 of §5 flips the repository; IN-11 keeps publishing
  there); the keyless model path is in place (the alm-example is standalone +
  Ollama); the positioning is in place (the CSV leads with govern, gate,
  audit); the `AgentCorpus` CRD is still about 100 KB with twenty optional
  subsystems, and the sample CR is what a visitor reads, so it stays tiny.
- **What the listing costs:** the 10–20 % of a week above, and a response
  expectation on `SECURITY.md` once images are public. With a bus factor of
  one, `maturity: alpha`, channel `alpha` and "Community (best-effort; no SLA)"
  stay until a second maintainer exists; the channel gate makes that a
  decision with evidence rather than a flag flip.

## 9. Risks and loose ends

- **Runtime 0.28.0 is a hard dependency of the operator now.** Until it is
  tagged and its image published, the operator `main` cannot run a corpus;
  the e2e proves everything except `status.roleIndex` reaching `Known`.
- **The image build runs the index at build time** in both Containerfiles;
  this has not yet run on the build host. A failure there is a tree that does
  not parse, which is the point, but the first run should be watched.
- **GitHub-hosted runner availability** on the account cancelled queued jobs
  twice on 2026-10-05; a re-run resolved it. If it recurs on the release tag,
  `release.yml` needs the same re-run, nothing else.
- **`acc-promote`** must learn the operator pair before the public repository
  receives its first squash; until then `flg77/acc-operator` is empty and the
  README links to it.
- **Dependabot** keeps opening console-plugin dependency PRs on the operator
  repository; they are routine and unreviewed so far.
- **The 0.3.0 scope decision** (§5 step 3) is the one open choice; the
  recommendation is to fold.
- **The lab migration is tooled, not run.** `migrate_api_group.py` has been
  exercised against exported fixtures only; its first run against a live
  0.2.x cluster is bb3, with the dry run through the 0.3.0 webhooks before
  the apply. A collective applied before its corpus reports `RoleIndexKnown`
  is refused for every role, so `apply` runs twice.
