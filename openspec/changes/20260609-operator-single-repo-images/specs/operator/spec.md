# Operator capability — delta: single-repository image addressing

## ADDED

### Image addressing

**REQ-OP-IMG-001** `AgentCorpusSpec` SHALL accept an optional `imageRepository` string.
When empty (the default), the operator SHALL preserve existing behavior and render every
component image as `<spec.imageRegistry>/<component>:<tag>`.

**REQ-OP-IMG-002** When `imageRepository` is non-empty, the operator SHALL render every
component image as `<imageRepository>:<component>-<tag>`, where `<component>` is the fixed
component name (`acc-agent-core`, `nats`, `redis`, `acc-kafka-bridge`,
`acc-runtime-evidence-bridge`) and `<tag>` is the component's version including any
existing suffix (e.g. `-alpine`). Example: `quay.io/flg77/acc_images:nats-2.10-alpine`.

**REQ-OP-IMG-003** A single shared helper SHALL be the sole constructor of component image
references across the agent, NATS, Redis, Kafka-bridge, and runtime-evidence-bridge
reconcilers. No reconciler SHALL build image strings inline.

**REQ-OP-IMG-004** `AgentCorpusSpec` SHALL accept an optional `imagePullSecrets` list of
secret names. When non-empty, every pod the operator renders (agent, NATS, Redis, bridges)
SHALL include those names in `PodSpec.ImagePullSecrets`. When empty, no pull secrets SHALL
be added.

## MODIFIED

### Infrastructure storage provisioning

**REQ-OP-STORAGE-001** (was: NATS StatefulSet emitted PVC `storageClassName: ""`
unconditionally, ignoring `spec.infrastructure.nats.storageClass`) The NATS reconciler
SHALL set the PVC `storageClassName` to `spec.infrastructure.nats.storageClass` when that
field is non-empty, and SHALL otherwise leave `storageClassName` unset (nil) so the
cluster default StorageClass applies. The reconciler SHALL NOT emit an empty-string
`storageClassName`.

**REQ-OP-STORAGE-002** (NEW behavior) `RedisSpec` SHALL accept an optional `storageClass`
field, and the Redis reconciler SHALL apply the same rule as REQ-OP-STORAGE-001: use the
field when set, otherwise leave `storageClassName` unset; never emit `""`.

**REQ-OP-STORAGE-003** Both reconcilers' existing `Upsert` semantics SHALL be retained:
because `VolumeClaimTemplates` are immutable and the mutate function patches only
`Replicas` and `Template`, the corrected `storageClassName` SHALL apply to newly created
StatefulSets; pre-existing StatefulSets SHALL be recreated operationally to adopt it.
