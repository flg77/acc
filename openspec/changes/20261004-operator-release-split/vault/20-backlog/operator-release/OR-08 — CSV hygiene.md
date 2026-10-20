---
type: backlog-item
area: operator-release
id: OR-08
status: done
size: S
depends: [OR-04, OR-05]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-08 — CSV hygiene

**What.** `createdAt` set by `make bundle`; one operator-sdk version in `builder` and `metadata/annotations.yaml` (1.34 vs 1.36 today); `repository` annotation; `links`, `provider.url`, `maintainers` → the new repo; `support` kept honest; `description` opens with *govern, gate, audit*, then the not-Red-Hat disclaimer; `com.redhat.openshift.versions` matched to what CI runs; `minKubeVersion`; alm-examples = one standalone `AgentCorpus` with Ollama (no RHOAI, no MaaS), one `AgentCollective`, one `AccCatalog`, one `AccPackageInstall`, all admitting on kind; no `replaces`/`skips` in 0.3.0; `ci.yaml` `updateGraph: semver-mode`.

**Done when.** `bundle validate` with optional suites is clean and the four examples apply on kind.

**2026-10-05 — done** (acc-spearhead-operator, branch `or-08-csv-hygiene`, stacked on OR-06). The CSV leads with *govern, gate and audit AI agents*, names the four resources, links operator / runtime / ecosystem, `repository` + `provider` + `maintainers` on `flg77/acc-operator`, disclaimer at the end. alm-examples rewritten to things that install: core CONTROL roles (the old sample named `analyst`/`synthesizer`, which are package roles), the real `acc-ecosystem` catalog URL (the old sample pointed at a non-existent domain), one package install; no namespaces; `e2e` applies all four from the installed CSV. `com.redhat.openshift.versions: v4.14` open-ended. Found for OR-12: `known_roles.txt` (the collective webhook's catalogue) is stale against the roles tree and its generator reads a path that no longer exists.

**2026-10-05 — follow-up on the same branch (PR #10 green: ci + e2e).** The first e2e run failed before the install: the OLM registry pod that serves the bundle crash-looped and the install plan never appeared; the job collected nothing from that pod. The bundle content, rendered as `operator-sdk run bundle` renders it (operator-registry 1.39.0), serves under opm 1.47.0 and 1.74.0; a control run of OR-06's e2e on the same day passed; the re-run passed. Not reproduced. The e2e diagnostics now print events and current/previous logs of every pod in the namespace. Found on the way: the optional validators had been failing silently as advisory since OR-06 (`name=operatorhub/v2` is not a valid label value; the operatorframework suite already contains operatorhubv2, capabilities, categories, alpha-deprecated-apis, good-practices) — `make bundle-validate-all` runs that suite plus `community` and blocks `ci`; the CSV declares `linux/amd64` and `linux/arm64` (`operatorframework.io/arch.*`, `os.linux` labels, flagged by the multiarch validator); the description's markdown table became a list and `make bundle` refuses a CSV containing `---`, which `operator-sdk run bundle` splits the rendered catalog on (harmless while the CSV is base64 in the catalog, fatal once it is rendered as `olm.csv.metadata`). Still advisory: the scorecard `olm` suite in `e2e` — its output is dominated by the `olm-crds-have-resources` CRD dump; read it in OR-12 and decide whether to make the suite blocking.
