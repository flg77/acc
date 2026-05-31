# OpenSpec — ACC role package format + community hub

| Field | Value |
|---|---|
| Change ID | `20260531-acc-role-package-format` |
| Status | Proposed (Phase 1 ready to implement) |
| Sibling | `20260527-agentcard-discovery` (post-install discovery surface) · `20260527-a2a-agent-interop` (cross-hub transport) |
| Companion | `20260530-assistant-agent-of-agents` (gatekeeper proposes infusion of packaged roles) |
| Notes mirror | `Notes/Development/AgenticCellCorpus/ACC Openspec/20260531-acc-role-package-format — OpenSpec (proposed).md` |
| Brainstorm | `Notes/Development/AgenticCellCorpus/ACC-Role-Format/Role package format + community hub — brainstorm.md` |

## Problem statement

Every ACC role today lives in-tree under `roles/<name>/role.yaml`,
with its skills + MCP wrappers + governance bindings + golden prompts
scattered across the monorepo. The roster of useful roles has grown
faster than the ACC core; `coding_agent` / `clinical_reviewer` /
`research_planner` have **nothing in common with the bus + governance +
TUI** that ACC owns, yet they ship together as one image and one
release cadence. Three concrete pain points:

1. **Edge deployments carry every role.** A field-deployed
   `equipment_diagnostician` on a Pi pulls the full image because we
   can't ship roles separately. Edge mode (`Edge-Hub-A2A topology`)
   is functionally crippled by image size.
2. **Communities can't contribute domain roles.** There is no signed,
   versioned, distributable unit for "a clinical reviewer role with
   PubMed access and GRADE summarisation." Today every domain role
   is upstreamed into the monorepo or maintained as a private fork.
3. **No trust + maturity story.** Operators on regulated stacks need
   to know "this role is community-vetted to maturity tier X with
   signed provenance from publisher Y." Today they have neither.

The proposal: extract roles into **versioned, signed packages** (`.accpkg`)
with their skills + MCPs + memory seeds + governance bounds + golden
tests bundled; distribute them via a **community hub**; slim ACC core
to *just the substrate* (bus, cognitive core, governance, gatekeeper
control plane). Edge nodes pull exactly one specialist package; hub
nodes pull many.

## Design decisions (bootstrap defaults from the brainstorm's 10 questions)

1. **Format** — Tar.gz `.accpkg` is canonical; OCI artifact via ORAS
   is a distribution wrapper around the same tarball. Both.
2. **Hub** — Single canonical hub (`acc-roles.dev` or operator-self-
   hosted) for Phase 4 MVP; federation deferred to Phase 7.
3. **Naming** — Scoped (`@community/role`). NPM-proven; scales to
   community + vendor coexistence.
4. **Trust** — Cosign keyless OIDC for mandatory signing; SPIFFE
   binding optional when the operator's hub runs SPIRE.
5. **Maturity authority** — Hybrid. Community reviews count;
   curators veto promotions; verified-publisher tier short-circuits
   community-count thresholds.
6. **Migration pilot** — `coding_agent` (workspace category).
   Highest value, broadest test surface, no domain-specific MCPs to
   broker.
7. **Bootstrap** — ACC core auto-fetches a baseline package set
   (workspace tier) on first install; `acc-deploy.sh up --bare`
   skips bootstrap for edge / air-gap.
8. **Verified publisher** — Process design deferred to Phase 5.
   First verified-publisher candidate: Red Hat for RHOAI packages.
9. **MCP packaging** — Hybrid. Manifest references
   `modelcontextprotocol/registry` entries by default; inline
   bundling allowed for air-gap or unpublished MCPs.
10. **Telemetry** — Opt-in only. Signals: drift, policy_update
    cadence, infusion count, Cat-C denial rate. Aggregated; no
    per-operator data.

## Three lifted invariants (table stakes — every phase)

1. **Substrate ≠ role.** The ACC core never hard-depends on a
   specific packaged role. Removing all packages must leave a
   bootable substrate.
2. **Signed-or-refused.** ACC core verifies cosign signature on
   every package before infuse. Unsigned packages refused under
   default `verified-only` mode; `--allow-unsigned` is operator-
   explicit and audit-logged.
3. **Maturity-gated default.** Marketplace UI defaults to `stable`
   tier + above. Operators on regulated stacks (compliance pane
   shows AI-Act-classified roles) inherit this default; hobbyists
   can opt down.

## Phase summary

| Phase | Status | Deliverable |
|---|---|---|
| 1 | Proposed | `.accpkg` format spec v1 + `acc-pkg build/verify` reference CLI |
| 2 | Deferred (Phase 1 must land) | Migrate `coding_agent` to a package; document as template |
| 3 | Deferred | ACC core slim — extract workspace + light-utility roles into packages; bootstrap pre-pull |
| 4 | Deferred | Hub MVP — static read-only on GitHub Pages / S3; `acc-hub fetch` CLI |
| 5 | Deferred | Hub v1 — publish API, ratings, search, verified-publisher tier, TUI Marketplace pane |
| 6 | Deferred | Community domain-specialist packages at scale (clinical, legal, research) |
| 7 | Deferred | Cross-hub federation via A2A + AgentCard (gates on `20260527-a2a-agent-interop` Phase 3+) |

## Phase 1 design (the format + reference CLI)

### `.accpkg` layout

```
<scope>-<name>-<version>.accpkg          (tar.gz)
├── accpkg.yaml                          # manifest (schema v1)
├── role.yaml                            # role definition
├── governance.yaml                      # Cat-A/B/C bindings
├── skills/                              # bundled skills
│   ├── <skill-name>.py
│   └── skills.yaml
├── mcps/                                # MCP refs or inline
│   └── mcps.yaml
├── memory-seed/                         # optional
│   └── notes.jsonl
├── policy/                              # community-vetted SIP bounds
│   └── policy-bounds.yaml
├── golden-prompts/                      # smoke tests
│   └── smoke.yaml
├── tests/                               # role-level integration
├── docs/
│   ├── ROLE_CARD.md
│   └── CHANGELOG.md
└── signatures/
    ├── manifest.sig                     # cosign
    └── provenance.json                  # in-toto attestation
```

### `accpkg.yaml` schema (v1, abbreviated)

Full schema lives in `openspec/specs/role-package/spec.md`. Key fields:

- `schema_version: 1`
- `name`, `version` (SemVer, independent of acc-core)
- `maintainer[]` (name / email / org)
- `category: control | workspace | domain-specialist`
- `domain: <free-form>` (the Hub indexes this)
- `acc_core: { min_version, max_version, features_required[] }`
- `depends_on: { roles[], skills[], mcps[] }`
- `governance: { default_category, policy_bounds_signed_by,
  ai_act_classification, data_residency }`
- `infusion: { default_workspace_access, default_operating_mode,
  recommended_model }`
- `policy: { pinned_default[], drift_cap_default,
  update_every_n_tasks_default }`
- `community: { maturity, reviewers[], rating_avg, rating_count }`
  (set by Hub; ignored on local install)
- `provenance: { source_repo, source_commit, built_by, built_at,
  attestation, cosign_signature }`

### Phase 1 deliverables

- **`acc/packaging/` module:**
  - `accpkg.py` — manifest model (Pydantic v2; `extra="forbid"`)
  - `builder.py` — `acc-pkg build <dir> -o <name>.accpkg` (tarball
    + signed manifest)
  - `verifier.py` — `acc-pkg verify <pkg>` (cosign signature +
    schema validation + dependency capability check)
  - `installer.py` — `acc-pkg install <pkg>` (unpacks to
    `/var/lib/acc/packages/<scope>/<name>-<version>/`; registers
    with arbiter for infusion; no automatic infuse)
- **`acc-pkg` CLI** — `build / verify / install / inspect / list`
  subcommands; matches the `acc-deploy.sh` shell style.
- **`openspec/specs/role-package/spec.md`** — formal manifest schema
  + SHALL requirements (signature mandatory; schema_version forward-
  compat rules; dependency resolution semantics).
- **Reference golden package** — `examples/accpkg/hello-role/` — a
  minimal `hello-role.accpkg` that prints "hello from a packaged
  role" and exercises every manifest field. Used by CI + docs.
- **Tests:** `tests/test_accpkg_build.py`,
  `tests/test_accpkg_verify.py`, `tests/test_accpkg_install.py`,
  `tests/test_accpkg_manifest_schema.py`.

### Phase 1 does NOT include

- Hub. Not built. Packages are local-file artifacts in Phase 1.
- ACC core slimming. The existing `roles/` tree is untouched. The
  package format coexists with the in-tree role system.
- TUI Marketplace pane. Defer to Phase 5.
- Federation. Defer to Phase 7.
- Migration of `coding_agent`. Defer to Phase 2.

The Phase 1 deliverable is **purely additive**: a new format +
tooling. Nothing existing breaks. Operators ignore it entirely until
they want to publish or install their first package.

## Maturity ladder (community-rated)

| Tier | Bar |
|---|---|
| `alpha` | Author-published; no community review. Golden prompts may fail. |
| `beta` | ≥ 1 community reviewer; golden prompts pass on reference ACC core. |
| `stable` | ≥ 3 reviewers across distinct orgs; integration tests pass; **policy bounds vetted**; ≥ 30 days since publish. |
| `hardened` | ≥ 6 reviewers; ≥ 90 days production telemetry from ≥ 2 operators; security review complete; SLA on critical-issue response. |

Tier is **earned, not declared.** Manifest's `maturity:` is the
*requested* tier; the Hub sets the awarded tier on accept.

## Trust model

- **Mandatory cosign signature** on every `.accpkg`. ACC core refuses
  unsigned packages under default config.
- **Keyless OIDC** (GitHub Actions identity for org packages; user
  key for individuals). No CA infrastructure on the operator side.
- **in-toto attestation** records build inputs (source commit,
  toolchain versions, test results).
- **Optional SPIFFE binding** — packages can carry a SPIFFE-ID stub
  that the operator binds at infuse time when SPIRE is available;
  post-install AgentCards then carry the same identity.

## Restructured ACC core (Phase 3 target — DEFERRED)

### Stays in core

Bus · cognitive_core · agent runtime · policy_layer · governance
primitives · oversight_queue · `assistant` (gatekeeper) ·
`arbiter` · `compliance_officer` · `dreamer` (when proposed lands) ·
TUI · backends · `acc-deploy.sh` + lifecycle-watcher.

### Moves to packages

`coding_agent`, `coding_agent_tester`, `ingester`, `synthesizer`,
`analyst`, `research_planner`, all domain specialists, all
workspace-bound MCP wrappers, all golden-prompt suites except the
substrate smoke baseline.

### Edge mode after Phase 3

`acc-edge-image` ≈ 80–120 MB = substrate + ONE infused specialist
package. No gatekeeper, no compliance pane, no dreamer (verdicts
forward to hub via ACC-9 NATS bridge — see
[[40 no orig Spec - ACC-9 bridge deprecation path via A2A Phase 3 - followup - 20260531]]).

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Supply-chain attack (malicious package) | Cosign + in-toto + maturity-gated install + verified-publisher tier; default to `stable`+ on regulated stacks |
| Format drift (v1 manifest → v2) | `schema_version` field; ACC core supports last N schemas; deprecation warnings; in-tree migration tooling |
| Hub single point of failure | Mirroring + federation (Phase 7) + offline bundle support from Phase 1 |
| Community ratings gameable (sockpuppets) | Verified-publisher tier; reviewer karma weighting; org-distinct reviewer requirement for stable |
| ACC core too aggressive in slimming (breaks operators) | `acc-deploy.sh up --classic` retains v0.3.x behaviour through Phase 3 migration; bootstrap pre-pull keeps workspace roles available by default |
| Maintainer abandonment (stale packages) | Maturity demotion after N days without activity; auto-flag in Marketplace UI |
| Policy-bounds vetting rubber-stamped | Package-vetted bounds are *defaults*, not ceilings; operator can always override; SIP-P2's rails still gate |
| MCP runtime dependency hell | Pin MCP versions in manifest; capability test on infuse; refuse infuse if MCP unreachable |
| Edge node lacks identity | SPIFFE binding optional; SVID-less edge uses hub-issued install token |
| Fragmentation across hubs | Single canonical index format; federation protocol from Phase 7 |
| Regulatory burden on community domain roles | Cat-A/B/C bindings travel in package; `ai_act_classification` field; verified-publisher tier for regulated domains |

## Follow-on proposals (spawn after Phase 1 lands)

- `20260601-acc-role-package-hub-mvp` — static read-only hub
  + `acc-hub fetch` CLI (Phase 4 design).
- `20260601-acc-role-package-coding-agent-migration` — Phase 2
  pilot.
- `20260601-acc-role-package-offline-bundles` — air-gapped operator
  workflow.
- `20260601-acc-role-package-tui-marketplace-pane` — TUI search +
  install UX.
- `20260601-acc-role-package-federation-protocol` — Phase 7
  cross-hub discovery design.
- `20260601-acc-role-package-telemetry-standard` — opt-in field
  metrics for maturity auto-promotion + privacy guarantees.
- `20260601-acc-role-package-verified-publisher-process` — how an
  organisation becomes verified; Red Hat as first candidate.

## Linked

- Brainstorm:
  `Notes/Development/AgenticCellCorpus/ACC-Role-Format/Role package format + community hub — brainstorm.md`
- Discovery surface: `20260527-agentcard-discovery` — every
  installed package publishes one AgentCard.
- Cross-hub transport: `20260527-a2a-agent-interop` — Phase 7
  federation rides on this.
- Skill+MCP precursor: in-tree manifest delivery (PR-49/PR-51/PR-A2,
  note 06) — formalised and externalised by this proposal.
- Ecosystem editor: PR-A/B/C (note 07) — the Marketplace pane
  extends this.
- Gatekeeper: `20260530-assistant-agent-of-agents` — Phase 5 TUI
  Marketplace pane uses AoA-P2b queue to gate package installs.
- SIP: `20260530-acc-self-improvement-policy-gradient` —
  community-vetted policy bounds travel in `policy/policy-bounds.yaml`.
- Dreamer: `20260530-acc-dreaming-agent` — `memory-seed/` block in
  a package is its starting memory_notes corpus.
- Drift follow-ups closed by Phase 3:
  [[37 no orig Spec - Orchestrator routing superseded by AoA gatekeeper - followup - 20260531]],
  [[40 no orig Spec - ACC-9 bridge deprecation path via A2A Phase 3 - followup - 20260531]],
  [[42 no orig Spec - collective.yaml schema gains managed_sub_collectives - followup - 20260531]].
