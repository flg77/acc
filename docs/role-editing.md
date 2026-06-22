# Editing, authoring & releasing roles

How to change an existing role, author a new role or skill, and pipe a new
release. Pairs with [`role-authoring.md`](role-authoring.md) (role.yaml schema)
and [`role-model-mapping.md`](role-model-mapping.md) (which model a role runs on).

## 1. Edit an existing role (TUI)

**Ecosystem (pane 6) → Roles → pick a role → ROLE DETAIL.**

- The `role.yaml` view is **read-only by default**. Press **✎ Edit** (or `e`) to
  unlock the inline editor, change it, then **Save role.yaml** (`s`). Saving
  re-locks it so a stray keypress can't corrupt it.
- **Open role.yaml / role.md in $EDITOR** launches an external editor. The
  agent-core image now ships `nano` and sets `EDITOR=nano`; set your own `$EDITOR`
  to override. (Before 2026-06-22 this fell back to `vi`, which wasn't installed,
  and raised `[Errno 2]` — use the inline **✎ Edit** if your image lacks an
  editor.)

Edits to an **in-tree** role (the 7 control roles) take effect on the next agent
boot / re-apply. Edits to a **pack** role change the installed copy under
`$ACC_PACKAGES_ROOT`; re-publish the pack to make it durable (§4).

## 2. Author a NEW role or skill (review-gated)

The Assistant (and you) can write new capabilities. The workflow is **deliberately
review-gated** — never write files blind:

1. **Domain expert first.** Route to the role with the most relevant knowledge
   (`[PROPOSE_ROUTE:<expert_role>:…]`) to source the domain logic.
2. **Draft.** Use the `role_author` / `skill_author` skills with `mode=draft` —
   they return the file content and write **nothing**.
3. **Reviewer optimises.** Route the draft to `reviewer`
   (`[PROPOSE_ROUTE:reviewer:…]`); iterate until it passes. Optimise *before* any
   file lands.
4. **Write.** Re-invoke with `mode=write` → files land under `roles/<name>/` or
   `skills/<name>/`. Add the new skill to the target role's `allowed_skills` and
   add a test.

CLI-equivalent of the scaffold step:

```bash
# draft (prints content, writes nothing)
acc-cli skill invoke role_author  --args '{"name":"tide_watcher","purpose":"Watch tides."}'
acc-cli skill invoke skill_author --args '{"name":"tide_fetch","purpose":"Fetch tide data.","risk_level":"LOW"}'
# write (after review)
acc-cli skill invoke skill_author --args '{"name":"tide_fetch","purpose":"...","mode":"write"}'
```

## 3. Where new roles/skills live

| Kind | Home | Effect |
|---|---|---|
| Control role / core skill | **in-tree** (`roles/`, `skills/`) | ships in the runtime image; release = commit → promote → image rebuild |
| Movable role / pack skill | **`@acc/<pack>`** in `acc-ecosystem` | published to the catalog; installed with `./acc-deploy.sh pkg add @acc/<pack>` |

## 4. Pipe the release

Use the `release_pipe` skill to get the exact, ordered steps for the artifact —
it plans, it does not execute the gated steps:

```bash
acc-cli skill invoke release_pipe --args '{"kind":"role","name":"financial_analyst","pack":"@acc/capital-markets-roles","version":"1.1.0"}'
```

Returns the pipeline:

1. **Reviewer optimisation** (reviewer) — gate.
2. **Write** the reviewed files (agent).
3. **Build** the family pack — `python tools/build_family_pkg.py <pack>` (agent).
4. **Sign + verify** — cosign keyless + Enterprise Contract (**operator** — gated).
5. **Publish** to the catalog (**operator-only** — `acc-pkg publish`).
6. **Catalog index** + version bump (agent).
7. **Promote** spearhead → mirror — `acc-promote` (**operator** — gated).
8. **Consume** — `./acc-deploy.sh pkg add <pack>@<version>` then infuse.

> Sign / publish / promote are **operator-only** by ACC policy — the agent plans
> them and hands them to you; it never publishes or promotes itself. For an
> **in-tree** role the pipeline is commit → tests → `acc-promote` → image rebuild →
> bump the CatalogSource.

## 5. Install pack roles (so they show up)

Multiple packs in one call (fixed 2026-06-22 — the old form silently dropped
extras):

```bash
./acc-deploy.sh pkg add @acc/workspace-roles @acc/devops-roles @acc/business-roles
./acc-deploy.sh pkg add @acc/business-roles@^1.0 --catalog acc-canonical   # pin a catalog
./acc-deploy.sh pkg list                                                    # confirm
```

After install, Ecosystem → Roles shows the pack roles; the Assistant can
`[PROPOSE_INFUSE:@scope/pack@constraint:reason]` to acquire them autonomously
(infusion always routes through the Compliance queue).
