# 20261004-operator-release-split — proposal

Backlog: vault `20-backlog/operator-release/` **OR-00** (the index) and items
**OR-01 … OR-12**. The vault notes are shipped beside this proposal under
`vault/` so they can be copied into the Obsidian vault as they are. Related:
`docs/operator-certification.md` (the 0.1.0-era roadmap this supersedes in
part), `docs/MIRROR-WITHHELD.md` (the promote discipline the operator repo
inherits), `20260912-image-release-pipeline` (IN-11a), the business plan
(`acc-business/business-plan.md`, 2026-10-01, §3 platform, §6 channel partners,
§9 the `acc.redhat.io` risk).

**Status:** accepted — OR-01 decided 2026-10-04 (D-032: API group **`acc.ic3networks.com`**), OR-02 in progress · **Date:** 2026-10-04 · **Owner:** the maintainer

## Why

The ACC operator is a Go module inside `acc-spearhead/operator/` plus a console
plugin at `console-plugin/`. It is OLM level 3, shipped twenty-eight private
releases (0.2.28 today) through a lab-only chain — a workstation `git archive`
streamed over ssh to `acc1` (fallback `bb3`), a go-toolset container there, a
hand-pushed image to a **private** `quay.io/flg77/acc_images`, an `opm index`
into a private `CatalogSource`. That chain works for the lab and for nothing
else:

* **Nobody outside can install it.** The images are private (the CRD doc says
  so; the CSV carries an `imagePullSecrets: acc-images-pull` default). There is
  no public catalog, no listing, no install page.
* **Nobody outside can build it.** The module embeds `roles/ skills/ mcps/` from
  the spearhead root (`make sync-manifests`, `//go:embed all:data/...`) and the
  unit tests read `../../../acc/nats_permissions.yaml`. The operator cannot be
  built from its own directory, let alone from its own repository.
* **The repository has no CI.** No `.github/`. `make bundle` is not runnable
  (`config/manifests/` does not exist, there is no `kustomize` target), so the
  CSV is hand-maintained; `tests/test_operator_crd_bundle_sync.py` exists
  because the bundle fell twelve properties behind the base CRDs for six weeks.
  Two `opm` scratch directories (`bundle_tmp…`, `index_build_tmp…`, an
  `index.Dockerfile…`) are committed.
* **It says it is something it is not.** API group `acc.redhat.io`, module path
  `github.com/redhat-ai-dev/agentic-cell-corpus/operator`, Containerfile label
  `vendor="Red Hat"`, `PROJECT` domain `redhat.io` — while the CSV description
  opens with "NOT a Red Hat product". The business plan lists this as a risk to
  settle **before the first upstream release** (§9).
* **The bundle only installs on OpenShift.** It ships a `ConsolePlugin` object
  and a Service with `service.beta.openshift.io/serving-cert-secret-name`.
  On a vanilla Kubernetes cluster the InstallPlan fails on the unknown kind.
  We target both catalogs.

The ask is one release the maintainer can cut from a laptop or from GitHub
Actions, with no lab host in the path, published to **operatorhub.io**
(`k8s-operatorhub/community-operators`) **and** to the OpenShift embedded
OperatorHub (`redhat-openshift-ecosystem/community-operators-prod`). Everything
below is in service of that release, and only that.

## Options, checked

### A. Repository topology

| | Option | Verdict |
|---|---|---|
| A1 | **Keep the operator in `acc-spearhead`**, publish only a bundle directory to the catalogs | Rejected. The catalogs link back to a source repository and an issue tracker; a private one fails review intent and the `go:embed` coupling stays. |
| A2 | **One public repository** `flg77/acc-operator`, develop in the open | Viable and cheapest. Rejected for now: the operator tree carries lab material (host aliases, an IP, the private-catalog runbooks, `hack/*.sh`) and unstable work the maintainer does not want public mid-flight. The runtime already runs a private spearhead → public mirror model; the operator should not be the one component on a different discipline. |
| A3 | **Two repositories**, private `flg77/acc-spearhead-operator` (full history via `git subtree split -P operator`, plus `console-plugin/`) and public `flg77/acc-operator` (curated squash per release, as `flg77/acc` is today) | **Recommended.** Matches the request. One change to the request: name the public repo **`acc-operator`**, not `acc-mirror-operator`. The public name is what the CSV `links`, `provider.url`, the `repository` annotation and the OperatorHub listing show; "mirror" is our internal term and today's mirror is simply `flg77/acc`, not `acc-mirror`. Keep `mirror` as the **git remote name**. |

The withheld list for the operator (the operator's own `MIRROR-WITHHELD.md`):
`hack/build-remote.sh`, `hack/deploy-private-catalog.sh`,
`hack/acc-operator-ops.sh`, `config/private-catalog/`,
`docs/private-quay-to-ocp-catalog-deploy.md`, `docs/rhoai-e2e-agentset-runbook.md`
(lab hosts), `config/samples/loan-origination/` (lab cluster names). The three
`opm` scratch directories are deleted, not withheld. Nothing in `operator/` or
`console-plugin/` touches the runtime's withheld paths (`plugins/`, the threat
model), so the operator repo starts clean.

What moves with the operator: `operator/`, `console-plugin/`, and from `tests/`
the two parity tests that read operator files
(`test_operator_crd_bundle_sync.py`, `test_console_plugin_models_parity.py`;
they become Go or Python tests in the operator repo). `tests/test_deploy.py`
and the Python modules only *mention* operator paths in docstrings; nothing in
`acc/` imports from `operator/`.

### B. The API group and the Red Hat naming

| | Option | Verdict |
|---|---|---|
| B1 | Keep `acc.redhat.io` | Fast, zero migration. Risk: `community-operators-prod` reviewers are Red Hat staff; a non-Red Hat operator whose API group and image label claim Red Hat is a plausible review block and a permanent misstatement. The CSV already has to disclaim it in bold. |
| B2 | **Rename the group now**, before the first public bundle, to **`acc.ic3networks.com`** (decided 2026-10-04, D-032) | **Recommended.** A CRD group cannot be changed in place; the only cheap moment is before anyone outside has a CR. Everything is `v1alpha1`. Mechanical: Go markers, `PROJECT`, CRD bases, CSV, webhook paths, `console-plugin/src/models.ts` + `console-extensions.json`, seven Python modules (`role_crd_loader`, `lifecycle`, `lifecycle_broker`, `deployment`, `runtime_evidence_*`, `pkg/agent_bom`), the Falco/Tetragon rules, gitops samples, docs. Lab clusters (bb3, lighthouse) re-create their CRs once. |
| B3 | Rename later with a conversion webhook | Not possible across groups; it would be a second API served in parallel for a deprecation period. Strictly more work than B2. |

Regardless of B: the Go module path becomes `github.com/flg77/acc-operator`, the
Containerfile label becomes `vendor="Agentic Cell Corpus (community)"`, and the
`PROJECT` domain follows the group. The business plan's §9 row is closed by B2.

### C. One bundle for Kubernetes and OpenShift

| | Option | Verdict |
|---|---|---|
| C1 | Two packages (`acc-operator` for Kubernetes without the plugin, `acc-operator-openshift` with it) | Doubles every release and confuses the listing. Rejected. |
| C2 | **One bundle; the console plugin leaves the bundle** and is created by the operator at runtime when the `console.openshift.io/v1` API is discovered — exactly how `consolelink.go` already behaves | **Recommended.** Bundle manifests shrink to CRDs + CSV; the plugin Deployment and Service move from the CSV into a reconciler; the `console.openshift.io/plugins` CSV annotation stays (OLM ignores it where the console is absent). Test on kind + OLM and on CRC. |

### D. Independence from the runtime tree

| | Option | Verdict |
|---|---|---|
| D1 | **Pin and fetch**: `make sync-manifests ACC_REF=v0.26.4` downloads the public `flg77/acc` tag tarball and copies `roles/ skills/ mcps/` and `acc/nats_permissions.yaml` into the module; the pin lives in one file (`ACC_RUNTIME_VERSION`) and becomes the CRD default for `spec.version` and the alm-example | **Recommended for 0.3.0.** Same artefact as today, built from a public, tagged source instead of a sibling directory. The drift test compares against the pinned tag instead of `../../../acc`. |
| D2 | Stop embedding; the agent image (which already bakes `models.yaml.example` and the control roles via the RPM) delivers roles, the operator only mounts | The right end state (the operator should not carry role content; the pack ecosystem already moves roles out of core). A runtime change (`ACC_ROLES_ROOT` on the image) — a maturity item, not a 0.3.0 blocker. |

### E. Public images

`quay.io/flg77/acc_images` is private. Options: make it public, or open a new
public organisation. Recommended: a **public Quay organisation** with one
repository per image (`acc-operator`, `acc-operator-bundle`, `acc-console-plugin`,
and the runtime images), signed with cosign keyless from GitHub Actions. The
single-repository, tag-multiplexed layout (`acc_images:acc-agent-core-0.26.4`)
can stay for the runtime if the IN-11 pipeline is not touched; the operator's
own three images get proper repositories because `containerImage` and
`relatedImages` are what reviewers and `preflight` look at. The CSV loses the
`imagePullSecrets` default; the CRD comment "it is a PRIVATE repository" goes.

### F. Build infrastructure

GitHub Actions on `ubuntu-latest` in the public repository, no self-hosted
runner: `go build` + unit tests with envtest, `make bundle` must be a no-op on a
clean tree (regenerate-and-diff), `operator-sdk bundle validate`, `scorecard`
and an `operator-sdk run bundle` smoke on **kind** with OLM installed, multi-arch
image build with buildx (`linux/amd64,linux/arm64`, the edge wants arm64), push
and cosign on a tag, `preflight check container` as a non-blocking job. The lab
hosts keep running the lab; they stop being a prerequisite for a release.

### G. Versioning and channels

The operator's version is **independent of the runtime's**; the compatibility
pin (D1) is the only link. The first public bundle is **`0.3.0`** on channel
`alpha` (what the bundle already declares). No `replaces`, no `skips`: the
public catalogs are empty, and the private-catalog 0.2.x line cannot be
upgraded across catalogs — lab clusters reinstall from the public catalog once.
`ci.yaml` uses `updateGraph: semver-mode` so later bundles need no hand-kept
`replaces` chain. Promote to `stable` only after the runtime API stops moving.

## What ships

Twelve work packages; OR-01 … OR-12 in the vault, with sizes and dependencies.

| # | Work package | Size | Depends on |
|---|---|---|---|
| OR-01 | Decisions A–G recorded in `docs/DECISIONS.md` (D-032) — **done 2026-10-04** | S | — |
| OR-02 | Split: `acc-spearhead-operator` (subtree, history kept) and `acc-operator` (curated squash); operator `MIRROR-WITHHELD.md`; delete the `opm` scratch dirs; `acc-promote` learns the second pair | M | OR-01 |
| OR-03 | Standalone build: `ACC_RUNTIME_VERSION` pin, `sync-manifests` from the public tag, drift test against the pin, `kustomize` target + `config/manifests/` so `make bundle` regenerates the CSV, Go 1.25 toolchain pinned | M | OR-02 |
| OR-04 | De-brand: module path, vendor label, `PROJECT` domain, and the API group rename across operator + console plugin + runtime (seven modules, rules, samples, docs); migration note for lab clusters | L | OR-01 |
| OR-05 | Console plugin to runtime discovery; bundle = CRDs + CSV; verified on kind and on CRC | M | OR-02 |
| OR-06 | Public registry: organisation, robot account, repositories, cosign; CSV/CRD defaults repointed; pull-secret default removed | S | OR-01 |
| OR-07 | GitHub Actions: build/test, bundle regenerate-and-diff, validate, scorecard + run-bundle on kind, image build/push/sign on tag, preflight (advisory) | M | OR-03, OR-05 |
| OR-08 | CSV hygiene: real `createdAt`, coherent `builder` (1.34 vs 1.36 today), `repository` annotation, `links`/`provider` to the new repo, `com.redhat.openshift.versions` reviewed, `minKubeVersion`, one alm-example that installs on a laptop kind cluster (standalone + Ollama), `maturity: alpha` | S | OR-04, OR-05 |
| OR-09 | Release 0.3.0: tag, images, bundle; `operator-sdk run bundle` on kind and on CRC/OpenShift 4.1x; the two catalog PRs (`operators/acc-operator/0.3.0` + `ci.yaml` in each repo); iterate on their CI | M | OR-06…OR-08 |
| OR-10 | Operator docs for a stranger: README with a 15-minute kind quickstart, install from OperatorHub, the compatibility table (operator ↔ runtime pin ↔ OCP/K8s); `docs/operator-*.md` move; spearhead docs point at the new repo | M | OR-09 |
| OR-11 | Spearhead cleanup: remove `operator/`, `console-plugin/` and the two parity tests; CHANGELOG points at the operator's own; README badges | S | OR-02, OR-10 |
| OR-12 | Maturity (after 0.3.0): D2 (roles from the image, no embed), a Helm chart of the operator for clusters without OLM, `stable` channel, certified-operators path only if a customer asks | L | OR-09 |

Sizes: S ≈ ½–1 day, M ≈ 2–3 days, L ≈ 4–5 days, for the one maintainer.

**Total to a published 0.3.0: about 20–28 engineer-days**, roughly 5–6 weeks of
one person's time alongside runtime work, plus the catalog review queues
(community CI is hours to days; the OpenShift repo may take a week). OR-04
without the group rename (B1) saves ~3 days and keeps the risk.

**Steady state afterwards:** ~½–1 day per operator release (tag → CI → two
catalog PRs), one extra catalog PR each time Red Hat adds an OCP minor, and a
review of the runtime pin on every runtime release that changes a CRD-visible
field. The dual-catalog cost is what the Kubernetes target buys.

## Acceptance of ACC, and what this creates

The business plan (2026-10-01) is blunt: no users, no community, Red Hat only in
practice, "too hard to get started". It names Red Hat the **enterprise channel**
and Ubuntu / upstream Kubernetes the **adoption channel**. This release serves
the first fully and the second only in part:

* **What a listing does.** It makes ACC installable by an OpenShift admin from
  the console in three clicks, which no amount of README does; it is the
  prerequisite for the "channel partners → Red Hat (certified operator)" row in
  §6 and for any design partner on OpenShift; and it is a credibility signal
  (a public repo, CI, signed images, a catalog entry) that the project has none
  of today.
* **What a listing does not do.** operatorhub.io reaches Kubernetes clusters
  that run **OLM**, and most upstream clusters do not. The adoption channel in
  the business plan is `pip install` + a Helm chart on ArtifactHub (§5 items 1
  and 4), not OLM. That is why OR-12 carries a Helm chart of the operator
  (generated from the same kustomize tree, near-zero marginal cost) and why
  this proposal must not be mistaken for the quickstart work.
* **What blocks acceptance that the operator cannot fix.** Public runtime
  images (OR-06 covers the registry; IN-11 must publish there), a model
  backend that works without a key (the alm-example must be standalone +
  Ollama, not RHOAI + MaaS), and the positioning: the CSV description today
  opens with a disclaimer and then "biological cell architecture"; it should
  open with *govern, gate, audit* as §2 of the plan decides. A 106 KB
  `AgentCorpus` CRD with twenty optional subsystems is honest but intimidating;
  the sample CR, not the schema, is what a visitor reads.
* **Effort it creates beyond the build.** Issues from strangers on two
  catalogs' worth of cluster versions; a security contact and a response
  expectation once images are public and signed; keeping the operator ↔
  runtime compatibility table true. Budget ~10–20 % of the maintainer's week
  once the listing is live; with a bus factor of one this is the cost that
  actually hurts, and it is why `maturity: alpha`, channel `alpha` and
  "Community (best-effort; no SLA)" stay in the CSV until a second maintainer
  exists (plan §7, month 12).

**Recommendation.** Do it, in the order above, with B2 (rename the group now)
and C2 (one bundle). Do not start OR-09 before OR-04 lands: a public bundle
with `acc.redhat.io` is the one mistake this plan cannot undo later.

## Impact

* **Affected code:** `operator/**` and `console-plugin/**` (move),
  `tests/test_operator_crd_bundle_sync.py`, `tests/test_console_plugin_models_parity.py`
  (move), the seven `acc/` modules and two `regulatory_layer/runtime/` rule files
  that name the group (OR-04), `gitops/argocd/applications/*` (group),
  `docs/operator-*.md`, `docs/MIRROR-WITHHELD.md` (the operator gets its own),
  `README.md` badges, `CHANGELOG.md`.
* **New repositories:** `flg77/acc-spearhead-operator` (private),
  `flg77/acc-operator` (public).
* **New external accounts:** a public Quay organisation and a robot account;
  forks of the two catalog repositories.
* **Backward compatibility:** 0.2.x installs from the private catalog are not
  upgraded in place (G). With B2 every existing CR is re-created once under the
  new group; the runtime's `role_crd_loader` and `lifecycle` read the group
  from one constant after OR-04.
* **Not in scope:** certification (`certified-operators`), the runtime image
  pipeline publishing (IN-11 phase 2), the quickstart and Helm chart of the
  runtime itself (business plan §5).

## Verification

1. A clean clone of `flg77/acc-operator` builds and passes unit tests with
   `make test` and no sibling directory present.
2. `make bundle` on a clean tree produces no diff (CI asserts it).
3. `operator-sdk bundle validate ./bundle` and `scorecard` pass in CI.
4. `operator-sdk run bundle <bundle>` installs on kind (OLM) **and** on CRC;
   the sample `AgentCorpus` reaches Ready on both with Ollama.
5. `git grep -nE "redhat\.io|redhat-ai-dev|vendor=\"Red Hat\""`, plus a grep for the lab host aliases,
   on the public repo returns nothing.
6. The two catalog PRs pass their CI; `operatorhub.io/operator/acc-operator`
   lists 0.3.0; the OpenShift console OperatorHub shows the tile on a 4.1x
   cluster with the community catalog enabled.
