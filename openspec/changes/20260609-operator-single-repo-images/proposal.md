# Proposal: Single-repository image addressing for the ACC operator

## Problem

The operator builds every image reference as `<spec.imageRegistry>/<name>:<tag>` with
the image **name** hardcoded per component (`acc-agent-core`, `nats`, `redis`,
`acc-kafka-bridge`, `acc-runtime-evidence-bridge`). This assumes the target registry can
host many distinctly-named repositories. Some environments cannot: the `rh-ai-apps`
sandbox can only publish to a **single private Quay repository** (`quay.io/flg77/acc_images`),
where images must be distinguished by **tag**, not repository name. As a result the
collective never leaves `ImagePullBackOff`. A latent bug compounds this: `nats.go` and
`redis.go` hardcode `storageClass := ""` and emit PVC `storageClassName: ""`, which
disables dynamic provisioning, and `nats.go` ignores the existing
`spec.infrastructure.nats.storageClass` field.

## Current behavior

`imageRegistry` (default `registry.access.redhat.com`) is prefixed onto fixed image
names. There is no way to address all components within one repository. PVCs are created
with `storageClassName: ""` and never bind.

## Desired behavior

An optional `spec.imageRepository` lets operators publish every component to one repo,
addressed as `<imageRepository>:<component>-<tag>` (e.g.
`quay.io/flg77/acc_images:acc-agent-core-0.1.0`, `:nats-2.10-alpine`, `:redis-6-alpine`).
When unset, current behavior is unchanged. Private repositories are supported via optional
`spec.imagePullSecrets` rendered onto pods. NATS/Redis PVCs honor a configurable storage
class and otherwise omit `storageClassName` so the cluster default applies.

## Success criteria

- [ ] With `imageRepository` set, all rendered pods reference `<repo>:<component>-<tag>`.
- [ ] With `imageRepository` unset, rendered references are byte-identical to today.
- [ ] NATS/Redis PVCs bind on a cluster whose only StorageClass is the default.
- [ ] Pods carry `imagePullSecrets` when configured; private pulls succeed.
- [ ] `go test ./...` passes, including new image-reference and storage unit tests.

## Scope

In: `imageRepository`, `imagePullSecrets`, centralized image helper, storage-class fix
(NATS + new Redis field), CRD/webhook updates, unit tests.
Out: building/pushing the actual images, cluster pull-secret creation, OLM bundle/catalog
re-publishing (tracked as operational follow-up, not code).

## Assumptions

- Single-repo tag format is `<component>-<version>` (suffixes like `-alpine` retained).
- The operator's own image (`acc-operator`) is addressed at deploy time via the Makefile
  `IMG` override, not by reconciler code — so no CRD field governs it.
- `imagePullSecrets` are referenced by name; the Secret is created out-of-band.
