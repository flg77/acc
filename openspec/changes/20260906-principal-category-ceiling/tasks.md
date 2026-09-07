# 20260906-principal-category-ceiling — tasks

## 1. The ceiling (`acc/identity.py`)
- [x] `CEILING_RANK`, `TIER_CEILING`, `tier_ceiling()`, `narrower()`, `exceeds_ceiling()`
      (`UNACCEPTABLE` above every ceiling; unknown risk ranks LOW like the role guard)
- [x] `Principal.ceiling` + `effective_ceiling` (never above the tier's); in `as_dict()`
- [x] `Grant.ceiling` + `effective_ceiling`; `access.yaml` round trip; a hand-edited value
      above the tier's is narrowed with a warning (`load_grants`)
- [x] `admit(ceiling=)` refuses an unknown value and a value above the tier's ("only narrow")
- [x] `resolve_external` carries the grant's ceiling onto the principal
- [x] `ceiling_of(task_payload)` — explicit → tier fallback → "" for unattributed

## 2. On the task
- [x] `channel_access.Admission.task_attribution()` stamps `requester_ceiling` (journal too)
- [x] `compat_endpoint.Caller.attribution()` stamps `requester_tier` + `requester_ceiling`
- [x] plan steps dispatched by the executor inherit the submitting task's attribution
      (2026-09-07, priority list 12.1): `attribution.ATTRIBUTION_KEYS` + `inherit_attribution`;
      `PlanExecutor._publish_task_assign` carries the plan's seven keys onto every step,
      member and re-issue unless the step names its own requester; `plan submit` stamps
      the full set as surface `cli` (a pooled memory source); tests
      `tests/test_plan_step_attribution.py` (11): every key copied, own requester kept,
      unattributed plan unchanged on the wire, ceiling_of on s1/s2/re-issue, memory scope
      of a slack plan's step = the requester's group, of a CLI plan's = the `cli` pool

## 3. Enforcement
- [x] `capability_dispatch.dispatch_invocations(requester_ceiling=)` → `_dispatch_one`:
      refused before escalation and before the mode / category gate; no oversight row
- [x] `agent.py` passes `ceiling_of(task)` at the dispatch site
- [x] `cognitive_core`: an assistant proposal above the ceiling is dropped, with a line in
      the reasoning trace; the operator's unattributed prompt is unchanged

## 4. Operator surface
- [x] `acc-cli access admit --ceiling`, `list` (ceiling column), `check` and `whoami` show it

## 5. Tests (`tests/test_principal_ceiling.py`)
- [x] tier table; `exceeds_ceiling` matrix; `ceiling_of` (explicit / tier / wider-than-tier /
      unattributed); admit narrow ok, widen refused, unknown refused, hand-edited file
      narrowed; channel + compat attribution; dispatch: HIGH refused with no queue row
      while LOW runs; no ceiling = role only; refused before escalation (guard never
      consulted); core drops an INFUSE for a MEDIUM requester and executes it for the
      operator

## 6. Records
- [x] `docs/DECISIONS.md` D-014; `CHANGELOG.md`; `docs/CAPABILITIES.md` access row;
      `docs/MANUAL.md`; `docs/ARCHITECTURE.md`; memory change `[1b]` cross-reference
- [ ] memory change Phase 4+: retrieval / publication floor on the stamped ceiling
