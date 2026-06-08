# 20260605-fsi-mcp-gateway-and-open-provisioning — tasks

## Slice 1 — Dual-source MCP/skill loader (HIGHEST — the current gap)
- [ ] `acc/mcp/registry.py::load_from` — add `_scan_packages(packages_root)`
      enumerating `<ACC_PACKAGES_ROOT>/<scope>/<name>-<ver>/mcps/` via
      `acc/pkg/registry.py::Registry.find_by_name(...).install_path`, scanned
      BEFORE the in-tree dir.
- [ ] `acc/skills/registry.py::load_from` — identical packages scan for `skills/`.
- [ ] `acc/agent.py` — registry builders accept `packages_root` (default `ACC_PACKAGES_ROOT`).
- [ ] Tests: install a pack with `mcps/`+`skills/` into a temp `ACC_PACKAGES_ROOT`;
      assert the registries list the packaged server/skill. Pure-Python, no cluster.

## Slice 2 — `gateway` transport + env substitution
- [ ] `acc/mcp/manifest.py` — `MCPTransport += "gateway"`; add `gateway_ref`,
      `gateway_url` (default `${ACC_MCP_GATEWAY_URL}`), `gateway_auth: spiffe|sa_token|bearer`.
- [ ] `${ENV_VAR}` substitution for `url`/`gateway_url` in manifest load.
- [ ] `acc/mcp/client.py` — route `gateway` transport to `gateway_url` with the
      server name in the JSON-RPC envelope.
- [ ] Tests: unset gateway url -> boot/validation error; set -> client targets it.

## Slice 3 — deploy_mode provisioning resolver
- [ ] New `acc/mcp/resolver.py` (or extend `load_from`) implementing the
      resolution table keyed on `deploy_mode` (edge: bundled-stdio > in-tree >
      packaged > fail; DC: gateway > packaged > bundled > fail).
- [ ] Add `provision: {edge, datacenter}` block to `MCPManifest`.
- [ ] Tests: one manifest resolves stdio under edge, gateway under rhoai (table-driven).
- [ ] NOTE: depends on `stdio` transport actually working (currently NotImplementedError)
      — implement stdio or use a localhost-http shim for the edge path.

## Slice 4 — Operator REGISTER step (after 1-3; gated on gateway API)
- [ ] New `operator/api/v1alpha1/accmcpregistration_types.go` + reconciler,
      modeled on `AccPackageInstall`/`AccCatalog`.
- [ ] On successful SIGNED install, emit one `AccMcpRegistration` per
      `provision.datacenter` MCP; register into the cluster mcp-gateway as
      `acc/<server_id>`; de-register on delete.
- [ ] HARD GATE: `--allow-unsigned` install -> NO auto-registration.
- [ ] Auth via workload identity (SPIFFE/SVID or projected SA token), not static key.
- [ ] Tests: envtest — signed pack -> registration object; unsigned -> none. Scorecard.
- [ ] BLOCKED until the RHOAI MCP-gateway registration API/CRD is pinned — abstract behind an interface.

## Slice 5 — Package storage persistence (PVC)
- [ ] Operator mounts (optionally provisions) a PVC at `ACC_PACKAGES_ROOT`.
- [ ] Verify: pod restart -> `registry.json` + packages survive; loader finds them with no re-install.

## Follow-ups (not in slices 1-3)
- [ ] Feed CapabilityIndex from the resolver so orchestrator queries see
      package- and gateway-sourced MCPs.
- [ ] cuOpt-as-NIM + FSI data providers (fmp / internal positions DB) registered as
      gateway-served MCPs in DC (ties to `20260604-role-proposal-finance-agentset`).
