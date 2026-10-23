# 20261018-capability-digests — proposal

Vault proposal: `40-proposals/ACC Implementation/061 — Every capability has a
checksum`. Builds on 040 (A-BOM, `acc/pkg/agent_bom.py`) and
`20261015-ask-once-p5-standing-grants` (posture scan).

## Why

The operator, 2026-10-18: "acc-pkgs should have checksums of all attached
skills and MCP → agent bom! From now on we ensure that this matters and we
fail with an explicit message."

Today:
- **Packs.** `acc/pkg/build.py` hashes every file but stamps one aggregate
  `manifest.content_sha256`. `SkillRef` and `McpRef` (`acc/pkg/manifest.py`)
  carry no digest. Install checks the aggregate once.
- **At load.** The skill and MCP registries load from disk with no check.
  Image-shipped `skills/` and `mcps/` have no digest at all.
- **AgentBOM.** It pins packages, not the capabilities inside them.
- **Posture scan.** PS-01..06 runs before grants (`acc-cli posture scan`), but
  nothing fails CI over all role profiles.

## What changes

### Phase 1 (this change)
- `SkillRef.sha256` / `McpRef.sha256`. Manifest schema v2; the reader accepts
  v1 and v2. `build.py` computes per-ref tree digests inside the signed
  manifest.
- Built-in capability manifest `acc/pkg/data/builtin_capabilities.yaml` for
  image-shipped skills and MCPs, generated and signed at release, shipped in
  the RPM and image.
- `acc/capability_verify.py`: verify each skill and MCP tree before first use,
  from the skill and MCP registries.
  - A mismatch or a missing digest **refuses** the capability. The message
    names the capability, the expected digest, the actual digest and the
    source.
  - `ACC_CAPABILITY_VERIFY=report|enforce`: `report` for one release, then
    `enforce`.
- `AgentBOMSpec.capabilities[]` (name, kind, source pack@version, sha256).
  `acc-pkg bom resolve` and `acc-pkg bom verify --running`.
- Posture:
  - new rules PS-07 (capability without a verified digest), PS-08 (exec or
    host access without a sandbox requirement), PS-09 (`default_operating_mode`
    unset);
  - `acc-cli posture scan --fail-on <level>`;
  - `tests/test_role_profiles_posture.py`;
  - the Tekton `acc-build` gate and the GitHub workflow call it;
  - one line per finding that says what to change.
- Fix every finding in `roles/` in the same change. Rebuild the ecosystem packs
  with v2 manifests after the runtime release.

### Deferred
- Moving image-shipped skills into packs (vault 061 Q1, alternative).
- Operator `AgentBOM` CRD wrapper gaining `capabilities[]`.

## Impact
- **Affected code:**
  - `acc/pkg/manifest.py`, `build.py`, `agent_bom.py`, `cli.py`;
  - new `acc/pkg/builtin_capabilities.py` and `acc/capability_verify.py`;
  - `acc/skills/registry.py`, `acc/mcp/registry.py`;
  - `acc/posture_scan.py`, `acc/cli/grants_cmd.py`;
  - CI gate definitions, the RPM spec, `roles/*/role.yaml`.
- **New env knobs:** `ACC_CAPABILITY_VERIFY` (`report` | `enforce`).
- **Tests:**
  - manifest v1/v2 round trip;
  - per-ref digest;
  - built-in manifest generation and signature;
  - tamper → refused with the message;
  - report mode;
  - BOM resolve and verify;
  - PS-07..09;
  - the gate on fixtures and on the real roles.
- **Backward compatibility:** v1 packs install and run in report mode with a
  PS-07 finding. Enforce mode refuses them, which is the point, after one
  release of warning.

## What stays open after Phase 1
- The enforce date and the CI failure level: vault 061 Q2–Q3. Phase 1 uses
  report-then-enforce and `--fail-on MEDIUM`.
- Overlay capabilities (`ACC_OVERLAY_ALLOW_UNSIGNED`) stay dev-only and
  unverified; PS-07 names them.
