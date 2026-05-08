# Example — ACC Autoresearcher

Fully runnable showcase of multi-agent business research with an
**iteration loop** (Karpathy-style adopt-the-pattern), real web
research via three MCPs, and Cat-A-governed self-modifying
personas.

Pairs with `docs/examples/acc-autoresearcher-example.md` (the full
plan + design rationale).  Companion to
`examples/coding_split_skills/` (Example No. 1).

## What this demonstrates

* **Six specialist personas** chained via `depends_on` edges:
  planner → economist + competitor (parallel) → strategist →
  synthesizer ⇄ critic (iteration loop).
* **Real web research** via three MCPs:
  `web_browser_harness` (HIGH risk; Playwright + LLM-driven
  browser), `web_search_brave` (MEDIUM; query API),
  `web_fetch` (MEDIUM; URL → markdown with paywall detection).
* **Iteration loop** — the critic publishes `NEEDS_REVISE` on
  EVAL_OUTCOME; the arbiter re-issues the synthesize step with
  the critique appended.  Up to `ACC_RESEARCH_MAX_ITERATIONS`
  rounds (default 3).
* **Self-modifying personas** (opt-in) — when the synthesizer
  step has `enable_prompt_patches: true`, the critic may emit a
  `prompt_patch` field in its EVAL_OUTCOME; Cat-A A-021 sanity
  rules drop oversize / cross-persona / unbacked-section
  patches.
* **Cost cap** — `max_run_tokens` (operator-tunable via
  `ACC_RESEARCH_MAX_RUN_TOKENS`); exceeding it transitions the
  running step to FAILED + emits ALERT_ESCALATE.
* **Citation re-verification** — the critic re-fetches a sample
  of cited URLs to defend against fabricated citations; verify.sh
  cross-references inline citations against `web_fetch` invocations
  and exits non-zero when fewer than 30% of citations were
  re-fetched (`ACC_RESEARCH_MIN_VERIFIED_CITATIONS`).

## Plan structure (DAG)

```
                   ┌─── plan ────────────────────────────┐
                   │  research_planner (1)               │
                   │   → KNOWLEDGE_SHARE outline         │
                   └────────────────┬────────────────────┘
                                    │ depends_on
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     │
        ┌──────────┐          ┌────────────┐              │
        │ economics│          │ competitive│              │
        │ ≤3 mems  │          │ ≤3 mems    │              │
        └────┬─────┘          └──────┬─────┘              │
             │  depends_on           │                    │
             └───────────┬───────────┘                    │
                         ▼                                │
                  ┌──────────────┐                        │
                  │ strategy (1) │                        │
                  └──────┬───────┘                        │
                         │ depends_on                     │
                         ▼                                │
                  ┌──────────────┐                        │
                  │ synthesize(1)│ ◀──┐ iteration loop    │
                  └──────┬───────┘    │ (E1)              │
                         │ depends_on │                   │
                         ▼            │                   │
                  ┌──────────────┐    │                   │
                  │ critique (1) │    │                   │
                  │  PASS → done │    │                   │
                  │  NEEDS_REVISE├────┘                   │
                  │   re-issue   │                        │
                  │   synthesize │                        │
                  └──────────────┘                        │
```

## Prerequisites

1. ACC repo cloned + `pip install -e .` done in a `.venv`.
2. `acc-deploy.sh` works locally (podman + podman-compose installed).
3. **An LLM backend** reachable from the agent containers — Anthropic,
   OpenShift AI vLLM, or local Ollama with a 70B+ model (smaller
   models produce thin reports).
4. **A Brave Search API key** (free tier 2k/month):
   https://brave.com/search/api/.
5. **OK with the live cost** — a typical Anthropic Sonnet 4.5 run
   lands ~$3-5 in token spend.  Cap via
   `ACC_RESEARCH_MAX_RUN_TOKENS`.

## Run it (one command)

```bash
cd /path/to/agentic-cell-corpus
cp examples/acc_autoresearcher/.env.example examples/acc_autoresearcher/.env
$EDITOR examples/acc_autoresearcher/.env       # set ACC_*_API_KEY + BRAVE_API_KEY

# Default topic
./examples/acc_autoresearcher/run.sh

# Custom topic
./examples/acc_autoresearcher/run.sh --topic agentic-ai-edge-2027

# With live PLAN re-broadcasts streaming
./examples/acc_autoresearcher/run.sh --watch
```

`run.sh` will:

1. Source `.env` into the process environment.
2. Compute `<topic-slug>-<YYYYMMDD>` from `--topic`; create
   `runs/<topic>-<date>/` and export `ACC_RUN_OUTPUT_DIR`.
3. `acc-cli role lint` every research persona's `role.md`.
4. `./acc-deploy.sh up` (TUI + AUTORESEARCHER profiles).
5. Patch the plan's `max_run_tokens` from
   `ACC_RESEARCH_MAX_RUN_TOKENS` if set.
6. `acc-cli plan submit` to the active collective.
7. Print follow-up commands.

## Watch the topology in the TUI

```bash
acc-tui                # in another terminal
# Press 7 (Prompt) — cluster panel above the transcript.
# Press 4 (Comms)  — full PLAN DAG with step_progress + iteration_n.
# Press 5 (Performance) — capability_stats per skill / MCP.
```

While running:

```
> /cluster show                ← snapshot in transcript
> /cluster kill c-econ5678     ← kill a runaway researcher cluster
> /cancel <task_id>            ← single-task cancel
```

See `expected_topology.md` for phase-by-phase reference snapshots
of the cluster panel.

## Programmatic verification

```bash
./examples/acc_autoresearcher/verify.sh
```

Two layers:

1. **Cluster topology** — confirms ≥ `ACC_VERIFY_MIN_CLUSTERS` distinct
   clusters were observed during the run window.
2. **Citation re-fetch coverage** — parses the report's inline
   citations, cross-references `web_fetch.fetch` invocations from
   the bus log, exits non-zero when fewer than
   `ACC_RESEARCH_MIN_VERIFIED_CITATIONS` (default 0.30) of
   citations were re-fetched.

The implementation lives in `acc/research/citation_verifier.py`
and is unit-tested in `tests/test_citation_verifier.py`.

## Iteration-quality experiment

To answer the operator's question "do more iterations actually
improve quality, or does the LLM start drifting?":

```bash
# Baseline: 3 iterations
ACC_RESEARCH_MAX_ITERATIONS=3 \
    ./examples/acc_autoresearcher/run.sh --topic exp-iter-3
./examples/acc_autoresearcher/verify.sh

# Stretch: 5 iterations
ACC_RESEARCH_MAX_ITERATIONS=5 \
    ./examples/acc_autoresearcher/run.sh --topic exp-iter-5
./examples/acc_autoresearcher/verify.sh

# Compare
diff -u runs/exp-iter-3-*/agentic_ai_strategy_report.md \
        runs/exp-iter-5-*/agentic_ai_strategy_report.md \
    | wc -l
# More changed lines = more revision happened.

# Inspect critic verdicts:
grep '"verdict"' runs/exp-iter-3-*/.verification.json
grep '"verdict"' runs/exp-iter-5-*/.verification.json
```

If the 5-iteration run scores lower or its citation coverage
drops, lock the default at 3.  If it improves monotonically,
operators can safely bump.  Documented as a residual question in
the plan (revision 2).

## Tear down

```bash
./examples/acc_autoresearcher/clean.sh           # keeps runs/ artefacts
./examples/acc_autoresearcher/clean.sh --purge-runs   # wipes runs/ too
```

## Files in this directory

| File | Purpose |
|---|---|
| `.env.example` | Template — copy to `.env` and fill in. |
| `plan.yaml` | Canonical 5-step plan (planner / economics / competitive / strategy / synthesize / critique). |
| `plan.json` | JSON copy. |
| `task.md` | Operator-facing prose brief. |
| `expected_topology.md` | Phase-by-phase reference + anti-checks. |
| `run.sh` | One-shot runner. |
| `verify.sh` | Cluster topology + citation re-fetch verification. |
| `clean.sh` | Teardown + scratchpad eviction. |
| `README.md` | This file. |

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---|---|---|
| `target_role unknown` on every TASK_ASSIGN | Persona role directories not present | Confirm `roles/research_*/role.yaml` exist on the agent host. The persona family ships with PR #44 (E4). |
| Cluster panel header stays at `Clusters: 0` | Arbiter not wired with resolvers | Confirm PR #34 (D1) is on the agent host's main. |
| Synthesize step never re-runs despite NEEDS_REVISE | Iteration loop wiring | Confirm PR #41 (E1) is on the host; check `ACC_RESEARCH_MAX_ITERATIONS > 1`. |
| `acc-cli plan submit` rejects the YAML | YAML support missing | Confirm PR #35 (D2) is on main. |
| `verify.sh` exits non-zero with "no run directory found" | run.sh did not export `ACC_RUN_OUTPUT_DIR` | Re-run `run.sh`; verify.sh falls back to the most-recent `runs/*/` if the env var is missing. |
| `verify.sh` reports coverage_rate=0 with citations populated | Critic's web_fetch invocations not landing | Check critic's role.yaml allows `web_fetch` in `default_mcps`; check the bus log for `web_fetch.fetch` invocations. |
| `web_browser_harness` Cat-A blocks every invocation | Persona's `max_mcp_risk_level` not HIGH | Confirm role.yaml declares `max_mcp_risk_level: HIGH` (only the four research personas that use the harness do). |

## See also

* `docs/examples/acc-autoresearcher-example.md` — the full design plan (rev 2).
* `docs/SUBAGENT_COMMUNICATION.md` — Patterns A-E used by the personas.
* `docs/IMPLEMENTATION_subagent_clustering.md` — wire-protocol reference.
* `docs/CODING_AGENT_SUBROLES.md` — comparable persona library for coding clusters (Example No. 1).
