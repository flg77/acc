# ACC Role Template — How to Create a Custom Role

This directory is a copy-and-customize starting point for defining your own ACC agent role.

---

## Quick Start

1. Copy this directory: `cp -r roles/TEMPLATE roles/my_role_name`
2. Fill in every field in `role.yaml` (see field reference below)
3. Set rubric criteria in `eval_rubric.yaml` — **weights must sum to exactly 1.0**
4. Write your role's system prompt in `system_prompt.md`
5. Load it: the `RoleLoader` discovers `roles/my_role_name/role.yaml` automatically

The `RoleLoader` deep-merges `roles/_base/role.yaml` → `roles/my_role_name/role.yaml`.
Your values win. Fields you omit fall back to `_base` defaults.

---

## Field Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `purpose` | str | **Yes** | One sentence — what this role exists to do |
| `persona` | Literal | **Yes** | Must be one of: `concise \| formal \| exploratory \| analytical` |
| `task_types` | list[str] | **Yes** | UPPER_SNAKE_CASE; 4–10 items recommended |
| `seed_context` | str | **Yes** | Output format instructions injected into system prompt |
| `allowed_actions` | list[str] | **Yes** | See valid actions list below |
| `category_b_overrides` | dict | No | `token_budget`, `rate_limit_rpm`, `max_task_duration_ms` |
| `version` | str | No | SemVer — start at "1.0.0"; bump on every change |
| `progress_reporting_interval_ms` | int | No | Default 30000 (30 s) |
| `queue_backpressure_threshold` | int | No | Default 10 |
| `queue_backpressure_resume_threshold` | int | No | Default 8 |
| `knowledge_tags` | list[str] | No | snake_case tags for KNOWLEDGE_SHARE signals |
| `eval_rubric_ref` | str | No | Always "eval_rubric.yaml" |
| `can_nominate_episodes` | bool | No | Default true |
| `can_spawn_sub_collective` | bool | No | Default false |
| `max_parallel_tasks` | int | No | Default 1 |
| `plan_participant` | bool | No | Default true |
| `domain_id` | str | No | Knowledge domain (see domain naming below) |
| `domain_receptors` | list[str] | No | Empty = universal (receives all domain signals) |

> **Do NOT set `eval_rubric_hash`** — it is auto-computed by `RoleLoader` from
> the SHA-256 of `eval_rubric.yaml`. Setting it manually will be overwritten.

---

## Valid `allowed_actions`

**Read operations:**
- `read_vector_db` — query LanceDB/Milvus episode store
- `read_working_memory` — read from Redis working memory
- `read_scratchpad` — read from PLAN scratchpad

**Write operations:**
- `write_working_memory` — write to Redis working memory
- `write_scratchpad` — write to PLAN scratchpad

**Query operations:**
- `search_episodes` — semantic search over episode store

**Publish operations:**
- `publish_task` — publish a TASK_ASSIGN signal
- `publish_eval_outcome` — publish EVAL_OUTCOME after task completion
- `publish_episode_nominate` — nominate high-quality episodes for ICL
- `publish_knowledge_share` — broadcast KNOWLEDGE_SHARE signals

**Forbidden for non-arbiter roles (do not include these):**
- `publish_plan` — arbiter only
- `publish_centroid_update` — arbiter only
- `countersign_role_update` — arbiter only
- `publish_role_approval` — arbiter only
- `emit_metrics` — arbiter only

---

## Persona Output Format Guide

| Persona | Standard `seed_context` output format |
|---------|--------------------------------------|
| `concise` | JSON: `{result, confidence, next_action}`. Max 3 sentences in prose fields. |
| `formal` | JSON: `{summary, details, recommendations, confidence}`. Use structured sections. |
| `analytical` | JSON: `{findings, analysis, confidence, evidence}`. Cite data points explicitly. |
| `exploratory` | JSON: `{concepts, draft, alternatives, confidence}`. Generate multiple options. |

---

## Domain Naming Conventions

Predefined enterprise domains (use these as `domain_id` and in `domain_receptors`):

| Domain ID | Typical roles |
|-----------|--------------|
| `sales_revenue` | Sales, RevOps |
| `marketing` | Content, Demand Gen, Product Marketing |
| `product_delivery` | PM, DevOps, Data Eng, ML Eng |
| `customer_success` | Support, CSM, Technical Support |
| `finance_accounting` | Financial Analyst, FP&A, Risk |
| `people_hr` | Recruiter, HRBP, L&D |
| `legal_compliance` | Contract, Compliance |
| `operations_strategy` | Ops, Procurement, PM, BA |
| `it_security` | IT Support, Security, IT Ops |
| `software_engineering` | Engineers (cross-cutting) |
| `data_analysis` | Analysts (cross-cutting) |
| `governance` | Arbiter, Observer |

You may define new domain IDs for custom knowledge domains. Use `snake_case`.

**`domain_receptors`:** A role with `domain_receptors: [sales_revenue]` silently drops
PARACRINE signals tagged with any other domain. Leave empty for a universal receptor
that responds to all domain signals.

---

## Minimal Role Example

```yaml
role_definition:
  purpose: "Classify inbound support tickets by category and urgency."
  persona: "concise"
  task_types:
    - TICKET_CLASSIFY
    - ESCALATION_ROUTE
  seed_context: >
    Output JSON: {category, urgency, next_action, confidence}.
    Urgency: CRITICAL | HIGH | MEDIUM | LOW.
    Max 2 sentences in next_action.
  allowed_actions:
    - read_working_memory
    - publish_task
    - publish_eval_outcome
  version: "1.0.0"
  domain_id: "customer_success"
```

## Full Enterprise Role Example

See `roles/account_executive/role.yaml` for a complete example with all fields set.

---

## Rubric Weight Constraint

Weights in `eval_rubric.yaml` **must sum exactly to 1.0**.

Verify with:
```python
import yaml, math
data = yaml.safe_load(open("eval_rubric.yaml"))
total = sum(c["weight"] for c in data["criteria"].values())
assert math.isclose(total, 1.0, abs_tol=0.001), f"Weights sum to {total}"
```

Scoring guide pattern per criterion: `1.0 = excellent, 0.5 = acceptable, 0.0 = failure`

---

## Constitutional Rules

1. **Purpose must be one sentence** — the LLM uses it verbatim in role identification
2. **Persona must be one of the 4 literal values** — validation will reject anything else
3. **No arbiter-only actions** — using forbidden actions will cause a Cat-A block
4. **Rubric weights sum to 1.0** — the RoleLoader logs a warning if they don't
5. **task_types in UPPER_SNAKE_CASE** — convention enforced by TASK_ASSIGN signal schema
6. **version starts at 1.0.0** — the RoleLoader hot-reloads on version change

---

## Verification

After creating your role:

```bash
python -c "
from acc.role_loader import RoleLoader
r = RoleLoader('roles', 'my_role_name').load()
assert r.purpose, 'missing purpose'
assert r.persona in ('concise','formal','exploratory','analytical'), 'bad persona'
print(f'OK: {r.domain_id} | tasks={len(r.task_types)} | rubric_hash={r.eval_rubric_hash[:16]}')
"
```
