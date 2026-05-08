# Spec: ACC Operator — Feature Parity for D & E Epics

| Field         | Value                                                              |
|---------------|--------------------------------------------------------------------|
| Spec path     | `openspec/changes/20260508-operator-feature-parity-d-e/specs/operator/spec.md` |
| Capability    | operator                                                           |
| Base spec     | `openspec/changes/20260414-acc-operator-v0.1.0/specs/operator/spec.md` |
| Change ID     | 20260508-operator-feature-parity-d-e                               |

---

## MODIFIED

### Role validation

**REQ-OP-ROLE-001** (was: `agents[].role` enum constrained to 5 values) The
`AgentCollective.spec.agents[].role` and `spec.scaling.roleScaling[].role` fields SHALL
be validated by the regex `^[a-z][a-z0-9_]{1,62}$` with `MinLength=2` and
`MaxLength=63`. The closed `Enum=` marker SHALL be removed.

**REQ-OP-ROLE-002** (NEW behaviour layered on REQ-OP-ROLE-001) An admission webhook
registered for `AgentCollective` SHALL reject role names not present in the operator's
embedded `KnownRoles` catalogue. The rejection error SHALL include the closest matching
catalogue entries.

**REQ-OP-ROLE-003** The `KnownRoles` catalogue SHALL be generated at compile time from
the listing of `roles/*/role.yaml` directories in the source tree, via
`operator/hack/gen-catalogue.go` triggered by `//go:generate`.

## ADDED

### MCP server management

**REQ-OP-MCP-001** `AgentCorpusSpec` SHALL accept an optional `mcpServers` slice of
`MCPServerSpec` entries. Each entry SHALL carry: `Name` (DNS-label-safe pattern
`^[a-z][a-z0-9-]{1,62}$`), `Image`, `Replicas` (default 1), `Port` (default 8080), `Env`,
`SecretEnv`, `ShmSizeMi`, and `Resources`.

**REQ-OP-MCP-002** A new `MCPServerReconciler` SHALL emit one Deployment and one Service
per `MCPServerSpec`. The Service name SHALL be `acc-mcp-{Name}` so that it matches the
`url:` field present in `mcps/{Name}/mcp.yaml`, requiring no manifest rewrite.

**REQ-OP-MCP-003** When `MCPServerSpec.ShmSizeMi > 0`, the rendered Deployment SHALL
include a `Memory`-medium `emptyDir` mounted at `/dev/shm` with `sizeLimit` set to
`{ShmSizeMi} Mi`. This is required for the browser-harness MCP (Chromium needs ≥256 MiB
shared memory).

**REQ-OP-MCP-004** Per-MCP readiness SHALL be aggregated into
`AgentCorpusStatus.MCPServerStatuses[Name]` with fields `Ready`, `Replicas`, `ServiceURL`.

**REQ-OP-MCP-005** The `MCPServerReconciler` SHALL slot in the reconciler chain between
`KafkaBridgeReconciler` and `OTelCollectorReconciler`. MCP outages SHALL NOT block the
overall corpus phase from progressing — agents handle MCP unavailability via lazy-init in
`acc/mcp/registry.py`.

### Manifest delivery

**REQ-OP-MANIFEST-001** `AgentCorpusSpec` SHALL accept an optional `manifestDelivery`
enum field with values `all` (default) and `none`.

**REQ-OP-MANIFEST-002** When `manifestDelivery=all`, a new `ManifestDeliveryReconciler`
SHALL emit three corpus-namespace ConfigMaps: `acc-roles`, `acc-skills`, `acc-mcps`. The
content SHALL be sourced from `embed.FS` trees baked into the operator binary at compile
time from the live `roles/`, `skills/`, `mcps/` directories.

**REQ-OP-MANIFEST-003** ConfigMap keys SHALL flatten path separators by replacing `/`
with `__`. The corresponding agent-pod volume mount SHALL use an explicit `items[]`
projection list so that the in-pod filesystem layout preserves the original directory
structure (e.g. ConfigMap key `coding_agent_implementer__role.yaml` projects to
`/etc/acc/roles/coding_agent_implementer/role.yaml`).

**REQ-OP-MANIFEST-004** When `manifestDelivery=all`, agent pod containers SHALL receive
three `VolumeMount`s (`/etc/acc/roles`, `/etc/acc/skills`, `/etc/acc/mcps`, all
read-only) and three env vars (`ACC_ROLES_ROOT=/etc/acc/roles`,
`ACC_SKILLS_ROOT=/etc/acc/skills`, `ACC_MCPS_ROOT=/etc/acc/mcps`). When
`manifestDelivery=none`, the operator SHALL NOT inject these mounts or env vars (allowing
users to bake the trees into a custom agent image).

**REQ-OP-MANIFEST-005** The `ManifestDeliveryReconciler` SHALL slot first in the
reconciler chain after `PrerequisiteReconciler` and before `UpgradeReconciler`. The
ConfigMaps SHALL exist before any agent Deployment is built.

**REQ-OP-MANIFEST-006** The TUI sample at `operator/config/samples/acc_tui_deployment.yaml`
SHALL set `ACC_ROLES_ROOT=/etc/acc/roles` and `ACC_SKILLS_ROOT=/etc/acc/skills`, and SHALL
mount the corresponding ConfigMaps with `items[]` projection identical to the agent pods.
This achieves parity with `container/production/podman-compose.yml:464` where the TUI
container has `ACC_ROLES_ROOT=/app/roles`.

### Demo samples

**REQ-OP-SAMPLE-001** `operator/config/samples/` SHALL include a runnable autoresearcher
demo manifest (`acc_v1alpha1_agentcorpus_autoresearcher.yaml`) that deploys 6 research
personas plus the 3 autoresearcher MCP servers and matches the topology described in
`examples/acc_autoresearcher/expected_topology.md`.

**REQ-OP-SAMPLE-002** `operator/config/samples/` SHALL include a runnable coding-split
demo manifest (`acc_v1alpha1_agentcorpus_coding_split.yaml`) that deploys 5 coding
personas plus the echo MCP server and matches the topology described in
`examples/coding_split_skills/expected_topology.md`.

**REQ-OP-SAMPLE-003** The OLM bundle CSV `alm-examples` SHALL include both demo
manifests in addition to the legacy `sol-corpus` example, giving OperatorHub users a
one-click "Try it" path for either demo.

### Versioning

**REQ-OP-VERSION-001** The operator bundle CSV version SHALL bump to `v0.2.0` with
`replaces: acc-operator.v0.1.0` for OLM seamless upgrade. No CRD `apiVersion` bump is
required — all schema changes are additive or strict loosenings of validation.
