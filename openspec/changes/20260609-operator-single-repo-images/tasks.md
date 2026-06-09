# Tasks: Single-repository image addressing

## Phase 1 — Foundation (API + scaffolding)
- [ ] Add `ImageRepository string` and `ImagePullSecrets []string` to `AgentCorpusSpec` in `operator/api/v1alpha1/agentcorpus_types.go` (with kubebuilder doc comments).
- [ ] Add `StorageClass string` to `RedisSpec` in the same file (mirror the existing NATS field doc).
- [ ] Run `make manifests generate` to regenerate CRDs, DeepCopy, and RBAC; verify no unintended diffs.

## Phase 2 — Core logic
- [ ] Create `operator/internal/reconcilers/util/image.go` with `ComponentImage(corpus, component, tag)` implementing the repo-vs-registry branch.
- [ ] Fix `infra/nats.go`: honor `natsSpec.StorageClass`; set `StorageClassName` to a pointer only when non-empty (nil otherwise); never `""`.
- [ ] Fix `infra/redis.go`: same storage-class pointer logic using the new `redisSpec.StorageClass`.

## Phase 3 — Integration (wire call sites)
- [ ] Replace the image string in `collective/agent_deployment.go:161` with `util.ComponentImage(corpus, "acc-agent-core", corpus.Spec.Version)`.
- [ ] Replace `infra/nats.go:134` with `util.ComponentImage(corpus, "nats", natsSpec.Version+"-alpine")`.
- [ ] Replace `infra/redis.go:78` with `util.ComponentImage(corpus, "redis", redisSpec.Version+"-alpine")`.
- [ ] Replace `bridge/kafka_bridge.go:95` and `bridge/runtime_evidence_bridge.go:157` with `util.ComponentImage(...)`.
- [ ] Render `PodSpec.ImagePullSecrets` from `corpus.Spec.ImagePullSecrets` in the agent, NATS, Redis, and bridge pod templates when non-empty.

## Phase 4 — Testing
- [ ] Add `util/image_test.go`: table test covering legacy and single-repo output for all five components; assert legacy output is byte-identical to pre-change strings.
- [ ] Add storage-class unit tests (nats + redis): nil when class empty, set when provided, never `""`.
- [ ] Add a pull-secret propagation test (set → present on PodSpec; unset → absent).
- [ ] Run `go test ./...`; fix regressions until green.

## Phase 5 — Polish & rollout enablement
- [ ] Update `operator/config/samples/acc_v1alpha1_agentcorpus_rhoai.yaml` with commented `imageRepository`/`imagePullSecrets` examples.
- [ ] Document in the operator README: single-repo tagging scheme and the `make docker-build docker-push IMG=...` flow for the operator's own image.
- [ ] Operational follow-up (out of code scope, note in PR): build+push component images as `<repo>:<component>-<tag>`, create the `acc_images` pull Secret in `acc-system`, recreate the NATS/Redis StatefulSets, redeploy the operator with the single-repo `IMG`, and set `imageRepository`/`imagePullSecrets` on `AgentCorpus/acc-e2e`.
