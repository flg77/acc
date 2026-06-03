# 20260603-acc-pkg-pilot — tasks

## Phase 1 (v0.3.54) — Stage 0 pilot: `acc-pkg` minimal CLI + coding_agent extraction

### 1.1 Manifest schema v1
- [ ] `acc/pkg/manifest.py` — Pydantic `AccPkgManifest`:
  - `name: str` (`@scope/name` format, validator)
  - `version: str` (semver, validator)
  - `depends_on: list[Dependency]` where Dependency has
    `name` + semver constraint (`^X.Y`, `~X.Y.Z`, `>=X.Y`)
  - `roles: list[RoleRef]` (path within package)
  - `skills: list[SkillRef]` + tier (`core_baseline` skills MUST
    NOT appear in the package — validation refuses)
  - `mcps: list[McpRef]` + tier
  - `signed_dep_closure: list[str]` (sha256s; Stage 1 will
    populate; Phase 1 stub is empty list)
- [ ] JSON Schema export at `acc/pkg/schema/accpkg-v1.json`.
- [ ] Tests: name/version/semver validators, refusal on
  core-baseline skill leakage, refusal on cyclic depends_on.

### 1.2 Build / install / verify-stub
- [ ] `acc/pkg/build.py` — `build(source_dir, output_path)`:
  - Validate manifest
  - Deterministic tarball (sorted entries, fixed mtime, no uid/gid)
  - sha256 over content → `accpkg.yaml#content_sha256`
- [ ] `acc/pkg/install.py` — `install(pkg_path, root)`:
  - sha256 check
  - manifest schema validation
  - topological sort on `depends_on:` (refuses cycle)
  - unpack to `<root>/<scope>/<name>-<version>/`
  - update `<root>/registry.json` under flock
- [ ] `acc/pkg/verify.py` — **real cosign verify** (per brainstorm
  Q3b signing floor):
  - sha256 verify
  - manifest schema validate
  - `cosign verify-blob` against the resolving catalog's
    `required_signer:` (issuer + subject_pattern)
  - REFUSES on signer mismatch — no exceptions per tier
  - Prints `WARNING: Enterprise Contract policy depth is Stage 1`
    when the package lacks attestations beyond signature
- [ ] `acc/pkg/catalog.py` — layered catalog loader:
  - Loads `/etc/acc/catalogs.yaml` → `~/.acc/catalogs.yaml` →
    `<workspace>/.acc/catalogs.yaml`
  - Pydantic `Catalog` model (id, tier, mode, url|path,
    required_signer, priority)
  - `resolve(name, version) → ResolvedPackage | None`
  - Walks layers narrow→broad, sorts by priority within layer
  - Returns alternates list for Compliance pane display
  - Supports `mode: https` (fetch index.json) and `mode: file`
    (glob `<path>/<scope>/<name>-*.accpkg`)
- [ ] Default `/etc/acc/catalogs.yaml.example` shipped with ACC
  containing the `acc-canonical` (trusted) + `community-public`
  entries; operator-installed sample.
- [ ] `acc/pkg/registry.py` — flock-protected JSON registry index;
  `add() / remove() / list() / find_by_dep()`.
- [ ] Tests: roundtrip (build → install → list), sha256 mismatch
  refusal, cyclic dep refusal, flock concurrency (10 parallel
  installs), tmpdir `ACC_PACKAGES_ROOT`.

### 1.3 CLI
- [ ] `acc/pkg/cli.py` — argparse:
  - `python -m acc.pkg build <src> -o <out>`
  - `python -m acc.pkg install <pkg>`
  - `python -m acc.pkg verify <pkg>`
  - `python -m acc.pkg inspect <pkg>` (pretty-print manifest)
  - `python -m acc.pkg list` (registry contents)
- [ ] Console script entry in `pyproject.toml`: `acc-pkg = acc.pkg.cli:main`.
- [ ] **Automation-friendly contract** (per brainstorm Q3 matrix):
  - `--quiet` suppresses all non-error stdout
  - `--json` emits machine-readable output for `install / verify / inspect / list`
  - Idempotent re-install of same version → exit 0, no-op
  - Deterministic exit codes (`0` ok, `1` user error, `2` schema
    failure, `3` dep resolution failure, `4` sha256 mismatch,
    `5` unsigned-but-required)
  - No interactive prompts (operator-explicit `--allow-unsigned`
    flag rather than y/N)
- [ ] Tests: each subcommand's exit code + happy path; idempotent
  re-install; `--json` output validates against documented schema;
  `--quiet` produces zero stdout on success.

### 1.4 Skill/MCP tier classification
- [ ] `tools/classify_skills_mcps.py` — walks `roles/*/role.yaml`,
  collects skill + MCP refs, classifies each as:
  - `core_baseline` if listed in v0.3.50 stdlib set (12 skills +
    arxiv/wikipedia/semantic_scholar MCPs)
  - `bundle_in_role` if referenced by ≤1 movable role
  - `own_pack` if referenced by ≥2 movable roles AND not in baseline
- [ ] Emit `tools/skill_mcp_tiers.yaml` (committed; reviewable).
- [ ] Test: every skill + MCP referenced by ANY of the 44 movable
  roles appears in the tier YAML exactly once.

### 1.5 In-place pilot build tooling
- [ ] `tools/build_pilot_pkg.py <role-name>`:
  - Reads `roles/<name>/role.yaml`
  - Reads `tools/skill_mcp_tiers.yaml`
  - Assembles an ephemeral build tree in `build/pilot/acc-<name>/`
    by copying role.yaml + tier-2 skills + tier-2 MCPs (source
    stays in `acc/roles/` — copy, don't move)
  - Generates `accpkg.yaml` from the role's `allowed_skills` +
    `allowed_mcps` + tier classifications
  - Refuses if any referenced skill/MCP is unclassified
  - Invokes `acc-pkg build` on the assembled tree → produces
    `dist/acc-<name>-0.1.0.accpkg`
- [ ] Test: build `coding_agent`, verify generated `accpkg.yaml`
  validates against schema v1, verify `acc/roles/coding_agent/` is
  byte-identical before/after build.

### 1.6 Internal `acc1` Kubernetes hub bootstrap
- [ ] Operator-side step (documented, not automated):
  - Deploy minimal HTTPS endpoint on the team's `acc1` k8s
    cluster: nginx Deployment + Service + Ingress + ConfigMap-
    backed `index.json` + PVC for `.accpkg` blobs.
  - Ingress at `https://acc-hub.acc1.internal/` (or chosen
    hostname); TLS via existing acc1 cluster certs.
  - Document in `docs/acc-pkg.md` Stage-0 section.
- [ ] `examples/catalogs.dev.yaml` (NEW) declares the internal
  hub as a `trusted` tier catalog with `required_signer:` pointing
  at the team's development cosign identity (generated by
  `tools/cosign-pilot-keygen.sh`).
- [ ] Stage-0 publish path is manual: `kubectl cp` the
  `.accpkg` into the hub's PVC + edit `index.json` ConfigMap to
  list the new version. (Real `acc-pkg publish` is Stage 1.)

### 1.7a Catalog + signing tests
- [ ] Layered catalog precedence (workspace > user > system)
- [ ] Priority-within-layer collision resolution
- [ ] `mode: file` catalog resolves spearhead build output
- [ ] `mode: https` catalog resolves index.json (mocked)
- [ ] Alternates list populated when ≥2 catalogs advertise same pkg
- [ ] cosign verify REFUSES on signer issuer mismatch
- [ ] cosign verify REFUSES on subject_pattern mismatch
- [ ] Pilot keypair: operator generates local cosign keypair;
  test catalog declares it as required_signer; pilot pkg signed
  with that key; verify passes
- [ ] Unsigned pkg → install REFUSES with exit code 5 across all
  four tiers

### 1.7 Round-trip verification (the actual pilot)
- [ ] Test container fixture `tests/pkg/fixtures/vanilla_acc.dockerfile`:
  builds an ACC image with `roles/coding_agent/` REMOVED.
- [ ] Integration test `tests/pkg/test_pilot_roundtrip.py`
  (offline, no acc1 hub needed):
  1. Run `tools/build_pilot_pkg.py coding_agent` → produces
     `dist/acc-coding-agent-0.1.0.accpkg`.
  2. Sign with the pilot keypair.
  3. Spin vanilla container with a `mode: file` catalog pointing
     at the test dist dir.
  4. `python -m acc.pkg install @acc/coding-agent@0.1.0` inside
     the container.
  5. Run PR-K golden prompts for `coding_agent` against the
     installed role.
  6. Assert pass.
- [ ] Manual smoke test (operator-run, after acc1 hub bootstrapped):
  same flow but catalog is the live `https://acc-hub.acc1.internal/`
  endpoint. Validates the https code path end-to-end.

### Verification
- [ ] `pytest tests/pkg/ --no-cov -q` — all new pkg tests pass.
- [ ] `pytest tests/ --ignore=tests/container --no-cov -q` — full
  sweep stays at 2577+ passing.
- [ ] `pytest tests/pkg/test_pilot_roundtrip.py --no-cov -q` —
  pilot extraction round-trips through golden prompts.
- [ ] Manual: `acc-pkg verify <pkg>` prints the Stage-1 warning.
- [ ] Manual: `tools/skill_mcp_tiers.yaml` reviewed by operator
  before any extraction uses it.

## Phase 2 (deferred — Stage 1: trust chain)
- [ ] Real cosign signing in build pipeline
- [ ] Real `acc-pkg verify` (cosign + Enterprise Contract policy)
- [ ] Behavioral + safety eval YAML format
- [ ] `PROPOSE_INFUSE` handler in `acc/assistant_proposal.py`
- [ ] Konflux pipeline templates for `acc-ecosystem`

## Phase 3 (deferred — Stage 2: scale + public hub)
- [ ] Extract 5 coding_agent variants → `@acc/workspace-roles`
- [ ] Extract research_* → `@acc/research-roles`
- [ ] Extract business roles → `@acc/business-roles`
- [ ] Public `acc-ecosystem` mirror (Apache 2.0 + CLA)
- [ ] Hub MVP at `acc-roles.dev`
- [ ] `docs/CONTRIBUTING-ROLE.md`

## Phase 4 (deferred — Stage 3: edge)
- [ ] Bootc bundler with `--base {hummingbird|rhel-bootc|microshift|<custom>}`
- [ ] Air-gap bundle file (`--offline`)

## Strategic decisions captured in the parent brainstorm
- [x] Q1-Q8 + Q3a + Q3b answered in `<vault>\ACC Openspec\ACC Role Ecosystem\Ecosystem split — brainstorm.md`
- [x] Three independent repos (`acc-ecosystem`, `acc-web`, `acc-podman-desktop`) — repo split deferred to Stage 2
- [x] All packages ACC-owned for Stages 0–2; RH Foundation alignment is trajectory, not current state
- [ ] Operator-side: deploy internal hub on `acc1` Kubernetes (Phase 1.6 manual smoke test depends on this; the offline-catalog roundtrip test in 1.7 does not)
