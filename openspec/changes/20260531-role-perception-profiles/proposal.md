# OpenSpec — Role perception profiles (generalised perceive→decide→act)

| Field | Value |
|---|---|
| Change ID | `20260531-role-perception-profiles` |
| Status | Proposed (Phase 1 ready to implement) |
| Builds on | v0.3.43 PR #9 — assistant-action-loop Phase 1 (the proof-of-concept) · v0.3.42 PR #8 — orchestrator CapabilityIndex (the source) |
| Companion | `20260531-assistant-action-loop` (the gateway) · `20260531-orchestrator-repurpose-skills-mcp-specialist` (catalog source) |
| Notes mirror | `Notes/.../ACC Openspec/20260531-role-perception-profiles — OpenSpec (proposed).md` |
| Brainstorm | `Notes/.../ACC-Role-Perception-Profiles/Role perception profiles — brainstorm.md` |

## Problem statement

v0.3.43 (PR #9) gave the Assistant gatekeeper a perception step:
before every LLM call, snapshot capability + roster + sub-
collectives and inject as a `## Currently available` block. Live
verified on lighthouse 2026-05-31 20:38 — same model, same prompt,
same AUTO mode, the Assistant stopped hallucinating role names
(`worker-pool` → `coding_agent_architect`) and started emitting
dispatchable markers that AoA-P2b auto-executed.

But the implementation is **assistant-only-gated**:

```python
# acc/cognitive_core.py:_process_task_body
if self._role_label == "assistant" and self._bus is not None:
    self._perception = await snapshot_for_assistant(...)
```

Every other spawnable role in `roles/` (52 today: `coding_agent`,
`ingester`, `analyst`, `research_planner`, `clinical_reviewer`,
`compliance_officer`, `coding_agent_architect`, …) still has only
static `seed_context`. A coding worker spawned via today's verified
`[PROPOSE_SPAWN:coding_agent_architect:...]` flow will boot,
receive its first task, and make the **exact same hallucination
mistakes** the Assistant did this morning — because nothing tells
it what's live.

This proposal generalises the v0.3.43 fix: typed per-role
`perception_profile` field on `RoleDefinitionConfig` so every role
opts into the live-state slice that fits its job, with per-profile
marker validation, all reusing the same v0.3.42 CapabilityIndex
+ v0.3.43 roster_snapshot + v0.3.43 `PerceptionSnapshot` primitives.

## Design decisions (bootstrap defaults from the brainstorm)

1. **Per-role profile approach (option C)** — typed Literal field,
   not a bool flag. Different jobs need different slices; one-
   shape-fits-all is wasteful.
2. **Seven standard profiles** —
   `none` / `control` / `workspace` / `domain` / `reviewer` /
   `output` / `customer` / `queue`. Operator-defined custom
   profiles deferred to Phase 6+.
3. **Default `none`** — every role opts in explicitly. Backward
   compat preserved byte-identically.
4. **Phase 1 scope** — schema + the `workspace` profile only.
   Smallest unit that proves the dispatch table works on a
   different role category than `control`.
5. **Marker validation default: log + drop** — same as v0.3.43.
   Phase 6 adds operator-gated enforce mode.
6. **Snapshot caching: per-collective shared (Phase 4)** — Phase
   1 uses per-task RPC. Push-based shared cache lands in Phase 4
   matching the assistant-action-loop's Phase 4.
7. **Profile composition: forbidden** — exactly one profile per
   role. Operators who need a hybrid request a new standard
   profile via a follow-up OpenSpec.

## Three invariants (table stakes — every phase)

1. **Same substrate, different rendering.** Every profile uses the
   same `snapshot_for_assistant`-style fan-out (renamed
   `snapshot_for_role` in Phase 1); only the rendering function
   differs. CapabilityIndex + roster_snapshot are shared.
2. **Per-profile token cap.** Each profile declares an expected
   token budget (workspace ~400B, control ~1KB, queue ~900B,
   etc.). Renderers stay under cap via filtering + truncation;
   over-cap emits a `PERCEPTION_OVERFLOW` log warning.
3. **Backward compatible by default.** `perception_profile: none`
   is the default; existing roles see zero behaviour change until
   their role.yaml flips. Migration is opt-in per role.

## Phase summary

| Phase | Status | Profile + scope |
|---|---|---|
| 1 | Proposed | Schema + `workspace` profile (coding_agent, ingester, analyst, 6+ workspace roles) |
| 2 | Deferred | `domain` profile (research_planner, clinical_reviewer, 8+ specialists) |
| 3 | Deferred | `reviewer` + `output` profiles |
| 4 | Deferred | `queue` profile (compliance_officer) + Phase-4-style snapshot cache |
| 5 | Deferred | `customer` profile (gates on AoA-P5b TUI auth) |
| 6 | Deferred | Marker validation generalisation across `[ROUTE]`, `[DELEGATE]`, `[USE_*]`, `[CRITIQUE]` |
| 7 | Deferred | SIP perception-quality reward signal |

## Phase 1 design (the `workspace` profile)

### Schema change

```python
# acc/config.py
PerceptionProfile = Literal[
    "none", "control", "workspace", "domain",
    "reviewer", "output", "customer", "queue",
]

class RoleDefinitionConfig(BaseModel):
    ...
    # v0.3.45 — proposal 20260531-role-perception-profiles Phase 1.
    # Selects which live-state slice gets injected into the system
    # prompt before each LLM call.  `none` (default) preserves v0.3.42
    # behaviour byte-identically.  Renderers live in acc/perception.py.
    perception_profile: PerceptionProfile = "none"
```

### Refactor `acc/perception.py`

- Today's `render_currently_available_block(snapshot)` becomes the
  `control` profile renderer.
- New `_PROFILE_RENDERERS: dict[PerceptionProfile, Renderer]`
  registry; renderers receive the snapshot + the role's
  `RoleDefinitionConfig` so they can read `allowed_skills`,
  `allowed_mcps`, `cluster_id`, etc.
- `render_for_role(snapshot, role) -> str` dispatches.
- Today's `snapshot_for_assistant` becomes `snapshot_for_role`
  with a `profile: PerceptionProfile` arg that drives which
  sources get queried (workspace skips capability_query if it
  doesn't need the role catalog).

### Workspace profile rendering

For `coding_agent` example:

```
## Currently available

**Your workspace:** /workspace (recreate-on-select via host)
**Your allowed skills** (from role.yaml ∩ live catalog):
- file_edit: Edit files in the workspace
- run_tests: Execute the project test suite
**Your allowed MCPs** (from role.yaml ∩ live registry):
- github (MEDIUM): GitHub operations (open PR, comment, etc.)
- filesystem (LOW): Read/write workspace files
**Sibling workers in cluster `backend`:**
- coding_agent → coding-1, coding-2
- coding_agent_tester → tester-1

You may invoke skills via `[USE_SKILL:name:json_args]` and MCPs via
`[USE_MCP:name:json_args]`.  Names not in the lists above will be
rejected at dispatch.
```

Token cost target: ~400 bytes.

### Cognitive_core integration

Replace the assistant-only gate:

```python
# acc/cognitive_core.py
if role.perception_profile != "none" and self._bus is not None:
    try:
        from acc.perception import snapshot_for_role
        self._perception = await snapshot_for_role(
            bus=self._bus,
            cid=self._collective_id,
            sub_collectives=self._sub_collectives,
            profile=role.perception_profile,
            role=role,
        )
    except Exception:
        logger.debug("perception snapshot failed", exc_info=True)
        self._perception = None
```

`build_system_prompt` calls `render_for_role(perception, role)`
instead of `render_currently_available_block(perception)`.

### Per-profile marker validation

`validate_marker_target` becomes `validate_marker(profile, snapshot, marker)`:

- `control` profile: today's role-roster/catalog check.
- `workspace` profile: `[USE_SKILL:name:...]` → name in
  `role.allowed_skills`; `[USE_MCP:name:...]` → name in
  `role.allowed_mcps`. Already enforced by guardrails — this
  validation just makes the rejection observable in perception
  WARNINGs.

### Phase 1 deliverables

- `acc/config.py` — `PerceptionProfile` Literal + field on
  `RoleDefinitionConfig`.
- `acc/perception.py` — refactor to profile registry; `workspace`
  renderer; `snapshot_for_role` + `render_for_role`.
- `acc/cognitive_core.py` — gate flip from `role_label == "assistant"`
  to `role.perception_profile != "none"`.
- `roles/coding_agent/role.yaml`,
  `roles/ingester/role.yaml`,
  `roles/analyst/role.yaml` — flip to
  `perception_profile: workspace`.
- `roles/assistant/role.yaml` — gain explicit
  `perception_profile: control` (keeps current behaviour; makes
  the contract explicit + survives the gate flip).
- Tests:
  - `tests/test_perception_profile_schema.py` — Pydantic
    validation; default is `none`.
  - `tests/test_perception_profile_workspace.py` — workspace
    renderer shape; marker validation for `[USE_SKILL]` and
    `[USE_MCP]`.
  - `tests/test_perception_profile_dispatch.py` — cognitive_core
    gate flip honours all 8 profile values.

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Token cost ×N for N spawned roles | Per-profile token cap (declared in profile metadata); embedding-similarity filtering; truncation with "and X more" line |
| Phase 1 breaks roles whose role.yaml has typos in `perception_profile` | Pydantic `Literal` rejects invalid values at load — agent fails fast at boot, not silently mid-task |
| Latency stacks across many spawned roles | Phase 4 ships push-based shared snapshot cache (per-collective broadcast every 30s) |
| Custom profile sprawl | Schema constrains to the seven standard values; custom profiles require a follow-on OpenSpec adding to the Literal |
| Catalog source drift (orchestrator vs filesystem fallback) | Inherited from v0.3.43 — already handles stale flag + fallback |
| Marker validation false-rejects | Phase 1 ships log + drop (today's v0.3.43 behaviour); Phase 6 adds operator-gated enforce mode |
| Sub-collective profile selection mismatch | Domain profile (Phase 2) filters sub-collectives by role's `domain_id` ∩ sub-collective `domain` |
| Per-operator scope leakage (post-AoA-P5b) | Customer profile (Phase 5) is the per-operator-scoped profile; lands when TUI auth lands |
| Token budget violation across phases | Cat-B `token_budget` per role.yaml — operators audit + tune; v0.3.44 set the precedent |

## Follow-on proposals (spawn after Phase 1 lands)

- `20260601-role-perception-profile-domain` (Phase 2)
- `20260601-role-perception-profile-reviewer-output` (Phase 3)
- `20260601-role-perception-profile-queue` (Phase 4)
- `20260601-role-perception-profile-customer` (Phase 5)
- `20260601-role-perception-marker-validation-enforce` (Phase 6)
- `20260601-role-perception-sip-reward-signal` (Phase 7)
- `20260601-role-perception-shared-cache` (Phase 4 sub-proposal —
  push-based snapshot)

## Linked

- **Proof-of-concept (lighthouse-verified 2026-05-31 20:38):**
  `20260531-assistant-action-loop` Phase 1 — PR #9, v0.3.43.
- **Catalog source:**
  `20260531-orchestrator-repurpose-skills-mcp-specialist`
  Phase 1 — PR #8, v0.3.42.
- **Roster source:** arbiter heartbeat ring (PR-J / J-2) +
  `subject_roster_snapshot` (v0.3.43).
- **Sub-collectives:** AoA-P3a (v0.3.29).
- **Oversight queue (queue profile):** PR-Z1c.
- **SIP integration (Phase 7):**
  `20260530-acc-self-improvement-policy-gradient`.
- **Dreamer integration (M11 cross-role pattern detection):**
  `20260530-acc-dreaming-agent`.
- **Role-package format:** `accpkg.yaml` will carry
  `perception_profile` as a manifest field
  (`20260531-acc-role-package-format`).
