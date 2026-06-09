# Design: Single-repository image addressing

## Approach

Centralize image-reference construction behind one helper and switch the five call sites
to use it. Introduce two optional `AgentCorpusSpec` fields — `imageRepository` and
`imagePullSecrets` — that change *addressing* and *pull auth* without changing any
component's behavior. Fix the NATS/Redis storage-class handling in the same change because
it shares the affected reconcilers and blocks the same end-to-end goal. Default-off:
empty `imageRepository` reproduces today's output exactly.

## Files to modify

- `operator/api/v1alpha1/agentcorpus_types.go`
  - Add `ImageRepository string \`json:"imageRepository,omitempty"\`` to `AgentCorpusSpec`.
  - Add `ImagePullSecrets []string \`json:"imagePullSecrets,omitempty"\`` to `AgentCorpusSpec`.
  - Add `StorageClass string \`json:"storageClass,omitempty"\`` to `RedisSpec` (NATS already has it).
- `operator/api/v1alpha1/agentcorpus_webhook.go`
  - No new defaults required (`imageRepository`/`imagePullSecrets` default to empty =
    legacy behavior). Leave existing `imageRegistry`/`version` defaults intact.
- `operator/internal/reconcilers/util/image.go` *(new)*
  - `func ComponentImage(corpus *v1alpha1.AgentCorpus, component, tag string) string`
- Switch the five hardcoded sites to `util.ComponentImage(...)`:
  - `collective/agent_deployment.go:161` — `("acc-agent-core", corpus.Spec.Version)`
  - `infra/nats.go:134` — `("nats", natsSpec.Version+"-alpine")`
  - `infra/redis.go:78` — `("redis", redisSpec.Version+"-alpine")`
  - `bridge/kafka_bridge.go:95` — `("acc-kafka-bridge", corpus.Spec.Version)`
  - `bridge/runtime_evidence_bridge.go:157` — `("acc-runtime-evidence-bridge", corpus.Spec.Version)`
- `operator/internal/reconcilers/infra/nats.go` — storage-class fix (see below).
- `operator/internal/reconcilers/infra/redis.go` — storage-class fix (see below).
- Pod-spec rendering in the agent/infra/bridge reconcilers — set
  `PodSpec.ImagePullSecrets` from `corpus.Spec.ImagePullSecrets` when non-empty.
- `operator/config/crd/...` + `operator/config/samples/...` — regenerated via `make manifests`.

## Key logic

```go
// util.ComponentImage
if corpus.Spec.ImageRepository != "" {
    return fmt.Sprintf("%s:%s-%s", corpus.Spec.ImageRepository, component, tag)
}
return fmt.Sprintf("%s/%s:%s", corpus.Spec.ImageRegistry, component, tag)
```

Storage-class pointer (both nats.go and redis.go), replacing `storageClass := ""`:

```go
var scPtr *string                 // nil => cluster default StorageClass applies
if spec.StorageClass != "" {      // natsSpec / redisSpec
    sc := spec.StorageClass
    scPtr = &sc
}
// VolumeClaimTemplates[0].Spec.StorageClassName = scPtr  // never the empty string
```

## Data model changes

`AgentCorpusSpec`: `+imageRepository`, `+imagePullSecrets`. `RedisSpec`: `+storageClass`.
CRD schema regenerated. No removals; existing CRs remain valid.

## Error handling

- Empty `imageRepository` → legacy path; no behavioral change.
- `StorageClassName: nil` lets the default StorageClass bind; if a cluster has *no*
  default, PVCs stay Pending exactly as a normal misconfiguration would (surfaced via the
  existing infra-progressing condition) — strictly better than the current always-`""`.
- `Upsert` already only patches `Replicas`+`Template`, so VCT changes apply on create
  only; existing StatefulSets must be recreated (operational task, documented).

## Alternatives considered

- **Per-component image override fields** — rejected: five fields, more surface, doesn't
  express "one repo, many tags" cleanly.
- **Operator code untouched, manual pod patching with operator paused** — rejected:
  abandons operator reconciliation and is not durable.
- **Emit `storageClassName: ""` but pre-create matching PVs** — rejected: requires static
  EBS PVs; brittle.

## Testing strategy

- Unit (`util/image_test.go`): table test — legacy vs single-repo for each component;
  assert byte-identical legacy output.
- Unit (nats/redis): assert `StorageClassName` is `nil` when class empty, set when
  provided, and never `""`.
- Unit: assert `ImagePullSecrets` propagate to rendered PodSpecs when set, absent when not.
- Regression: `go test ./...` green; existing `runtime_evidence_bridge_test.go` still passes.
