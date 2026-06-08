# Design Memo: ACC MCP/Skill Provisioning — Bundle-in (Edge) vs Gateway-served (DC/RHOAI)

Audience: ACC operator + runtime maintainers. Intended to become an OpenSpec design change. Source claims verified against the tree on `business-roles-domain-split`.

## 1. Does .accpkg extract correctly in DC/RHOAI today?

**State: PARTIAL — extraction works; surfacing does not.**

The `.accpkg` *lands on disk inside the pod* correctly. What's missing is the runtime making the extracted skills/MCPs usable.

What works (verified):
- `AccPackageInstallReconciler.Reconcile` (operator/internal/controller/accpackageinstall_controller.go:90-130) finds a ready ACC pod, then exec's `acc-cli collective pkg-install-direct <name>@<constraint> --json` *inside* the pod. Cosign/EC verification and the `--allow-unsigned` audited bypass are wired (lines 117-121). This is shipping.
- `acc/pkg/registry.py` confirms the unpack target: `DEFAULT_ROOT = /var/lib/acc/packages` (line 49), overridable via `ACC_PACKAGES_ROOT` (lines 106-113), with flock + per-root thread-lock concurrency (lines 58-69). Each install writes a `RegistryEntry` carrying an absolute `install_path` (lines 77-94).

The exact missing piece (the gap that makes this "partial"):
- **No dual-source loader.** `MCPRegistry.load_from()` (acc/mcp/registry.py:137-184) resolves its root only from `ACC_MCPS_ROOT` or literal `"mcps"` (the `_mcps_root_default()` at lines 42-48) and scans exactly that one directory. There is **zero** code path that enumerates `<ACC_PACKAGES_ROOT>/<scope>/<name>-<version>/mcps/`. `SkillRegistry.load_from()` has the identical limitation. So a packaged MCP is extracted to `/var/lib/acc/packages/...` and then **silently ignored** — the registry never looks there.
- **Storage is ephemeral.** `/var/lib/acc/packages` is the pod's writable layer by default; the operator does not provision a PVC. On pod restart the tree is gone until the reconciler re-runs (idempotent, so it self-heals — but it's a re-fetch each time, and capability is unavailable during the window).

Net: extraction is real and verified; the runtime layer that would expose extracted capability is not built. Treat "DC extraction works" as **true for the bytes, false for the behavior.**

## 2. The duality: one model, two provisioning backends

The bundle-in model (edge) and gateway-served model (DC) are not two architectures — they are **two backends behind one resolver.** The unifying abstraction is: *a role names an MCP by `server_id`; the runtime resolves `server_id` → a transport binding at boot, parameterized by `deploy_mode`.*

Today the binding is hardcoded in `mcp.yaml` (`url: http://acc-mcp-echo:8080/rpc`, manifest.py:98) with no indirection. The fix is to make the manifest declare *intent* and let a resolver supply the *endpoint*.

### Proposed manifest extension

Add a third transport and a gateway-binding block. The manifest declares "I can be bundled here, OR I bind to this gateway-registered name":

```yaml
# mcps/cuopt_solver/mcp.yaml
server_id: cuopt_solver
transport: http            # existing default
url: ""                    # leave empty when gateway-resolvable
provision:
  edge:                    # air-gap / offline
    transport: stdio
    command: ["python", "-m", "acc_cuopt_local"]
  datacenter:              # RHOAI
    transport: gateway
    gateway_ref: "acc/cuopt_solver"   # name the cluster mcp-gateway registers it under
```

Two minimal schema additions to `MCPManifest` (manifest.py):
1. Extend `MCPTransport = Literal["http", "stdio", "gateway"]`.
2. Add a `provision: dict[deploy_mode, ProvisionBinding]` block, plus support for `${ENV_VAR}` substitution in `url` and `gateway_url` so a single DC-wide gateway can be injected (`ACC_MCP_GATEWAY_URL`).

### The resolver and its resolution order

A new `acc/mcp/resolver.py` (or extend `load_from`) resolves each `server_id` at boot. **Resolution order, per `deploy_mode`:**

| Priority | Edge mode | Datacenter (RHOAI) mode |
|---|---|---|
| 1 | `provision.edge` (bundled stdio) | gateway-registered name (`provision.datacenter.gateway_ref`) via `ACC_MCP_GATEWAY_URL` |
| 2 | in-tree `mcps/<id>` (http/stdio) | packaged `mcps/<id>` under `ACC_PACKAGES_ROOT` (http) |
| 3 | packaged `mcps/<id>` under `ACC_PACKAGES_ROOT` | bundled stdio (`provision.edge`) as fallback |
| 4 | hard fail (open mode means: don't silently no-op a named MCP) | hard fail |

Key design rule: **DC prefers the gateway; edge prefers the bundle; but each can fall back to the other.** This is what "stays open" means concretely — the same pack runs air-gapped on a bootc edge node and gateway-fronted in RHOAI without a rebuild. The resolver is the single seam where `deploy_mode` (already a first-class ACC concept) selects the backend.

## 3. Register-vs-consume: what the ACC-RHOAI operator should do

**Recommendation: (c) BOTH, with register-then-consume ordering — but split across two reconcilers.**

This is the dominant enterprise pattern (Red Hat MCP catalog + lifecycle operator + gateway; agentic-community gateway-registry; AWS AgentCore). ACC should mirror it rather than invent a parallel control plane.

### Concrete operator behavior

On `AccPackageInstall` in `deploy_mode: rhoai`:
1. **Extract as today** (unchanged — the verified Stage 0 path).
2. **REGISTER step (new):** after a successful install whose pack contains MCPs flagged `provision.datacenter`, the operator registers each as an **ACC-scoped server** in the cluster `mcp-gateway` (e.g. namespaced name `acc/<server_id>`, or the pack's `gateway_ref`). The gateway then owns routing, RBAC, and per-tool observability. This is a new reconciler responsibility — `AccPackageInstall` should either emit a child `AccMcpRegistration` CR or call the gateway's registration API directly. **Prefer a separate `AccMcpRegistration` CR** so registration lifecycle (and de-registration on uninstall) is declarative and auditable, matching the existing `AccCatalog`/`AccPackageInstall` CR split.
3. **CONSUME step:** roles bind to gateway-registered MCPs *by name* through the resolver (section 2). The agent never holds a per-server URL in DC mode — it holds `gateway_ref` and resolves through `ACC_MCP_GATEWAY_URL`.

### Non-negotiable gates on registration

- **Signing/attestation still gates registration.** Only a pack that passed cosign + EC verification at install time may be registered into the gateway. `--allow-unsigned` (already audited at controller line 119) must **block** auto-registration — an unsigned pack can be installed for dev but must never be promoted into the shared gateway namespace. The gateway is a shared blast radius; the trust boundary that protects the pod must also protect the cluster.
- **Auth:** the gateway is the OAuth 2.1 / token boundary. ACC agents authenticate to the gateway with a workload identity — **SPIFFE/SVID or a projected ServiceAccount token** (RHOAI already issues these), not a static `api_key_env`. The manifest's existing `api_key_env` becomes the *edge* credential; in DC, the resolver injects the gateway token instead.
- **Allow-listing:** the manifest's existing `allowed_tools`/`denied_tools` (manifest.py:108-110) stay enforced **client-side** as today, AND the gateway enforces its own RBAC. Defense in depth — the pack-author's allow-list and the platform team's policy are independent gates.

### What NOT to do
Do not have the operator stand up one pod per MCP and hardcode its Service DNS into `mcp.yaml`. That recreates the scattered-server anti-pattern the gateway exists to collapse. Registration into the existing cluster gateway is the correct DC primitive.

## 4. How this "stays open"

The requirement is that ACC is "MCP standard, not a closed bundle-only system." The design delivers that at three interop points:

1. **MCP is the wire.** Edge stdio and DC gateway both speak MCP JSON-RPC (already true — `MCPClient` is transport-agnostic at the call interface). A pack is portable because the protocol is identical; only the transport binding differs.
2. **Gateway registration is the interop point.** An ACC pack registered as `acc/<server_id>` is consumable by *any* MCP client on the cluster (Cursor, VS Code, another team's agent) — not just ACC. Conversely, ACC roles can bind to MCPs the platform team pre-registered that ACC never bundled. The bundle stops being a wall and becomes one *source* feeding a shared registry.
3. **Third-party capabilities are just gateway-served MCPs.** This is where the design earns its keep for FSI/DC:
   - **cuOpt-as-NIM** runs as a `NIMService` REST endpoint; ACC reaches it as a `transport: gateway` MCP bound to `gateway_ref` — no cuOpt code in the pack, no rebuild to point at a different cuOpt instance.
   - **FSI data providers** (fmp market data, the internal positions DB) are registered once into the gateway by the platform team, governed/audited centrally, and consumed by ACC finance roles by name. The pack ships the *role* and its `allowed_tools` contract; the *endpoint* is the platform's.

The escape hatch from lock-in: a pack that declares both `provision.edge` (bundled stdio) and `provision.datacenter` (gateway_ref) is genuinely portable. Air-gap doesn't force a closed format; DC doesn't force re-bundling.

## 5. Concrete next slices (ranked, mapped to seams)

Each slice is small and independently verifiable. Ranked by "unblocks the most with the least new surface."

**Slice 1 — Dual-source MCP/skill loader (the actual current gap).** *Highest priority; section 1 says this is the only thing making DC "partial."*
- Seam: `acc/mcp/registry.py::load_from` (line 137) and `acc/skills/registry.py::load_from` — add `_scan_packages(packages_root)` that enumerates `<ACC_PACKAGES_ROOT>/<scope>/<name>-<version>/mcps|skills/` *before* the in-tree scan, using `acc/pkg/registry.py::Registry.find_by_name` to get `install_path`.
- Wire: `acc/agent.py::_build_mcp_registry` / `_build_skill_registry` accept `packages_root` defaulting to `ACC_PACKAGES_ROOT`.
- Verify: install a pack with an `mcps/` dir into a temp `ACC_PACKAGES_ROOT`; assert `registry.list_server_ids()` includes the packaged server. Pure-Python unit test, no cluster.

**Slice 2 — `gateway` transport + `${ENV_VAR}` substitution.** *Unblocks DC consume.*
- Seam: `acc/mcp/manifest.py` — extend `MCPTransport` to include `"gateway"`; add `gateway_ref`, `gateway_url` (default `${ACC_MCP_GATEWAY_URL}`), `gateway_auth: spiffe|sa_token|bearer`; env-substitute `url`/`gateway_url` in `_load_manifest` (registry.py:85-118).
- Seam: `acc/mcp/client.py` — route `gateway` transport to `gateway_url` with the server name in the JSON-RPC envelope.
- Verify: manifest with `transport: gateway` + unset `ACC_MCP_GATEWAY_URL` → validation/boot error; set → client targets the gateway URL. Unit test on the client routing.

**Slice 3 — `deploy_mode` provisioning resolver.** *Ties slices 1+2 into the duality.*
- Seam: new `acc/mcp/resolver.py` (or extend `load_from`) implementing the section-2 resolution table keyed on the existing `deploy_mode`. Add the `provision:` block to `MCPManifest`.
- Verify: same manifest resolves to stdio under `deploy_mode=edge` and to gateway under `deploy_mode=rhoai`. Table-driven unit test over the four-row order.

**Slice 4 — Operator REGISTER step via `AccMcpRegistration` CR.** *Largest; do after 1-3 prove the runtime side.*
- Seam: new CR `operator/api/v1alpha1/accmcpregistration_types.go` + reconciler, modeled on `AccPackageInstall`/`AccCatalog`. On successful signed install, emit one `AccMcpRegistration` per `provision.datacenter` MCP; reconciler registers it into the cluster `mcp-gateway` and de-registers on delete. **Hard-gate on the install's signature status — unsigned ⇒ no registration.**
- Verify: envtest reconcile asserts a signed pack produces a registration object and an `--allow-unsigned` pack does not. Scorecard for the CRD.

**Slice 5 — Package storage persistence (PVC) for DC.** *Lower priority — reconciler idempotency masks it, but it removes the restart gap.*
- Seam: operator Deployment spec mounts a PVC at `ACC_PACKAGES_ROOT`; operator optionally provisions it. No Python change.
- Verify: kill the pod, confirm `/var/lib/acc/packages/registry.json` survives and the loader finds packaged MCPs without a re-install.

---

## Honest gaps / open questions to resolve in the OpenSpec design

- **Gateway registration API is unknown.** RHOAI's MCP gateway registration interface (CRD? REST? schema?) is not pinned down in the external findings — the AI Hub catalog is v3.4 developer preview. Slice 4 depends on this contract; the design must either target the RHOAI MCP lifecycle operator's CRDs or abstract registration behind an interface until that API stabilizes. **Do not build Slice 4 against a guessed schema.**
- **Workload identity choice (SPIFFE vs SA token)** depends on what the target RHOAI cluster runs. Leave the resolver's `gateway_auth` pluggable.
- **`stdio` transport is itself unimplemented** (manifest.py:21-24: client raises `NotImplementedError`). The edge fallback in the resolver assumes stdio works — Slice 3's edge path is blocked until stdio lands, or edge must fall back to bundled `http` against a localhost server. Flag this dependency explicitly.
- **Skills lag MCPs.** Stage 1.5 (`openspec/changes/20260605-acc-pkg-trust-and-assistant/proposal.md`) scopes the dual-source *role* loader and omits skills/MCPs. There is no "skill gateway" equivalent in ACC; for now packaged skills are filesystem-only (Slice 1 covers them), and the gateway pattern applies to MCPs only. Don't over-promise a skill-registry until agentskills.io-style external skill serving is actually targeted.
- **CapabilityIndex blindness:** the orchestrator's capability index still only sees in-tree resources; even after Slice 1, package-sourced and gateway-sourced MCPs won't appear in orchestrator queries until the index is fed from the resolver. Track as a follow-up, not part of slices 1-3.

Relevant files: `C:\Users\micro\Downloads\git\agentic\agentic-cell-corpus\acc\mcp\manifest.py`, `C:\Users\micro\Downloads\git\agentic\agentic-cell-corpus\acc\mcp\registry.py`, `C:\Users\micro\Downloads\git\agentic\agentic-cell-corpus\acc\skills\registry.py`, `C:\Users\micro\Downloads\git\agentic\agentic-cell-corpus\acc\pkg\registry.py`, `C:\Users\micro\Downloads\git\agentic\agentic-cell-corpus\acc\pkg\install.py`, `C:\Users\micro\Downloads\git\agentic\agentic-cell-corpus\operator\internal\controller\accpackageinstall_controller.go`, `C:\Users\micro\Downloads\git\agentic\agentic-cell-corpus\operator\internal\controller\acccatalog_controller.go`.