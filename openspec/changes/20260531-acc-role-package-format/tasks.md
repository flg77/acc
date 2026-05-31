# Tasks — `20260531-acc-role-package-format`

## Phase 1 — format + reference CLI

Purely additive. Existing `roles/` tree untouched.

- [ ] `openspec/specs/role-package/spec.md` — formal manifest schema
      v1 with SHALL requirements (signature mandatory; schema_version
      forward-compat; dependency resolution semantics).
- [ ] `acc/packaging/__init__.py` — module skeleton.
- [ ] `acc/packaging/accpkg.py` — Pydantic v2 `AccPkgManifest` model
      with `extra="forbid"`. Mirrors `RoleDefinitionConfig` pattern.
- [ ] `acc/packaging/builder.py` — `build(src_dir, out_path)`:
  - Validate manifest.
  - Tarball the layout (`accpkg.yaml`, `role.yaml`,
    `governance.yaml`, `skills/`, `mcps/`, `memory-seed/`,
    `policy/`, `golden-prompts/`, `tests/`, `docs/`).
  - Cosign-sign manifest; write `signatures/manifest.sig`.
  - Generate in-toto provenance; write `signatures/provenance.json`.
- [ ] `acc/packaging/verifier.py` — `verify(pkg_path)`:
  - Schema validation (`schema_version` supported).
  - Cosign signature check.
  - Capability check: `acc_core.min_version` ≤ local ACC ≤ `max_version`.
  - Dependency probe: MCP refs reachable (skip if `--offline`).
- [ ] `acc/packaging/installer.py` — `install(pkg_path, target_dir)`:
  - Verify first; refuse on failure unless `--allow-unsigned` set
    (audit-logged).
  - Unpack to `/var/lib/acc/packages/<scope>/<name>-<version>/`.
  - Register with arbiter (publish on a new
    `subject_package_registered(cid)` so the gatekeeper can propose
    infusion).
  - **Do not auto-infuse.** Install ≠ infuse.
- [ ] `acc/packaging/cli.py` — `acc-pkg` entry point:
  `build / verify / install / inspect / list` subcommands.
- [ ] `acc/signals.py` — `subject_package_registered(cid)` →
      `acc.{cid}.package.registered`.
- [ ] `examples/accpkg/hello-role/` — reference golden package with
      all manifest fields populated; minimal `role.yaml` that prints
      "hello from a packaged role"; one bundled skill; one inline
      MCP stub; one golden prompt.
- [ ] `docs/role-package-format.md` — operator-facing reference:
      what's in a `.accpkg`, how to build one, how the maturity
      ladder works, how trust is verified, how to install.
- [ ] Tests:
  - `tests/test_accpkg_manifest_schema.py` — schema validation
    (required fields; SemVer; category enum; `schema_version`
    handling).
  - `tests/test_accpkg_build.py` — build the hello-role fixture;
    assert tarball layout + signature presence.
  - `tests/test_accpkg_verify.py` — verify happy path; verify
    refuses tampered manifest; verify refuses wrong `acc_core`
    range; `--allow-unsigned` path audit-logged.
  - `tests/test_accpkg_install.py` — install happy path; install
    refuses on verify failure; `subject_package_registered`
    published.
  - `tests/test_accpkg_dependency_resolution.py` — multi-package
    install honours `depends_on.roles[]` ordering.

## Phase 2 — coding_agent migration — DEFERRED

Gates on Phase 1 landed + reviewed.

- [ ] Extract `roles/coding_agent/` into a standalone repo
      (`acc-community/coding-agent`).
- [ ] Author `accpkg.yaml` for `@acc-community/coding-agent@1.0.0`.
- [ ] GitHub Actions workflow: build + sign + publish on tag.
- [ ] Document the migration as a template repo.
- [ ] ACC core: keep the in-tree `coding_agent` for compatibility
      during the migration window; add a deprecation warning on boot
      when both in-tree + packaged versions present.

## Phase 3 — ACC core slim — DEFERRED

Gates on Phase 2 successful + ≥ 1 month of dual operation telemetry.

- [ ] Extract: `coding_agent`, `coding_agent_tester`, `ingester`,
      `synthesizer`, `analyst`, `research_planner`.
- [ ] `acc-deploy.sh up` auto-fetches the workspace baseline set on
      first install.
- [ ] `acc-deploy.sh up --bare` skips bootstrap (edge mode).
- [ ] `acc-deploy.sh up --classic` retains v0.3.x in-tree behaviour
      for the migration window.
- [ ] Edge image build target: `make acc-edge` produces the
      substrate-only image (~80–120 MB).
- [ ] Migration runbook for existing operators.

## Phase 4 — Hub MVP — DEFERRED

- [ ] Static read-only hub layout: `index.json`, `packages/<name>/
      meta.json`, `packages/<name>/<version>/*`, `reviews/<name>/
      <version>.jsonl`.
- [ ] `acc-hub fetch <name>[@<version>]` CLI.
- [ ] `acc-hub search <query>` (client-side filtering of
      `index.json`).
- [ ] GitHub Pages reference deployment for `acc-roles.dev`
      (or operator-self-hosted variant).
- [ ] Maturity tier reflected in `meta.json`; client respects
      default-tier filter.

## Phase 5 — Hub v1 — DEFERRED

- [ ] Publish API (auth via GitHub OIDC).
- [ ] Reviews + ratings API.
- [ ] Search API.
- [ ] Maturity-tier promotion automation (community count +
      curator veto).
- [ ] Verified-publisher tier (process design in sibling proposal).
- [ ] TUI Marketplace pane (search → preview → install gated by
      AoA-P2b queue).

## Phase 6 — Domain-specialist packaging at scale — DEFERRED

- [ ] First community package: `@acc-community/clinical-reviewer`
      with PubMed MCP, ClinicalTrials.gov MCP, GRADE summarisation
      skill, memory seed of 50 starter notes.
- [ ] Template repo: `acc-community/role-package-template`.
- [ ] CI/CD template (build + sign + publish on tag).
- [ ] Documentation site under `acc-roles.dev/docs/`.

## Phase 7 — Cross-hub federation — DEFERRED

Gates on `20260527-a2a-agent-interop` Phase 3+.

- [ ] Federation protocol design — how hubs cross-discover packages
      via AgentCard + A2A.
- [ ] Hub mirroring (offline + air-gap support).
- [ ] Multi-hub `acc-hub fetch` with fallback ordering.

## Follow-on proposals to spawn after Phase 1 lands

- [ ] `20260601-acc-role-package-hub-mvp`
- [ ] `20260601-acc-role-package-coding-agent-migration`
- [ ] `20260601-acc-role-package-offline-bundles`
- [ ] `20260601-acc-role-package-tui-marketplace-pane`
- [ ] `20260601-acc-role-package-federation-protocol`
- [ ] `20260601-acc-role-package-telemetry-standard`
- [ ] `20260601-acc-role-package-verified-publisher-process`
