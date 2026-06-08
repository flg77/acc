# 20260604-business-roles-domain-split — cuOpt enrichment design (DEFERRED)

> **Status: design only.**  No code in this change.  Ships as a
> follow-up OpenSpec change once the split lands.  Gated to
> `rhoai`/GPU deploy mode.

## What cuOpt is

[NVIDIA cuOpt](https://github.com/NVIDIA/cuopt) is a GPU-accelerated
decision-optimization engine (Apache-2.0) solving:

* **VRP** — vehicle/route problems (multi-stop, time windows, capacity).
* **LP** — large linear programs (PDLP GPU solver).
* **MILP** — mixed-integer programs (discrete decisions).

Consumed as a Python library (`pip install cuopt`), a self-hosted REST
server, or a NIM microservice.  Real performance needs a CUDA GPU
(Volta+); CPU execution exists but forfeits the speedup — so cuOpt is a
**datacenter/`rhoai`** capability, not a standalone/edge one.  No
community MCP server exists today, so ACC would author the first thin
wrapper.

## Integration shape

A first-party MCP, **`@acc/mcp-cuopt`** (tier `own_pack` — optional,
not bundled in any role):

```
mcps/cuopt/mcp.yaml          transport: http; url from config; risk: MEDIUM
  tools:
    solve_vrp(fleet, stops, constraints)   → routes + objective
    solve_lp(objective, constraints)       → assignment + objective
    solve_milp(objective, constraints, ints)→ assignment + objective
```

The wrapper is stateless glue: it POSTs a problem spec to an external
cuOpt REST/NIM endpoint and returns the solution.  The GPU service is
operator-provided infrastructure, not shipped by ACC.

## Deploy-mode gating

Wire the cuopt MCP only when `deploy_mode == "rhoai"` **and** a cuOpt
endpoint is configured — same pattern as `acc/config.py::build_backends()`.
On standalone/edge it's simply absent; roles that *could* use it degrade
to LLM/heuristic reasoning (no hard dependency, no governance change).

## Role → optimization use-case map

| Role | Problem | cuOpt type |
|---|---|---|
| `sales_operations_manager` | territory design, quota allocation | MILP / assignment |
| `revenue_operations_analyst` | territory/quota modelling | MILP |
| `operations_analyst` | scheduling, workflow/route optimization | VRP / MILP |
| `procurement_specialist` | sourcing / supplier selection | MILP |
| `project_manager` | resource leveling | LP |
| `finance_analyst` / `fpa_analyst` | budget / portfolio allocation | LP |
| `customer_success` / support | agent rostering, ticket routing | MILP / VRP |

## Why deferred

* GPU dependency limits it to `rhoai`; most ACC installs are
  standalone/edge.
* It's additive and orthogonal to the split — bundling it would widen
  blast radius and couple a docs/packaging change to a GPU integration.
* Authoring + testing the wrapper (and a CPU/no-GPU fallback contract)
  is its own slice with its own eval suite.
