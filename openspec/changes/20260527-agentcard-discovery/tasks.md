# Tasks — AgentCard auto-discovery (Kagenti interop)

Pair: [`20260527-a2a-agent-interop/`](../20260527-a2a-agent-interop/).
Docs: [`docs/kagenti-discovery.md`](../../../docs/kagenti-discovery.md).

## Phase 1 — operator labeling foundation (LANDED)
- [x] `KagentiSpec { Enabled bool }` on `AgentCollectiveSpec`
      (`operator/api/v1alpha1/agentcollective_types.go`); mirrors `SpiffeSpec`.
- [x] Deepcopy methods (`operator/api/v1alpha1/zz_generated.deepcopy.go`)
      regenerated; controller-gen idempotent.
- [x] CRD YAML field (`operator/config/crd/bases/acc.redhat.io_agentcollectives.yaml`)
      under `spec.kagenti.{enabled}`.
- [x] Label key + value constants (`LabelKagentiType`, `LabelKagentiTypeAgent`)
      in `operator/api/v1alpha1/common_types.go`.
- [x] `util.KagentiAgentLabel()` helper.
- [x] `collective/kagenti.go` — `KagentiEnabled()` + `AgentObjectLabels()`
      factored so the policy is unit-testable.
- [x] `collective/agent_deployment.go` — apply `objectLabels` (= canonical +
      optional Kagenti) to `ObjectMeta.Labels` + pod-template labels;
      keep `Selector.MatchLabels = labels` (canonical only — immutable).
- [x] Unit tests (`operator/test/unit/kagenti_label_test.go`, 8 tests).
- [x] User-facing doc (`docs/kagenti-discovery.md`).

## Phase 2 — card endpoint + signing (LANDED via the A2A change)
Phase 2 of *this* proposal is realised by Phases 1b + 5 of the A2A change:
- [x] HTTPS endpoint at `/.well-known/agent-card.json`
      — [`20260527-a2a-agent-interop/`](../20260527-a2a-agent-interop/) Phase 1b.
- [x] Card content sourced from `RoleDefinitionConfig` — same change, Phase 1.
- [x] SPIRE JWT-SVID card signing (`spire-jwt-svid` scheme in
      `authentication.schemes`; trust-domain enforcement) — same change, Phase 5.

## Phase 3 — deferred (small follow-ups)
- [ ] Operator-side per-role Kubernetes `Service` exposing the agent's A2A
      port to cluster mesh peers (in-pod endpoint is fully functional today;
      only externally-reachable discovery needs the Service).
- [ ] AgentCard CRD discovery on the *outbound* side: when calling a peer,
      resolve its URL via the Kagenti AgentCard CRD index rather than
      `AgentConfig.peer_a2a_urls`. Builds on the Phase-1 label.
- [ ] Spike on a live Kagenti operator (dev cluster) to confirm the
      auto-discovery + `targetRef` identity-binding contract end-to-end.

## Gate before promote (LANDED)
- [x] `go test ./...` in `operator/` green (8/8 unit tests).
- [x] `make manifests && make generate` idempotent (no drift).
- [x] Docs published (`docs/kagenti-discovery.md`).
