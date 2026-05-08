# Task — Agentic AI as a strategic Red Hat opportunity

Natural-language version of the same brief encoded in `plan.yaml`.
Use this when running an ad-hoc operator submission via the prompt
pane (target role: `research_planner`) instead of the multi-step
plan.

---

## Context

We are participating in Red Hat's Technical Thought Leadership
Accelerator Program.  Challenge topic: **Agentic AI**.  Our
contribution: argue that **ACC is the vehicle to drive Red Hat's
agentic business from the edge to the datacenter**.

## Goal

Produce a structured, sourced markdown report covering:

1. **Executive Summary** — lead with the "why now" finding.
2. **Market Economics** — TAM/SAM/SOM for agentic AI 2025-2030.
3. **The Edge Market** — why it's the next 10× scale event.
4. **Competitive Landscape** — Bedrock Agents, Agentspace,
   Copilot Studio, watsonx Orchestrate, plus open-source runtimes
   (CrewAI, LangGraph, Letta).
5. **Architecture Analysis** — how the leading vendors are built
   internally; edge support strengths + gaps.
6. **Red Hat Positioning** — strengths, gaps, the ACC-shaped
   opportunity.
7. **Forecast Assumptions** — 3/5/10-year horizons with
   sensitivity bands.
8. **Citations** — every URL the agents fetched, with claim
   attribution.

## Quality bar

- Every numeric claim has a `citation_tracker` entry pointing at a
  primary source.
- Paywalled sources are marked, not silently cited.
- "Red Hat positioning" argues from facts the competitor cards
  established — no marketing voice.
- "Why now" framing is ≤ 3 concrete events (e.g. EU AI Act
  enforcement, hyperscaler pricing changes, edge GPU availability).
- The critic re-fetches a sample of cited URLs to verify the
  claim mapping is honest; verify.sh exits non-zero if fewer than
  30% of citations were re-fetched (`ACC_RESEARCH_MIN_VERIFIED_CITATIONS`).

## Out of scope

- Slide / PDF deliverable.  Markdown only.
- Live updates as the world changes.  Each run produces a static
  snapshot — re-run for fresh data.
- Multi-language sources.  English primary sources only.
- Specialist academic MCPs (arXiv, etc.).  Brave + browser-harness
  + fetch cover most public material.
