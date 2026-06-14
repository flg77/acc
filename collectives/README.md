# Collective presets

Ready-made [`AgentCollectiveSpec`](../acc/collective.py) presets for the
standalone Podman stack. Each is a declarative agentset that
`./acc-deploy.sh apply` synthesizes into a `podman-compose` overlay and brings
up alongside the always-on baseline (ingester / analyst / arbiter).

The live/default spec is **`../collective.yaml`** (repo root, bind-mounted into
the TUI's Agentset editor). The files here are reusable templates you `apply`
by **bare name** — `apply` resolves a name to `collectives/collective.<name>.yaml`
(or `collectives/<name>.yaml` for the demos):

```bash
./acc-deploy.sh apply coding-split            # collectives/collective.coding-split.yaml
./acc-deploy.sh apply --dry-run capital-markets   # preview the reconcile diff
./acc-deploy.sh apply --prune collective.yaml     # reconcile-down: remove agents dropped from the spec
```

`apply` never removes the operator's attached `acc-tui` (orphan removal is
opt-in via `--prune`, and even then the `tui` profile is kept active).

## Packs

Most worker roles are served by signed `@acc/*` **family packs**, not in-tree.
A preset declares them in `required_packages:`, so `apply` resolves + verifies
+ installs them from the catalog at boot. Install ahead of time with either:

```bash
./acc-deploy.sh pkg add @acc/workspace-roles        # deploy wrapper (containerized acc-pkg)
./acc-pkg install @acc/workspace-roles@^1.0         # native acc-pkg flags
```

The 7 CONTROL roles (`arbiter`, `assistant`, `compliance_officer`, `ingester`,
`observer`, `orchestrator`, `reviewer`) are substrate — always present, never
served from a community pack, and never declared in `required_packages`.

## Presets

| Preset (`apply <name>`) | Roles | Family pack | Demonstrates |
|---|---|---|---|
| `coding-split` | 3× `coding_agent` | `@acc/workspace-roles` | parallel coding |
| `reviewer` | `coding_agent_implementer`/`_tester` + `reviewer` | `@acc/workspace-roles` | multimodel critic loop (cheap workers, strong reviewer) |
| `orchestrator` | `orchestrator` router + `coding_agent` + `analyst` | `@acc/workspace-roles` | capability routing to the right worker |
| `worker-pool` | dormant `coding_agent_*` slots | `@acc/workspace-roles` | arbiter-activated elastic capacity (`worker_pool`) |
| `autoresearcher` | 6 `research_*` roles | `@acc/research-roles` | the 5-phase research DAG → report |
| `assistant` | `assistant` | _(CONTROL — none)_ | on-demand concierge / maintenance |
| `capital-markets` | equity / fixed-income / macro / quant / options / portfolio … | `@acc/capital-markets-roles` | a full capital-markets desk |
| `demo-coding` | control plane + `@acc/workspace-roles` + `@acc/devops-roles` | both | proposal-018 coding+devops demo |
| `demo-financial` | control plane + business specialists | `@acc/business-roles` | proposal-018 finance demo |
| `demo-multi` | hub assistant + 2 managed sub-collectives | all of the above | `[DELEGATE:cid]` sub-collective routing |

## Keeping presets aligned

Run the **`/acc-collectives`** skill (or its underlying checker) to validate
every preset against the rest of ACC — that each agent role exists (CONTROL or
served by a declared pack), each `model:` is a real `models.yaml` id, each
`required_packages:` entry resolves in the catalog, and that non-CONTROL roles
are covered by a declared pack. CI runs the same checks in
`tests/test_demo_collectives.py` + `tests/test_collective_presets.py`.
