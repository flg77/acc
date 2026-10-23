# 20261018-capability-digests — tasks

Independent of 20261018-plan-card and 20261018-grant-once-allowances; can run
in parallel with them after the 058 stack.

## Phase 1 — a checksum per capability, checked, listed, gated
### 1.1 Manifest and build
- [x] `SkillRef.sha256`, `McpRef.sha256`; manifest `schema_version` 2; reader accepts 1 and 2
- [x] `acc/pkg/build.py`: per-ref tree digest (same algorithm as `content_sha256`, scoped to `path`) — `tree_digest()`, relpaths inside the capability dir, `__pycache__`/bytecode skipped
### 1.2 Built-in manifest
- [x] `acc/pkg/builtin_capabilities.py` → `acc/pkg/data/builtin_capabilities.yaml` for `skills/*`, `mcps/*` (incl. `_base`); committed, drift test in `tests/test_capability_verify.py`
- [ ] **[op]** Release signs it (channel key → `builtin_capabilities.yaml.sig`, `cosign sign-blob`); package data ships `*.sig` (pyproject done); hosts set `ACC_CAPABILITY_MANIFEST_PUBKEY` to require it
### 1.3 Verify at load
- [x] `acc/capability_verify.py`: pack manifest first, then the built-in manifest; cached by mtime and size
- [x] Called from `acc/skills/registry.py` and `acc/mcp/registry.py` before adapter instantiation (a refused `_base` refuses its root)
- [x] Refusal message: capability, expected digest, actual digest, source, what to do
- [x] `ACC_CAPABILITY_VERIFY=report|enforce` (default `report` this release)
### 1.4 AgentBOM
- [x] `AgentBOMSpec.capabilities[]`; BOM schema bump (`spec.schema_version: 2`)
- [x] `acc-pkg bom resolve`, `acc-pkg bom verify --running`
### 1.5 Role QA gate
- [x] Posture PS-07, PS-08, PS-09 (PS-08 needs the new role field `sandbox: required`)
- [x] `acc-cli posture scan --fail-on <level>` (exit code), SARIF kept
- [x] `tests/test_role_profiles_posture.py` over `roles/`, installed packs, `collectives/*.yaml` (collectives: strict xfail on PS-04 until the presets are pinned, see 1.6)
- [x] Tekton `acc-build` gate: runs pytest, so the test file is the gate (no GitHub workflow in this repo); `acc doctor --check capabilities` lists what enforce would refuse
### 1.6 Fix the roles
- [x] Run the gate; fix `roles/*/role.yaml`. Before: 14 role findings at MEDIUM+ (12 × PS-09, 1 × PS-08 assistant, 1 × PS-02 capacity_advisor) + 14 × PS-04 in collectives. After: 0 role findings (1 LOW PS-03, by design); 14 × PS-04 remain
- [ ] **[op, other repo]** rebuild ecosystem packs with v2 manifests after the runtime release, then pin `collectives/*.yaml` `required_packages` to exact versions and drop the xfail
### Verification
- [x] `pytest tests/pkg tests/test_capability_verify*.py tests/test_posture*.py tests/test_role_profiles_posture.py`
- [x] lighthouse tamper test: refused with the message (enforce), logged (report) — 2026-10-19, staged c7ea1561
- [x] CI gate red on a fixture, green on main (pytest fixtures; live CI run pending)
- [x] `acc-pr-test` evidence doc — vault `70-evidence/ACC-Tests/061-capability-digests-lighthouse` (651 passed on lighthouse)

## Phase 2 (deferred)
- [ ] Image skills into packs (061 Q1 alternative)
- [ ] Operator `AgentBOM` CRD gains `capabilities[]`
