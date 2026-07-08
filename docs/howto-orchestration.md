# HOWTO — agentset orchestration shapes (the task-adaptive pattern selector)

> **What it is.** The assistant already decides *who* acts (route / hand-over /
> spawn / infuse). Orchestration adds *what shape* the work takes: one agent, a
> parallel fan-out, an N-verifier adversarial pass, a generate-&-filter panel, a
> tournament, or a loop-until-done sweep. `acc/orchestration/` names those six
> shapes and provides a pure `select_pattern()` heuristic; the assistant is taught
> to **name** the shape it picks in its reasoning.
>
> **Status: P0 (describe-only).** Nothing here dispatches — the arbiter still signs
> every `ROLE_ASSIGN`. P0 ships the vocabulary + selector + the assistant hint.
> The real multi-agent dispatch lands in P1–P4 (see the roadmap below).
>
> Design: *ACC Roadmap → Agentset orchestration patterns — the assistant as a
> task-adaptive pattern selector*; **ACC Implementation 053**.

## The six shapes

| Shape | Use when | Rough width |
|---|---|---|
| **single-agent** | one well-scoped step (default — don't fan out a one-liner) | 1 |
| **classify-and-act** | a mixed inbox — route each item to a specialist by type | 1 |
| **fan-out-and-synthesize** | many *homogeneous* items (files, tickets, endpoints), then combine | N items |
| **adversarial-verification** | high-consequence findings that must be trusted — N independent verifiers, majority-confirm | `verifier_n` |
| **generate-and-filter** | open-ended "give me options" — produce many, score, keep the best | a few |
| **tournament** | subjective "which is best" (naming, copy, plan) — head-to-head judging | a few |
| **loop-until-done** | unknown-size discovery ("find *all* …") — repeat + dedupe until a dry pass | 1 (iterated) |

## Enable it on a role

Set one flag in `role.yaml` (the Assistant ships with it on):

```yaml
role_definition:
  orchestration_hint: true
```

That appends a static **`## Orchestration shapes`** block to the role's system
prompt (part of the cacheable prefix — no per-task token cost). The role then
names the shape it chose in its reasoning trace. It emits **no new marker**;
it keeps using the normal `PROPOSE_*` markers to actually act.

## The selector (for developers)

`acc/orchestration/patterns.py` is pure and unit-tested:

```python
from acc.orchestration import PatternSignals, select_pattern

choice = select_pattern(PatternSignals(item_count=20, token_budget=100_000))
# choice.shape == Shape.FAN_OUT ; choice.est_agents == 8 ; choice.why == "…"
```

**Signals** are governance-sourced or task-derived (never model output):
`task_type`, `item_count`, `consequence` (`LOW|MEDIUM|HIGH`), `open_ended`,
`subjective`, `exhaustive`, `token_budget`.

**Precedence** (most-specific wins; default is always single-agent):
1. `consequence == HIGH` → **adversarial-verification** (a governance instrument —
   high-stakes findings must be independently confirmed).
2. `exhaustive` → **loop-until-done**.
3. `subjective` → **tournament**.
4. `open_ended` → **generate-and-filter**.
5. `item_count >= fan_out_min_items` → **fan-out-and-synthesize**.
6. `task_type ∈ {TRIAGE, DISPATCH, CLASSIFY, ROUTE}` → **classify-and-act**.
7. else → **single-agent**.

**Budget guard.** A shape is never proposed if the role's Cat-B `token_budget`
can't afford its fan-width (`est_agents × min_budget_per_agent`); it downgrades to
single-agent and records `downgraded_from`. Unknown budget (`0`) never blocks.

## Cat-B tuning (Q2)

The knobs are a `SelectorThresholds` (Cat-B tunable per collective; conservative
defaults so an unconfigured collective never over-orchestrates):

| Knob | Default | Meaning |
|---|---|---|
| `fan_out_min_items` | 3 | below this, a batch isn't worth fanning out |
| `verifier_n` | 3 | independent verifiers in an adversarial pass |
| `max_fan_width` | 8 | cap on parallel workers |
| `min_budget_per_agent` | 2048 | per-agent budget a shape needs to be affordable |

## Relationship to `/loop` (Q5 — no duplication)

**loop-until-done is NOT a new loop engine.** ACC already has `/loop <interval>
<prompt>` (`acc/slash_commands.py`, recurring time-interval dispatch) and the
Assistant's `_proactive_wakeup_loop` (`acc/agent.py`). When P1 implements
loop-until-done it reuses that dispatch machinery and only adds a **convergence
stop-condition** (dedupe-until-dry + `max_rounds`) — the difference from `/loop`
is *when to stop* (nothing-new vs. a fixed interval), not a second runner.

## Roadmap (what P0 sets up)

- **P0 (this) — done:** vocabulary + `select_pattern` + the describe-only hint.
- **P1:** N-independent-verifier majority (generalise the reviewer critic loop,
  diverse lenses) + loop-until-dry (convergence guard on the existing loop).
- **P2:** fan-out & synthesize over a worklist (worker pool → synthesizer),
  budget-metered.
- **P3:** generate-&-filter + tournament (the genuinely new shapes),
  oversight-visible.
- **P4:** learned selection — the reward harness (SIP) scores pattern outcomes and
  proposes Cat-C `task→pattern` rules (observe→propose→approve; never
  auto-enforce).

## References

- `acc/orchestration/patterns.py` (the selector) · `acc/orchestration/__init__.py`.
- `acc/config.py` (`orchestration_hint`) · `acc/cognitive_core.py`
  (`build_system_prompt` injection) · `roles/assistant/role.yaml`.
- Tests: `tests/orchestration/test_select_pattern.py`.
- Design: ACC Implementation **053**; ACC Roadmap orchestration report.
