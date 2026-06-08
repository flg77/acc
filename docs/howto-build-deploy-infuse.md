# How-To: Build, Deploy & Infuse — the ACC role lifecycle

The end-to-end workflow for getting a role from source into a running
collective, driven from any of the three control surfaces: the **CLI**,
the **TUI**, and the **WebGUI**.

```
   author ──▶ BUILD ──▶ PUBLISH ──▶ DEPLOY ──▶ INFUSE ──▶ verify
  (sources)  (.accpkg)  (catalog)   (stack)   (install)  (resolve)
```

ACC core ships only the **7 control roles** (`arbiter`, `assistant`,
`compliance_officer`, `ingester`, `observer`, `orchestrator`,
`reviewer`) in-tree. **Every other role** — coding agents, researchers,
the corporate domains, the FSI agentset — is distributed as a signed
`.accpkg` **package** and *infused* at deploy/run time. This guide is
how that happens.

> **Surface cheat-sheet**
> | I want to… | CLI | TUI | WebGUI |
> |---|---|---|---|
> | Build a pack | ✅ | — | — |
> | Stand up a stack | ✅ | — | — |
> | Browse the catalog | ✅ `acc-pkg list --available` | ✅ Marketplace | ✅ `/roles` |
> | Propose an infusion | ✅ | ✅ Marketplace → Install | ✅ `POST /api/roles/install` |
> | Approve an infusion | ✅ (operator) | ✅ Compliance pane | ✅ `/api/oversight` |
> | Author/edit a role | ✅ `acc-pkg new-role` | ✅ Ecosystem → Roles | — |
>
> **Build + deploy are CLI-only.** Infusion can start anywhere, but the
> actual install is always gated by the **signing floor** and (for the
> assistant/marketplace paths) **arbiter approval**.

---

## 0. Concepts in 60 seconds

| Term | Meaning |
|---|---|
| **`.accpkg`** | A byte-deterministic gzip-tar of role(s) + bundled skills/MCPs + evals + optional policy. Carries a `content_sha256`. |
| **Control roles** | The 7 governance roles, always in-tree. Never shadowed by a package (`@acc/control-roles` is their only legitimate pack home). |
| **Catalog** | An entry in `catalogs.yaml` pointing ACC at a registry (`mode: https` or `file`) + the `required_signer` it trusts. Layered **system → user → workspace**; higher `priority:` wins ties. |
| **Tier** | `trusted` / `community` / `self` — changes the *depth* of policy on top of the (mandatory) signature. |
| **Signing floor** | Every install cosign-verifies against `required_signer`. **Keyless** = OIDC issuer + subject regex; **keypair** = a pinned `key_path` (`.pub`). `ACC_ALLOW_UNSIGNED=1` bypasses it (audit-logged) — dev/test only. |
| **`ACC_PACKAGES_ROOT`** | Where packages install. Default `/var/lib/acc/packages`. The dual-source loader resolves a role from here first, in-tree second. |
| **Boot-time fetch** | `acc-cli collective pkg-install <spec>` resolves + verifies + installs every `required_packages:` entry before agents spawn. |
| **`PROPOSE_INFUSE`** | The marker the assistant (or Marketplace/WebGUI) emits to *propose* a package; it lands in the Compliance queue for operator approval. |

---

## 1. Build

### 1.1 An in-tree role (acc repo, Stage-0 pilot)

```bash
# from agentic-cell-corpus/
python tools/build_pilot_pkg.py coding_agent --version 0.2.0
# → dist/acc-coding_agent-0.2.0.accpkg   (role + non-core-baseline skills/MCPs)
```

Exit codes: `0` ok · `1` user error · `2` an unclassified skill/MCP (add
it to `tools/skill_mcp_tiers.yaml`).

### 1.2 A family / umbrella pack (the spearhead)

Editable role sources live in the private
[`acc-ecosystem-spearhead`](https://github.com/flg77/acc-ecosystem-spearhead);
that is where the public `@acc/*` packs are built.

```bash
# from acc-ecosystem-spearhead/  (acc must be importable)
./sync-sources.sh ../agentic-cell-corpus          # refresh vendored skills/mcps/tools
PYTHONPATH=../agentic-cell-corpus ./build-all.sh   # 7 domain packs + umbrella

# or one pack from its manifest:
python tools/build_family_pkg.py --manifest manifests/finance.yaml --version 1.0.0
python tools/build_umbrella_pkg.py --version 2.0.0   # @acc/business-roles@2.0 (depends_on the 7)
```

A manifest is just `{name, description, roles:[…]}`. The umbrella is a
meta-pack: empty roles, `depends_on` the seven — ACC's transitive
resolver (`fetch_and_install_closure`) pulls the whole closure from a
single `required_packages: ["@acc/business-roles@^2.0"]`.

### 1.3 Scaffold your own pack (contributor path)

```bash
acc-pkg init my-helper --scope @you --output ./my-helper   # scaffold
acc-pkg new-role my_role --pack ./my-helper                 # add a role
acc-pkg validate ./my-helper                                # lint (exit 2 on error)
acc-pkg build ./my-helper -o dist/my-helper-0.1.0.accpkg    # deterministic build
acc-pkg inspect dist/my-helper-0.1.0.accpkg                 # manifest preview
acc-pkg eval ./my-helper                                    # behavior+safety eval summary
```

A consumable pack needs three things: a schema-valid `accpkg.yaml`, a
cosign signature, and an `evals/` attestation (≥1 behavioral + 1 safety
eval passing on the curated-LLM panel).

### 1.4 Private / secret packs

For non-redistributable verticals (e.g. RH-Mastery) sources live in the
spearhead's `secret/` and **never** reach the public registry:

```bash
# from acc-ecosystem-spearhead/
PYTHONPATH=../agentic-cell-corpus python tools/build_secret_pkg.py \
    --manifest secret/manifests/rh-mastery.yaml --version 0.1.0 \
    --stage-into ~/.acc/secret-catalog        # → secret-dist/ + a tier:self file catalog
```

See `secret/PROCEDURE.md`. Output is gitignored; a guard test fails if a
secret role ever appears in a public manifest.

---

## 2. Publish (make a pack fetchable)

### 2.1 Public catalog (acc-ecosystem)

Built `.accpkg`s flow from the spearhead to the public
[`acc-ecosystem`](https://github.com/flg77/acc-ecosystem) registry
(GitHub Pages). The catalog ACC trusts:

```yaml
# /etc/acc/catalogs.yaml · ~/.acc/catalogs.yaml · <workspace>/.acc/catalogs.yaml
catalogs:
  - id: acc-canonical
    tier: trusted
    mode: https
    url: https://flg77.github.io/acc-ecosystem
    required_signer:                       # keyless (OIDC)
      issuer: https://token.actions.githubusercontent.com
      subject_pattern: "^https://github\\.com/flg77/acc-ecosystem/"
    priority: 100
```

The publisher runbook is
[`docs/PUBLISHING-FAMILY-PACKS.md`](PUBLISHING-FAMILY-PACKS.md).

> **Signing reality (2026-06).** The packs on Pages are currently signed
> with a **pinned cosign keypair** (see keypair config below) while the
> keyless-OIDC publish path is being wired to deploy signatures to Pages
> (not just GitHub Releases). For a keypair catalog, swap `required_signer`
> to:
> ```yaml
>     required_signer:
>       issuer: "acc-ecosystem-keypair"     # audit label
>       subject_pattern: ".*"                # ignored in keypair mode
>       key_path: /etc/acc/keys/acc-ecosystem.pub
> ```
> Verification needs the `cosign` binary on PATH (`ACC_COSIGN_BIN` to
> override). Pin **cosign v2** — v3 changed `verify-blob` tlog defaults.

### 2.2 Private / local catalog (file mode)

```yaml
catalogs:
  - id: my-private
    tier: self
    mode: file
    path: ~/.acc/secret-catalog            # holds <scope>/<name>-<ver>.accpkg + .sha256
    required_signer: { issuer: local, subject_pattern: ".*" }
    priority: 500
```

Install self-tier packs with `ACC_ALLOW_UNSIGNED=1` (audit-logged) until
you sign them.

---

## 3. Deploy (stand up a collective)

All deploy is via `./acc-deploy.sh` (podman-compose under the hood).

### 3.1 First run

```bash
./acc-deploy.sh setup            # scaffold ./.env + the apply-watcher
./acc-deploy.sh build            # build images (cached); rebuild = --no-cache --pull
./acc-deploy.sh up               # start the stack  (add --webgui for the web UI)
./acc-deploy.sh status           # confirm health
```

### 3.2 The packages volume (required for packaged roles)

Because packaged roles resolve from `ACC_PACKAGES_ROOT`, every agent
mounts a shared `acc-packages` volume at `/var/lib/acc/packages`
(wired in `container/production/podman-compose.yml`). Without it, an
agent whose role lives in a pack (e.g. `analyst`, `coding_agent` from
`@acc/workspace-roles`) cannot resolve its role.

### 3.3 Declarative agentset + boot-time fetch

Declare the collective and the packs it needs, then `apply`:

```yaml
# collective.yaml
collective_id: sol-01
required_packages:
  - "@acc/workspace-roles@^1.0"
  - "@acc/business-roles@^2.0"      # umbrella → pulls all 7 domain packs
agents:
  - { role: analyst, replicas: 1, cluster_id: dom }
```

```bash
./acc-deploy.sh apply collective.yaml --dry-run   # show reconcile diff
./acc-deploy.sh apply collective.yaml             # fetch required_packages, then up the delta
```

`apply` runs `acc-cli collective pkg-install` (resolve → cosign-verify →
install into the volume), idempotent against already-installed packs, and
then synthesizes a compose overlay for any agents not yet running.

### 3.4 Flavoured / immutable deploy

Bake packs into an image at build time (edge / air-gap / reproducible):

```bash
# build an immutable flavour image (control-roles always baked; + chosen packs)
./acc-deploy.sh flavour fsi --packs "@acc/capital-markets-roles@^0.1" \
    --registry quay.io/flg77 --push

# roll a whole dedicated stack: emits collective.fsi.yaml AND builds/pushes a baked image
./acc-deploy.sh new-stack fsi --packs "@acc/capital-markets-roles@^0.1" \
    --agents "equity_analyst,portfolio_manager" --profile dc \
    --registry quay.io/flg77 --push
```

Profiles: `edge-min` (arbiter+ingester+observer) · `edge` (+orchestrator)
· `full` / `dc` (all 7 control roles + your domain agents). See
[`docs/howto-edge.md`](howto-edge.md) and
[`docs/howto-rhoai.md`](howto-rhoai.md).

---

## 4. Infuse — the three surfaces

### 4.1 CLI

Direct, scriptable. Best for operators and CI.

```bash
# discover
acc-pkg list --available                      # across all layered catalogs
acc-pkg list --available --name @acc/finance-roles

# install via a collective spec (verifies + installs the whole required_packages set)
acc-cli collective pkg-status   collective.yaml      # what's missing?
acc-cli collective pkg-install  collective.yaml      # fetch + verify + install

# install one pack directly (operator reconciler path)
acc-cli collective pkg-install-direct "@acc/finance-roles@^1.0"

# install a local .accpkg file (keypair-verified)
acc-pkg install dist/acc-finance-roles-1.0.0.accpkg --key /etc/acc/keys/acc-ecosystem.pub
acc-pkg install ./pkg.accpkg --allow-unsigned        # dev/test only, audit-logged
```

### 4.2 TUI

Launch with `./acc-deploy.sh up` (TUI is on by default) or attach to the
`acc-tui` container. Screens switch with number keys `1`–`8`.

**Browse & propose — Marketplace.** Filter (`/`), select a package,
`Enter` to **Install** → this stages a `PROPOSE_INFUSE` marker into the
Compliance queue (it does **not** install directly).

**Approve — Compliance pane** (`ComplianceScreen`). Open the
**"Package Proposals (PROPOSE_INFUSE)"** section (`p` to focus); each row
shows ID · Package · Constraint · Tier · Signer · Status. `a` =
**Approve**, `r` = **Reject**. Approval dispatches
`fetch_and_install_closure` (catalog resolve → cosign verify → install).

**Author & schedule — Ecosystem → Roles tab.** Browse the role catalog
(`↑/↓`, `/` to filter), preview `role.md` / `role.yaml`, edit inline
(`e` → `s` to save, atomic + Pydantic-validated), then `i` /
**"Schedule infusion → Nucleus"** to pre-fill the Infuse form.

**Compose & apply a role — Nucleus (Infuse) screen.** Pick a role,
fill collective/cluster/purpose/persona, `Ctrl+A` / **Apply** → publishes
`ROLE_UPDATE` + upserts `collective.yaml`; status shows
"Awaiting arbiter approval…" → "✓ Agent registered".

**Reconcile a whole agentset — Ecosystem → Agentset tab.** Edit
`collective.yaml` inline, **Validate**, **Apply** (touches the
apply-request marker; the host watcher runs `acc-deploy.sh apply`).

**Manage catalogs — Catalogs screen.** Add/remove/re-prioritise catalog
entries (writes `<workspace>/.acc/catalogs.yaml`).

### 4.3 WebGUI

Start with `./acc-deploy.sh up --webgui` (binds `127.0.0.1:8080` by
default; auth via `ACC_WEBGUI_AUTH_MODE`, see
[`docs/howto-webgui-htpasswd.md`](howto-webgui-htpasswd.md)). The WebGUI
is **propose + govern + observe**; the final install runs through the same
arbiter-approved path.

```
GET  /api/roles/available?filter=…          browse the catalog (viewer)
POST /api/roles/install                      propose an infusion → Compliance queue (operator)
GET  /api/governance/proposals               review pending rule/pkg proposals (viewer)
POST /api/governance/proposals/{id}/decision approve / reject (operator)
POST /api/infuse                             publish ROLE_UPDATE (awaits arbiter) (operator)
POST /api/oversight                          approve/reject an oversight item (operator)
GET  /ws/{collective_id}                     live snapshot + signal stream
```

Flow: **browse** (`/api/roles/available`) → **propose**
(`/api/roles/install`) → **review governance** → **approve**
(`/api/oversight` or the proposal decision) → the agent side completes the
install. RBAC has two roles: `viewer` (read/trace) and `operator`
(propose/approve/prompt).

---

## 5. Verify & operate

```bash
acc-pkg list                       # installed packages
acc-pkg owner analyst              # (-qf) which pack provides a role/skill/MCP
acc-pkg contents @acc/workspace-roles   # (-ql) what a pack provides
acc-pkg info @acc/finance-roles    # (-qi) package detail
acc-pkg verify-installed           # (-qv) re-check content hashes (exit 4 on tamper)
acc-pkg rdeps @acc/hr-roles        # reverse deps before removal
acc-pkg uninstall @acc/hr-roles    # (exit 3 if depended on; --force to override)
```

Confirm an agent resolved a packaged role from logs:

```bash
./acc-deploy.sh logs acc-agent-analyst        # REGISTERING role=analyst + heartbeats
acc-pkg owner analyst                          # → @acc/workspace-roles@1.0.2 + install_path
```

---

## 6. Worked example — connected deploy with keypair-signed packs

The path used to bring up the lighthouse test node (boot-time fetch,
keypair signing floor honoured):

```bash
# host: install cosign v2 (acc/pkg/verify.py matches v2 verify-blob semantics)
curl -fsSL -o ~/bin/cosign \
  https://github.com/sigstore/cosign/releases/download/v2.4.1/cosign-linux-amd64
chmod +x ~/bin/cosign

# 1. update + build the new image (carries acc-cli collective pkg-install)
cd /path/to/acc && git pull --ff-only && ./acc-deploy.sh build

# 2. catalog (keypair mode) + the verifier pubkey + a minimal spec, then fetch:
#    catalogs.yaml → required_signer.key_path: /etc/acc/keys/acc-ecosystem.pub
#    pkgspec.yaml  → required_packages: ["@acc/workspace-roles@^1.0"]
podman run --rm --network host \
  -e ACC_PACKAGES_ROOT=/var/lib/acc/packages -e ACC_COSIGN_BIN=/usr/local/bin/cosign \
  -v production_acc-packages:/var/lib/acc/packages:U,z \
  -v ~/bin/cosign:/usr/local/bin/cosign:ro \
  -v ~/acc-deploy/catalogs.yaml:/etc/acc/catalogs.yaml:ro,z \
  -v ~/acc-deploy/keys/acc-ecosystem.pub:/etc/acc/keys/acc-ecosystem.pub:ro,z \
  -v ~/acc-deploy/pkgspec.yaml:/spec.yaml:ro,z \
  --entrypoint acc-cli localhost/acc-agent-core:0.2.0 \
  collective pkg-install /spec.yaml --json        # → installed @acc/workspace-roles@1.0.2

# 3. roll the stack onto the populated volume
./acc-deploy.sh down && ./acc-deploy.sh up --webgui

# 4. verify
./acc-deploy.sh status                            # all agents healthy
podman exec acc-agent-analyst acc-pkg owner analyst   # → @acc/workspace-roles@1.0.2
```

> In production the boot-fetch is wired into `apply` (step 2 becomes
> `./acc-deploy.sh apply collective.yaml`); the manual one-shot above is
> shown so each stage is explicit.

---

## Related how-tos

- [`docs/howto-deploy.md`](howto-deploy.md) — deployment deep-dive
- [`docs/howto-role-infusion.md`](howto-role-infusion.md) — infusion internals
- [`docs/howto-agentsets.md`](howto-agentsets.md) — declarative `collective.yaml`
- [`docs/howto-tui.md`](howto-tui.md) — the terminal UI
- [`docs/howto-webgui-htpasswd.md`](howto-webgui-htpasswd.md) — WebGUI auth
- [`docs/howto-edge.md`](howto-edge.md) · [`docs/howto-rhoai.md`](howto-rhoai.md) — edge / RHOAI
- [`docs/PUBLISHING-FAMILY-PACKS.md`](PUBLISHING-FAMILY-PACKS.md) — publisher runbook
- [`acc-ecosystem`](https://github.com/flg77/acc-ecosystem) — the public registry
- [`acc-ecosystem-spearhead`](https://github.com/flg77/acc-ecosystem-spearhead) — pack sources
