# Live acc1 smoke — operator runbook

After PRs #20–#28 land on `main`, this is the one-shot sequence the
operator runs to validate the full ecosystem chain against the
internal acc1 Kubernetes hub.

## When to run

* After merging the Stage 0 + Stage 1 PR stack
* After redeploying `gitops/acc-hub/` to acc1
* As a smoke when a new pilot role lands and you want to prove the
  pipeline still works end-to-end against live infra

## Prerequisites (one-time)

| Tool | Where to get it | Notes |
|---|---|---|
| `cosign` | <https://docs.sigstore.dev/cosign/installation/> | The signing-floor enforcer |
| `kubectl` | Distro package or <https://kubernetes.io/docs/tasks/tools/> | Must point at acc1 |
| `jq` | Distro package | Index ConfigMap edits |
| `python` + `acc-pkg` | `pip install -e .` from repo root | Console script entry from `pyproject.toml` |

## Running

```bash
# From the repo root, on a kubectl context pointing at acc1:
bash tools/smoke-acc1-hub.sh
```

Defaults:

* role = `coding_agent`
* version = `0.1.0`
* hub URL = `https://acc-hub.acc1.internal`
* keys dir = `~/.acc/keys`

Overrides:

```bash
bash tools/smoke-acc1-hub.sh \
  --role research_planner \
  --version 0.2.0 \
  --hub-url https://acc-hub.test.internal
```

## What the script does

| Phase | Step |
|---|---|
| 0 | Preflight — checks `cosign`, `kubectl`, `python`, `jq`, `curl`, `acc-pkg` on PATH |
| 1 | Deploys `gitops/acc-hub/` if not present; waits for rollout; curls `/index.json` |
| 2 | Generates cosign pilot keypair via `tools/cosign-pilot-keygen.sh` if not on disk |
| 3 | Builds pilot pkg via `tools/build_pilot_pkg.py`; signs with `cosign sign-blob`; pushes to hub via `gitops/acc-hub/publish-to-hub.sh`; re-fetches index.json and asserts the new entry |
| 4 | Downloads tarball + sig from the live hub; runs `acc-pkg install` into a tmp sandbox root; cosign-verifies against the pilot pubkey |
| 5 | `RoleLoader` resolves the role from the installed package (not in-tree); printed audit log proves dual-source resolution path |

Exit codes mirror the `acc-pkg` CLI contract (0–6), plus two
smoke-specific codes:

* `7` — hub deployment / network failure
* `8` — round-trip verification failure (Phase 5 assertion)

## Hermetic CI equivalent

`tests/pkg/test_live_smoke_hermetic.py` runs the same five phases
in-process against a file-mode catalog with mocked cosign so PR-time
CI exercises the chain without touching acc1.  The bash script's
`tools/smoke-acc1-hub.sh` references the same Python helpers
(`build_pilot_pkg`, `RoleLoader`, `ACC_PACKAGES_ROOT`); a sanity
test confirms the script is committed + references the right
entry points.

## What this script does NOT exercise

* The Stage 1.6 operator reconciler (`AccPackageInstall` /
  `AccCatalog` CRs) — deferred to Stage 1.6b along with the live
  exec-into-pod plumbing.  Until that ships, the operator drives
  installs via this script; afterward, GitOps drives them.
* The Compliance pane "Package proposals" TUI/WebGUI tab — the
  dispatch logic is wired in #24, the visual surface is deferred.
* Konflux pipeline (`gitops/tekton/pipelines/accpkg-build.yaml`
  from #27) — that's the CI-side build+sign+publish replacement for
  the manual cosign + publish-to-hub steps in this script.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `cosign: command not found` | Install cosign first (see Prerequisites). |
| `kubectl get ns acc-hub` returns "NotFound" | Hub not deployed — script applies it; check `kubectl auth can-i` for namespace create. |
| `acc-hub deployment did not become ready` (exit 7) | PVC not bound, ingress missing TLS cert, or image pull issue.  `kubectl -n acc-hub describe pod` first. |
| `cosign sign-blob failed` (exit 5) | Pilot key passphrase mismatch — set `COSIGN_PASSWORD=""` for an unencrypted dev key. |
| `acc-pkg install failed (rc=5)` | Signer mismatch — the catalog's `required_signer.key_path` doesn't point at the pubkey that signed.  Re-sync `~/.acc/catalogs.yaml`. |
| `RoleLoader did not resolve from installed package` (exit 8) | Cache hit on the in-tree role.  Clear `ACC_PACKAGES_ROOT` and rerun. |
