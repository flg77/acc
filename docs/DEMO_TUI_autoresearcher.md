# TUI Demo Walkthrough — ACC Autoresearcher

**Audience:** anyone showing the autoresearcher demo (PRs #41-#46) to a
stakeholder. Reads top-to-bottom. Each step says **what to type**,
**what to look at**, and **what proves the feature works**.

**Pre-requisites:**

* `main` checked out — every PR in the autoresearcher series merged
  (#41-#46).
* `examples/acc_autoresearcher/.env` populated (copied from
  `.env.example` with at least the LLM creds + `BRAVE_API_KEY`).
* Nothing else — `run.sh` brings the stack up, lints personas, and
  submits the plan in one command.

---

## Demo overview (~10 minutes)

| Phase | Time | TUI screen | Demonstrates |
|---|---|---|---|
| 0. Stack health + research personas | 1m | Soma (1) + Ecosystem (6) | Stack up, six research personas registered, three real MCPs healthy. |
| 1. Cluster panel idle | 30s | Prompt (7) | `Clusters: 0` baseline. |
| 2. Submit the plan | 30s | (CLI side) | `run.sh --topic <slug>` orchestrates everything. |
| 3. Planner cluster | 1m | Prompt (7) | `c-plan…` 1 agent · `skill:plan_outline`. |
| 4. Economics + competitive in parallel | 2m | Prompt (7) + Comms (4) | Heuristic fan-out; `skill:market_sizer` / `skill:competitor_profile` / `mcp:web_browser_harness`. |
| 5. Strategy step | 1m | Prompt (7) | Fixed:1; `skill:report_drafter`. |
| 6. Synthesize ⇄ critique iteration loop | 2m | Prompt (7) + Comms (4) | `iteration_n` increments; `mcp:web_fetch.fetch` invocations from critic. |
| 7. Slash command intervention | 30s | Prompt (7) | `/cluster show` snapshot; `/cluster kill` available. |
| 8. Programmatic verification | 1m | (CLI side) | `verify.sh` two-layer check exits 0; report file inspection. |

---

## Phase 0 — stack health (`1` Soma + `6` Ecosystem)

Press **`1`**. Look at the **AGENTS** panel.

**What you should see**

* Six rows for the configured `research_*` agents in
  `state = ACTIVE`.
* `LLM` column populated.

Press **`6`** to switch to Ecosystem.

**What you should see**

* SKILLS table lists 13+ rows including the 6 new research stubs:
  `plan_outline`, `citation_tracker`, `market_sizer`,
  `competitor_profile`, `report_drafter`, `critic_verdict`.
* MCP SERVERS table lists 4+ rows including the three real research
  MCPs: `web_browser_harness` (HIGH), `web_search_brave` (MEDIUM),
  `web_fetch` (MEDIUM).
* Selecting a `research_*` role on the right-hand panel shows the
  HIGH-risk personas (planner, economist, competitor, strategist)
  declare `max_mcp_risk_level: HIGH`.

**Talk track**

> Six personas registered. The harness is HIGH risk because it
> drives a real browser through arbitrary content; the four
> personas that need it explicitly opt in via Cat-A. The
> synthesizer + critic stay MEDIUM — they consume + verify, they
> don't navigate.

---

## Phase 1 — cluster panel idle (`7` Prompt)

Press **`7`**. The cluster panel reads `Clusters: 0`. Empty
transcript with placeholder.

---

## Phase 2 — submit the plan (CLI side)

Drop to a shell:

```bash
cd /path/to/agentic-cell-corpus
cp examples/acc_autoresearcher/.env.example examples/acc_autoresearcher/.env
$EDITOR examples/acc_autoresearcher/.env       # set BRAVE_API_KEY etc.
./examples/acc_autoresearcher/run.sh --topic agentic-ai-strategy
```

`run.sh` will:
1. Source `.env`.
2. Compute `runs/agentic-ai-strategy-<YYYYMMDD>/` and export
   `ACC_RUN_OUTPUT_DIR`.
3. `acc-cli role lint roles/research_*/role.md` — fails fast on a
   schema regression.
4. `./acc-deploy.sh up` (TUI + AUTORESEARCHER profiles).
5. Patch the plan's `max_run_tokens` from
   `ACC_RESEARCH_MAX_RUN_TOKENS` if set.
6. `acc-cli plan submit examples/acc_autoresearcher/plan.yaml`.
7. Print follow-up commands.

Switch back to the TUI.

---

## Phase 3 — planner cluster (T+~5s)

The cluster panel updates. Expand it (chevron):

```
▼ Clusters: 1 (Σ 1 agents)
  c-plan1234 · research_planner · 1 agents · fixed strategy, count=1
    ● research_planner-aaa · skill:plan_outline · step 3/6 · running
```

**What this proves**

* `fixed: 1` estimator → 1-member cluster.
* `skill:plan_outline` is the live default skill — D4-style stub
  registered + persona's `default_skills` resolves.

**Talk track**

> Planner runs alone — the design contract can't fragment. The
> outline it publishes via KNOWLEDGE_SHARE is what every
> downstream researcher reads.

Press `4` (Comms). The PLAN DAG renders six rows; `plan` is
`RUNNING`. The knowledge_feed shows one entry: `tag=business_research,
type=research_outline`.

---

## Phase 4 — economics + competitive in parallel (T+~30s)

Back to `7`. The cluster panel:

```
▼ Clusters: 3 (Σ 4 agents)
  c-plan1234 · research_planner · 1 agents · fixed strategy, count=1
    ● research_planner-aaa · skill:plan_outline · step 6/6 · complete
  c-econ5678 · research_economist · 2 agents · 5200 tokens, +1 difficulty
    ● research_economist-bbb · skill:market_sizer · step 2/6 · running
    ● research_economist-ccc · skill:market_sizer · step 1/6 · running
  c-comp9012 · research_competitor · 2 agents · 4800 tokens, +1 difficulty
    ● research_competitor-ddd · skill:competitor_profile · step 2/6 · running
    ● research_competitor-eee · skill:competitor_profile · step 3/6 · running
```

**What this proves**

* Heuristic estimator — economist's `forecast → +1` and `edge → +1`
  bumps surfaced; competitor's `architecture → +1` and `edge → +1`
  bumps did the same.
* Both clusters truly parallel — no `depends_on` between them.

Press **`5`** (Performance). The capability_stats panel shows
climbing counts:

```
mcp:web_browser_harness.browse   12  ok=12  fail=0
mcp:web_search_brave.search       8  ok=8   fail=0
mcp:web_fetch.fetch              23  ok=22  fail=1   (paywalled retried)
skill:market_sizer                4  ok=4   fail=0
skill:competitor_profile          6  ok=6   fail=0
skill:citation_tracker           18  ok=18  fail=0
```

**What this proves**

* Real MCPs fire from inside the agent containers.
* `web_fetch.fetch`'s paywall detection counts paywalled responses
  as a fail (so the operator sees them in capability_stats) but
  doesn't fail the whole task.

**Talk track**

> Two researchers in parallel, each fanning out via heuristic
> estimator. The browser-harness is doing real navigation — that's
> a Playwright-driven browser inside the container reading
> Bedrock's docs and Agentspace's pricing pages right now.

---

## Phase 5 — strategy step (T+~120s)

Switch back to `7`. The economist + competitor clusters drop after
their 30s grace window:

```
▼ Clusters: 1 (Σ 1 agents)
  c-strat3456 · research_strategist · 1 agents · fixed strategy, count=1
    ● research_strategist-fff · skill:report_drafter · step 4/6 · running
```

`depends_on` honoured — strategist runs only after both predecessors
COMPLETE. Comms shows `step_progress: economics: COMPLETE,
competitive: COMPLETE, strategy: RUNNING`.

---

## Phase 6 — synthesize ⇄ critique iteration loop (T+~180s)

Synthesizer:

```
▼ Clusters: 1 (Σ 1 agents)
  c-syn7890 · research_synthesizer · 1 agents · fixed strategy, count=1
    ● research_synthesizer-ggg · skill:report_drafter · step 5/6 · running
                                                       (iteration 1/3)
```

After it completes, critic:

```
▼ Clusters: 1 (Σ 1 agents)
  c-crit1234 · research_critic · 1 agents · fixed strategy, count=1
    ● research_critic-hhh · skill:critic_verdict · step 4/6 · running
```

Performance shows critic's `mcp:web_fetch.fetch` invocations climb
— these are the citation re-fetches the verifier will
cross-reference.

**If the critic emits NEEDS_REVISE:**

```
▼ Clusters: 1 (Σ 1 agents)
  c-syn7890 · research_synthesizer · 1 agents · fixed strategy, count=1
    ● research_synthesizer-ggg · skill:report_drafter · step 1/6 · running
                                                       (iteration 2/3)
```

Same `cluster_id` — only the internal `task_id` changes. The
`iteration N/M` badge updates.

**What this proves**

* Iteration loop (E1) wired end-to-end: critic verdict reaches the
  arbiter, synthesize step re-runs with critique appended.
* Critic's `web_fetch` invocations land in the run's
  TASK_COMPLETE.invocations log so verify.sh can cross-reference.

**Talk track**

> Critic just spot-checked five citations — three verified
> against the URL they cite, one paywalled (and marked as such),
> one couldn't be re-fetched within the timeout. Score 0.62, below
> the 0.70 threshold, so NEEDS_REVISE. The synthesizer is
> rebuilding now with the critique appended to its prompt. Same
> cluster_id — the panel correctly shows iteration 2/3.

---

## Phase 7 — slash command intervention

In the prompt textarea:

```
/cluster show
```

System block appears in the transcript with the current snapshot:

```
[system] cluster c-syn7890 · research_synthesizer · 1 agents
[system]   research_synthesizer-ggg · skill:report_drafter · running
```

If you wanted to abort the synthesizer mid-iteration:

```
/cluster kill c-syn7890
```

(Operator-side cancel publish works today; agent-side cooperative
checkpoint is the documented short-term ROADMAP item.)

---

## Phase 8 — programmatic verification (CLI side)

Once the plan transitions terminal (typically T+~300s):

```bash
./examples/acc_autoresearcher/verify.sh
```

Two layers:

1. **Cluster topology** — confirms ≥ 5 distinct clusters were
   observed during the run window.
2. **Citation re-fetch coverage** — extracts inline citations from
   the report, cross-references with `mcp:web_fetch.fetch`
   invocations, exits non-zero when fewer than 30% were re-fetched.

```
▶ Run dir: runs/agentic-ai-strategy-20260508/
▶ Subscribing acc.sol-01.> for 300s...
Clusters observed:
  c-plan1234   members=1
  c-econ5678   members=2
  c-comp9012   members=2
  c-strat3456  members=1
  c-syn7890    members=1
  c-crit1234   members=1
Distinct clusters: 6 (min required: 5)
▶ Citation verification (threshold 0.30)...
{
  "citations": [...],
  "coverage_rate": 0.42,
  "threshold": 0.3,
  "ok": true
}
✓ Verify OK
  - cluster topology: 6 distinct clusters (min 5)
  - citation coverage: ≥ 0.30
```

Inspect the report:

```bash
cat runs/agentic-ai-strategy-20260508/agentic_ai_strategy_report.md | head -80
```

**What this proves**

* End-to-end run produces a structured deliverable.
* Citation discipline is enforced + auditable.
* Cluster panel + bus log agree on topology (verify.sh reads only
  the bus, not the TUI).

**Talk track**

> Reproducible verification. The cluster panel is rendering the
> same data the bus log carries — verify.sh confirms they match.
> If we ran this in CI nightly we'd have a regression watchdog
> for the entire research pipeline.

---

## Iteration-quality experiment

To answer "do more iterations actually improve quality?":

```bash
ACC_RESEARCH_MAX_ITERATIONS=3 ./examples/acc_autoresearcher/run.sh \
    --topic exp-iter-3
ACC_RESEARCH_MAX_ITERATIONS=5 ./examples/acc_autoresearcher/run.sh \
    --topic exp-iter-5

# Compare:
diff -u runs/exp-iter-3-*/agentic_ai_strategy_report.md \
        runs/exp-iter-5-*/agentic_ai_strategy_report.md | wc -l
grep '"verdict"' runs/exp-iter-{3,5}-*/.verification.json
```

If the 5-iteration run scores monotonically higher with more
verified citations, operators can safely bump the default. If it
drifts (more revisions, lower coverage), the 3-iteration default
holds.

---

## Known gaps to acknowledge during the demo

* **Agent-side TASK_CANCEL handler** — operator publishes today;
  cooperative checkpoint inside `CognitiveCore.process_task`
  follows (ROADMAP S1).
* **Real-LLM citation verification** — today's verifier confirms
  the audit trail (which URLs the critic re-fetched). An *active*
  fetch-and-compare-claim step is future hardening.
* **Cat-C promotion of useful prompt_patches** — patches live in
  the episode log; promoting them to permanent
  `system_prompt.md` edits flows through the Compliance-screen
  oversight queue (deferred).
* **Live cost tracking in the TUI** — `tokens_used` is on the wire
  + the cost cap fires on it; a per-plan cost panel in the TUI is
  a future enhancement.

---

## What to say if something goes wrong

| Symptom | Diagnosis | Backup talking point |
|---|---|---|
| `target_role unknown` on every TASK_ASSIGN | `roles/research_*/` not on agent host | Verify PR #44 (E4) is in main; `acc-cli role list` should show all six. |
| Cluster panel header stays at `Clusters: 0` | resolver wiring missing | PR #34 (D1) prerequisite; spot-check `acc/agent.py:266`. |
| browser-harness Cat-A blocks every invocation | Persona's `max_mcp_risk_level` ≠ HIGH | Mention the operator's deliberate opt-in: "this is the system telling us a HIGH-risk MCP can't be reached without explicit role authorisation". |
| Synthesize never re-runs after NEEDS_REVISE | E1 not landed or `max_iterations: 1` | Confirm `enable_prompt_patches: true` and `max_iterations >= 2` in plan.yaml. |
| `verify.sh` reports coverage 0 with citations populated | Critic isn't actually re-fetching | Open the critic's role.yaml; ensure `web_fetch` is in `default_mcps`. |
| BRAVE_API_KEY rejected | Key invalid / quota exhausted | Switch to browser-harness only by removing brave from `default_mcps` of researchers. |
