---
type: backlog-item
area: operator-release
id: OR-07
status: done
size: M
depends: [OR-03, OR-05]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-07 — CI in GitHub Actions

**What.** In the public repo, `ubuntu-latest`, no self-hosted runner.
- `ci.yml`: `make build`, `make test` (envtest), `make bundle && git diff --exit-code`, `operator-sdk bundle validate ./bundle --select-optional suite=operatorframework`, `operator-sdk scorecard ./bundle` on kind.
- `e2e.yml`: kind + `operator-sdk olm install` + `operator-sdk run bundle` + the sample `AgentCorpus` Ready (Ollama, small CPU model).
- `release.yml` on `v*`: buildx `linux/amd64,linux/arm64` for operator, bundle, console plugin; push; cosign; `preflight check container` advisory; GitHub release with a `bundle/` tarball.
- Branch protection.

**Why.** This is the independence from the build infrastructure the release needs: `acc1`/`bb3`/`lighthouse` keep running the lab and stop being a prerequisite.

**Done when.** A tag produces signed public images and a validated bundle with no human step but the tag.

**2026-10-05 — written** (acc-spearhead-operator, branch `or-07-ci`, stacked on OR-04 → OR-05 → OR-03). Three workflows: `ci` (build/vet/envtest, generated code committed, `make bundle-check`, optional validators advisory until OR-08, parity tests, console plugin build), `e2e` (kind + local registry + OLM, `run bundle` with the run's images, CSV Succeeded, CRDs, defaulting webhook + CRD validation, console plugin no-op, scorecard basic/olm), `release` on `v*` (bundle-check for the tag's version, buildx amd64+arm64, cosign keyless, preflight advisory, GitHub release). Plus `dependabot.yml` (gomod, npm, actions) and the Containerfile cross-compiling per `TARGETARCH`. Linted with actionlint; every Makefile target the workflows call was run locally. **No runner has executed them yet** (this session has no Docker daemon and cannot trigger Actions on a draft PR of a stacked branch): expect one round of fixes. The corpus-Ready wait in `e2e` is gated on `RUNTIME_E2E=true` until OR-06 makes the runtime images public. Branch protection is the maintainer's click.

**2026-10-05 — green.** PR #8 on acc-spearhead-operator: `ci` and `e2e` pass. Three fixes the runner found that this session's container could not: controller-tools v0.16.1 does not compile under Go 1.25 (its x/tools v0.24.0; → v0.16.5, output identical but the version annotation), docker needs `-f Containerfile` (podman finds it by default), `operator-sdk run bundle --use-http` for the plain-HTTP kind registry. The bundle installs through OLM on vanilla Kubernetes; the defaulting webhook and CRD validation answer; the console plugin runnable stays a no-op; scorecard basic passes. Open: branch protection (maintainer click); `RUNTIME_E2E=true` once OR-06 makes the runtime images public; `release.yml` has not run (needs a tag and the OR-06 secrets).
