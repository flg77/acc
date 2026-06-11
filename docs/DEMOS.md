# ACC demo collectives

Two runnable demo collectives that showcase the assistant routing a
prompt to the genuinely best-matched specialist role (proposal 019
catalog awareness), plus a parent that hosts both as sub-collectives.

| Demo | File | Specialists |
|---|---|---|
| Coding + devops | `examples/collectives/demo-coding.yaml` | coding_agent (+ architect/reviewer), devops_engineer, ml_engineer |
| Financial | `examples/collectives/demo-financial.yaml` | financial_analyst, fpa_analyst, contract_analyst, risk_compliance_analyst, account_executive, business_analyst |
| Multi (parent) | `examples/collectives/demo-multi.yaml` | hub assistant routes to the two demos as sub-collectives |

Every demo carries the control plane — `assistant` (router),
`orchestrator`, `reviewer` (critic loop on the stronger model), and
`compliance_officer` — from the in-tree CONTROL roles.

## Prerequisites

Install the family packs the demos consume (signed v1.0.2 from the
public hub, or build locally):

```bash
acc-pkg install @acc/workspace-roles@^1.0
acc-pkg install @acc/devops-roles@^1.0
acc-pkg install @acc/business-roles@^1.0
```

(`acc-deploy.sh apply` also installs anything in `required_packages:`
automatically at boot — Stage 1.5.3.)

## Run the financial demo

```bash
./acc-deploy.sh apply examples/collectives/demo-financial.yaml
acc-tui   # or acc-webgui
```

In the Prompt pane (key `7`), target `assistant` and send a financial
task, e.g.:

> Build a 4-quarter cash-runway forecast from this P&L snapshot.

What to watch (proposal 019 in action):
1. The assistant runs `catalog_query` and its `[REASON:]` block names
   the candidates it considered — e.g. routing to `financial_analyst`
   over `business_analyst` because the goal needs DCF modelling.
2. If the best-matched specialist is dormant, the assistant emits
   `[PROPOSE_SPAWN:...]`; if a needed pack isn't installed,
   `[PROPOSE_INFUSE:...]` (operator-gated on the Compliance pane).
3. If nothing fits well, the assistant emits a `[ROLE_GAP:...]`
   finding — grounded in reviewer + compliance feedback — instead of
   force-routing.

## Run the coding demo

```bash
./acc-deploy.sh apply examples/collectives/demo-coding.yaml
```

Send a technical task to `assistant`, e.g.:

> Write an Ansible playbook to distribute a MOTD with host stats.

## Run both at once (sub-collectives)

```bash
./acc-deploy.sh apply examples/collectives/demo-multi.yaml
```

The hub assistant delegates each prompt to the sub-collective that
owns its domain (`software_engineering` → coding; `business_finance`
→ financial) via `[DELEGATE:cid:reason]`.

## Models

The demos reference real `models.yaml` ids — `claude-haiku` for cheap
workers, `claude-sonnet` for the reviewer + senior specialists. Swap
to local ids (`ollama-llama32-3b`, `ollama-qwen25-14b`, `vllm-local`)
for an offline demo; the topology is model-agnostic.

## Notes

* `required_packages:` pins `^1.0`, which picks up the signed `1.0.2`
  packs automatically.
* The full golden-prompt regression suite + the Prompt-pane Workflow
  visualization (proposal 018 PR-DEMO2..4) layer on top of these
  collectives in follow-up work.
