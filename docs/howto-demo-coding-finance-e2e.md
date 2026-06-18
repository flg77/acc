# How-to: Coding + Finance end-to-end demo (external MaaS)

Build a real artifact end-to-end with ACC: a **Keycloak-secured stock-quotes web
app** — React (CMS-style) frontend + FastAPI backend pulling Yahoo Finance
quotes — whose implementation **plan is reviewed by a powerful reviewer** before
a devops role renders the **Kubernetes/RHOAI** deploy. The coding agents and the
reviewer run on an **external Model-as-a-Service (MaaS)** gateway, not a local
model.

This is the "coding + finance" slice of the demo family (compare
[`DEMOS.md`](DEMOS.md) and [`howto-coding-split-demo.md`](howto-coding-split-demo.md)).
It is also the worked example for the two things operators most often get wrong:
**package install / role infusion** (auto-mode assistant vs. manual) and
**prompt construction for multi-agent review**.

Preset: [`collectives/collective.e2e-demo.yaml`](../collectives/collective.e2e-demo.yaml).
Golden prompt: [`examples/golden_prompts/e2e_demo_stock_quotes.yaml`](../examples/golden_prompts/e2e_demo_stock_quotes.yaml).

---

## 1. The collective at a glance

| Cluster | Agents | Model | Why |
|---|---|---|---|
| `ctl` | `assistant`, `orchestrator`, `compliance_officer` | `maas-qwen3-14b` | routing, infusion decisions, governance — wants reasoning |
| `build` | `coding_agent_architect`, `coding_agent_implementer` ×2, `coding_agent_tester`, **`reviewer`** | coding → `maas-llama-scout-17b`, reviewer → `maas-qwen3-14b` | cheap/fast bulk coding, **one powerful reviewer** in the same cluster so the critic loop ties them |
| `deploy` | `devops_engineer` | `maas-llama-scout-17b` | Keycloak / k8s / RHOAI manifests |

Two design choices matter:

- **`collective_id: sol-01`.** When you `apply` a preset onto the *running
  baseline stack*, its agents must share the baseline arbiter's `collective_id`
  (`sol-01`) or PLANs and the critic loop never reach them. The file is still
  applied by its name (`apply e2e-demo`); only the runtime namespace is `sol-01`.
  (Demos meant for a *dedicated* stack — `acc-deploy.sh new-stack` — carry their
  own id + their own arbiter.)
- **Reviewer in the worker cluster.** The arbiter's critic loop
  (`plan._maybe_reissue_for_revise`) re-issues a reviewed step on a
  `NEEDS_REVISE` verdict. Putting the powerful reviewer in the same cluster as
  the cheap coders is the high-ROI pattern from
  [`multimodel_reviewer.md`](multimodel_reviewer.md).

---

## 2. MaaS models and the API key — *where does the key go?*

The two MaaS models live on one LiteLLM gateway and are registered in
[`models.yaml`](../models.yaml):

```yaml
- model_id: maas-qwen3-14b           # powerful — reviewer + control plane
  backend: openai_compat
  model: "qwen3-14b"
  base_url: "https://maas-rhdp.apps.maas.redhatworkshops.io/v1"
  api_key_env: "MAAS_API_KEY"
- model_id: maas-llama-scout-17b     # coding agents + devops
  backend: openai_compat
  model: "llama-scout-17b"
  base_url: "https://maas-rhdp.apps.maas.redhatworkshops.io/v1"
  api_key_env: "MAAS_API_KEY"
```

**The key is never in `collective.yaml` or `models.yaml`** — those are committed.
`models.yaml` names the env var (`api_key_env: MAAS_API_KEY`); the secret value
lives in `.env` (gitignored):

```bash
echo 'MAAS_API_KEY=sk-...' >> .env      # the "click-show-key-to-reveal" LiteLLM key
```

At synthesis time `acc.models.model_env` turns `api_key_env` into
`ACC_LLM_API_KEY_ENV=MAAS_API_KEY`; the `openai_compat` backend reads that var at
**call time** and sends `Authorization: Bearer $MAAS_API_KEY` — re-read on every
call, so key rotation needs no restart of the *process* (a `.env` change still
needs the container recreated, since `env_file` is read at container start).

`collective.yaml` agents only reference models by `model_id`. That is the whole
"API key stanza."

Validate the key before you depend on it (a stale workshop key returns `401`):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST https://maas-rhdp.apps.maas.redhatworkshops.io/v1/chat/completions \
  -H "Authorization: Bearer $(grep -m1 '^MAAS_API_KEY=' .env | cut -d= -f2-)" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-14b","messages":[{"role":"user","content":"OK"}],"max_tokens":4}'
# expect 200
```

---

## 3. Methodology — the end-to-end pipeline

```
build images → redeploy (create the packages volume) → INSTALL/INFUSE the role packs
→ apply e2e-demo → run a plan/prompt → review the verdict
```

Each stage exists for a reason; skipping one is where demos fail.

1. **Build** — `./acc-deploy.sh build` rebuilds the `:0.2.0` images from the
   checkout. Needed whenever agent/TUI *code* changed (role resolution, the
   reviewer contract, the package surfaces). Pure config (`collective.yaml`,
   `acc-deploy.sh`) does **not** need a rebuild — it's read host-side / bind-mounted.
2. **Redeploy** — `./acc-deploy.sh down && up`. This is what *creates and mounts*
   the named `acc-packages` volume (project-prefixed `production_acc-packages`)
   into every agent. A running stack that predates this volume can't hold
   installed packs.
3. **Install / infuse** the role packs into that volume — §4. This is the step
   with the most moving parts.
4. **Apply** — `./acc-deploy.sh apply e2e-demo` synthesizes the overlay and
   brings up the `acc-cell-*` agents. `apply` is additive (`--no-recreate`
   default); use `--recreate` when an overlay field changed (model, volume,
   `collective_id`) so the cells actually pick it up.
5. **Run** — submit a plan or a prompt (§5) and watch the critic loop.

### Why the packages volume is non-negotiable (acc-spearhead#91)

Synthesized cells **must** mount `acc-packages:/var/lib/acc/packages` — the same
volume the base agents use. Before #91 they only bind-mounted `../../roles`
(in-tree CONTROL roles), so any agent whose role is **served by a pack**
(`coding_agent_*`, `devops_engineer`) couldn't resolve its `role.yaml` and booted
**DORMANT** even with the pack installed. Symptom to watch for in logs:

```
agent: role 'devops_engineer' has no resolvable role.yaml yet
       (package not installed; only the generic default is available) — booting DORMANT
```

If you see that *after* installing the pack, your cells aren't mounting the
volume (rebuild from a checkout that includes #91, re-`apply --recreate`).

---

## 4. Package install / infusion — **AutoMode assistant vs. Manual**

Family-pack roles (`@acc/workspace-roles`, `@acc/devops-roles`, …) are not in the
base image. Getting them into the running ecosystem is **infusion**: catalog walk
→ cosign-verify → install into the packages volume → the role becomes spawnable.
There are two ways to drive it, and they differ in *who initiates*, not in the
end state.

### 4a. AutoMode (assistant-driven)

The `assistant` is a capability-aware navigator, not just an advisor. Its loop:

1. **Ground in the catalog.** Before routing, it invokes the `catalog_query`
   skill, which returns `installed_roles` (running / dormant / installed),
   `available_packages` (advertised but not installed), and `control_roles`.
2. **Detect the gap and propose.** If the task needs a role only available in an
   un-installed pack, it emits, *after* its reasoning block:
   ```
   [PROPOSE_INFUSE:@acc/workspace-roles@^1.0:operator needs a coding + review team]
   ```
3. **The Compliance gate.** Here is the crucial rule: **`PROPOSE_INFUSE` ALWAYS
   routes through the Compliance queue, regardless of operating mode.** Other
   markers (`PROPOSE_ROUTE`, `PROPOSE_SPAWN`) auto-execute under `AUTO`; infusion
   does **not** — it changes filesystem state and requires a cosign signature
   verification, so it is `_NEVER_AUTOEXEC`. Even in AutoMode the operator (or a
   policy) approves the infusion in the Compliance pane before anything installs.
4. **Install on approval, then spawn.** On approval the catalog-walk +
   cosign-verify + install runs; then the assistant emits
   `[PROPOSE_SPAWN:role:cluster:reason]` to wake the now-installed role
   (the arbiter sends a signed `ROLE_ASSIGN` to a dormant worker).

So "AutoMode" means **the assistant discovers the gap, grounds it in the live
catalog, and drives the whole sequence** — capability-missing → infuse → spawn →
route — with the operator only approving the one signed install gate. The
operating mode (`PLAN` / `ACCEPT_EDITS` / `ASK_PERMISSIONS` / `AUTO`) governs how
much of the *rest* (routing, spawning) the dispatcher auto-executes; infusion is
always gated.

**When to use it:** interactive sessions where you want the assistant to figure
out *which* pack/role a goal needs and walk you through it. The
`#84 boot-and-wait` behavior pairs with this: a cell whose pack isn't installed
boots dormant and **self-promotes** once the pack lands in the volume.

### 4b. Manual (operator-driven)

You install the pack yourself; no assistant in the loop. Three equivalent
surfaces (they align on the native `acc-pkg` flags):

```bash
# 1. acc-deploy wrapper (runs acc on the HOST python)
./acc-deploy.sh pkg add @acc/workspace-roles            # from the catalog
./acc-deploy.sh pkg add @acc/devops-roles@^1.0 acc-canonical   # pin a catalog id
./acc-deploy.sh pkg list                                # installed
./acc-deploy.sh pkg list --available                    # what the catalog offers

# 2. native acc-pkg (file install / passthrough)
acc-pkg install ./dist/@acc-workspace-roles-1.0.2.accpkg

# 3. one-step infuse: install the pack AND apply the role in one go
acc-cli collective infuse <cid> reviewer \
  --from-pkg @acc/workspace-roles@^1.0          # (--install is an alias)
```

`required_packages:` in the preset is the *declarative* form of the same thing —
`apply` resolves + verifies + installs them at boot (Stage 1.5.3). In the TUI, the
Ecosystem screen → `g` ("Get pack") is the in-pane manual surface.

### 4c. The lighthouse reality (host can't write the volume) — acc-spearhead#85

On a containerized stack the packages root `/var/lib/acc/packages` is the
**in-container `acc-packages` volume**, which the *host* user cannot write. So:

- **`acc-deploy.sh pkg add` (host) fails** with `PermissionError: /var/lib/acc`.
  Since #85, `apply <preset-with-required_packages>` no longer crashes on this —
  it prints an actionable note and **defers** resolution to in-container,
  continuing the apply. But host-side `pkg add` is still a no-go on such a host.
- **Provision *in a container* that mounts the volume.** With rootless podman the
  volume's `_data` is owned by a sub-uid, so the host can't write it directly
  either — but a one-off container *can* (the `:U` mount maps it to the container
  user). The image, however, ships **without `cosign`**, so signed verification
  needs the host's `cosign` mounted in:

  ```bash
  # SIGNED install-from-file into the real volume (no signature bypass)
  curl -fsSL -o /tmp/p/devops-roles-1.0.2.accpkg     <catalog>/packages/acc/devops-roles-1.0.2.accpkg
  curl -fsSL -o /tmp/p/devops-roles-1.0.2.accpkg.sig <catalog>/packages/acc/devops-roles-1.0.2.accpkg.sig
  podman run --rm \
    -v production_acc-packages:/var/lib/acc/packages:U \
    -v /tmp/p:/pkgs:ro \
    -v /home/flg/acc-deploy/keys:/etc/acc/keys:ro \
    -v "$(command -v cosign)":/cosign:ro -e ACC_COSIGN_BIN=/cosign \
    localhost/acc-agent-core:0.2.0 \
    python -m acc.pkg.cli install /pkgs/devops-roles-1.0.2.accpkg \
      --signature /pkgs/devops-roles-1.0.2.accpkg.sig \
      --key /etc/acc/keys/acc-ecosystem.pub
  # → "verified devops-roles-1.0.2.accpkg against keypair:acc-ecosystem.pub (mode=keypair)"
  ```

  Never reach for `--allow-unsigned` to "make it work" — verify against the
  catalog keypair instead.

> **Known catalog bug (acc-spearhead#92):** the published `acc-canonical` index
> carries a `bundle_url` field that `CatalogIndexEntry` rejects (`extra=forbid`),
> so catalog *index* resolution currently fails ("no catalog advertises …"). Until
> that's fixed, provision via the signed install-from-file path above. Verify the
> result with `podman exec <cell> python -m acc.pkg.cli list`.

### 4d. AutoMode vs Manual — summary

| | AutoMode (assistant) | Manual (operator) |
|---|---|---|
| Initiator | assistant, on detecting a capability gap | operator |
| Catalog grounding | `catalog_query` skill, automatic | you run `pkg list --available` |
| Install trigger | `[PROPOSE_INFUSE]` → **Compliance approval** | `pkg add` / `infuse --from-pkg` / TUI `g` |
| Auto-executes under AUTO? | **No** — infusion is always Compliance-gated | n/a (you run it) |
| Then | `[PROPOSE_SPAWN]` + `[PROPOSE_ROUTE]` | declare in `required_packages` / spawn |
| Best for | interactive, "figure out what I need" | scripted/repeatable provisioning, CI |

Both land the pack in `acc-packages` and make the role spawnable. AutoMode adds
*discovery + grounding*; Manual adds *determinism*.

---

## 5. Running it — and prompt techniques for the best results

### 5a. The reviewer contract

The `reviewer` role emits a strict JSON verdict that the agent surfaces as
`eval_outcome` on `TASK_COMPLETE`:

```json
{"verdict": "GOOD|PARTIAL|NEEDS_REVISE|BAD",
 "critique": "<concise, specific, actionable>",
 "prompt_patch": {"append": "<optional extra instruction for the worker>"}}
```

`NEEDS_REVISE` drives the arbiter's critic loop: it re-issues the reviewed step
with the critique appended (and the `prompt_patch` if the step sets
`enable_prompt_patches: true`), up to `max_iterations`.

### 5b. How context actually flows (the most important technique)

A PLAN is a DAG; `depends_on` **sequences** steps — it does **not** thread an
upstream step's *output* into a downstream step's prompt. There is **no
`{step:X}` interpolation.** If you write a reviewer step whose `task_description`
says "review the plan below: {step:design}", the reviewer receives that text
*literally* and correctly answers `BAD — "no work was provided for review"`.

Cross-step / cross-agent context flows two supported ways:

1. **Inline critic loop (recommended for review).** Give the *worker* the task;
   the reviewer critiques the worker's **own output** via `eval_outcome`. The
   reviewer never needs the text threaded in — it reviews what the worker just
   produced. This is the multimodel pattern and the cleanest review demo.
2. **`KNOWLEDGE_SHARE` pub/sub.** Plan members publish artifacts
   (e.g. an architect publishes its `draft_interface` as a `KNOWLEDGE_SHARE`) that
   peer members consume. Use this when several roles must build on one shared
   artifact within a plan.

So to demo "the coding plan is reviewed by the reviewer," prefer a **single
worker step with the reviewer in the loop**, or wire the architect to publish a
`KNOWLEDGE_SHARE` — not a `depends_on` chain that assumes output threading.

### 5c. Writing task_descriptions that get good output

Small/mid models (14–17B like `qwen3-14b` / `llama-scout-17b`) reward specificity
and punish vagueness — an underspecified prompt makes them **echo their role
guidance** or **ask "what problem would you like me to tackle?"** instead of doing
the work. For the best results:

- **State the concrete deliverable and constraints in the step itself** — don't
  rely on context the system won't thread. e.g. *"Produce a numbered
  implementation plan for a React (CMS-style) + FastAPI stock-quotes app with
  Keycloak OIDC, deployable to Kubernetes/RHOAI. Sections: architecture,
  components, API endpoints, auth flow, deployment."*
- **Pin the output shape** — "numbered sections", "valid JSON with keys …",
  "only the manifests". Shape constraints sharply improve mid-model output.
- **Match model to job** — reviewer/critique on the strongest model
  (`maas-qwen3-14b`), bulk generation on the cheaper one
  (`maas-llama-scout-17b`). The reviewer's judgement is what lifts cheap workers.
- **Use the critic loop for quality** — set `max_iterations: 2-3` +
  `enable_prompt_patches: true` on the worker step so a `NEEDS_REVISE` actually
  iterates instead of shipping a weak first draft.

### 5d. Submit a run

```bash
# single worker step + inline review (recommended) — JSON via stdin
cat <<'JSON' | ./acc-deploy.sh cli plan submit - --collective sol-01 --watch
{ "plan_id": "e2e-demo-1", "collective_id": "sol-01",
  "steps": [
    {"step_id": "build", "role": "coding_agent_architect", "max_iterations": 3,
     "enable_prompt_patches": true,
     "task_description": "Produce a numbered implementation plan for a stock-quotes web app: React (CMS-style) frontend, FastAPI backend serving Yahoo Finance quotes, Keycloak OIDC auth, deployable to Kubernetes/RHOAI. Sections: architecture, components, API endpoints, auth flow, deployment."}
  ] }
JSON
```

The Diagnostics pane (TUI #9) re-runs the shipped golden prompt
`e2e_demo_stock_quotes` against the live stack the same way.

---

## 6. Troubleshooting (seen in the field)

| Symptom | Cause | Fix |
|---|---|---|
| Cell logs "booting DORMANT … package not installed" | cell not mounting `acc-packages` (pre-#91) or pack not in the volume | rebuild incl. #91, `apply --recreate`; verify `pkg list` in the cell |
| `apply` aborts with `PermissionError: /var/lib/acc` | host can't write the in-container volume (#85) | upgrade past #85 (it defers + continues); provision in-container |
| `no catalog advertises @acc/…` | catalog index `bundle_url` parse bug (#92) | signed install-from-file (§4c) until fixed |
| Model calls return `HTTP 401` | stale/invalid `MAAS_API_KEY` | refresh the key in `.env`, `apply --recreate` so cells reload it |
| Reviewer says "no work was provided" | relied on `{step:X}` threading (not a feature) | inline critic loop or `KNOWLEDGE_SHARE` (§5b) |
| PLAN never dispatches | preset `collective_id` ≠ arbiter's | use `collective_id: sol-01` on the baseline stack |
| Agent "what problem would you like me to tackle?" | task_description too vague for a mid-size model | specify deliverable + output shape (§5c) |

---

## See also
- [`multimodel_reviewer.md`](multimodel_reviewer.md) — the critic loop in depth
- [`howto-role-infusion.md`](howto-role-infusion.md) — infusion mechanics
- [`howto-agentsets.md`](howto-agentsets.md) — presets + `apply` lifecycle
- [`howto-rhoai.md`](howto-rhoai.md) — deploying onto an RHOAI cluster
- [`collectives/README.md`](../collectives/README.md) — the preset index
