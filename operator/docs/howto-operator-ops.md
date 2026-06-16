# How-to: operate the ACC operator on RHOAI/OLM with `acc-operator-ops.sh`

A field guide to `operator/hack/acc-operator-ops.sh` — the repeatable runbook for
diagnosing, shipping, and recovering the ACC operator on the OpenShift/RHOAI
cluster fronted by **acc1**. It grew out of the 2026-06-16 session where the
`acc-demo-coding-webgui` pod crash-looped; this doc explains both the tooling and
the model it encodes so the next incident is a one-liner.

> Companion docs: [`private-quay-to-ocp-catalog-deploy.md`](private-quay-to-ocp-catalog-deploy.md)
> (the canonical image→bundle→catalog build, which this script mirrors) and the
> OpenSpec change `openspec/changes/20260616-webgui-signaling-and-observer-resilience/`.

---

## The mental model (read this once)

The operator is **OLM-managed**, not a plain Deployment:

```
Subscription acc-operator ──> CatalogSource acc-catalog ──> CSV acc-operator.vX
        (channel alpha,                (grpc index image            (owns the
         Automatic approval)            on quay)                     controller Deployment)
```

Three consequences drive everything below:

1. **You cannot fix the operator by editing its Deployment.** The CSV owns it and
   OLM reconciles the Deployment *back* from the CSV — exactly the same way the
   operator reconciles the webgui Deployment back from the `AgentCorpus`. The
   only durable lever is **a new index image in the CatalogSource**.
2. **The trigger is one `oc patch`.** Point the CatalogSource at a higher index;
   because the Subscription is `Automatic`, OLM creates+approves the InstallPlan
   and rolls the upgrade with no further input.
3. **The upgrade graph must connect.** The new bundle's CSV needs
   `replaces: acc-operator.v<previous>`, and the new index should be built
   `--from-index` the previous one. The script handles both.

Everything lives in one public repo, `quay.io/flg77/acc_images`, with
component-prefixed tags: `acc-operator-<VER>`, `acc-operator-bundle-<VER>`,
`acc-operator-index-<VER>`.

### Why a webgui change ships as an *operator* release

The webgui pod is generated *by the operator* (`operator/internal/reconcilers/ui/webgui.go`).
A fix to how its Deployment is templated (e.g. injecting `ACC_NATS_URL`) is an
operator-code change. You rebuild the **operator** image and let it re-template
the webgui Deployment — you do **not** rebuild the webgui image, and you do not
bump the corpus.

---

## Prerequisites

- **SSH to acc1.** The workstation has no `oc`/`podman`/Go; the script runs every
  cluster/build step on acc1 over SSH (key `~/.ssh/rsa-key-acc1`). Running *on*
  acc1? Set `ACC_LOCAL=1` to skip SSH.
- **`podman login quay.io`** on the build host — for `build`/`hotpatch`/`release`.
  The script guards on this and will not (cannot) log in for you. Pushing
  private-source images is intentionally a human step (the agent exfil classifier
  blocks it).
- **`oc login`** on acc1 with rights to patch the CatalogSource + read the demo
  namespaces — for `ship`/`verify`/`unblock`.
- The shared `/git` checkout on acc1 at `/git/development/agentic/acc-spearhead`
  (the script auto-derives `REPO` from its own location).

---

## Command reference

Run `acc-operator-ops.sh -h` for the summary, or `acc-operator-ops.sh <cmd> -h`
for any command's detail. Severity tags below: **[read-only]**, **[quay]** (pushes
images), **[cluster]** (mutates the cluster).

### `diagnose [deploy] [container]` — *[read-only]*
Default `acc-demo-coding-webgui webgui`. The standard CrashLoopBackOff SOP:
1. resolve the newest pod of the deployment;
2. per-container `ready / restartCount / lastExit` (a 2-container webgui pod hides
   *which* half failed — this surfaces it; here it was `webgui` exit 3, sidecar fine);
3. **current and previous** (`-p`) logs — a crash loop means the live log is often
   mid-backoff, so the previous instance carries the real stack trace;
4. the container's runtime env;
5. a diff against the healthy **TUI's** NATS env — a missing/wrong `ACC_NATS_URL`
   jumps straight out.

### `preflight` — *[safe]*
`make docker-build` on acc1 with a throwaway local tag, no push. The Go build runs
inside the Containerfile's `go-toolset` stage, so it compiles your current checkout
and proves the image packages before you spend a real version number.

### `build` — *[quay]*
Needs `VER` and a prior `podman login quay.io`. Mirrors the proven
`deploy-private-catalog.sh`, retargeted to public quay:
1. operator image → `$REG:acc-operator-$VER` (`make docker-build` + push);
2. bundle manifests regenerated at `$VER` **inside the go-toolset container**
   (acc1 has no host `kustomize`/`operator-sdk`); CSV `replaces` forced to the
   live version so the upgrade edge exists;
3. bundle image → `$REG:acc-operator-bundle-$VER`;
4. index → `$REG:acc-operator-index-$VER`, built `--from-index` the previous index.

`PREV` auto-detects from the live CSV; override with `PREV=x.y.z`.

### `ship` — *[cluster]*
Patches the CatalogSource to `index-$VER`, then polls until
`installedCSV == acc-operator.v$VER` and `phase == Succeeded`. Ctrl-C is safe — OLM
continues server-side.

### `verify` — *[read-only]*
Operator CSV version + the webgui Deployment's `ACC_NATS_URL`/`ACC_COLLECTIVE_IDS`
(the fix) + webgui pod `2/2 Running`.

### `release` — *[quay + cluster]*
`build → ship → verify`, then appends a one-line entry to the FLEET decisions log
and pushes it. `PREV` is captured once up front so the build's upgrade edge and the
log agree. Opt out of the log with `FLEET_LOG=0`. **The normal path.**

### `hotpatch` — *[quay + cluster]*
Fast dev iteration: build+push only the operator image (tag `…-$VER-dev`) and patch
the **CSV's** embedded deployment image; OLM reconciles the Deployment from the CSV
and re-pulls (`imagePullPolicy=Always`). Skips bundle/index. **Not** a real OLM
version — a CatalogSource re-sync reverts it. Use `release` to ship for real.

### `unblock [deploy]` / `rebuild-operator-up` — *[cluster]*
Emergency stop-the-crash-loop with no new image: scale the operator to 0 (so it
stops reverting the Deployment) and inject `ACC_NATS_URL`/`ACC_COLLECTIVE_IDS`.
**Caveat:** this pauses reconciliation for *every* corpus and is undone the moment
you `rebuild-operator-up`. Prefer `release`.

### `fleet-log "<message>"`
Manually append a dated entry to the FLEET decisions log and push (pull-before-edit,
append-only). `release` calls it automatically.

---

## Typical flows

**Investigate a crash:**
```bash
operator/hack/acc-operator-ops.sh diagnose
```

**Ship an operator fix (e.g. PR #98) as 0.2.10:**
```bash
VER=0.2.10 operator/hack/acc-operator-ops.sh preflight     # optional sanity
#   on acc1, your pane:   podman login quay.io
VER=0.2.10 operator/hack/acc-operator-ops.sh release        # build → ship → verify → fleet-log
```

**Buy time before the image is ready:**
```bash
operator/hack/acc-operator-ops.sh unblock
# ... later, once the real operator is shipped:
operator/hack/acc-operator-ops.sh rebuild-operator-up
```

---

## Safe vs. mutating, at a glance

| Command | Reads | Pushes images | Mutates cluster |
|---|:--:|:--:|:--:|
| `diagnose`, `verify`, `preflight` | ✅ | — | — |
| `build` | ✅ | ✅ | — |
| `ship`, `unblock`, `rebuild-operator-up` | ✅ | — | ✅ |
| `release`, `hotpatch` | ✅ | ✅ | ✅ |

---

## Troubleshooting

- **`NOT LOGGED IN`** from `build`/`hotpatch` → run `podman login quay.io` on acc1.
- **`could not detect PREV`** → the live CSV query found nothing; pass `PREV=x.y.z`.
- **`ship` never reaches Succeeded** → check the InstallPlan:
  `oc -n acc-system get installplan` and the CatalogSource pod in
  `openshift-marketplace` (a bad index image leaves the catalog `CONNECTING`).
- **`make bundle` tooling balks in the container** → fall back to the `opm render`
  FBC path in `hack/deploy-private-catalog.sh build`.
- **webgui still crash-loops after `ship`** → run `verify`; if the env is present
  but the pod is still down, `diagnose` the *previous* logs for a new cause.

---

## Fleet integration

`release` (and the manual `fleet-log`) append to
`acc-dev-harness/coordination/FLEET.md` — pull-before-edit, append-only, pushed
from wherever the script runs (where your git auth lives). Set `FLEET_LOG=0` to
skip, or `ACC_HARNESS_DIR` to relocate the ledger. See the **acc-fleet** skill.
