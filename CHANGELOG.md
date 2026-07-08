# Changelog

All notable changes to the **`flg77/acc`** runtime are recorded here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning per [SemVer](https://semver.org/spec/v2.0.0.html).

Tracked since proposal 003 (ACC TUI usability hardening,
2026-05-13) — earlier changes are reconstructable from
`git log` but not back-filled into this file.

## [Unreleased]

### Added

- **Open Knowledge Format (OKF) foundation — P0–P2.** A pure-Python
  [`acc.okf`](acc/okf/) toolkit for OKF v0.1 bundles: parse, three-rule
  conformance validation (tolerant of the soft failures the spec says
  consumers MUST accept), emit, and a **non-destructive** `from_obsidian`
  transform (a messy vault → a conformant *parallel* bundle: type inference,
  `[[wikilink]]` → bundle-relative markdown links, front-matter enrichment,
  generated `index.md`). Surfaced to agents as two skills: the pure **`okf`**
  conformance helper (`format` / `validate_text` / `infer_type`) — LOW-risk and
  **granted to every role by default** (format discipline, not data access) —
  and the workspace-gated **`okf_transform`** (`validate_bundle` / `query` /
  `write_concept` / `from_vault`, HIGH-risk, trust-flag enforced). P2 indexes a
  bundle into the collective document store (`acc.okf.index_bundle`), stamping
  each concept's `type`/path as tags to seed the future per-domain retrieval
  filter. See ACC Roadmap: *Open Knowledge Format (OKF) in ACC*.

## [0.5.17 – 0.5.49] — 2026-06-29 → 2026-07-06

> Published incrementally across the 0.5.x line; the authoritative
> per-version boundaries are the annotated git tags (`git tag`,
> `git show vX.Y.Z`) and the GitHub release notes. Latest release:
> **v0.5.49** (2026-07-06). Everything below is additive and opt-in.

### Added

- **OpenShell kernel-enforced exec sandbox (Model 2).** An opted-in agent's
  code execution (`shell_exec` / `python_exec`) is delegated into a per-agent
  [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandbox
  (Landlock + seccomp + per-binary egress) carrying the corpus's Cat-A/B/C
  policy — the operator provisions it (policy `ConfigMap` +
  `openshell sandbox create` initContainer + OIDC/SPIFFE gateway auth) and the
  runtime (`acc/sandbox`) delegates **fail-closed**. Opt-in via `spec.sandbox`
  + `gatewayURL`; default-OFF and inert otherwise; live kernel-denial smoke
  pending. Adds a `[sandbox]` extra (the `openshell` CLI) + `features/sandbox.yaml`
  + `spec.sandbox.{image,credentialsSecret}`. (v0.5.49)
- **Golden-prompt eval-history + MLflow experiments (045/G).** Run-by-`task_id`
  enrichment (tokens · compliance · verdict), golden→eval-pack promotion,
  MLflow run-logging (TUI + CLI) with RHOAI trace deep-links, and an
  edge↔DC round-trip via MLflow; the WebGUI Diagnostics screen reaches parity
  (run / history / MLflow / promote). (v0.5.25, v0.5.27–28, v0.5.35, v0.5.42)
- **Golden-prompt portability.** Golden-pack boot auto-detect, an `acc-pkg`
  golden-pack export, and a Diagnostics → Pack button. (v0.5.36–38)
- **Golden Prompt pane redesign (047).** A stacked focus-resize layout, a
  version picker + a Form editor (Title/Desc, New/Export/Save), CSV + JSON
  import/export (human + agentic interchange), and a watch-dir picker. (v0.5.39–42)
- **Global command palette (`Ctrl+P`) + navigation unification (050).** A shared
  `NavScreen` base, the palette, and a `Ctrl+A` leader for the overflow panes
  (Marketplace / Catalogs). (v0.5.44, v0.5.48)
- **Writable, cosign-verifying catalog endpoint (marketplace P0)** — publishes
  and verifies packages against the catalog's `required_signer`. (v0.5.29)
- **Signed-image release build.** The `flavour` builder gains `--signer-key` +
  cosign; **acc-deploy semantic versioning** ties image tags + the TUI banner to
  the git code release. (v0.5.30–31)
- **TUI prompt persistence + durable sessions** — sent-prompt history and
  detach/resume survive restarts (+ `acc-deploy restart`). (v0.5.34)
- **Visible role→model mapping + assistant-loop closers (044).** `models.yaml`
  `role_models` + `/model`, surfaced in Nucleus + Configuration; an inline GATE
  CARD resolves oversight from the Prompt window; the assistant continues after
  an infuse install; promoted-role model re-resolve on `ROLE_ASSIGN`; golden
  prompts gain durable export/import. (v0.5.17–24)

### Changed

- **TUI 050 layout convergence** — `%`→`fr` + min-height sizing, a single
  styling home, help coverage, and a dead-rule sweep. (v0.5.45–47)
- Diagnostics reachability + catalog-discovery UX polish (045 slices 1–2). (v0.5.33)

### Fixed

- Marketplace / Catalogs **crash-on-open** (a shared `NavScreen` base) + the
  Ecosystem `m` / `c` entries. (v0.5.43)
- Operator `AgentCorpus` **admission** — empty-OTel-endpoint backfill (G1) and
  Milvus gated on `vectorBackend` (G2). (v0.5.32, PR #147)

### Security

- Overlay `allow_unsigned` **prod-guard** + CLI roots-root alignment. (v0.5.22, PR #135)
- **Dependabot** clearance — `uv.lock` refresh + `cryptography` 48.0.1. (v0.5.26, PR #141)

## [0.3.1 – 0.5.16] — 2026-05-14 → 2026-06-28

> These changes were published incrementally across the 0.3.x and 0.5.x
> releases; the changelog was not carved per tag during that run. The
> authoritative per-version boundaries are the annotated git tags
> (`git tag`, `git show v0.5.16`) and the GitHub release notes. Latest
> release: **v0.5.16** (2026-06-28).

### Removed

- **The 43 movable roles have been extracted from `roles/` to four
  published packages (Stage 2 cutover).**  The dual-source
  `RoleLoader` now resolves them from the installed-package path —
  add `required_packages:` to your `collective.yaml` before
  upgrading:

  ```yaml
  required_packages:
    - "@acc/workspace-roles@^1.0"   # coding_agent + variants, analyst, synthesizer
    - "@acc/research-roles@^1.0"    # research_planner, research_critic, ...
    - "@acc/business-roles@^1.0"    # 25 business roles
    - "@acc/devops-roles@^1.0"      # data_engineer, ml_engineer, ...
  ```

  Packages are signed (Sigstore keyless OIDC) and served from
  `https://flg77.github.io/acc-ecosystem`.  The 7 CONTROL roles
  (arbiter, assistant, compliance_officer, ingester, observer,
  orchestrator, reviewer) stay in core — they ARE the substrate.

  Runbook: `docs/CUTOVER-PLAN.md`.  Migration guide:
  `docs/MIGRATING-FROM-INTREE.md`.  The deprecation warning that
  surfaced in the preceding release is gone — the in-tree dir no
  longer exists for movable roles, so the warning code path is
  removed too.


### Added

- **acc-webgui — optional FastAPI + React web frontend (proposal acc-webgui).**
  A new optional container: a browser frontend with feature parity to
  the terminal UI `acc-tui` plus enhanced tracing views.  Opt-in — a
  separate image + compose profile; nothing changes for existing
  deployments, and `acc-tui` is unchanged and not deprecated.

  - **PR-1** — FastAPI backend (`acc/webgui/`): `ObserverHub` reuses
    `acc.tui.client.NATSObserver` + `CollectiveSnapshot` (parity is
    structural, not a fork); WebSocket `/ws/{cid}` live push + REST
    read endpoints; `pyproject.toml` `[webgui]` extra + `acc-webgui`
    console script.
  - **PR-2** — React + Vite + TypeScript frontend (`webgui/` tree):
    app shell, collective switcher, the 8 parity screens.
  - **PR-3** — action endpoints (`infuse` / `prompt` / `oversight` /
    `test-llm`) + `acc/channels/webgui.py` `WebPromptChannel`.
  - **PR-4** — enhanced tracing: a task-step waterfall, a PLAN DAG
    view, and a tamper-evident audit-chain timeline (the audit endpoint
    re-verifies each record's `evidence_hash`).
  - **PR-5** — capability-tiered auth (`oauth-proxy` / `oidc` /
    `token`); viewer/operator RBAC; the server refuses a non-loopback
    bind when no auth is configured.
  - **PR-6** — multi-stage `Containerfile.webgui` (Node build stage
    discarded — the runtime image is Python-only); compose `webgui`
    profile; operator `acc_webgui_deployment.yaml` sample.

  The backend reuses the TUI's framework-agnostic data layer, so a new
  signal type appears in both UIs for free.  See `docs/webgui.md`.

- **Runtime-evidence Cat-A (proposal 015, Phase 3).**  A
  provider-agnostic kernel-event evidence layer for Category-A
  governance — ACC folds `execve`/`openat`/`connect` evidence (what an
  agent process *actually did*) into Cat-A, alongside the existing
  metadata evaluation.  Opt-in via `governance.runtimeEvidence.enabled`
  (default `false`); observe-by-default.

  - **PR-1** — `RuntimeEvidenceSpec` on `GovernanceSpec`; status
    `runtimeEvidence` + `RuntimeEvidenceReady` condition;
    `PrerequisiteStatus` gains `rhacsInstalled` / `falcoInstalled` /
    `tetragonInstalled` / `netobservInstalled`; resource-level
    `HasAPIResource` detection (Tetragon's `TracingPolicy` is detected
    by *kind*, not the `cilium.io` group it shares with the CNI).
  - **PR-2** — new `acc-runtime-evidence-bridge` image
    (`acc/runtime_evidence_bridge.py`) with an adapter framework + the
    Tetragon and NetObserv adapters; operator
    `RuntimeEvidenceBridgeReconciler`.
  - **PR-3** — RHACS (Red Hat-preferred) and Falco adapters; backend
    auto-selection (RHACS > Falco > Tetragon).
  - **PR-4** — `KERNEL_EVENT` NATS signal; `CognitiveCore` subscribes,
    correlates events to its own pod, and folds them into Cat-A via
    the new `KernelEventEvaluator`; downward-API pod identity on agent
    pods.
  - **PR-5** — `regulatory_layer/category_a/kernel_events.rego`
    (K-001/K-002/K-003) + recommended Tetragon/Falco rule samples.

  Provider-agnostic: ACC consumes whichever runtime-security tool the
  cluster runs and never installs one.  See `docs/runtime-evidence.md`
  and proposal 015.

- **L7 / eBPF NetworkPolicy (proposal 014, Phase 1).**  An opt-in,
  capability-tiered network-isolation layer for ACC agent pods,
  delivered entirely operator-side (no Python runtime change).
  Opt-in via `spec.networkPolicy.enabled` (default `false`) — upgrading
  the operator never drops traffic.

  - **PR-1/PR-2** — `NetworkPolicySpec` on `AgentCorpusSpec`; status
    `networkPolicy` block + `NetworkPolicyReady` condition;
    `PrerequisiteStatus` gains `ciliumInstalled` /
    `ovnEgressFirewallSupported`; `APIGroupChecker` detects `cilium.io`
    and `k8s.ovn.org` plus a CNI-enforcement heuristic.
  - **PR-3** — new `NetworkPolicyReconciler` (`internal/reconcilers/
    security/`); Tier 1 = default-deny + DNS + same-namespace +
    coarse external-HTTPS standard `NetworkPolicy` objects for agent
    pods.  Honest about K3s/Flannel (emits objects, reports
    `CNIDoesNotEnforce`).
  - **PR-4** — Tier 2 FQDN egress via OVN `EgressFirewall` or Cilium
    `CiliumNetworkPolicy` (auto-selected, emitted as unstructured
    objects — no heavy CRD-type vendoring).
  - **PR-5** — Tier 3 Cilium L7 (HTTP-method-scoped egress); `mode:
    audit` emits the policy set without the default-deny as a safe
    canary.

  Cilium is **not** a prerequisite — Tier 1 standard `NetworkPolicy`
  is the portable must-have; Tiers 2/3 auto-negotiate from detected
  cluster capability.  See `docs/network-policy.md` and proposal 014.

- **NATS NKey authentication (proposal 013, Phase 0c).**  Per-role
  NKey identities + a server-enforced publish/subscribe permission
  matrix for the NATS bus, integrating into all three deploy modes
  (standalone / edge / rhoai).  Opt-in via `security.nkey.enabled`
  (default `false`) — with the switch off, every NATS connection is
  byte-for-byte unchanged.

  - **PR-1** — split the shared `acc.{cid}.task` subject into
    `acc.{cid}.task.assign` (TASK_ASSIGN) and `acc.{cid}.task.complete`
    (TASK_COMPLETE) so the permission matrix can grant "assign work"
    and "report completion" independently per role.  `subject_task()`
    is retained for one release as a deprecated alias.
  - **PR-2** — new `NKeyConfig` model under `SecurityConfig`
    (`ACC_NKEY_*` env overrides); canonical `acc/nats_permissions.yaml`
    permission matrix consumed by both the operator's Go renderer and
    the Python CLI; `acc/nats_permissions.py` loader; contract test
    `tests/test_nats_permissions.py` fails CI if a subject in
    `acc/signals.py` is left uncovered.  TUI Configuration screen
    surfaces the resolved `nkey.enabled` / `nkey.role`.
  - **PR-5** — `NATSBackend` and the TUI `NATSObserver` thread an
    NKey seed into `nats.connect()` when enabled (fail closed on a
    missing seed, never silently anonymous); new `acc/nkeys.py`
    (pure-Python Ed25519 NKey generation + `nats.conf` authorization
    rendering) and the `scripts/acc-nkeys` CLI (`generate` /
    `render-conf`) for standalone mode; `podman-compose.yml` gains an
    opt-in `nats.conf` mount.

  Eight identities: the six agent roles plus a read-only `tui`
  surface and an edge `leaf`-node link.  See `docs/nats-nkeys.md`.

- **Edge SPIFFE guide + cross-mode compatibility e2e (proposal 012
  PR-4).**  Closes proposal 012.  New `docs/spiffe-edge.md` — the
  edge SPIFFE guide: the topology decision tree (nested / federated
  / ed25519), per-topology config, offline survival + the
  `offline_action` table, the ed25519→spiffe migration path,
  troubleshooting, and the six-direction bi-directional
  compatibility matrix.  `docs/howto-edge.md` gains a SPIFFE
  optional section; `docs/spiffe.md` gains an edge-interoperability
  section.

  New `tests/integration/test_spiffe_edge_e2e.py` — a crypto-level
  e2e that models each trust topology with synthetic SPIRE
  keypairs, mints JWT-SVIDs as a SPIRE workload API would, and
  verifies them through the production `acc.spiffe_verify` path.
  13 tests cover all six matrix directions (rhoai↔rhoai,
  rhoai↔edge, edge↔rhoai, edge↔edge same/different nested sites,
  edge↔edge federated), the federation negative control (cross-
  trust fails without a bundle exchange), strict subject binding,
  and the offline partition→degrade/rotate→recovery timeline.
  No cluster required — runs unattended in CI.

  Proposal 012 status with this PR: all four PRs landed.  Edge
  SPIFFE ships as opt-in; edge stays on `ed25519` by default.

- **SPIFFE operator guide + v0.5.0 default-flip plan (proposal 011
  PR-5).**  Closes proposal 011.  New `docs/spiffe.md` — the
  operator-facing guide for SPIFFE workload identity: prerequisites
  (SPIRE / spire-controller-manager / SPIFFE CSI driver), the
  `security.signing_mode` + `security.spiffe.*` config surface,
  trust-domain naming, the three-stage migration
  (`ed25519` → `spiffe`+fallback → `spiffe` strict), an end-to-end
  flow diagram, verification commands, and a troubleshooting table.
  `docs/howto-rhoai.md` gains a SPIFFE optional-prerequisite entry;
  `docs/role-sync.md` cross-links it.

  **Planned for v0.5.0**: the `rhoai` row of
  `_SIGNING_MODE_BY_DEPLOY_MODE` flips from `ed25519` to `spiffe`,
  so a fresh rhoai operator gets SPIFFE by default
  (`allow_ed25519_fallback` still defaults true, so the flip is
  safe).  `standalone` + `edge` stay on `ed25519`.  Operators pin
  `signing_mode: ed25519` explicitly to opt out.

- **Edge federation + configurable offline action (proposal 012
  PR-3).**  Completes the edge SPIFFE story: cross-trust between
  federated edge sites, and a configurable response to a partitioned
  (stale) trust bundle.

  - `SpiffeSpec` gains `federationPeers []string`.  When
    `edgeTopology: federated`, `SpiffeReconciler` issues one
    `ClusterFederatedTrustDomain` custom resource per peer so this
    edge's SPIRE trusts SVIDs from the peer trust domains.  Peer
    entries are `<trust-domain>@<bundle-endpoint-url>` pairs;
    malformed entries are skipped + surfaced in `status.spiffeError`
    (one bad peer doesn't block the others).  Operator RBAC gains
    `clusterfederatedtrustdomains`.
  - New `acc/spiffe_offline.py` — `OfflineBundleMonitor` watches the
    SPIRE trust-bundle file age and, when it crosses
    `offline_max_age_h`, applies the configured `offline_action`:
    `rotate` (keep serving — the edge SPIRE rotates), `degrade`
    (read-only), or `shutdown` (fail-safe exit).  It is a building
    block — `check()` classifies + `start()` runs a poll loop that
    emits an `acc.spiffe.offline` audit event and invokes a handler;
    the agent bootstrap wires the handler that performs the actual
    degrade/shutdown (same module-then-wire-up split as proposal
    010's `RoleSyncListener`).
  - New `deploy/edge-spire/federation-peer.yaml.example` +
    expanded `README.md` (federated-topology install runbook +
    the `offline_action` behaviour table).
  - 31 new tests — 15 Go in `spiffe_federation_test.go` /
    extensions (per-peer CR issuance, no-peers + malformed-peer
    error paths, nested-ignores-federation) + 16 Python in
    `tests/test_spiffe_offline.py` (freshness classification,
    missing-bundle, all three actions, event publication, poll
    loop with handler).

  Inert by design — no behaviour change until an operator sets
  `deployMode: edge` + `edgeTopology: federated` (or wires the
  offline monitor).  012 PR-4 (docs + cross-mode e2e) closes the
  proposal.

- **Agent-side SPIFFE JWT-SVID verification (proposal 011 PR-4).**
  When `security.signing_mode` is `spiffe`, a ROLE_UPDATE carries the
  arbiter's JWT-SVID in its `signature` field; the agent verifies it
  against the SPIRE trust bundle the `spiffe-helper` sidecar writes
  to disk.

  - New `acc/spiffe_verify.py` — `verify_jwt_svid()` checks signature
    (against the JWKS bundle), `aud`, `exp`, optionally `sub`;
    `SpiffeVerifier` re-reads the bundle each call so SPIRE bundle
    rotation is honoured without an agent restart.  `PyJWT` is a new
    dependency (small, pure-Python, reuses the existing
    `cryptography` dep).
  - `acc/role_store.py` — `apply_update` dispatches verification
    through `_verify_signature`, switching on `signing_mode`:
    `spiffe` → `_verify_spiffe`, `ed25519` → the existing path.
    When `security.spiffe.allow_ed25519_fallback` is true a SPIFFE
    failure degrades to the Ed25519 path — a transient SPIRE problem
    doesn't strand the collective during the migration window.
  - New `security.spiffe.arbiter_spiffe_id` config field
    (`ACC_SPIFFE_ARBITER_ID` env) — when set, the verifier enforces
    the JWT `sub` claim; when blank, arbiter identity rests on the
    existing `approver_id` check.
  - `RenderSpiffeHelperConfig` (operator) emits `jwt_bundle_file_name`
    so spiffe-helper writes the JWKS bundle the verifier needs.
  - 25 new tests — 20 in `tests/test_spiffe_verify.py`, 5 in
    `tests/test_role_store.py::TestApplyUpdateSpiffe`.

  A JWT-SVID attests arbiter identity + audience, not ROLE_UPDATE
  content integrity (that stays the `approver_id` + role-version
  checks) — see the `acc/spiffe_verify.py` module docstring.

- **Nested SPIRE topology + edge-qualified SPIFFE IDs (proposal 012
  PR-2).**  Extends the operator-side SPIFFE provisioning to edge
  deployments.

  - `SpiffeSpec` gains `edgeTopology` (`nested | federated | ed25519`,
    default `nested`) + `edgeSiteID`.  `AgentCollectiveStatus` gains
    `edgeSiteID`.  CRD bases hand-updated to match.
  - `SpiffeReconciler` now site-qualifies the SPIFFE ID when the
    owning `AgentCorpus` has `deployMode: edge` **and**
    `spiffe.edgeTopology: nested`:
    `spiffe://<trust-domain>/edge/<site-id>/role/<collective>`.
    Federated / ed25519 topologies and all non-edge deploy modes keep
    the flat `spiffe://<trust-domain>/role/<collective>` form.
    `nested` without an `edgeSiteID` reports a config error via
    `status.spiffeError` rather than failing reconciliation.
  - New `deploy/edge-spire/` manifests: `nested-spire-server.values.yaml`
    (Helm values overlay for the upstream `spiffe/spire` chart in
    nested mode), `edge-bundle-fetcher.yaml` (PVC + CronJob caching
    the parent trust bundle for offline survival), and a `README.md`
    install runbook.
  - 5 unit tests in `operator/test/unit/spiffe_edge_test.go` —
    site-qualified ID, missing-site-id error, federated plain ID,
    non-edge topology ignored, ed25519 topology plain ID.

  Inert by design — no behaviour change until an operator sets
  `deployMode: edge` + `spec.spiffe`.  012 PR-3 adds federation +
  the configurable offline action.

- **`spiffe-helper` sidecar injection (proposal 011 PR-3).**  When an
  `AgentCollective` has `spec.spiffe.enabled: true`, every agent pod
  gains a `spiffe-helper` sidecar that materialises the pod's
  X.509-SVID + JWT-SVID into a shared `emptyDir`.  The agent container
  reads the SVID files from there — no agent code change beyond the
  env vars the operator now sets.

  - New `operator/internal/reconcilers/collective/spiffe_sidecar.go`:
    `ApplySpiffeSidecar` mutates a built agent `Deployment` to add the
    sidecar + three volumes (`spiffe-svids` emptyDir,
    `spiffe-workload-api` CSI volume for the SPIRE Workload API socket,
    `spiffe-helper-config` ConfigMap) + the
    `spiffe.io/spire-managed-identity` pod annotation.  No-op when
    SPIFFE is disabled.
  - `RenderSpiffeHelperConfig` produces the `helper.conf` HCL;
    `AgentDeploymentReconciler` upserts it as a per-collective
    ConfigMap (`<collective>-spiffe-helper`).
  - Agent container gets a read-only `/run/spire/sockets` mount plus
    `ACC_SPIFFE_SVID_MOUNT_PATH`, `ACC_SVID_X509_PATH`,
    `ACC_SVID_JWT_PATH` env vars.
  - 6 unit tests in `operator/test/unit/spiffe_sidecar_test.go`.

  Inert by design — pods are unchanged until an operator sets
  `spec.spiffe.enabled`.  PR-4 wires the agent-side verifier that
  consumes these SVID files.

- **Operator-side SPIFFE provisioning — `ClusterSPIFFEID` issuance
  (proposal 011 PR-2).**  When an `AgentCollective` carries
  `spec.spiffe.enabled: true`, the operator issues a matching
  `ClusterSPIFFEID` custom resource so spire-controller-manager
  attests the collective's agent pods.

  - New `SpiffeSpec` on `AgentCollectiveSpec` (`enabled`,
    `trustDomain`) + three status fields (`spiffeID`,
    `spiffeIssued`, `spiffeError`).  CRD bases + deepcopy
    hand-updated to match.
  - New `SpireInstalled` prerequisite — `PrerequisiteReconciler`
    detects the `spire.spiffe.io` API group via the new
    `APIGroupChecker.SpireInstalled()` helper.
  - New `collective.SpiffeReconciler` issues / updates one
    `ClusterSPIFFEID` per SPIFFE-enabled collective.  SPIFFE ID
    format `spiffe://<trust-domain>/role/<collective-name>`;
    trust domain defaults to `<corpus>.acc.local` when blank;
    `podSelector` targets pods by the `acc.io/collective` label.
  - Strict no-op when `spec.spiffe` is absent/disabled or when
    spire-controller-manager is not installed — the latter
    surfaces a reason in `status.spiffeError` rather than
    failing reconciliation.  SPIFFE stays opt-in.
  - RBAC: operator ClusterRole gains
    `spire.spiffe.io/clusterspiffeids` (full verbs).
  - 7 unit tests in `operator/test/unit/spiffe_reconciler_test.go`.

  Inert by design — no `AgentCollective` carries `spec.spiffe`
  until an operator opts in.  PR-3 mounts the `spiffe-helper`
  sidecar; PR-4 wires the agent-side verifier.

- **`security.spiffe` edge fields + cross-field validators
  (proposal 012 PR-1).**  Extends proposal 011's `SpiffeConfig`
  with 11 fields covering the edge-deployment topology, offline
  survival, and NATS-mTLS fallback (Q1–Q6 resolutions from
  proposal 012 §8).  Inert by design — every existing deployment
  still defaults to `signing_mode: ed25519` so the new fields are
  ignored.

  New fields on `SpiffeConfig`:

  | Field | Type | Default |
  |---|---|---|
  | `edge_topology` | `nested \| federated \| ed25519` | `nested` |
  | `edge_site_id` | str | `""` |
  | `parent_spire_url` | str | `""` |
  | `federation_peers` | list[str] | `[]` |
  | `offline_bundle_cache_path` | str | `/run/spire/cache/bundle.pem` |
  | `offline_max_age_h` | float | `72.0` |
  | `bundle_refresh_h` | float | `6.0` |
  | `offline_action` | `rotate \| degrade \| shutdown` | `rotate` |
  | `parent_unreachable_action` | `block \| degrade` | `degrade` |
  | `nats_mtls_cert_path` | str | `""` |
  | `nats_mtls_key_path` | str | `""` |

  New `ACCConfig._validate_edge_spiffe_fields` model validator
  enforces topology-specific requirements **only when
  `deploy_mode: edge` AND SPIFFE is enabled AND `signing_mode: spiffe`**:

  - `edge_topology: nested` requires `parent_spire_url` +
    `edge_site_id` (Q5 resolution: operator-supplied,
    consistency with `trust_domain` + `parent_spire_url`).
  - `edge_topology: federated` requires ≥ 1 `federation_peers` entry.
  - `offline_action: rotate` requires `edge_topology: nested`
    (rotation needs a local SPIRE server).

  Non-edge deploy modes ignore the edge fields entirely.
  Operators who run `deploy_mode: edge` with `signing_mode: ed25519`
  also skip the topology checks — SPIFFE-aware fields stay
  advisory until SPIFFE is actually consumed.

  Nine new env-var overrides for the 9 string/scalar edge fields
  (`ACC_SPIFFE_EDGE_TOPOLOGY`, `ACC_SPIFFE_EDGE_SITE_ID`,
  `ACC_SPIFFE_PARENT_URL`, `ACC_SPIFFE_OFFLINE_MAX_AGE_H`,
  `ACC_SPIFFE_BUNDLE_REFRESH_H`, `ACC_SPIFFE_OFFLINE_ACTION`,
  `ACC_SPIFFE_PARENT_UNREACHABLE_ACTION`,
  `ACC_NATS_MTLS_CERT_PATH`, `ACC_NATS_MTLS_KEY_PATH`).
  `federation_peers` (list) stays YAML-only since `_apply_env`
  writes scalar strings.

  19 new tests in `tests/test_config.py::TestSpiffeEdgeDefaults`
  covering defaults, every cross-field validator path, non-edge
  topology skip, all three `edge_topology` happy paths, env-var
  roundtrip on every scalar override, and invalid-value rejection
  for both new `Literal` types.

- **`security.signing_mode` + `security.spiffe` config surface
  (proposal 011 PR-1).**  Foundational PR for SPIFFE workload
  identity.  Inert by design — every existing deployment sees
  zero behaviour change because every `deploy_mode` still defaults
  to `signing_mode: ed25519` in v0.4.x.

  New fields under `security:` in `acc-config.yaml`:

  | Field | Type | Default |
  |---|---|---|
  | `signing_mode` | `ed25519 \| spiffe \| auto` | `auto` |
  | `spiffe.enabled` | bool | `false` |
  | `spiffe.trust_domain` | str | `""` |
  | `spiffe.svid_mount_path` | str | `/run/spire/sockets` |
  | `spiffe.jwt_audience` | str | `acc-role-update` |
  | `spiffe.allow_ed25519_fallback` | bool | `true` |

  `signing_mode: auto` resolves to a per-`deploy_mode` default via
  `_SIGNING_MODE_BY_DEPLOY_MODE` (mirrors proposal 010's
  `_ROLE_SOURCE_BY_DEPLOY_MODE` pattern).  Resolver entry exists
  for every deploy_mode in v0.4.x.  v0.5.0 flips the `rhoai` row
  from `ed25519` → `spiffe` once 011 PR-2..PR-5 land.

  Six env-var overrides: `ACC_SIGNING_MODE`, `ACC_SPIFFE_ENABLED`,
  `ACC_SPIFFE_TRUST_DOMAIN`, `ACC_SPIFFE_SVID_MOUNT_PATH`,
  `ACC_SPIFFE_JWT_AUDIENCE`, `ACC_SPIFFE_ALLOW_ED25519_FALLBACK`.

  TUI Configuration screen surfaces the resolved values read-only
  ("Signing mode: ed25519 (spiffe.enabled=no; proposal 011)").
  Existing `arbiter_verify_key` (the legacy Ed25519 static key)
  is untouched and remains fully functional.

  15 new tests in `tests/test_config.py::TestSpiffeDefaults`
  covering defaults, per-deploy-mode resolution, explicit
  overrides, env-var roundtrip, invalid-value rejection, and a
  meta-test that fails if a future `deploy_mode` is added
  without updating `_SIGNING_MODE_BY_DEPLOY_MODE`.

- **Proposal 010 wire-up — projector ↔ detector ↔ listener ↔ TUI badge.**
  Connects the building blocks that landed inert in PR-3 / PR-4 / PR-5
  so they actually fire end-to-end:

  - `RoleCRDProjector.__init__` gains an optional
    `conflict_detector` kwarg.  When supplied, every successful
    `project_one()` calls `detector.record_our_write(role_id, body)`,
    so subsequent file-watcher events for that role classify as
    `echo` (within `conflict_window_s` with matching content) instead
    of false-positive conflicts.  Detector exceptions are caught + logged
    so a misbehaving detector never breaks the projector's hot loop.

  - `ACCTUIApp` instantiates a single shared `RoleSyncListener`
    (`app._role_sync_listener`) and subscribes the first connected
    NATS client to `acc.role.sync.>`.  Every received event routes
    through `listener.handle_event` and broadcasts a `_RoleSyncEvent`
    message so screens re-render without polling.  Subscription
    failures log + degrade gracefully (badge stays empty).

  - `EcosystemScreen` gains a `#role-sync-badge` Static widget at the
    top of the role detail panel.  Refreshes on row select and on
    every `_RoleSyncEvent` broadcast.  Three rendering tiers from
    PR-5's `render_badge()`: fresh conflict (red), aged conflict
    (dim), applied-only (dim), missing-state (empty / hidden).

  - 5 new integration tests in `tests/test_role_sync_wireup.py`
    cover projector→detector recording, projector backwards-compat
    when no detector is supplied, detector exception isolation, and
    a detector→listener round-trip that proves the on-wire JSON
    payload is the same on both sides of the NATS hop.

  This is the closing piece of proposal 010 — bi-directional file ↔
  CRD sync is now operator-observable end-to-end.

- **TUI role-sync listener + comprehensive docs (proposal 010 PR-5).**
  Closes proposal 010 — the role-sync feature is now operator-facing.

  - `acc/tui/role_sync_listener.py` — subscribes to
    `acc.role.sync.>`, maintains a per-role `RoleSyncState` (last
    conflict, last applied, counters), and exposes `render_badge()`
    returning Rich-markup for a Static widget.  Three rendering
    tiers: fresh conflict (red, within `badge_window_s` = 5 min),
    aged conflict (dim), applied-only (dim).  15 unit tests in
    `tests/test_role_sync_listener.py`.
  - `docs/role-sync.md` — comprehensive operator-facing doc
    covering the three modes (`files | crd | mirror`), defaults per
    `deploy_mode`, conflict-window semantics + sequence diagram for
    echo handling in mirror mode, plus a runbook for switching
    modes safely.

  Wiring the listener into the existing TUI NATS subscription
  (which is collective-scoped, but role-sync events are global) is
  the only deferred piece — handled by a tiny follow-up PR.  The
  listener itself is inert today and ready to consume events as
  soon as it's connected.

- **Mirror-mode conflict detection + NATS events (proposal 010 PR-4).**
  New module `acc.role_sync_conflict.ConflictDetector` classifies every
  file-watcher event as **echo** (our own CRD-driven write coming back
  through the watcher), **applied** (genuine operator edit propagating
  forward), or **conflict** (concurrent file + CRD write within the
  `conflict_window_s` window).  Conflicts publish on
  `<events_subject>.conflict` carrying enough payload (winner/loser
  source, loser snippet, RFC3339 timestamp) for an audit log + the
  PR-5 TUI badge.

  - Last-writer-wins semantics — no three-way merge.  Operators see
    the conflict event; correction is the next edit.
  - Time source injectable (`now=` kwarg) so unit tests drive the
    window deterministically without `time.sleep`.
  - NATS publisher injectable — production wires
    `acc.backends.signaling_nats`; tests inject a recording fake.
  - Counters (`applied_count`, `echo_count`, `conflict_count`)
    exposed for future `/metrics` integration.

  12 new unit tests in `tests/test_role_sync_conflict.py` cover all
  three classification outcomes, per-role isolation, counter
  increments, publisher absence + exception swallowing, and subject
  normalisation.

  Wiring into `RoleCRDProjector` is intentionally deferred to a
  separate small PR so the classifier can land + be reviewed in
  isolation.  The detector is currently inert in production builds.

- **Agent-side CRD → file projection (proposal 010 PR-3).**  The Python
  mirror of PR-2's Go-side watcher.  When `role_sync.role_source` is
  `crd` or `mirror`, the new `acc.role_crd_loader.RoleCRDProjector`
  polls the Kubernetes API for `AgentCollective` resources and writes
  their `spec.roleDefinition` block to `roles/<id>/role.yaml`.  The
  existing `acc.role_loader.RoleLoader` file watcher then picks up the
  write naturally — no new code path inside the agent's hot loop.

  - New module `acc/role_crd_loader.py` (~330 LOC):
    - `CRDClient` Protocol so tests can supply a fake without a real
      cluster.
    - `KubernetesCRDClient` lazy-imports `kubernetes` only when
      `role_source` requires it — agents in the `files` default mode
      don't pay the dependency cost.
    - `RoleCRDProjector` polls every `poll_interval_s` (default 30),
      writes files atomically (`*.tmp` + `os.replace`), and is fully
      idempotent: in-memory cache + on-disk content check both
      short-circuit no-op rewrites.
    - Generated files carry a sentinel comment naming the source CRD
      so operators can `cat` and understand the origin.
  - 19 new unit tests in `tests/test_role_crd_loader.py` covering
    sentinel-strip, idempotency, atomic writes, exception swallowing,
    polling lifecycle, and field-translation.

  Production `KubernetesCRDClient` exercised only by integration
  tests on acc1 (no live-cluster requirement in CI).

- **Operator-side file → CRD projection (proposal 010 PR-2).**  When
  the operator binary is started with `--role-source files` (or
  `mirror`), it watches `<roles-root>/<id>/role.yaml` on disk and
  patches the matching `AgentCollective.spec.roleDefinition` whenever
  the file changes.  Default behaviour is unchanged: `--role-source`
  defaults to `crd` so existing deployments see no difference.

  - New `operator/internal/filewatch/` package: `Watcher` wraps
    `fsnotify` with debouncing (500 ms default — collapses
    editor write-rename storms); `ParseRoleFile` reads the on-disk
    snake_case YAML and translates to the camelCase CRD shape;
    `RoleDefinitionsEqual` short-circuits no-op patches.
  - `AgentCollectiveReconciler` gains `RoleSource`, `RolesRoot`,
    `Namespace` fields and a public `ProjectRoleFile(ctx, roleID)`
    method called by the file-watcher goroutine.
  - `operator/cmd/main.go` adds `--role-source`, `--roles-root`,
    `--role-sync-namespace` flags (each fall back to the matching
    `ACC_*` env var) and registers the watcher as a `manager.Runnable`
    so it joins the manager's start/stop lifecycle.
  - CR patches are tagged with annotation
    `acc.io/role-sync-source: file-mirror@<RFC3339-ts>` so observers
    can attribute the change.  PR-4's conflict detector will use this.
  - On operator startup the watcher does a one-shot sweep of every
    existing `<id>/role.yaml` so CR state catches up to whatever was
    edited while the operator was down.

  PR-3 (CRD → file projection) and PR-4 (mirror-mode conflict
  events) build on this foundation.

- **`role_sync` config section + `role_source` flag (proposal 010
  PR-1).**  New top-level `role_sync:` block in `acc-config.yaml`
  with three fields:

  | Field | Type | Default |
  |---|---|---|
  | `role_source` | `files \| crd \| mirror \| auto` | `auto` |
  | `conflict_window_s` | float | `2.0` |
  | `events_subject` | str | `acc.role.sync` |

  When `role_source` is `auto` (the default) it resolves at
  validation time to a per-`deploy_mode` value:

  | `deploy_mode` | resolved `role_source` |
  |---|---|
  | `standalone` | `files` |
  | `edge` | `mirror` |
  | `rhoai` | `crd` |

  Environment overrides: `ACC_ROLE_SOURCE`,
  `ACC_ROLE_SYNC_CONFLICT_WINDOW_S`,
  `ACC_ROLE_SYNC_EVENTS_SUBJECT`.

  **PR-1 is inert** — no behaviour change in the operator
  reconciler or `role_loader`.  This PR only lands the flag and
  its resolution so PR-2/PR-3/PR-4 can switch on it.  The TUI's
  Configuration screen surfaces the resolved value read-only
  (`Role sync: files (deploy_mode=standalone; proposal 010)`).

### Fixed

- **TUI repo-root discovery for pip-installed acc-tui.**  The
  Ecosystem + Configuration screens silently rendered empty
  Role / Skills / MCPs tables when ``acc-tui`` was run from
  outside the repo with no env vars set — the operator's actual
  failure mode from ``ACC TUI / ACC REVIEW 14.5.md``.

  ``acc/tui/path_resolution.py`` gains a new discovery tier
  between the module-anchored fallback and the cwd fallback:

  1. ``$ACC_REPO_ROOT`` env var (new) — if set, the directory's
     ``roles/`` / ``skills/`` / ``mcps/`` are used.
  2. Cwd walk-up — up to 8 ancestors are scanned for an
     ``acc-deploy.sh`` marker (or ``pyproject.toml`` + an ``acc/``
     subdirectory).  An operator who ``cd``'s anywhere inside
     their checkout gets the repo's manifests surfaced
     automatically.

  Existing env-var-per-dir (``ACC_ROLES_ROOT`` etc.) and
  module-anchored paths still take precedence, so nothing breaks
  in development or container layouts.

- **Empty-roles diagnostic on the Ecosystem screen.**  When the
  resolver can't find any roles (all four tiers miss), the
  screen now surfaces an operator-facing warning notify listing
  the env-var options + the walk-up convention, instead of
  silently rendering an empty table.

### Added

- **`tests/test_tui_user_experience.py`** — 19 UX-flow tests
  that exercise the operator's actual workflow against the
  repo's real ``roles/`` / ``skills/`` / ``mcps/`` (not synthetic
  ``tmp_path`` fixtures).  Covers all six issues from
  ``ACC REVIEW 14.5.md``: roles load, row-highlight populates
  detail, Schedule infusion button arms and fires, Edit
  role.yaml / role.md buttons invoke spawn, Skills + MCPs
  surface on Configuration, LLM Endpoints documents the config
  path.  Plus four new tests pinning the repo-discovery fix
  (env-var override, cwd walk-up, graceful fallback, typo'd
  env-var tolerance).

## [0.3.0] — 2026-05-14 — Slots 004 → 009 (operator-requested follow-ups)

### Added

- **`parent_role: str | None` on RoleDefinitionConfig** — proposal
  004.  First-class subrole hierarchy.  Default `None` keeps every
  existing role working with no migration.
- **Migrated `coding_agent_*` roles** declare
  `parent_role: coding_agent`.  Research roles stay flat
  (no top-level `research` parent).
- **Ecosystem subrole listing prefers declared parent_role.**  Two-
  pass lookup in `_subrole_siblings`: declared (scans every
  role.yaml's `parent_role`) → falls back to directory-name glob
  for unmigrated roles.  Markdown section label flips between
  "Subroles (declared)" and "Subroles (directory-derived)" so
  operators see which surface populated the list.
- **`acc/scheduler` package** — `Schedule` dataclass +
  `ScheduleStore` (YAML round-trip) + `next_fire_time` cron
  evaluator (subset: `* * * * *`, `*/N * * * *`, `M * * * *`,
  `0 H * * *`, `M H * * *`).  Proposal 005.
- **`acc-cli schedule` subcommand group** — `add` / `list` /
  `remove` / `run-once`.  Run-once is the daemon entry-point;
  operator wires it into cron / systemd-timer / Windows Task
  Scheduler.  Fires due schedules as TASK_ASSIGN signals on
  `acc.{cid}.task` with `from_agent=acc-scheduler`,
  `task_type=SCHEDULED`, `plan_id=schedule-<name>`.
- **`schedules/_example.yaml`** + `.gitignore` entry for
  `schedules/*.yaml` (operator-local schedules stay out of git;
  `_example.yaml` ships in-repo as a template).
- **`docs/role-authoring.md`** — boundary doc codifying the
  proposal 003 §10 memo: role.md owns narrative, role.yaml owns
  identity + defaults, Nucleus owns per-infusion deltas, Prompt
  owns task content only.  Proposal 006.
- **`acc-cli role audit <name>`** — content-drift linter.
  Codes LINT001 (yaml missing) → LINT005 (md H1 unrelated to
  yaml purpose).  Warnings-only by default; `--strict` exits 1.
  Heuristic substring-match for shared morphology (`research`
  matches `researcher`).
- **TUI Infuse form parity with CLI** — proposal 008.  The
  TUI's `action_apply` now loads the selected role's full
  `RoleDefinitionConfig.model_dump()` from disk and overlays the
  9 form fields, so the published `role_definition` is a
  superset of the CLI's wire shape (previously the TUI dropped
  ~6 fields).  `category_b_overrides` preserves disk-only keys
  and overlays only `token_budget` + `rate_limit_rpm`.  Closes
  the known parity gap noted in v0.2.0.
- **TUI Ecosystem: "Edit role.yaml" + "Edit role.md" buttons.**
  Proposal 007.  Spawns the operator's `$EDITOR` (resolved via
  env var with `$VISUAL` + platform fallback) on the selected
  role's files.  Non-blocking `Popen`; file-watcher from
  proposal 003 PR-3 catches the save and refreshes the detail
  pane.  Missing `role.md` is auto-created with a stub
  template + pointer at `docs/role-authoring.md`.

### Removed

- **TUI Ecosystem: Skills + MCPs + Active LLM Backends widgets.**
  Proposal 009.  These three tables (kept on Ecosystem for one
  release as a back-compat migration aid in proposal 003 PR-4)
  are removed.  Canonical home is the Configuration pane
  (pane 8) since v0.2.0.  Upload buttons (`Upload skill` /
  `Upload MCP`) move along with them.  Tests targeting the
  removed widgets are deleted; coverage lives in
  `tests/test_configuration_screen_pilot.py`.

## [0.2.0] — 2026-05-14 — TUI usability hardening (proposal 003)

Closes proposal 003 (operator's Obsidian vault — `ACC
Implementation/003 - ACC TUI usability hardening.md`).  Six PRs
landed on main between 2026-05-13 and 2026-05-14: #54 (PR-1),
#55 (PR-2), #56 (PR-3), #57 (PR-4), #58 (PR-5), #59 (PR-6).

### Added

- **TUI Ecosystem: `role.md` narrative surface.**  The role detail
  panel now reads `roles/<name>/role.md` alongside `role.yaml` and
  renders it in a `Markdown` widget at the top of a two-section
  collapsible.  The raw yaml is preserved under a second
  collapsible (closed by default).  Roles without a `role.md`
  show a friendly placeholder pointing operators at the
  forthcoming authoring guideline (slot 006).  (PR-2 of proposal
  003 — PR #55.)
- **TUI Ecosystem: role search filter.**  An `Input` widget above
  the ROLE LIBRARY DataTable narrows the visible rows by
  case-insensitive substring match against name / domain /
  persona.  Clearing the input restores the full list.  Backed by
  an in-memory cache (`_all_role_rows`) so the filter doesn't
  re-read disk per keystroke.  (PR-2.)
- **TUI Ecosystem: roles/ directory watcher.**  A polling task
  (default 2 s; configurable via `ACC_TUI_ROLE_WATCH_INTERVAL_S`)
  diffs a fingerprint of role names + per-file mtimes and posts
  a `RolesChangedMessage` when external edits to `role.yaml` or
  `role.md` are detected.  The handler reloads the role cache +
  re-applies the current filter substring (preserved across
  refresh) + re-renders the detail pane for the active row.
  Operator gets a 3-second toast confirming the refresh.
  (PR-3 of proposal 003 — PR #56.)
- **TUI Ecosystem: advisory selection lock.**  Selecting a role
  row takes an advisory `filelock.FileLock` on the role's
  `role.yaml`.lock; released on row change, screen unmount, or
  process exit.  Lock failure (another process holds it) surfaces
  as a warning toast — the operator can still proceed.  Most
  external editors ignore advisory locks, so this primarily
  protects against two TUI sessions stomping on each other.
  (PR-3.)
- **`RolesChangedMessage`** public message added to
  `acc/tui/messages.py` (PR-3).
- **TUI: Configuration pane (pane 8).**  New `ConfigurationScreen`
  at `acc/tui/screens/configuration.py` with three tabs:
  *LLM Endpoints*, *Skills*, *MCPs*.  Reachable via the new `8`
  keybinding from any screen.  (PR-4 of proposal 003 — PR #57.)
- **TUI: LLM Endpoints tab.**  Shows the configured
  `ACCConfig.llm` summary (backend, model, base_url, timeout) as
  read-only text plus a live per-agent table fed from snapshots.
  *Test connection* button HEAD-pings the configured `base_url`
  via stdlib `urllib.request` (no new dependency) and surfaces
  latency + status / unreachable reason.  Writing back to
  role.yaml under a new `llm_endpoint` key is deferred to a
  follow-up.  (PR-4.)
- **TUI: Skills + MCPs tabs (canonical home).**  The Skills and
  MCP-servers tables (plus their *Upload skill* / *Upload MCP*
  file-picker flows) now have their canonical home on the
  Configuration pane.  The Ecosystem copies remain for one
  release as a migration aid; a follow-up PR will remove them.
  (PR-4.)

### Changed

- **NavigationBar extended to 8 panes.**  Module docstring + key
  list + `BINDINGS` updated; every screen's local BINDINGS list
  now includes `("8", "navigate('configuration')",
  "Configuration")`.  (PR-4.)
- **Snapshot fan-out** in `acc/tui/app.py:_apply_snapshot` now
  pushes the active snapshot into the Configuration screen too,
  so its live LLM-backends table refreshes per HEARTBEAT.  (PR-4.)
- **TUI Performance: per-agent table extended.**  New columns
  Cluster, Intent, Subagents, Active task.  Cluster cell shows
  the short cluster_id when the agent is a member of an active
  cluster (sourced from `snap.cluster_topology`); Intent shows
  the first 80 chars of the agent's `task_progress_label`;
  Subagents shows the cluster's total member count; Active task
  shows `current/total` step + age since last heartbeat.
  (PR-5 of proposal 003 — PR #58.)
- **TUI Performance: CLUSTER OVERVIEW panel.**  Reuses the
  ClusterPanel widget from the Prompt screen so the same
  rendering produces consistent cluster_id / target_role /
  members / skill_in_use info across screens.  (PR-5.)
- **TUI Soma / Dashboard: governance counters get definitions.**
  Each Cat-A / Cat-B / Cat-C counter row is paired with a
  one-line definition pulled from a single
  `GOVERNANCE_TAXONOMY` constant at `acc/tui/screens/dashboard.py`
  module bottom (not view-hardcoded so the taxonomy text is
  editable in one place).  (PR-5.)
- **TUI Soma / Dashboard: TOKEN BUDGET BY CLUSTER panel.**  New
  panel rolls up per-agent `token_budget_utilization` grouped by
  `cluster_topology` membership; renders one row per active
  cluster as `cluster_id · target_role · N agents · avg X% /
  worst Y%` with colour coding (green < 75% / yellow < 90% /
  red ≥ 90% on worst single agent).  Empty state shows a calm
  placeholder.  (PR-5.)
- **TUI Ecosystem: directory-derived subrole listing.**  When the
  selected role has sibling directories matching `<role>_*` glob
  with a `role.yaml` (e.g. `coding_agent` → `coding_agent_architect`,
  `coding_agent_implementer`, …), they're listed under a "Subroles
  (directory-derived)" markdown section appended to the detail
  pane's `role.md` view.  Labelled explicitly as directory-derived
  because the first-class `parent_role` field is deferred to
  proposal 004.  (PR-6 of proposal 003 — PR #59.)

### Tests

- **CLI ↔ TUI infuse parity** (`tests/test_infuse_parity.py`,
  NEW, 7 cases).  Pins the ROLE_UPDATE payload envelope as
  byte-equivalent across both surfaces (modulo `ts`), pins the
  role_definition intersection-only parity (recursive — covers
  nested `category_b_overrides`), documents the TUI form's
  known field omissions vs the CLI's full pydantic
  `model_dump()` so regression in either direction surfaces
  immediately, and asserts that neither path leaks
  secret-shaped tokens (`api_key=` / `password=` / …) in the
  payload string form.  (PR-6.)

### Known parity gap (deferred follow-up)

The TUI Infuse form emits a 9-field subset of the full
`RoleDefinitionConfig`.  The CLI emits the full pydantic
`model_dump()`.  The test suite pins this state as the current
reality; closing it (either by extending the TUI form or by
teaching the arbiter to default-fill missing keys) is tracked
as out-of-scope and deferred to a follow-up proposal.

### Fixed

- **TUI Prompt: cancel-on-timeout.**  The Prompt screen now
  publishes `TASK_CANCEL` on `acc.{cid}.task.cancel` when the
  receive loop times out, instead of silently abandoning the
  in-flight task.  Without this fix, vLLM / llama.cpp backends
  kept generating against the dropped task; the operator's work
  was discarded and the late `TASK_COMPLETE` landed on a screen
  the operator had moved past.  (PR-1 of proposal 003 — PR #54.)

### Changed

- **TUI Prompt timeout default** raised from 60 s to 180 s for
  slow local LLM backends.  Configurable via the
  `ACC_PROMPT_TIMEOUT_S` environment variable.  (PR-1.)
- **TUI Prompt transcript message** when receive times out now
  reads "(cancelled after Ns — no reply; TASK_CANCEL published)"
  instead of "(timeout after 60s — no reply)".  Reflects the
  fact that the system actually cancelled rather than gave up.
  (PR-1.)

### Added

- `CHANGELOG.md` (this file) — Keep-a-Changelog format,
  introduced alongside the proposal 003 development cycle.
- `acc/tui/screens/prompt.py:_resolve_timeout()` helper —
  reads `ACC_PROMPT_TIMEOUT_S` with safe fallback to the
  default; warns on malformed / non-positive values.  (PR-1.)
- `acc/tui/screens/prompt.py:_mark_cancelled()` /
  `_is_cancelled()` — 256-entry FIFO of task_ids cancelled by
  the timeout path, for late-TASK_COMPLETE suppression.  Public
  API; not yet wired into the agent-entry append path (the
  channel layer already returns on first signal, so no
  late-reply hazard exists today).  (PR-1.)

## [0.1.0] — pre-proposal-003 baseline

Reconstructable from `git log`; not back-filled here.  Notable
landmarks for context:

- Sub-agent clustering (PRs #26–#30).
- Autoresearcher demo + iteration loop (PRs #41–#46).
- Operator (Kubernetes/OpenShift) scaffold (PRs #47–#51).
- Podman Desktop extension sibling repo
  (`flg77/acc-podman-desktop`) shipped to v0.3.0 in parallel.
