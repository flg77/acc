# Plan — ACC Autoresearcher Demo (Example No. 2)

> **Status:** *plan for review — revision 2.* Operator answers from
> `ACC Researcher.md` folded in (browser-harness primary, output to
> `runs/<topic>-<date>/`, self-modifying personas opt-in, critic
> re-fetches in scope, paywall detection in scope). Nothing
> implemented yet. The intended workflow remains: read end to end,
> confirm the residual open questions, then start E1.
>
> Companion to the merged `examples/coding_split_skills/`
> (Example No. 1) — same scaffolding conventions; new skills + MCPs
> + personas + one new wire-protocol field + one new optional
> capability (prompt patches) gated behind plan-step config.

## Context

[karpathy/autoresearch](https://github.com/karpathy/autoresearch) is
a *single-agent ML self-improvement loop*: one process iteratively
edits `train.py`, runs a 5-minute training pass, evaluates
`val_bpb`, keeps or discards. We **adopt the pattern** (iterate →
evaluate → keep/revise → repeat) and apply it to a **multi-agent
business research scenario** that ACC is uniquely well-suited to:
parallelisable researcher personas with skill-aware fan-out, an
explicit critic loop driving revision, and Cat-C rule promotion
catching long-term patterns across runs.

### The framing

Red Hat's "Technical Thought Leadership Accelerator Program" —
challenge topic Agentic AI. Our deliverable: a researched +
referenced markdown report arguing that **ACC is the vehicle to
drive Red Hat's agentic business from the edge to the datacenter**.
The report has to ground every claim in current public sources, so
the demo runs **real web research** via real MCP servers (operator
decision: real research, not stub round-trip).

### Output deliverable

A single markdown file under a per-run directory at the repo root:

```
runs/<topic-slug>-<YYYYMMDD>/
├── agentic_ai_strategy_report.md     ← the canonical deliverable
├── .meta.json                         ← cluster_ids, member task_ids,
│                                        iteration_n per step,
│                                        critic verdicts (replay manifest)
├── citations/                         ← per-URL fetched content
│   ├── <sha8>.md                       (one file per cited URL)
│   └── index.json                      (URL → sha8 mapping)
└── traces/                            ← per-persona TASK_PROGRESS log
    ├── planner.log
    ├── economist.log
    ├── ...
```

> **Operator note flagged in `ACC Researcher.md`** suggested
> `roles/researcher-role/run-[topic-date]`. We deviate to a top-level
> `runs/` directory because `roles/` is reserved for role
> *definitions* — `RoleLoader` walks `roles/` looking for `role.yaml`,
> and a `runs/` subdirectory under there would either be loaded as a
> role or require special-casing. **`runs/<topic>-<date>/` at the
> repo root** is functionally identical from the operator's
> perspective and clean from the loader's. The whole `runs/` tree
> is `.gitignore`d. **Flag this for confirmation.**

Section structure:
1. Executive Summary
2. Market Economics — TAM/SAM/SOM for agentic AI 2025-2030
3. The Edge Market — why it's the next 10× scale event
4. Competitive Landscape — hyperscaler agent platforms + OSS runtimes
5. Architecture Analysis — how competitors are built
6. Red Hat Positioning — strengths, gaps, the ACC-shaped opportunity
7. Forecast Assumptions — 3/5/10-year horizons + sensitivity
8. Citations — every URL the agents fetched, with claim attribution

Output written by the `research_synthesizer` persona via
`[SKILL: report_drafter]` markers. No JSON / slide artefacts in
this iteration — the markdown report is the canonical deliverable.

---

## What's already there + what's new

### Reuses (no work required)

| Surface | From | What we use it for |
|---|---|---|
| PlanExecutor cluster fan-out | PR #27 + #34 | Fans the economist + competitor steps out into multi-member clusters |
| `cluster_id` propagation | PR #26 | Critic sees per-cluster member outputs |
| Markdown role authoring | PR #28 | Six new personas authored as `role.md` |
| Cluster panel | PR #29 | Operator watches the research clusters live |
| Slash commands | PR #30 | `/cluster show`, `/cluster kill`, `/cancel` for runaway research |
| Skill registry + Cat-A A-017 | Phase 4.3 | Governs which skills each persona may invoke |
| MCP registry + Cat-A A-018 | Phase 4.3 + PR #24 | Same, for MCP tools |
| EVAL_OUTCOME → episode log → Cat-C promotion | ACC-10 + ACC-12 | Long-term self-improvement across runs |

### Net-new (this plan)

| New | Layer | Why |
|---|---|---|
| **REVISE** verdict on EVAL_OUTCOME | wire | Drives the intra-task iteration loop |
| `max_iterations` field on PLAN steps | wire | Caps re-runs (default 3) |
| Arbiter re-issue logic for REVISE | runtime | Implements the iteration loop |
| Six research personas | roles/ | The actual research workforce |
| Six stub research skills | skills/ | Governance + audit anchors for the LLM-emitted findings |
| Two real MCP servers wired | mcps/ + container | Web search + page fetch |
| `examples/acc_autoresearcher/` scenario | examples/ | The runnable demo |

---

## Six personas — the research workforce

Same convention as the coding-split family: each persona is a
narrowed version of a base research role with distinct system
prompt, default skills, estimator, and eval rubric. Cancellation
behaviour declared per persona.

### 1. `research_planner` (1 instance, drives the run)

**Purpose.** Read the task brief; produce the report outline +
per-section research questions; publish the outline as
KNOWLEDGE_SHARE so all downstream researchers consume the same
contract. Equivalent of `coding_agent_architect`.

**Skills (default).** `plan_outline` (stub) — emits a JSON outline
that all researchers read from the cluster scratchpad.

**Skills (allowed).** `plan_outline`, `web_search` (in case a
question's framing needs grounding before researchers fan out).

**Estimator.** `fixed: 1` — multiple planners would fragment the
contract.

**Domain receptors.** `business_research`.

### 2. `research_economist` (≤3 instances, market sizing + edge market + forecasts)

**Purpose.** Produce TAM/SAM/SOM market-size estimates for the
agentic-AI market overall and the edge sub-market. Cite every
number to a public source. Forecast 3/5/10-year horizons with
sensitivity analysis.

**Skills (default).** `web_search`, `web_fetch`, `market_sizer`,
`citation_tracker`.

**Skills (allowed).** All of the above + `report_drafter` for
self-validation.

**Estimator.** `heuristic` — `base: 1, per_n_tokens: 4000, cap: 3`,
difficulty bumps `forecast → +1`, `edge → +1`.

**Domain receptors.** `business_research`, `economic_analysis`.

### 3. `research_competitor` (≤3 instances, competitive landscape + architecture)

**Purpose.** Profile the leading agent platforms (Bedrock Agents,
Agentspace, Copilot Studio, watsonx Orchestrate, CrewAI, LangGraph,
Letta, etc.). Document architecture, pricing, openness, edge
support. Cite docs + announcements.

**Skills (default).** `web_search`, `web_fetch`,
`competitor_profile`, `citation_tracker`.

**Skills (allowed).** Above + `report_drafter`.

**Estimator.** `heuristic` — `base: 1, per_n_tokens: 3000, cap: 3`,
difficulty bumps `architecture → +1`, `edge → +1`.

**Domain receptors.** `business_research`, `competitive_analysis`.

### 4. `research_strategist` (1 instance, Red-Hat-specific framing)

**Purpose.** Read the economist + competitor outputs from the
scratchpad. Produce the "Red Hat positioning" section: strengths,
gaps, the ACC-shaped opportunity, "why now" framing. The
opinionated voice of the report.

**Skills (default).** `web_search` (light grounding only),
`citation_tracker`, `report_drafter`.

**Skills (allowed).** Above + `competitor_profile` for cross-check.

**Estimator.** `fixed: 1` — strategy voice can't fragment.

**Domain receptors.** `business_research`, `strategic_analysis`.

### 5. `research_synthesizer` (1 instance, fuses + drafts the report)

**Purpose.** Read every researcher's output from the cluster
scratchpad. Apply the planner's outline. Produce the canonical
markdown report. Embed citations inline with the same URL → claim
mapping the researchers wrote.

**Skills (default).** `report_drafter`, `citation_tracker`.

**Skills (allowed).** Above + `web_fetch` for last-mile citation
verification.

**Estimator.** `fixed: 1`.

**Domain receptors.** `business_research`.

### 6. `research_critic` (1 instance, drives the iteration loop)

**Purpose.** Read the synthesizer's draft. Score against the
rubric: sourcing, internal consistency, coverage of the planner's
outline, Red Hat-positioning argument strength. Emit verdict PASS,
NEEDS_REVISE, or FAIL. NEEDS_REVISE injects critique text into the
synthesizer's task_description and the arbiter re-issues the step.

**Skills (default).** `critic_verdict` (stub — emits structured
verdict + critique) + `report_drafter` (read-only inspection).

**Skills (allowed).** Above + `web_search` for spot-checking
specific claims.

**Estimator.** `fixed: 1`.

**Domain receptors.** `business_research`.

---

## Skills + MCPs map

### Real MCP servers (NEW — one container each)

Operator decision (`ACC Researcher.md`): browser-harness is the
**primary** research tool; Brave Search remains as a lighter
fallback for query-only tasks; fetch covers known-URL retrieval.

| MCP | Source | Risk | Used by | Notes |
|---|---|---|---|---|
| **`web_browser_harness`** | [browser-use/browser-harness](https://github.com/browser-use/browser-harness) — Playwright + LLM-driven browser. Container ships Chromium + headless harness; needs an API key for the harness's underlying LLM (reuses `ACC_ANTHROPIC_API_KEY`). | **HIGH** (executes JS in untrusted contexts; full browser surface) | economist, competitor, strategist, planner | Primary research tool. Personas declare `max_mcp_risk_level: HIGH` to reach it. |
| `web_search_brave` | [modelcontextprotocol/servers/brave-search](https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search) — needs `BRAVE_API_KEY`. Free tier 2k queries/month. | MEDIUM | All researchers as fallback when a quick keyword lookup is enough. | Used when browser-harness is overkill (single-fact lookup). |
| `web_fetch` | [modelcontextprotocol/servers/fetch](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch) — converts a known URL to markdown. **Wrapped in this PR with paywall detection** — surfaces `paywalled: true` when 401/402/HTTP-content-says-paywalled is observed so the citation tracker can flag it. | MEDIUM | economist, competitor, strategist, synthesizer, **critic (re-fetch verification)** | Lightweight; primary path for citation re-verification. |

All three ship as containers in `container/production/podman-compose.yml`
under a new `acc-autoresearcher` profile activated by env var
`AUTORESEARCHER=true`. Manifests under
`mcps/web_browser_harness/`, `mcps/web_search_brave/`,
`mcps/web_fetch/`.

**Risk-ceiling implication.** `web_browser_harness` is HIGH risk
because the harness drives a real browser through arbitrary
operator-untrusted pages — a malicious site can attempt phishing
flows, JS-based fingerprinting, or attempt to cause the harness to
follow social-engineering instructions. Researcher personas must
explicitly raise `max_mcp_risk_level: HIGH` (default is MEDIUM); the
operator opts in via the role.md authoring step, and Cat-A A-018
gates per-invocation as usual. **Cat-A enforcement is the whole
point of this surface** — without it, agentic browser automation
would be a known-bad-pattern in the runtime.

### Stub skills (NEW — six pass-through manifests)

Same `StubCodingSkill`-style adapter pattern as PR #36. Each is a
governance anchor + audit trail for an LLM-emitted output category;
the LLM does the actual reasoning.

| Skill | Risk | Domain | Used by | Output shape |
|---|---|---|---|---|
| `plan_outline` | LOW | business_research | planner | JSON outline (sections, questions per section) |
| `citation_tracker` | LOW | business_research | every researcher + synthesizer | List of `{url, claim, confidence}` triples |
| `market_sizer` | LOW | economic_analysis | economist | JSON `{tam, sam, som, year, source_urls}` |
| `competitor_profile` | LOW | competitive_analysis | competitor + strategist | JSON vendor card |
| `report_drafter` | LOW | business_research | synthesizer + strategist + critic (read) | Markdown section body |
| `critic_verdict` | LOW | business_research | critic | `{verdict: PASS\|NEEDS_REVISE\|FAIL, score: 0..1, critique: str}` |

### Skill ↔ persona matrix

|  | plan_outline | browser_harness | web_search | web_fetch | citation_tracker | market_sizer | competitor_profile | report_drafter | critic_verdict |
|---|---|---|---|---|---|---|---|---|---|
| planner | **default** | allowed | allowed | — | — | — | — | — | — |
| economist | — | **default** | allowed (fallback) | **default** | **default** | **default** | — | allowed | — |
| competitor | — | **default** | allowed (fallback) | **default** | **default** | — | **default** | allowed | — |
| strategist | — | **default** | allowed | allowed | **default** | — | allowed | **default** | — |
| synthesizer | — | — | — | allowed | **default** | — | — | **default** | — |
| critic | — | allowed | allowed | **default (re-fetch)** | — | — | — | allowed (read) | **default** |

Bold = `default_skills`; "allowed" = in `allowed_skills` but not in
`default_skills`. The cluster panel will render the active default
in the `skill_in_use` column for the typical execution path.

**Notable.** The critic now lists `web_fetch` as **default** because
it re-fetches a sample of cited URLs to verify the citation_tracker
mapping (operator decision in revision 2 — see open question #7).
This is the core defence against "the LLM made up a URL".

---

## Wire-protocol additions

### REVISE verdict + max_iterations + optional prompt_patch

EVAL_OUTCOME today supports `verdict ∈ {GOOD, BAD, PARTIAL}`. We
add **`NEEDS_REVISE`** as a fourth value carrying:

```json
{
  "signal_type": "EVAL_OUTCOME",
  "task_id": "<step task_id>",
  "verdict": "NEEDS_REVISE",
  "overall_score": 0.62,
  "criteria_scores": {...},
  "critique": "Missing 2025 GA dates; SAM-vs-TAM ratio incoherent; …",
  "iteration_n": 1,
  "max_iterations": 3,

  // Optional — only present when the plan-step config opted in to
  // self-modifying prompts.  See "Self-modifying personas" below.
  "prompt_patch": {
    "target_persona": "research_synthesizer",
    "patch_kind": "append",       // or "prepend" / "replace_section"
    "section_marker": null,       // for replace_section
    "text": "When sourcing claims, always link to a primary source ..."
  }
}
```

The `prompt_patch` field is the operator-decision-2 surface (see
open question #5 from revision 1). The critic *may* propose a
patch to the synthesizer's system prompt for the next iteration.
Patches are scoped per-step + per-iteration, never written back to
disk. Cat-A A-021 (NEW — see below) caps total patch length and
forbids patches against any persona other than the one being
re-issued.

### Arbiter response in PlanExecutor

```
on_task_complete(payload):
    ...existing path...
    eval = lookup_eval_outcome(task_id)
    if eval.verdict == "NEEDS_REVISE" and step.iteration_n < step.max_iterations:
        step.iteration_n += 1
        new_task_description = (
            step.original_task_description
            + f"\n\n## Critic feedback (iteration {step.iteration_n}):\n"
            + eval.critique
        )
        re-issue TASK_ASSIGN for the step (new task_id, same cluster_id)
        return  # do NOT transition step yet
    # transition as normal
```

PLAN-step schema gains optional `max_iterations: int = 3` and a
runtime-only `iteration_n: int = 0`. Re-issued tasks share the
parent cluster_id so the panel shows a continuous timeline. New
test module: `tests/test_iteration_loop.py`.

### Cat-A guards

Two new rules:

**A-020** — iteration cap:
```rego
deny_iteration_overrun if {
    input.action == "TASK_REISSUE"
    input.iteration_n > input.step.max_iterations
}
```

**A-021** — prompt patch sanity (only when self-modification is enabled):
```rego
# Patch length capped (default 2000 chars; configurable per
# collective via Cat-B prompt_patch_max_chars).
deny_oversize_prompt_patch if {
    input.action == "PROMPT_PATCH_APPLY"
    count(input.patch.text) > data.cat_b.prompt_patch_max_chars
}

# Patch must target the persona of the step being re-issued —
# the critic CANNOT modify a peer's prompt.
deny_cross_persona_patch if {
    input.action == "PROMPT_PATCH_APPLY"
    input.patch.target_persona != input.step.role
}

# Replace-section patches only land when the persona's
# system_prompt.md actually contains the named section marker.
deny_unbacked_section_replace if {
    input.action == "PROMPT_PATCH_APPLY"
    input.patch.patch_kind == "replace_section"
    not contains(input.system_prompt, input.patch.section_marker)
}
```

A-021 is the safety floor for self-modification: a runaway critic
can't escalate by rewriting an unrelated persona's prompt or
landing arbitrary-length text. Constitutional bumps 0.5.0 → 0.6.0.

---

## DAG of the demo plan

```
                 ┌─── plan ──────────────────────────────┐
                 │  research_planner (1)                 │
                 │   → KNOWLEDGE_SHARE outline           │
                 └────────────────┬──────────────────────┘
                                  │ depends_on
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
      ┌────────────┐      ┌────────────┐    (sibling consumers)
      │ economics  │      │ competitive│
      │ economist  │      │ competitor │
      │ ≤3 members │      │ ≤3 members │
      └─────┬──────┘      └─────┬──────┘
            │  depends_on       │
            └─────────┬─────────┘
                      ▼
               ┌─────────────────┐
               │ strategy        │
               │ strategist (1)  │
               └────────┬────────┘
                        │ depends_on
                        ▼
               ┌─────────────────┐
               │ synthesize      │
               │ synthesizer (1) │
               └────────┬────────┘
                        │ depends_on (iteration loop here)
                        ▼
               ┌─────────────────┐
               │ critique        │
               │ critic (1)      │
               │  PASS → done    │
               │  NEEDS_REVISE → │
               │   re-run        │
               │   synthesize    │
               │   (iteration_n) │
               └─────────────────┘
```

**Parallelism.** `economics` and `competitive` run in parallel (no
inter-dependency; both gated on `plan`). Each fans out into a
multi-member cluster via heuristic estimator. Cluster sizes for a
real run typically settle at 2-3 members each based on the token
estimate the task description grows to.

**Critical path.** `plan → economics → strategy → synthesize →
critique`. Each `NEEDS_REVISE` adds one synthesize + critique
round-trip.

---

## Self-regulation — short-term + long-term

### Self-modifying personas (operator opt-in, revision 2)

Operator decision (`ACC Researcher.md`): the critic *may* modify
the synthesizer's system prompt for iteration N+1, gated per
plan-step by an explicit flag and bounded by Cat-A A-021.

**Plan-step config** (new optional fields):

```yaml
- step_id: "synthesize"
  role: "research_synthesizer"
  depends_on: ["strategy"]
  max_iterations: 3
  enable_prompt_patches: true        # default false
  prompt_patches_writable_to:        # whitelist of personas the
    - research_synthesizer            # critic may patch (cannot
                                      # patch upstream personas).
```

**Lifecycle.**

1. Critic emits EVAL_OUTCOME with verdict=NEEDS_REVISE +
   `prompt_patch` field.
2. Arbiter validates the patch via Cat-A A-021 (length, target
   persona, section marker present).
3. On reissue, the arbiter assembles the synthesizer's
   *effective* system prompt: persona's static
   `system_prompt.md` plus the patch applied per `patch_kind`.
   The patched prompt is what the synthesizer's CognitiveCore sees
   on iteration N+1.
4. The patch lives in the iteration's TASK_ASSIGN payload and the
   episode log. `system_prompt.md` on disk is **never modified**.
5. After the step transitions to COMPLETE (or hits the cap), the
   patch is discarded.

**Audit.** Every patch is recorded in the episode log. Cat-C
promotion can later observe "the same patch consistently improved
score" and propose a permanent edit to the persona's
`system_prompt.md` — but the edit is always a Compliance-screen
oversight item, never automatic.

**Defaults.** `enable_prompt_patches: false` everywhere by default.
The autoresearcher demo opts in only on the synthesize step. Other
demos and ad-hoc plans see no behavioural change.

### Short-term — intra-task iteration loop

Already described above. The critic's `NEEDS_REVISE` verdicts
cap at 3 iterations per run by default. Each iteration injects
the previous critique into the synthesizer's prompt; the
synthesizer is expected to address each point.

The critic uses a deterministic rubric (defined per-persona in
`eval_rubric.yaml`):
* `sourcing` 0.30 — every claim has a citation_tracker entry.
* `coverage` 0.25 — every section in the planner's outline is
  present.
* `red_hat_positioning` 0.20 — argument strength for the
  ACC-shaped opportunity.
* `internal_consistency` 0.15 — claims do not contradict each
  other.
* `security` 0.10 — no fabricated CVE references; sourcing
  matches what the URL actually says.

Threshold: `overall_score < 0.70` → `NEEDS_REVISE`. Below 0.40
→ `FAIL` (cluster step FAILED, plan terminal).

### Long-term — Cat-C rule promotion

Existing ACC-10 / ACC-12 mechanism. Every researcher emits an
EVAL_OUTCOME on every TASK_COMPLETE. The arbiter's episode-log
clustering promotes patterns that consistently score GOOD across
runs:

* "When the economist hits a +1 forecast bump, also auto-add an
  +1 sensitivity bump" — observed pattern → Cat-C rule.
* "Critic verdicts on a draft below 0.5 always include
  insufficient sourcing" → suggested rubric weight rebalance.
* "Strategist outputs that cite competitor.architecture.openness
  always score higher" → suggest the strategist persona prompt
  bake that explicitly.

Cat-C promotion is automatic — runs scoped to this demo populate
the same episode log every other ACC run does. Operators see the
promoted rules in the Compliance screen.

### What's NOT self-regulated (limits)

* The plan structure (six steps, named personas) is fixed per run.
  Promoting "we should have spawned a 7th critic for this run"
  would be a meta-cognitive ROADMAP item, not in this PR.
* The critic's rubric weights are static within a run. Cat-C may
  surface "the rubric wants rebalancing" but the rebalance is an
  operator decision.

---

## Example directory layout (the deliverable shell)

```
examples/acc_autoresearcher/
├── README.md                    — one-command flow + troubleshooting
├── .env.example                 — ACC_*_API_KEY + BRAVE_API_KEY + scenario knobs
├── plan.yaml                    — six-step DAG with the iteration loop
├── plan.json                    — JSON copy
├── task.md                      — operator-facing prose brief
├── expected_topology.md         — phase-by-phase reference for verify.sh
├── run.sh                       — sources .env, lints personas, brings stack up,
│                                  computes <topic-slug>-<date> + sets
│                                  ACC_RUN_OUTPUT_DIR before submitting plan
├── verify.sh                    — confirms ≥3 clusters + report file written
├── clean.sh                     — teardown + scratchpad eviction
└── (per-run artefacts land in repo-root  runs/<topic-slug>-<date>/)

runs/                            — gitignored output tree (top-level)
└── <topic-slug>-<YYYYMMDD>/
    ├── agentic_ai_strategy_report.md
    ├── .meta.json
    ├── citations/<sha8>.md
    └── traces/<persona>.log
```

`.env.example` keys (additions vs. coding example):

```bash
# Real-MCP profile (activates browser_harness + brave_search + fetch
# containers)
AUTORESEARCHER=true

# browser-harness needs an LLM endpoint to drive the browser
# (re-uses ACC_ANTHROPIC_API_KEY or ACC_OPENAI_API_KEY by default).
# Set BROWSER_HARNESS_HEADLESS=false locally to see the browser.
BROWSER_HARNESS_HEADLESS=true

# Brave Search MCP — fallback when browser-harness is overkill.
# Free tier 2k queries/month.
BRAVE_API_KEY=BSA-...

# Iteration loop knobs
ACC_RESEARCH_MAX_ITERATIONS=3        # operator hint: 3 default;
                                     # 5 if E5 quality experiment
                                     # shows monotonic gains (no
                                     # degradation observed).
ACC_RESEARCH_CRITIC_THRESHOLD=0.70

# Self-modifying-personas opt-in.  When unset, plan.yaml's
# enable_prompt_patches field still applies; this is just the
# global override for ad-hoc runs.
ACC_RESEARCH_ENABLE_PROMPT_PATCHES=true

# Output destination (run.sh computes <topic-slug>-<date>
# automatically and exports ACC_RUN_OUTPUT_DIR).
ACC_RUNS_ROOT=./runs
```

---

## Six-PR breakdown (proposed implementation order)

| PR | Branch | Scope | LOC budget |
|---|---|---|---|
| **E1** | `feat/eval-revise-iteration-loop` | REVISE verdict on EVAL_OUTCOME; `max_iterations` field; arbiter re-issue logic; **prompt_patch wire field + Cat-A A-021 + patched-prompt assembly** (gated behind plan-step `enable_prompt_patches`); A-020; tests | **~1100** |
| **E2** | `feat/mcp-research-tools` | Manifests + containers for `web_browser_harness` (browser-use), `web_search_brave`, `web_fetch` (with paywall detection wrapper); pre-existing acc-mcp-echo pattern as template; smoke tests against each | **~1200** |
| **E3** | `feat/research-stub-skills` | Six stub skill manifests + `StubResearchSkill` adapter; tests | ~500 |
| **E4** | `feat/research-personas` | Six research personas; tests | ~1500 |
| **E5** | `feat/example-acc-autoresearcher` | The example/ scenario + .env + run.sh (computes per-run output dir) / verify.sh / clean.sh + plan.yaml + `runs/` gitignore; **critic re-fetches cited URLs as part of its rubric** (≤200 LOC); **iteration-quality experiment** (E5 verification step runs the demo at max_iterations=3 and =5, compares scores, documents finding) | **~900** |
| **E6** | `docs/acc-autoresearcher` | New `docs/AUTORESEARCHER_*.md` reference + `docs/DEMO_TUI_autoresearcher.md` walkthrough; updates to ROADMAP | ~700 |

E1, E2, E3 land in parallel; E4 stacks on E3; E5 integrates all
four; E6 closes.

**Revised total budget: ~5900 LOC.** Heavier than coding-split
(~4600) primarily due to:
* browser-harness containerisation (Playwright + Chromium image)
* prompt-patch wiring + A-021 sanity rules
* citation re-verification logic in E5

---

## Verification plan

### Per-PR

| PR | Test |
|---|---|
| E1 | `pytest tests/test_iteration_loop.py` — synthetic NEEDS_REVISE → step re-runs with critique injected; iteration cap honoured; A-020 rejects overrun. |
| E2 | `pytest tests/test_mcp_web_search.py tests/test_mcp_web_fetch.py` — JSON-RPC handshake against the live containers (parallel acc-mcp-echo pattern); manifest validation. |
| E3 | `pytest tests/test_research_stub_skills.py` — six manifests load, adapter round-trips text. |
| E4 | `pytest tests/test_research_personas.py` — persona schema invariants (rubric weights sum 1.0, security ≥ 10%, default skills ⊆ allowed, default skills resolve in registry). |
| E5 | `bash examples/acc_autoresearcher/verify.sh` against acc1 — ≥3 distinct cluster_ids, output file present + > 5KB. |

### End-to-end (post-merge, on acc1)

```bash
cp examples/acc_autoresearcher/.env.example examples/acc_autoresearcher/.env
$EDITOR examples/acc_autoresearcher/.env       # set BRAVE_API_KEY + ACC_ANTHROPIC_API_KEY
./examples/acc_autoresearcher/run.sh
acc-tui                                          # press 7 — cluster panel shows 5+ clusters
./examples/acc_autoresearcher/verify.sh          # exit 0 + report > 5KB
cat examples/acc_autoresearcher/output/agentic_ai_strategy_report.md
```

---

## Open questions — resolution log + residuals

### Resolved in revision 2 (operator answers from `ACC Researcher.md`)

| # | Question | Decision |
|---|---|---|
| 1 | Web search backend | **browser-harness primary**; Brave Search as fallback; both ship |
| 2 | Iteration cap default | **3** default; E5 runs a quality experiment at 3 vs. 5 to see whether more iterations degrade output |
| 3 | Critic threshold | **0.70** confirmed |
| 4 | Output destination | **`runs/<topic>-<date>/` at repo root, gitignored** (deviation from operator's literal "roles/researcher-role/run-..." note flagged at the top of this doc — `roles/` is reserved for definitions) |
| 5 | Self-modifying personas | **In scope**, opt-in per plan-step via `enable_prompt_patches`. New wire field `prompt_patch` on EVAL_OUTCOME + Cat-A A-021. Synthesizer-only by default in this demo |
| 6 | Live LLM cost | **Acceptable** — document the per-run cost; defaults to Anthropic for quality |
| 7 | Critic re-fetches cited URLs | **In scope** — landed inside E5 (~200 LOC) as part of the critic's rubric |
| 8 | Paywall detection | **In scope** — landed inside E2 as a wrapper around the fetch MCP |

### Residual open questions (need decisions before E1 starts)

1. **`runs/` directory location.** Operator's note suggested
   `roles/researcher-role/run-[topic-date]`; this plan deviates to
   `runs/<topic-slug>-<date>/` at the repo root because `roles/`
   is reserved for role definitions consumed by `RoleLoader`. Is
   the deviation acceptable? If not, alternatives: (a) put runs
   under a top-level `output/` instead of `runs/`; (b) add a
   `RoleLoader.skip_dirs` config so `roles/researcher-role/` can
   coexist as a non-role directory.
   **Recommendation:** keep as `runs/<topic>-<date>/` —
   conventional, no loader changes needed.

2. **browser-harness Risk classification.** browser-harness is
   genuinely HIGH risk (executes JS in untrusted contexts) — but
   personas opt in via `max_mcp_risk_level: HIGH`. Should this
   default also gate behind a Cat-B setpoint
   (`max_browser_concurrency`) so a runaway researcher cluster
   can't open 50 browsers in parallel? **Recommendation:** yes,
   add `max_browser_concurrency: 3` to `_base/role.yaml`'s
   category_b_overrides and enforce in the harness MCP.

3. **iteration-quality experiment scope.** E5 verification is
   to run the demo at max_iterations=3 and at =5, compare
   `overall_score` trajectories, document whether higher caps
   *degrade* quality (the "is there degradation" operator
   question). This adds wall-clock + LLM cost (one extra run per
   E5 verification cycle). **Recommendation:** in scope — it's
   the empirical answer to the operator's residual concern, and
   the data informs the default for v2.

4. **prompt_patch persistence beyond a single run.** The plan
   says "patches discarded after step COMPLETE; never written
   back to disk". Cat-C *might* eventually surface "this patch
   consistently improved score" and propose a permanent edit.
   Should that proposal flow be in this PR series, or a
   follow-up? **Recommendation:** follow-up. The Cat-C
   suggestion mechanism can already write to the
   Compliance-screen oversight queue without modification; we
   don't need new wiring for it now.

5. **browser-harness container size.** The reference image
   (Chromium + headless harness + harness's LLM client) is on
   the order of 1.5-2 GB. That's a big chunk of the demo
   footprint. Should we ship a leaner alternative
   (Firefox-only, no LLM-driven flow) for operators who prefer
   smaller images? **Recommendation:** ship the reference image
   in E2; document the size; offer the lean variant as a
   future optimisation.

6. **Topic slug derivation.** `runs/<topic-slug>-<date>/` —
   how does the slug get computed? Three options: (a) operator
   passes via `--topic` to run.sh; (b) parsed from the plan's
   `task_description` first ~30 chars; (c) the planner persona
   emits a `topic_slug` field in its outline. **Recommendation:**
   (a) — explicit + reproducible; verify.sh asserts the
   directory name matches.

---

## Out of scope (deferred)

* **Self-modifying personas.** See open question #5.
* **Citation re-verification** in the iteration loop. See #7.
* **Multi-language sources.** First demo is English-only. Adding a
  per-source language tag + a translation skill is a follow-up.
* **arXiv / Google Scholar specialised MCPs.** Brave + fetch
  cover most public research material; deeper academic sourcing
  is a separate research-tools PR.
* **Slide / PDF deliverable.** The user explicitly said markdown
  only for this iteration.
* **Real-time / live-update reports.** Each run produces a
  static snapshot; "incremental updates as the world changes" is
  ROADMAP-territory.

---

## What this proves to a stakeholder

* ACC's clustering does *real research work*, not just cosmetic
  parallelism.
* The iteration loop pattern (Karpathy's contribution) ports
  cleanly to a multi-agent governance-first runtime.
* Operator stays in the loop the whole time — cluster panel shows
  every researcher, critic verdict appears in the transcript,
  `/cluster kill` works the same way it does in the coding demo.
* The Cat-C learning surface means runs *get better over time*
  — the same demo replayed in a month surfaces patterns that the
  first run revealed.
* Real MCPs are first-class. Web search + page fetch land as
  governed skills that respect Cat-A A-018 risk ceilings, get
  audit-logged by the registry, and show up in the Performance
  screen capability_stats.

---

## Decision request — revision 2

Operator-confirmed (revision 2 folded in):

* [x] Six-persona shape (planner / economist / competitor /
      strategist / synthesizer / critic).
* [x] **browser-harness** as primary research tool, Brave Search
      fallback.
* [x] `max_iterations: 3` default; **5 if quality experiment shows
      no degradation**; critic threshold 0.70.
* [x] Output to **`runs/<topic-slug>-<date>/`** (top-level,
      gitignored).
* [x] Cost profile acceptable (Anthropic default).
* [x] **Self-modifying personas opt-in** per plan-step
      (`enable_prompt_patches`). Cat-A A-021 enforces.
* [x] **Critic re-fetches cited URLs** (in E5).
* [x] **Paywall detection** in `web_fetch` (in E2).

Residuals — all confirmed, E1 can start:

* [x] **`runs/<topic>-<date>/` at repo root** confirmed.
* [x] **`max_browser_concurrency: 5`** — Cat-B setpoint added (operator
      bumped from the suggested 3).
* [x] **iteration-quality experiment in E5 verification** — confirmed.
* [x] **Topic-slug via `run.sh --topic <slug>`** — confirmed; TUI
      trigger added to ROADMAP for a future PR.
* [x] **browser-harness container size (~1.5-2 GB)** — confirmed.
* [x] **prompt_patch persistence to disk via Cat-C** — deferred per
      revision 2.

Additional operator decision folded in:

* **Cost configurability.** New knob: `ACC_RESEARCH_MAX_RUN_TOKENS`
  (default unset = no cap) + matching `max_run_tokens` field on the
  PLAN payload. Arbiter accumulates per-plan token usage from each
  TASK_COMPLETE's new `tokens_used` field; when the sum exceeds the
  cap, further reissues are refused, the plan transitions to FAILED
  with `block_reason: "max_run_tokens_exceeded"`, and an
  ALERT_ESCALATE fires for the operator.  Lands in E1 (+~150 LOC,
  E1 budget revised to ~1250).
