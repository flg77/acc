# Release how-to: operator 0.3.0, step by step

2026-10-07. The same text is the *Release how-to* tab of the published summary page.

Every command of the maintainer sequence (`summary.md` §5), in order, with what to check after each. Step 1 (the quay.io repositories, the robot account) is done; this starts at the checks that prove it and ends at the two catalog PRs. Run it top to bottom; each step says where it runs (your workstation, acc1, or GitHub).

## Where you are

Step 1 is done: the four public repositories on quay.io, `acc_images` public, the robot `flg77+acc_images` with write on all. Three checks prove it before anything is tagged, from your workstation:

```bash
# the repositories are public (anonymous pull works)
for r in acc-operator acc-operator-bundle acc-console-plugin acc-operator-chart acc_images; do
  curl -s "https://quay.io/api/v1/repository/flg77/$r" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["name"], "public" if d.get("is_public") else "PRIVATE")'
done

# the robot can push (a one-file throwaway image under a tag you delete right after)
podman login quay.io -u 'flg77+acc_images'          # the robot token
d=$(mktemp -d) && printf 'FROM scratch\nCOPY Containerfile /probe\n' > "$d/Containerfile"
podman build -t quay.io/flg77/acc-operator:probe "$d"
podman push quay.io/flg77/acc-operator:probe && echo "robot can push"
# then delete the probe tag in the quay.io UI (Tags, trash icon) or with the API token

# the operator repository carries the secrets release.yml logs in with
gh secret list -R flg77/acc-spearhead-operator          # REGISTRY_USERNAME, REGISTRY_PASSWORD
gh variable list -R flg77/acc-spearhead-operator        # IMAGE_ORG only if not quay.io/flg77; RUNTIME_E2E comes in step 4
```

`no secrets found` means the repository side of step 1 is still open: the robot exists on quay.io but `release.yml` has nothing to log in with. Set them from acc1 with `gh` (the token can come straight from podman's auth file after the login above, so nothing is pasted):

```bash
gh secret set REGISTRY_USERNAME -R flg77/acc-spearhead-operator -b 'flg77+acc_images'
tok=$(python3 -c 'import json,base64,os; f=os.environ.get("XDG_RUNTIME_DIR","/run/user/%d"%os.getuid())+"/containers/auth.json"; a=json.load(open(f))["auths"]["quay.io"]["auth"]; print(base64.b64decode(a).decode().split(":",1)[1])')
printf '%s' "$tok" | gh secret set REGISTRY_PASSWORD -R flg77/acc-spearhead-operator
unset tok
gh secret list -R flg77/acc-spearhead-operator        # both listed
```

`no variables found` is fine: `IMAGE_ORG` is only for images outside `quay.io/flg77`, and `RUNTIME_E2E` is set in step 4.

- [ ] five repositories print `public`
- [ ] `robot can push`, probe tag deleted
- [ ] the two secrets exist on `flg77/acc-spearhead-operator` (the workflow reads `secrets.REGISTRY_USERNAME` and `secrets.REGISTRY_PASSWORD`; the robot's username is `flg77+acc_images`)

## Step 2. Runtime v0.28.0: the tag, the package, the mirror, the images

The operator's floor and default pin is runtime 0.28.0, and it fetches that tag from the public mirror `flg77/acc`. So the runtime release goes first, the full routine from `packaging/rpm/README.md` (*The release routine*), plus the images. 2b to 2e run from your workstation, not from acc1: the build host is the ssh alias `acc-build` (the `Host acc-build` stanza in your `~/.ssh/config`, user `acc-build` on `acc-build.ic3net.internal`, from `packaging/build-host/HOWTO.md`) and `acc-promote` is a workstation skill outside the repository; acc1 has neither, and a run started there stops at the first ssh with "Could not resolve hostname acc-build" before anything is built. Only 2a (the commit and the tag) can be done from any clone.

On a Windows workstation the bash legs (2b, 2d, 2e) run in WSL, never in PowerShell, from a clone inside WSL (not under `/mnt/c`, which turns the line endings into CRLF) and with the `acc-build` stanza in WSL's own `~/.ssh/config`; `ssh acc-build hostname` and `ssh acc1 hostname` must both answer before the pipeline is started. The alternative is to run them on acc1 after adding the same stanza there. `acc-promote` (2c) is a Claude Code skill, not a program (`docs/MIRROR-WITHHELD.md`: "the `acc-promote` skill and the release routine carry the how"): installed under the workstation's `~/.claude/skills/acc-promote`, source `acc-dev-harness/skills/acc-promote/SKILL.md`. Run `/acc-promote` in a Claude Code session opened in the acc-spearhead checkout at the tag, in PowerShell as it was for v0.27.1; it needs no WSL. It knows the runtime pair only; the operator pair (step 6) is done by hand.

**2a. The release commit and the tag**, on `acc-spearhead` `main` (it carries D-033 and the group rename since #538 and #534):

```bash
cd acc-spearhead && git checkout main && git pull
sed -i 's/^version = "0.27.0"/version = "0.28.0"/' pyproject.toml
sed -i 's/^__version__ = "0.27.0"/__version__ = "0.28.0"/' acc/__init__.py
# CHANGELOG.md: the "## [Unreleased]" heading becomes "## [0.28.0] — 2026-10-07", and a new empty "## [Unreleased]" goes above it
# (the release notes script refuses a version without a dated section)
grep -n '^## \[' CHANGELOG.md | head -3
git add -A && git commit -s -m "release: 0.28.0" && git push origin main
git tag -a v0.28.0 -m "acc 0.28.0" && git push origin v0.28.0
```

**2b. Leg 1, the package into the lab channel** (build host, verification on acc1; `rpm -q` and `acc --version` must agree):

```bash
packaging/rpm/release-pipeline.sh v0.28.0 --dry-run           # narrates every leg first
packaging/rpm/release-pipeline.sh v0.28.0 --standalone-key
```

**2c. Leg 2, the promote**: the curated commit and the tag `v0.28.0` on the mirror. This is what the operator's `make fetch-runtime` clones.

```bash
# in a Claude Code session in the checkout on the workstation:  /acc-promote
git ls-remote --tags https://github.com/flg77/acc v0.28.0        # one line, or the operator cannot fetch its pin
```

**2d. Legs 3 and 4, the public channel** (COPR from the mirror's tag, then the GitHub release on the mirror with COPR's packages and a signed `SHA256SUMS`):

```bash
packaging/rpm/release-copr.sh v0.28.0 && packaging/rpm/release-github.sh v0.28.0
```

**2e. The images.** The operator pulls, at `spec.version`, `acc-agent-core`, `acc-webgui`, `acc-tui` and `acc-runtime-evidence-bridge` from `spec.imageRepository` (default `quay.io/flg77/acc_images`), plus the pinned `nats` and `redis`. Two pipelines, both build on `acc-build` from the tag and push nothing; the push is yours:

```bash
# the agent image from the released RPM (the IN-11 provenance chain; needs 2b)
packaging/images/build-agent-rpm.sh v0.28.0                    # verifies: acc-runtime 0.28.0 inside, arbitrary UID, acc.agent imports
# every other code image from the source at the tag; skip agent-core, the RPM build above owns that tag
packaging/images/build-images.sh v0.28.0 --only webgui,tui,runtime-evidence-bridge,cli,catalog,pkg
packaging/images/build-images.sh v0.28.0 --only nats,redis --with-infra   # only if quay.io has no nats-2.10.22-alpine / redis-7.2 yet
```

Both scripts end by printing the `podman push` lines. On acc-build, logged in as the robot:

```bash
ssh acc-build 'podman login quay.io -u flg77+acc_images'
ssh acc-build 'for c in acc-agent-core acc-webgui acc-tui acc-runtime-evidence-bridge acc-cli acc-catalog acc-pkg; do podman push quay.io/flg77/acc_images:$c-0.28.0; done'
```

The build bakes the role index (`python3 -m acc.roles_index --out …`) in both agent Containerfiles, so a roles tree that does not parse fails here, not on a cluster. Not in any pipeline: `acc-kafka-bridge`, which the operator deploys only when Kafka is reachable; the lab has none.

**2f. Prove it from outside the lab**, anonymously:

```bash
podman pull quay.io/flg77/acc_images:acc-agent-core-0.28.0
podman run --rm quay.io/flg77/acc_images:acc-agent-core-0.28.0 python3 -m acc.roles_index --no-packages | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["runtime"], len(d["roles"]), "roles")'
# and the operator can fetch its pin
cd ../acc-spearhead-operator && make fetch-runtime && make check-permissions
```

- [ ] `v0.28.0` on `acc-spearhead` and on the mirror `flg77/acc`; COPR build and GitHub release on the mirror
- [ ] acc1 upgraded from the channel, `acc --version` says 0.28.0
- [ ] `acc_images:acc-agent-core-0.28.0` pulls anonymously and prints runtime `0.28.0` with the control roles
- [ ] `acc-webgui-0.28.0`, `acc-tui-0.28.0`, `acc-runtime-evidence-bridge-0.28.0` on quay.io
- [ ] `make fetch-runtime` and `make check-permissions` pass on the operator clone

## Step 3. Fold OR-14 and OR-15 into 0.3.0 (operator repository, one PR)

The recommendation from the summary, taken: the first public release ships with the image-sourced roles, the runtime floor and the channel gate. Three files change, nothing in code. On a branch of `flg77/acc-spearhead-operator`:

```bash
git checkout -b release-0.3.0-fold origin/main
grep -n '^## \|^### ' CHANGELOG.md | head -12      # [Unreleased] after 0.3.0 at line 9; [0.3.0] at 73 with Added, Changed, Fixed
```

**CHANGELOG.md.** Move the four parts of the `## [Unreleased] — after 0.3.0` section into the `## [0.3.0]` section below it, then delete the emptied heading:

1. Its *Upgrade notes* paragraph (the floor, `spec.manifestDelivery` deprecated, the ConfigMaps removed) becomes a third bullet of the 0.3.0 upgrade notes, and the existing bullet *Pair it with a runtime that knows the group* is rewritten to name the number: runtime **0.28.0 or later**, the floor, the default pin. Drop the sentence about the agents keeping running on the old operator's ConfigMaps: no 0.3.0 user has them.
2. Its `### Added` bullet (the channel gate) goes to the top of 0.3.0's `### Added`.
3. Its `### Changed` bullets (roles from the image, the index Job, the collective webhook, the floor, `sync-permissions`) go to the top of 0.3.0's `### Changed`. In the 0.3.0 `### Added`, the bullet *Built from the pinned public runtime release (OR-03)* still says the operator embeds the trees; rewrite it to say the pin names the runtime whose NATS permission matrix is vendored and that the roles come from the image.
4. Its `### Removed` becomes a new `### Removed` in 0.3.0, after `### Fixed`.
5. Leave an empty `## [Unreleased]` heading above `## [0.3.0]` for what comes next.

**README.md.** The compatibility table has two rows, `next (OR-14)` and `0.3.0`. Keep one, named `0.3.0`, with the `next` row's cells (runtime 0.28.0 by default, floor 0.28.0 with the image-sourced roles, CI versions). Delete the old `0.3.0` row.

**docs.** `git grep -n 'after 0.3.0\|next (OR-14)\|up to 0.3'` should then be empty outside the CHANGELOG history and the withheld files; `docs/migrate-from-0.2.md` already assumes the floor is in 0.3.0.

```bash
make bundle-check && .venv/bin/python -m pytest -q test/parity test/migration    # nothing generated changes; the gate still reads alpha
git add -A && git commit -s -m "CHANGELOG: fold the roles-from-image line and the channel gate into 0.3.0"
git push -u origin release-0.3.0-fold && gh pr create -R flg77/acc-spearhead-operator --fill
```

- [ ] CHANGELOG has one `## [0.3.0]` section with the three upgrade-note bullets, and an empty `## [Unreleased]` above it
- [ ] README compatibility table has one `0.3.0` row
- [ ] PR merged to `main` with `ci` and `e2e` green

## Step 4. The release commit and the tag (operator repository)

On a clone of `flg77/acc-spearhead-operator` at `main`, after step 3 is merged. The release never rewrites the bundle, so the committed bundle must already be the 0.3.0 one with a real `createdAt`.

```bash
git clone https://github.com/flg77/acc-spearhead-operator && cd acc-spearhead-operator
grep -n '^VERSION' Makefile                      # VERSION ?= 0.3.0
make bundle BUNDLE_CREATED_AT=now               # stamps createdAt; runs the channel gate (alpha / Candidate: open)
make bundle-validate-all                        # the suites both catalogs run
make helm-check                                 # the chart matches the CRDs and the CSV
git status --short                              # bundle/manifests/*.clusterserviceversion.yaml and nothing unexpected
```

Then the CHANGELOG date (the `## [0.3.0]` heading loses `unreleased` and gets today's date, step 3 already folded the rest) and the commit, signed off:

```bash
sed -i 's/^## \[0.3.0\] — unreleased (the date is set by the release commit)/## [0.3.0] — 2026-10-07/' CHANGELOG.md
grep -n '^## \[0.3.0\]' CHANGELOG.md
git add -A && git commit -s -m "release: 0.3.0"
git push origin main
```

Wait for `ci` and `e2e` on that commit (`make bundle-check` in `ci` is the same check the release runs first), then the two repository variables and the tag:

```bash
gh run list -R flg77/acc-spearhead-operator -b main -L 4           # ci, e2e green on the release commit
gh variable set RUNTIME_E2E -R flg77/acc-spearhead-operator -b true   # e2e now waits for a Ready corpus and a Known role index (needs step 2 done)
git tag -a v0.3.0 -m "acc-operator 0.3.0" && git push origin v0.3.0
gh run watch -R flg77/acc-spearhead-operator $(gh run list -R flg77/acc-spearhead-operator -w release -L 1 --json databaseId -q '.[0].databaseId')
```

What `release.yml` does on the tag, in order, so you know where a failure sits:

1. `make bundle-check VERSION=0.3.0`: refuses unless the committed bundle is this version.
2. logs in to quay.io with the two secrets, builds and pushes `acc-operator` and `acc-console-plugin` for linux/amd64 and linux/arm64, then `acc-operator-bundle`.
3. `make helm-push`: the chart to `oci://quay.io/flg77/acc-operator-chart:0.3.0`.
4. cosign keyless, by digest, on the three images and the chart (GitHub OIDC; no key to manage).
5. `preflight check container` on the operator image, advisory, as a workflow artifact.
6. the GitHub release `v0.3.0` with the bundle tarball, the catalog-submissions tarball, the chart package and the 0.3.0 CHANGELOG section as notes.

- [ ] `ci` and `e2e` green on the release commit
- [ ] `release` green; four tags on quay.io: `acc-operator:0.3.0`, `acc-console-plugin:0.3.0`, `acc-operator-bundle:0.3.0`, `acc-operator-chart:0.3.0`
- [ ] the GitHub release lists three assets

If the queued jobs are cancelled with no log (the runner starvation seen on 2026-10-05), re-run the workflow from the Actions tab; nothing else is needed. If `release` fails after some images were pushed, fix, delete the tag (`git push origin :refs/tags/v0.3.0`), re-tag the fixed commit: the pushes are idempotent.

## Step 5. Verify the public artefacts, then migrate the lab

**Signatures**, from any machine with `cosign`:

```bash
V=0.3.0
for i in acc-operator acc-console-plugin acc-operator-bundle acc-operator-chart; do
  cosign verify quay.io/flg77/$i:$V \
    --certificate-identity-regexp 'github.com/flg77/acc-(spearhead-)?operator' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com >/dev/null && echo "signed: $i"
done
```

**Kubernetes**, a kind cluster (this is the README quickstart a stranger will run):

```bash
kind create cluster --name acc
operator-sdk olm install --version 0.31.0
kubectl create namespace acc-system
operator-sdk run bundle quay.io/flg77/acc-operator-bundle:$V -n acc-system --timeout 10m
kubectl -n acc-system apply -f config/samples/acc_v1alpha1_agentcorpus_standalone.yaml   # from the operator clone; the Category-A ConfigMap it needs is in docs/install.md
kubectl -n acc-system wait agentcorpus/sol-corpus --for=condition=RoleIndexKnown --timeout=10m
kubectl -n acc-system wait agentcorpus/sol-corpus --for=jsonpath='{.status.phase}'=Ready --timeout=15m
kubectl -n acc-system get agentcorpus sol-corpus -o jsonpath='{.status.roleIndex.runtime}{"\n"}'   # 0.28.0
operator-sdk cleanup acc-operator -n acc-system && kind delete cluster --name acc
```

**Helm**, the same cluster shape without OLM (cert-manager first):

```bash
kind create cluster --name acc-helm
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
kubectl -n cert-manager wait deploy --all --for=condition=Available --timeout=5m
helm install acc-operator oci://quay.io/flg77/acc-operator-chart --version $V -n acc-system --create-namespace
kubectl -n acc-system rollout status deploy/acc-operator-controller-manager
# the same sample and waits as above, then
helm -n acc-system uninstall acc-operator && kind delete cluster --name acc-helm
```

**OpenShift 4.14 or later** (CRC or a lab cluster): the same `run bundle`, plus the console plugin (`oc get consoleplugin acc-console-plugin`) and the operator's tile showing the four alm-examples under *Create*. On the lab clusters this step is the migration itself, which installs 0.3.0 from the public bundle.

**The lab migration**, bb3 first, lighthouse on another day. The public procedure is `docs/migrate-from-0.2.md`; the cluster-specific sequence (Subscription `acc-operator`, CatalogSource `acc-catalog` in `openshift-marketplace`, the old CRDs) is in `docs/howto-operator-ops.md`, section *The 0.3.0 cut-over on the lab clusters*. In one screen, from acc1 logged in to the cluster:

```bash
cd /git/development/agentic/acc-spearhead/operator && git pull
D=/git/migration-0.3.0/bb3
python3 hack/migrate_api_group.py export $D
python3 hack/migrate_api_group.py rewrite $D --version 0.28.0
diff <(python3 -m json.tool $D/old/agentcorpora.json) <(python3 -m json.tool $D/new/agentcorpora.json)
oc delete accpackageinstalls.acc.redhat.io,agentcollectives.acc.redhat.io,agentcorpora.acc.redhat.io,acccatalogs.acc.redhat.io -A --all
oc -n acc-system delete subscription acc-operator
oc -n acc-system delete csv -l operators.coreos.com/acc-operator.acc-system
oc -n openshift-marketplace delete catalogsource acc-catalog
oc delete crd agentcorpora.acc.redhat.io agentcollectives.acc.redhat.io acccatalogs.acc.redhat.io accpackageinstalls.acc.redhat.io
operator-sdk run bundle quay.io/flg77/acc-operator-bundle:0.3.0 -n acc-system
oc -n acc-system rollout status deploy/acc-operator-controller-manager
python3 hack/migrate_api_group.py apply $D --dry-run
python3 hack/migrate_api_group.py apply $D          # a second time once the corpora report RoleIndexKnown, if a collective was refused
python3 hack/migrate_api_group.py verify $D         # exit 0: every object back, no PVC lost
```

- [ ] four `signed:` lines
- [ ] kind: `sol-corpus` Ready, role index runtime `0.28.0`, from the public bundle
- [ ] Helm: the same on a cluster without OLM
- [ ] OpenShift: console plugin present, four examples under Create
- [ ] bb3 migrated, `verify` exit 0, fleet-logged; lighthouse the same on another day

## Step 6. The public repository `flg77/acc-operator`

One curated commit per release, squashed onto the mirror's `main` from the release tag, minus the ten withheld paths listed in `docs/MIRROR-WITHHELD.md`. `acc-promote` does this for the runtime pair; it does not know the operator pair yet, so the first release is done by hand with the commands below (teach `acc-promote` the pair afterwards, with this list as its withheld set).

```bash
cd acc-spearhead-operator
git remote add mirror https://github.com/flg77/acc-operator.git      # the remote name stays `mirror` (D-032)
git fetch mirror 2>/dev/null || true                                  # empty on the first release
git worktree add ../acc-operator-curated v0.3.0 --detach && cd ../acc-operator-curated

# the withheld list, verbatim from docs/MIRROR-WITHHELD.md
git rm -rq hack/build-remote.sh hack/deploy-private-catalog.sh hack/acc-operator-ops.sh \
  config/private-catalog config/samples/loan-origination \
  docs/private-quay-to-ocp-catalog-deploy.md docs/rhoai-e2e-agentset-runbook.md docs/howto-operator-ops.md \
  docs/SPEARHEAD.md docs/MIRROR-WITHHELD.md

# nothing promoted may point at something withheld, and no Red Hat identifier
git grep -nEi "build-remote|deploy-private-catalog|acc-operator-ops|private-catalog|loan-origination|private-quay|rhoai-e2e-agentset|howto-operator-ops|MIRROR-WITHHELD|SPEARHEAD\.md|spearhead-operator/|the spearhead" -- . ':!CHANGELOG.md' && echo "STOP: fix the pointer on main first" || echo "clean"
git grep -nEi 'redhat\.io|redhat-ai-dev|vendor="Red Hat"' -- . ':!CHANGELOG.md' && echo "STOP" || echo "clean"

# the first release: one commit with no history
git checkout --orphan public && git add -A
git -c user.name='<your GitHub name>' -c user.email='<the email on your GitHub account>' commit -s -q -m "acc-operator 0.3.0"
git diff --stat v0.3.0 HEAD | tail -1                                 # exactly the withheld paths, deleted
git push mirror HEAD:main
git tag -a v0.3.0 -m "acc-operator 0.3.0" && git push mirror v0.3.0  # the CSV's repository and provider links resolve to this tag
cd ../acc-spearhead-operator && git worktree remove ../acc-operator-curated
```

Before pushing the tag: `release.yml` is in the curated tree too and fires on `v*` there. Either set the same two secrets on `flg77/acc-operator` first, so its run reproduces the same images (a second keyless signature on the same digests, which the verify regexp in step 5 already accepts), or expect that one run to fail at the registry login and ignore it. Setting the secrets is the cleaner choice:

```bash
gh secret set REGISTRY_USERNAME -R flg77/acc-operator -b 'flg77+acc_images'
gh secret set REGISTRY_PASSWORD -R flg77/acc-operator       # paste the robot token
```

Branch protection on the mirror, once, after the first `ci` and `e2e` runs there have reported:

```bash
gh api -X PUT repos/flg77/acc-operator/branches/main/protection \
  -f required_status_checks.strict=true -f 'required_status_checks.contexts[]=ci' -f 'required_status_checks.contexts[]=e2e' \
  -F enforce_admins=false -F required_pull_request_reviews=null -F restrictions=null
```

Later releases: the same, but instead of `--orphan` start from `mirror/main` (`git checkout -b public mirror/main && git rm -rq . && git checkout v0.3.1 -- . && <the git rm of the withheld list>`), so the mirror keeps one commit per release.

- [ ] both greps print `clean`
- [ ] `flg77/acc-operator` `main` has one commit, tagged `v0.3.0`, with `ci` and `e2e` green
- [ ] the README's links and the CSV's `repository` URL resolve

## Step 7. The two catalog PRs

Once, before the first: fork `k8s-operatorhub/community-operators` and `redhat-openshift-ecosystem/community-operators-prod` under `flg77` (the account in `CATALOG_REVIEWERS`, so a PR by it merges on green). Then, from the operator clone at the `v0.3.0` tag:

```bash
git checkout v0.3.0
make catalog-submissions                 # dist/catalog-submissions/, the same tree the GitHub release carries
find dist/catalog-submissions -maxdepth 6 -type d
```

**operatorhub.io** (Kubernetes):

```bash
gh repo fork k8s-operatorhub/community-operators --clone=false
git clone https://github.com/flg77/community-operators ../community-operators && cd ../community-operators
git checkout -b acc-operator-0.3.0 origin/main
cp -R ../acc-spearhead-operator/dist/catalog-submissions/k8s-operatorhub/community-operators/operators/acc-operator operators/
git add operators/acc-operator
git -c user.name='<your GitHub name>' -c user.email='<the email on your GitHub account>' commit -s -m "operator acc-operator (0.3.0)"     # DCO: author and sign-off must match
git push -u origin acc-operator-0.3.0
gh pr create -R k8s-operatorhub/community-operators --title "operator acc-operator (0.3.0)" --body "First release of the ACC operator. Bundle: quay.io/flg77/acc-operator-bundle:0.3.0 (cosign keyless). Update graph: semver-mode."
cd -
```

**OpenShift** (the community catalog in the console):

```bash
gh repo fork redhat-openshift-ecosystem/community-operators-prod --clone=false
git clone https://github.com/flg77/community-operators-prod ../community-operators-prod && cd ../community-operators-prod
git checkout -b acc-operator-0.3.0 origin/main
cp -R ../acc-spearhead-operator/dist/catalog-submissions/redhat-openshift-ecosystem/community-operators-prod/operators/acc-operator operators/
git add operators/acc-operator
git -c user.name='<your GitHub name>' -c user.email='<the email on your GitHub account>' commit -s -m "operator acc-operator (0.3.0)"
git push -u origin acc-operator-0.3.0
gh pr create -R redhat-openshift-ecosystem/community-operators-prod --title "operator acc-operator (0.3.0)" --body "First release of the ACC operator. Bundle: quay.io/flg77/acc-operator-bundle:0.3.0 (cosign keyless). Channels from release-config.yaml (Candidate); FBC for v4.14 to v4.22."
cd -
```

What each pipeline checks, all met by the generated tree: the version directory equals the CSV `spec.version`; `ci.yaml` valid (`updateGraph: semver-mode` on Kubernetes; the FBC catalog mapping on OpenShift); `release-config.yaml` naming the Candidate channel; the required CSV fields; `operator-sdk bundle validate` with the operatorframework suite. On OpenShift the pipeline creates the `semver.yaml` template itself and opens the catalog PR; `/pipeline restart operator-hosted-pipeline` as a PR comment re-runs a failed pipeline. Review queues take hours to days, up to a week.

- [ ] PR open on `k8s-operatorhub/community-operators`, pipeline green, merged
- [ ] PR open on `redhat-openshift-ecosystem/community-operators-prod`, hosted pipeline green, the follow-up catalog PR merged
- [ ] https://operatorhub.io/operator/acc-operator shows 0.3.0 with the description, the icon and the four examples

## Step 8. ArtifactHub, and done when

Once per repository, in the browser: https://artifacthub.io, *Control panel*, *Repositories*, *Add*; kind *Helm charts*, URL `oci://quay.io/flg77/acc-operator-chart`, name `acc-operator`. ArtifactHub indexes every version tag from then on; `Chart.yaml` already carries the `artifacthub.io/*` annotations. Verified-publisher status (optional) wants an `artifacthub-repo.yml` with the repository ID it assigns, pushed beside the chart.

```bash
# a day later
curl -s https://artifacthub.io/api/v1/packages/helm/acc-operator/acc-operator | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'   # 0.3.0
```

**Done when**, the same list as OR-00:

- [ ] https://operatorhub.io/operator/acc-operator lists 0.3.0
- [ ] an OpenShift 4.14 or later console with the community catalog shows the ACC tile and installs it from channel `candidate-v0`
- [ ] `helm install acc-operator oci://quay.io/flg77/acc-operator-chart --version 0.3.0` on kind with cert-manager yields a Ready corpus (step 5), and the chart is listed on ArtifactHub
- [ ] `flg77/acc-operator` has the tagged curated commit; the GitHub release on `flg77/acc-spearhead-operator` carries the two tarballs and the chart package
- [ ] on the public repository: `git grep -nEi 'redhat\.io|redhat-ai-dev|vendor="Red Hat"'` and the withheld-pointer grep from `MIRROR-WITHHELD.md` are both empty

**Afterwards**, not gating: Dependabot's console-plugin PRs on the operator repository; a stranger installs on kind from the README without help (OR-10's last box); the OpenShift check of the console plugin on a cluster (OR-05's last box); bb3 and lighthouse migrated (OR-04 closes).
