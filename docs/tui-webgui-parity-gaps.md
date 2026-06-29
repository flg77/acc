# TUI ↔ WebGUI parity — feature-gap analysis (2026-06-30)

The ACC operator surface ships **two front-ends over the same NATS substrate**:
the **TUI** (`acc/tui/`) and the **WebGUI** (`acc/webgui/` backend + `webgui/src/`
React). The WebGUI is the **RHOAI-dashboard-facing** surface, so for *full RHOAI
integration* its parity with the TUI is what determines whether a datacenter
operator can do — from the browser — what an edge operator does in the TUI.

**Verdict:** WebGUI ≈ **60% parity**. The one **critical** gap is the
**Diagnostics → golden-prompt eval-history** surface (the entire proposal-G
arc) — present in the TUI, effectively absent in the WebGUI. That gap *is* the
missing datacenter eval loop, so it's the top RHOAI-integration item.

## Surface inventory

| Surface | TUI | WebGUI | Status |
|---|---|---|---|
| Dashboard / Soma | ✅ | ✅ | full |
| Prompt (chat) | ✅ rich (reasoning stream, waterfall, workspace) | ✅ chat only | partial |
| Infuse / Nucleus | ✅ role builder + active-LLM + caps | ✅ form (role_id/purpose/persona) | partial |
| Compliance | ✅ OWASP grades + violation log + oversight + gap-scan + proposals | ✅ oversight + frameworks + gap-scan + proposals; **no OWASP grade table / violation log** | partial |
| Configuration | ✅ LLM hot-swap + role→model table + Skills/MCP upload | ✅ LLM test + model registry view; **no role→model assign, no save, no upload** | partial |
| **Diagnostics (eval-history)** | ✅ **run · history · versions · enrichment · def-of-good · → Eval · MLflow links** | ❌ **read-only golden list only** | **ABSENT** |
| Ecosystem / Marketplace / Catalogs / RoleEditor | ✅ | ✅ (Marketplace/Catalogs/RoleEditor full) | parity |
| Trace (waterfall / PLAN DAG / audit) | partial (Prompt) | ✅ dedicated screens | webgui ahead |

Backend today (`acc/webgui/routes_*.py`): `GET /api/diagnostics/golden` returns
the prompt **list only** (`routes_governance.py:80`). There is **no** run /
history / enrichment / promote endpoint.

## Prioritized gaps

### CRITICAL — the datacenter eval loop (proposal-G parity)
| Gap | WebGUI today | Effort | Reuses |
|---|---|---|---|
| Run a golden prompt | absent | M | `golden_prompts.run_one` + `WebPromptChannel` (mirror `routes_action.send_prompt`) |
| Per-prompt run history | absent | S | `golden_prompts.read_run_history(name)` → GET |
| Per-run enrichment (tokens · compliance · verdict) | absent | S | already on `GoldenResult`/`run_history.jsonl` (P2) |
| MLflow trace deep-link | absent | S | `mlflow_runs.mlflow_trace_url(task_id)` (P3) |
| Definition-of-good panel | absent | S | `prompt.expects` + the run's `eval_verdict` |
| Versions + restore | absent | S | `golden_prompts.list_versions/read_version/diff_versions` (P1) |
| → Eval promotion | absent | M | `pkg.evals.from_golden_prompt` + `dump_behavior_eval` (P3) |

All of the above reuse **already-shipped** runtime functions — the parity work
is **API + React**, not new engine code.

### SECONDARY — observability + config
| Gap | Effort | Note |
|---|---|---|
| OWASP grade table + violation log (Compliance) | S | data already in the snapshot; frontend-only |
| role→model assignment + save (Configuration, 044 O4) | M | `role_model_map.resolved_role_model_rows` + a POST |
| LLM hot-swap save / Skills-MCP upload | M | mirror the TUI flows |

### TERTIARY — polish
Prompt reasoning stream / invocation waterfall, Nucleus active-LLM + caps
(044 O3), operating-mode selector, gate-cards in the Prompt pane.

## Recommended sequencing

1. **WebGUI Diagnostics eval-history** (CRITICAL) — a new `routes_diagnostics.py`
   (run / history / promote, returning the enriched `GoldenResult` + the MLflow
   link) + a React **Diagnostics** screen mirroring the TUI. This is the
   load-bearing "full RHOAI integration" item: it puts the eval loop + the
   MLflow deep-links on the datacenter dashboard. ~5–7 d.
2. **Compliance OWASP/violation display** + **Configuration role→model** — both
   small, high-value observability/config wins. ~3 d.
3. Polish (tertiary) as demand surfaces.

Everything here reuses shipped runtime functions (proposal G P1–P3, 044 O3/O4),
so each slice is API + React + tests over a stable engine.

## References
- TUI Diagnostics (the template): `acc/tui/screens/diagnostics.py`.
- WebGUI backend: `acc/webgui/routes_governance.py` (golden list),
  `acc/webgui/routes_action.py` (the `WebPromptChannel` run pattern).
- WebGUI React: `webgui/src/screens.tsx` (Diagnostics stub), `webgui/src/api/client.ts`.
- Runtime to reuse: `acc/golden_prompts.py`, `acc/pkg/evals.py`,
  `acc/backends/mlflow_runs.py`, `acc/role_model_map.py`.
- Proposal G (eval-history): `ACC-PR/Proposals/PR-PROPOSAL-G`.
