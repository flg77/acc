---
type: backlog-item
area: operator-release
id: OR-06
status: in-progress
size: S
depends: [OR-01]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-06 — Public registry and signing

**What.** A public Quay organisation; repositories `acc-operator`, `acc-operator-bundle`, `acc-console-plugin`; a robot account with push, stored as a GitHub Actions secret; cosign keyless (GitHub OIDC) on every pushed image. CSV `containerImage`, `relatedImages` and deployment images repointed; CRD `imageRepository` default and its 'PRIVATE repository' comment updated; the `imagePullSecrets` default removed (field stays).

**Note.** The runtime images must be public too for a stranger's `AgentCorpus` to come up — that is [[IN-11]] phase 2 (publishing), not this item. The operator's listing with private runtime images is a listing nobody can use.

**Done when.** `podman pull` of the three images works anonymously; `cosign verify` passes.

**2026-10-05 — code done** (acc-spearhead-operator, branch `or-06-public-registry`, stacked on OR-07 → OR-04 → OR-05 → OR-03). `IMAGE_ORG = quay.io/flg77`; one public repository per operator image (`acc-operator`, `acc-operator-bundle`, `acc-console-plugin`, `acc-operator-index`); `release.yml` logs in to the registry host, pushes and cosign-signs by digest; no pull secret on the manager; the CRD's `imagePullSecrets` default is gone and `imageRepository` (`quay.io/flg77/acc_images`, the runtime images) is documented public. Bonus: `spec.version` defaults to the runtime the operator was built for (`ACC_RUNTIME_VERSION` via `-ldflags -X`), so a corpus without a version deploys the runtime whose roles and permissions the operator embeds. Found: operator-sdk already lists `RELATED_IMAGE_*` as `relatedImages` (since OR-05) — the OR-03 sed had produced a duplicate key; and the OR-04 base still carried `replaces`/`skips` — gone now.

**Maintainer steps (quay.io + GitHub, not scriptable from here):**
1. quay.io: create **public** repositories `flg77/acc-operator`, `flg77/acc-operator-bundle`, `flg77/acc-console-plugin` (a first push would auto-create them *private*); make `flg77/acc_images` public.
2. quay.io: a robot account (e.g. `flg77+acc_operator_ci`) with **write** on those four.
3. GitHub `acc-spearhead-operator` → Settings → Secrets: `REGISTRY_USERNAME`, `REGISTRY_PASSWORD` (the robot). Variable `IMAGE_ORG` only if not `quay.io/flg77`.
4. Once `acc_images` is public and carries the pinned runtime version (`acc-agent-core-0.27.0`, …): variable `RUNTIME_E2E=true` so `e2e` waits for a Ready corpus.
No organisation needed for the community catalogs; `IMAGE_ORG` moves everything later.

**2026-10-05 — PR #9 green** on the first run (`ci` + `e2e`). The e2e webhook check confirms the linker-set default: a corpus created without `spec.version` reads back `0.27.0`, the pin the image was built with. Only the quay.io/GitHub steps above remain.
