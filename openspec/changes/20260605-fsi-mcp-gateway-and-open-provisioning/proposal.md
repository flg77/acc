# 20260605-fsi-mcp-gateway-and-open-provisioning — proposal

## Why

ACC bundles roles + skills + MCPs into a signed `.accpkg`. Bundling is
the right default for **edge** (air-gap, offline, self-contained). But in
the **datacenter / RHOAI** the enterprise pattern externalizes MCPs and
skills behind an **MCP gateway / AI Hub** (preregistered servers, central
RBAC, observability) — and NVIDIA's agentic reference architecture treats
*Tools & Skills* and *Security & Governance* as external, separately-served
capabilities. ACC must support both **without a rebuild** and **stay open**
(MCP as the wire standard, the gateway as the interop point).

Investigation (workflow `fsi-mcp-gateway-design`) found a concrete gap:
**the `.accpkg` extracts correctly in-cluster, but the runtime never
surfaces the extracted skills/MCPs** — the registries scan only the
in-tree dir, not `ACC_PACKAGES_ROOT/<scope>/<name>-<ver>/{mcps,skills}/`.
So in DC the bytes land and are then silently ignored.

## What changes

A `deploy_mode`-aware **provisioning resolver** that binds a role's named
MCP to a transport per environment — **edge → bundled stdio; DC → external
gateway** — with a documented resolution order and fallback. Plus the
operator REGISTER step so the ACC-RHOAI operator publishes a signed pack's
MCPs into the cluster gateway (as ACC-scoped servers) and roles consume
them by name. Signing/EC attestation gates registration; unsigned packs
may install for dev but are never promoted into the shared gateway.

See [`design.md`](design.md) for the full memo (extraction state, the
duality model + manifest extension, register-vs-consume, how it stays
open) and [`tasks.md`](tasks.md) for the ranked slices.

## Scope (ranked slices)

1. **Dual-source MCP/skill loader** — close the actual gap: scan
   `ACC_PACKAGES_ROOT` before in-tree. (Highest priority; pure-Python,
   unit-testable.)
2. **`gateway` transport + `${ENV_VAR}` substitution** in `MCPManifest` +
   client routing.
3. **`deploy_mode` provisioning resolver** (`provision:` block) tying 1+2
   into the edge/DC duality.
4. **Operator REGISTER step** via a new `AccMcpRegistration` CR
   (signed-only; de-register on uninstall).
5. **Package storage persistence (PVC)** for DC to remove the
   pod-restart re-fetch window.

## Impact

* **acc-core:** `acc/mcp/registry.py`, `acc/skills/registry.py`,
  `acc/mcp/manifest.py`, `acc/mcp/client.py`, new `acc/mcp/resolver.py`,
  `acc/agent.py` registry wiring.
* **operator:** new `AccMcpRegistration` CRD + reconciler (modeled on
  `AccPackageInstall`/`AccCatalog`); optional PVC for `ACC_PACKAGES_ROOT`.
* **Backward compatible:** edge/standalone unchanged; the resolver
  defaults to today's in-tree+bundled behavior when no gateway is set.

## Open questions (must resolve before Slice 4)

* **RHOAI MCP-gateway registration API is not pinned** (AI Hub is dev
  preview) — abstract registration behind an interface; do not build
  Slice 4 against a guessed schema.
* **Workload identity** (SPIFFE vs projected SA token) is cluster-dependent
  — keep `gateway_auth` pluggable.
* **`stdio` transport is unimplemented** (client raises NotImplementedError)
  — the edge fallback needs stdio (or a localhost-http shim) first.
* **Skills have no gateway equivalent** — packaged skills stay
  filesystem-only (Slice 1); the gateway pattern is MCP-only for now.
* **CapabilityIndex** still sees only in-tree resources — feeding it from
  the resolver is a tracked follow-up.

## References

* Workflow run: `fsi-mcp-gateway-design` (wf_0dd4a5e9-059).
* Related: `20260604-business-roles-domain-split` (transitive resolver),
  `20260605-acc-pkg-trust-and-assistant` (dual-source *role* loader),
  `20260604-role-proposal-finance-agentset` (cuOpt-as-NIM, FSI data MCPs).
