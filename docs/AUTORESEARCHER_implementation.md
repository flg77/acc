# Autoresearcher Demo — Implementation Reference

**Status:** PRs #41-#46 merged on `main`. Local test totals: 186 passing
across all related modules (see "Test matrix" below).

This is the PR-by-PR reference for the multi-agent business-research
showcase that adopts Karpathy's `autoresearch` iterate→evaluate→keep
loop pattern. Read alongside
[`docs/examples/acc-autoresearcher-example.md`](examples/acc-autoresearcher-example.md)
for design rationale; this file is the **reference for what shipped**.

> **PR numbering used in this document mirrors the GitHub PRs:**
>
> | Plan PR | GitHub PR | Branch | Stack base |
> |---|---|---|---|
> | E1 | [#41](https://github.com/flg77/acc/pull/41) | `feat/eval-revise-iteration-loop` | `main` |
> | E2 | [#42](https://github.com/flg77/acc/pull/42) | `feat/mcp-research-tools` | `main` (independent) |
> | E3 | [#43](https://github.com/flg77/acc/pull/43) | `feat/research-stub-skills` | `main` (independent) |
> | E4 | [#44](https://github.com/flg77/acc/pull/44) | `feat/research-personas` | E3 |
> | E5 | [#45](https://github.com/flg77/acc/pull/45) | `feat/example-acc-autoresearcher` | all of the above |
> | E6 | [#46](https://github.com/flg77/acc/pull/46) | `docs/acc-autoresearcher` | E5 |

---

## What we built

ACC's existing PLAN signal (PR #27) and cluster fan-out (PRs #26-#30)
already let the arbiter dispatch parallel sub-agents per step. The
autoresearcher demo extends this with three runtime additions and one
operator-facing scenario:

1. **Iteration loop** — a critic persona reviews each synthesized
   draft; on `NEEDS_REVISE` the arbiter re-issues the synthesize step
   with the critique appended. Capped at `max_iterations` per PLAN
   step.
2. **Self-modifying personas** (opt-in) — the critic may emit a
   structured `prompt_patch` field on EVAL_OUTCOME; Cat-A A-021
   sanity rules apply.
3. **Cost cap** — `max_run_tokens` accumulates per-plan across
   TASK_COMPLETE; over-budget transitions step to FAILED + alerts.
4. **Real research** — three new MCP servers (`web_browser_harness`,
   `web_search_brave`, `web_fetch`) ship as containers; six research
   personas reference them under Cat-A A-018 risk ceilings.

---

## High-level architecture

```
operator submits plan
       │
       ▼
┌────────────────────────────────────────────┐
│ research_planner (1)                        │
│  → KNOWLEDGE_SHARE outline                  │
└──────────────┬──────────────────────────────┘
               │
   ┌───────────┴───────────┐
   ▼                       ▼
┌──────────────┐    ┌──────────────┐    (fan out per heuristic)
│ economist ≤3 │    │ competitor ≤3│
│ web_*_harness│    │ web_*_harness│
│ market_sizer │    │ competitor_  │
│ citation_*   │    │ profile      │
└──────┬───────┘    └──────┬───────┘
       └────────┬──────────┘
                ▼
       ┌────────────────┐
       │ strategist (1) │
       │ report_drafter │
       └────────┬───────┘
                ▼
   ┌────────────────────┐
   │ synthesizer (1)    │ ◀──── re-issue on NEEDS_REVISE
   │ enable_prompt_     │       (E1 iteration loop)
   │ patches: true      │
   └────────┬───────────┘
            ▼
   ┌────────────────────┐
   │ critic (1)         │
   │ critic_verdict     │
   │ web_fetch (verify) │
   └────────┬───────────┘
            │
            ├─ verdict PASS → step COMPLETE
            └─ verdict NEEDS_REVISE → re-issue synthesize
                                        with critique injected
```

Aggregation, cluster_id propagation, and the cluster panel all carry
through unchanged from the coding-cluster series (PRs #26-#30).

---

## E1 — Iteration loop + cost cap + prompt patches

**Files added/modified:** `acc/signals.py`, `acc/plan.py`,
`regulatory_layer/category_a/constitutional_rhoai.rego`,
`tests/test_iteration_loop.py`.

### EVAL_OUTCOME verdict expansion

A fourth verdict on top of `GOOD` / `PARTIAL` / `BAD`:

```python
EVAL_VERDICT_NEEDS_REVISE = "NEEDS_REVISE"
EVAL_VERDICTS = frozenset({EVAL_VERDICT_GOOD, EVAL_VERDICT_PARTIAL,
                           EVAL_VERDICT_NEEDS_REVISE, EVAL_VERDICT_BAD})
```

When the agent's CognitiveCore lifts a critic verdict onto
TASK_COMPLETE, it does so via the new `eval_outcome` payload field:

```json
{
  "signal_type": "TASK_COMPLETE",
  "task_id": "...",
  "tokens_used": 4250,
  "eval_outcome": {
    "verdict": "NEEDS_REVISE",
    "critique": "Missing 2025 GA dates; SAM-vs-TAM ratio incoherent",
    "prompt_patch": {
      "patch_kind": "append",
      "text": "Always cite primary sources first.",
      "target_persona": "research_synthesizer"
    }
  }
}
```

### `_Step` + `_Plan` state additions

```python
@dataclass
class _Step:
    # ... existing fields ...
    max_iterations: int = 1
    iteration_n: int = 0
    original_task_description: str = ""
    enable_prompt_patches: bool = False
    prompt_patches_writable_to: list[str] = []
    last_critique: str = ""
    last_prompt_patch: dict = {}

@dataclass
class _Plan:
    # ...
    max_run_tokens: int = 0
    tokens_used: int = 0
    cost_cap_breached: bool = False
    cost_cap_reason: str = ""
```

### Arbiter re-issue logic

`PlanExecutor._maybe_reissue_for_revise` returns one of three states:

| State | Caller behaviour |
|---|---|
| `"reissued"` | Step stays RUNNING with new task_id; broadcast skipped |
| `"transitioned"` | Helper set status (cost cap → FAILED); fall through to broadcast |
| `"no_action"` | Caller proceeds with COMPLETE transition |

Critique is appended to the **original** task description (no
compounding drift across iterations). Cluster-aggregation steps are
explicitly excluded — the iteration loop is for single-step revisions.

### Cat-A guards (constitutional 0.5.0 → 0.6.0)

* **A-020** `deny_iteration_overrun` — re-issue past `max_iterations`.
* **A-021** three rules:
  - `deny_oversize_prompt_patch` (text > 2000 chars by default).
  - `deny_cross_persona_patch` (target_persona must equal step.role
    or appear in `prompt_patches_writable_to`).
  - `deny_unbacked_section_replace` (replace_section needs a real
    section_marker present in the persona's `system_prompt.md`).

### Wire payload changes

Every TASK_ASSIGN (legacy + clustered) now carries `iteration_n` and
`max_iterations` so downstream telemetry can render a consistent
"iteration N/M" badge. Re-issued payloads also carry the appended
critique in `task_description` and (when honoured) the validated
`prompt_patch` dict.

### Tests — 15 cases

Pin every state transition: back-compat (no `eval_outcome` →
unchanged), critique injection, no compounding across iterations,
iteration cap collapses to COMPLETE (not FAILED), cost cap refuses
reissue + emits ALERT_ESCALATE, prompt patches gated by opt-in,
A-021 sanity rules drop bad patches (peer-persona / oversize /
unknown kind / unbacked section), `prompt_patches_writable_to`
whitelist allows cross-persona patches when explicitly opted in,
cluster-aggregation regression guard.

---

## E2 — Real research MCP servers

**Files added/modified:** `mcps/web_browser_harness/`,
`mcps/web_search_brave/`, `mcps/web_fetch/`,
`container/production/podman-compose.yml`,
`container/production/Containerfile.web_*`,
`tests/test_mcp_web_*.py`.

Three real MCP server containers ship under a new `acc-autoresearcher`
compose profile activated by `AUTORESEARCHER=true`:

| Manifest | Risk | Source | Notes |
|---|---|---|---|
| `web_browser_harness` | **HIGH** | [browser-use/browser-harness](https://github.com/browser-use/browser-harness) — Playwright + LLM-driven browser | Personas declare `max_mcp_risk_level: HIGH` to reach it. Cat-B `max_browser_concurrency: 5` (operator decision). |
| `web_search_brave` | MEDIUM | [modelcontextprotocol/servers/brave-search](https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search) | Free tier 2k/month; needs `BRAVE_API_KEY`. |
| `web_fetch` | MEDIUM | [modelcontextprotocol/servers/fetch](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch), wrapped with **paywall detection** | Surfaces `paywalled: true` when 401/402 / paywall content observed; citation_tracker can flag accordingly. |

### Risk classification rationale

`web_browser_harness` is HIGH because the harness drives a real
browser through arbitrary operator-untrusted pages — phishing flows,
JS-based fingerprinting, social-engineering attempts. The operator
opts in via the role.md authoring step (E4); Cat-A A-018 gates
per-invocation as usual. Without explicit `max_mcp_risk_level: HIGH`,
researcher personas would not be able to reach the harness at all.

---

## E3 — Six stub research skills

**Files added:** `skills/{plan_outline,citation_tracker,market_sizer,
competitor_profile,report_drafter,critic_verdict}/`,
`tests/test_research_stub_skills.py`.

Same pattern as the D4 coding-stubs: LOW-risk pass-through manifests
backed by a shared `StubResearchSkill` adapter. Each skill is a
governance + audit anchor for an LLM-emitted output category; the
LLM does the actual reasoning.

| Skill | Domain | Used by | Output shape |
|---|---|---|---|
| `plan_outline` | business_research | planner | JSON outline (sections, questions per section) |
| `citation_tracker` | business_research | every researcher + synthesizer | `[{url, claim, confidence}]` |
| `market_sizer` | economic_analysis | economist | `{tam, sam, som, year, source_urls}` |
| `competitor_profile` | competitive_analysis | competitor + strategist | JSON vendor card |
| `report_drafter` | business_research | synthesizer + strategist | Markdown section body |
| `critic_verdict` | business_research | critic | `{verdict, score, criteria_scores, critique, prompt_patch?}` |

Domain ids align with the receptor model documented in
`docs/SUBAGENT_COMMUNICATION.md` so paracrine signals (KNOWLEDGE_SHARE)
reach the right consumers.

### Tests — 11 cases

All six manifests load; LOW-risk; per-skill_id audit attribution;
per-skill module isolation; domain-id alignment; coexistence with
the D4 coding-skill family.

---

## E4 — Six research personas

**Files added:** `roles/research_{planner,economist,competitor,
strategist,synthesizer,critic}/role.md|role.yaml|system_prompt.md|
eval_rubric.yaml`, `tests/test_research_personas.py`.

Each persona is a *narrowed* research role with distinct system
prompt, default skill set, MCP set, estimator config, and eval
rubric.

| Persona | Estimator | max_par | Default skills | Default MCPs | MCP risk |
|---|---|---|---|---|---|
| `research_planner` | fixed:1 | 1 | plan_outline | web_search_brave | HIGH |
| `research_economist` | heuristic | 3 | market_sizer, citation_tracker | web_browser_harness, web_fetch | HIGH |
| `research_competitor` | heuristic | 3 | competitor_profile, citation_tracker | web_browser_harness, web_fetch | HIGH |
| `research_strategist` | fixed:1 | 1 | citation_tracker, report_drafter | web_browser_harness | HIGH |
| `research_synthesizer` | fixed:1 | 1 | report_drafter, citation_tracker | web_fetch | MEDIUM |
| `research_critic` | fixed:1 | 1 | critic_verdict | web_fetch | MEDIUM |

### Difficulty bumps (heuristic personas)

* `research_economist`: `forecast → +1`, `edge → +1`
* `research_competitor`: `architecture → +1`, `edge → +1`

### Receptor alignment

* All six listen to `business_research`.
* `research_economist` adds `economic_analysis`.
* `research_competitor` adds `competitive_analysis`.
* `research_strategist` adds `strategic_analysis` +
  `competitive_analysis`.

### Eval rubric weights — sum 1.0 + security ≥ 10%

| Persona | Heaviest weight | Security |
|---|---|---|
| planner | outline_completeness 0.35 | 0.10 |
| economist | factual_accuracy 0.30 | 0.10 |
| competitor | vendor_coverage 0.30 | 0.10 |
| strategist | argument_strength 0.30 | 0.15 |
| synthesizer | section_coverage 0.30 | 0.15 |
| critic | finding_accuracy 0.35 | 0.10 |

### Tests — 33 cases

Schema invariants per persona × 5 invariants + 5 cross-persona checks
including HIGH-risk opt-in for browser-harness users + MEDIUM-only
for synthesizer/critic.

---

## E5 — Runnable scenario + citation re-verification

**Files added:** `acc/research/citation_verifier.py`,
`acc/research/__init__.py`, `examples/acc_autoresearcher/*` (10 files),
`tests/test_citation_verifier.py`.

### Citation re-verification — `acc/research/citation_verifier.py`

Read-only post-run analysis: parses inline citations from the
synthesizer's report, cross-references with `mcp:web_fetch.fetch`
invocations from the run's TASK_COMPLETE payloads, computes
coverage_rate.

```python
from acc.research import (
    extract_inline_citations,
    verify_against_invocations,
    summarise,
)

citations = extract_inline_citations(report_md)
report = verify_against_invocations(report_md, run_invocations)
summarise(report, threshold=0.30)
# report.ok is True iff coverage_rate >= 0.30
```

Tolerates: paywalled markers (leading/trailing), JSON-string args,
malformed shapes, missing args. **Does not re-fetch URLs itself**
(that would double demo cost) — the critic persona's runtime
re-fetch is the source of truth; this module confirms the audit
trail.

### Example scenario directory

```
examples/acc_autoresearcher/
├── README.md            — one-command flow + iteration-quality experiment
├── .env.example         — every operator-configurable variable
├── plan.yaml + plan.json — 6-step DAG with iteration loop opt-in
├── task.md              — operator-facing prose brief
├── expected_topology.md — phase-by-phase reference + anti-checks
├── run.sh               — --topic <slug> + --watch flags
├── verify.sh            — two-layer post-run check
└── clean.sh             — teardown + --purge-runs option
```

`run.sh` orchestrates: source `.env`, compute
`runs/<topic-slug>-<date>/`, lint personas, bring stack up, patch
`max_run_tokens` from env, submit plan.

`verify.sh` orchestrates: subscribe to bus for window, parse
cluster_id + agent_id, build invocations array via embedded Python
helper, run citation_verifier, exit 0 only when both layers pass.

### Tests — 18 cases

Citations extraction (no section, basic, paywalled markers,
trailing punctuation, H3 heading, bounds at next section,
deduplication); cross-reference (refetched marking, repeat counting,
non-fetch ignored, JSON-string args, malformed shapes); summarise
(default 0.30 threshold, passes when above, no-citations is
not-ok, threshold=0.0 disables, to_dict round-trip).

---

## E6 — This document set

**Files added/modified:** `docs/AUTORESEARCHER_implementation.md`
(this file), `docs/AUTORESEARCHER_index.md`,
`docs/DEMO_TUI_autoresearcher.md`, plus ROADMAP additions.

Closes the series with operator-facing reference + walkthrough
documentation. No code changes.

---

## File map

| Module | Role | Introduced in |
|---|---|---|
| `acc/research/citation_verifier.py` | Post-run citation coverage analysis | E5 |
| `acc/plan.py` | Iteration loop + cost cap + prompt patches | E1 |
| `acc/signals.py` | NEEDS_REVISE verdict + EVAL_VERDICTS frozenset | E1 |
| `mcps/web_browser_harness/` | Browser-harness MCP manifest | E2 |
| `mcps/web_search_brave/` | Brave Search MCP manifest | E2 |
| `mcps/web_fetch/` | Fetch MCP manifest with paywall wrapper | E2 |
| `container/production/Containerfile.web_*` | Three new container images | E2 |
| `skills/{plan_outline,citation_tracker,market_sizer,competitor_profile,report_drafter,critic_verdict}/` | Six stub research skills | E3 |
| `roles/research_{planner,economist,competitor,strategist,synthesizer,critic}/` | Six research personas | E4 |
| `examples/acc_autoresearcher/` | Runnable scenario | E5 |
| `regulatory_layer/category_a/constitutional_rhoai.rego` | A-020, A-021 rules | E1 |

Test modules added: `tests/test_iteration_loop.py` (15),
`tests/test_mcp_web_browser_harness.py` + `test_mcp_web_fetch.py` +
`test_mcp_web_search_brave.py` (~21 combined),
`tests/test_research_stub_skills.py` (11),
`tests/test_research_personas.py` (33),
`tests/test_citation_verifier.py` (18). Total **~98 new tests**.

---

## Test matrix (local, Python 3.14, no acc1)

| Suite | Result |
|---|---|
| `tests/test_iteration_loop.py` | 15/15 |
| `tests/test_mcp_web_*.py` | 21/21 |
| `tests/test_research_stub_skills.py` | 11/11 |
| `tests/test_research_personas.py` | 63/63 (parametrised) |
| `tests/test_citation_verifier.py` | 18/18 |
| Wider sweep across PR-26..30 + D-series + E-series | **186/186** |

---

## Wire-protocol cheat sheet

### TASK_COMPLETE may carry

```json
{
  "signal_type": "TASK_COMPLETE",
  "task_id": "...",
  "tokens_used": 4250,
  "eval_outcome": {
    "verdict": "NEEDS_REVISE",
    "critique": "...",
    "prompt_patch": {
      "patch_kind": "append",
      "text": "...",
      "target_persona": "research_synthesizer"
    }
  }
}
```

### TASK_ASSIGN gains (legacy + cluster paths)

```json
{
  "signal_type": "TASK_ASSIGN",
  "iteration_n": 0,
  "max_iterations": 1,
  "task_description": "...",
  "prompt_patch": {...}        // only when iteration_n > 0 + opt-in
}
```

### PLAN payload may carry

```yaml
max_run_tokens: 250000
steps:
  - step_id: synthesize
    role: research_synthesizer
    max_iterations: 3
    enable_prompt_patches: true
    prompt_patches_writable_to: [research_synthesizer]
```

All optional. Legacy publishers ignored.

---

## Known gaps + roadmap

1. **Agent-side TASK_CANCEL handler** (still ROADMAP S1 — operator
   publishes; cooperative checkpoint inside
   `CognitiveCore.process_task` follows).
2. **Cat-C promotion of useful prompt_patches to disk** — patches
   live in episode log; promotion to permanent system_prompt.md
   edits is a separate Compliance-screen oversight flow (deferred).
3. **Mixed-role clusters** (ROADMAP M1) — would let one cluster
   hold an economist + a critic simultaneously.
4. **Real LLM-driven citation re-verification** — today's verifier
   confirms the audit trail; an active "fetch and compare claim"
   step is a future hardening track.
5. **Iteration-quality experiment as automated CI run** —
   instructions in the README; not yet automated.
