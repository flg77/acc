# Tasks — `20260531-role-perception-profiles`

## Phase 1 — schema + `workspace` profile

- [ ] `acc/config.py` — `PerceptionProfile` Literal type
      (8 values: `none / control / workspace / domain / reviewer
      / output / customer / queue`); `perception_profile` field
      on `RoleDefinitionConfig` with default `none`.
- [ ] `acc/perception.py` — refactor:
  - [ ] `_PROFILE_RENDERERS: dict[PerceptionProfile, Renderer]`
        registry.
  - [ ] Today's `render_currently_available_block` becomes the
        `control` profile renderer (registered under that key).
  - [ ] Implement `workspace` profile renderer (skills ∩ catalog,
        MCPs ∩ catalog, sibling workers in same cluster_id,
        workspace path).
  - [ ] `render_for_role(snapshot, role) -> str` dispatcher.
  - [ ] `snapshot_for_role(bus, cid, profile, role, ...)` —
        successor to `snapshot_for_assistant`; profile-aware
        source selection.  Backward-compat shim:
        `snapshot_for_assistant` calls
        `snapshot_for_role(..., profile="control")`.
  - [ ] `validate_marker(profile, snapshot, marker)` dispatcher —
        per-profile validator registry.
  - [ ] Per-profile token budget constants:
        `PROFILE_TOKEN_BUDGETS: dict[PerceptionProfile, int]`.
- [ ] `acc/cognitive_core.py` — flip the gate from
      `role_label == "assistant"` to
      `role.perception_profile != "none"`.  Replace
      `snapshot_for_assistant` call with `snapshot_for_role`.
      Replace `render_currently_available_block` with
      `render_for_role`.
- [ ] `roles/assistant/role.yaml` — add explicit
      `perception_profile: control`. Document that this preserves
      v0.3.43/4 behaviour byte-identically.
- [ ] `roles/coding_agent/role.yaml` — set
      `perception_profile: workspace`.
- [ ] `roles/ingester/role.yaml` — set
      `perception_profile: workspace`.
- [ ] `roles/analyst/role.yaml` — set
      `perception_profile: workspace`.
- [ ] Tests:
  - [ ] `tests/test_perception_profile_schema.py` — Pydantic
        validation: default is `none`; invalid string rejected;
        all 8 values accepted.
  - [ ] `tests/test_perception_profile_workspace.py` — workspace
        renderer:
        - Lists skills ∩ catalog with risk_level.
        - Lists MCPs ∩ catalog with risk_level.
        - Lists sibling workers in same cluster_id.
        - Surfaces workspace path from role.yaml.
        - Does NOT list role catalog (workers don't route).
        - Stays under 400-byte budget.
  - [ ] `tests/test_perception_profile_dispatch.py` —
        cognitive_core gate flip:
        - `none` profile → no perception call, no block.
        - `control` profile → today's behaviour preserved.
        - `workspace` profile → workspace renderer fires.
        - Invalid profile (caught at config load) → boot fails.
  - [ ] `tests/test_perception_marker_validation_workspace.py` —
        `[USE_SKILL:name:...]` and `[USE_MCP:name:...]`
        validation against role's `allowed_*` lists.

## Phase 2 — `domain` profile — DEFERRED

Gates on Phase 1 landed + at least one live trace showing a
domain specialist behaving better with the new profile.

- [ ] Implement `domain` profile renderer (sub-collectives
      matching role's `domain_id`, sibling specialists, MCPs
      with domain tags).
- [ ] `[DELEGATE:cid:reason]` validation against domain-matching
      sub-collectives.
- [ ] Flip `research_planner`, `clinical_reviewer`,
      `research_synthesizer`, `contract_analyst`,
      `financial_analyst`, `marketing_analyst`,
      `security_analyst` to `perception_profile: domain`.

## Phase 3 — `reviewer` + `output` profiles — DEFERRED

- [ ] `reviewer` profile: peer being reviewed + recent episode
      summary + sibling reviewers.
- [ ] `[CRITIQUE:peer_id:verdict]` validation.
- [ ] `output` profile: recent source artefacts + output schema
      + downstream consumers.
- [ ] Flip `research_critic`, `coding_agent_reviewer`,
      `reviewer` to `reviewer`; `synthesizer`,
      `research_synthesizer`, `content_marketer`,
      `product_marketer` to `output`.

## Phase 4 — `queue` profile + shared cache — DEFERRED

- [ ] `queue` profile: live `HumanOversightQueue` snapshot +
      Cat-C proposals + framework gap status.
- [ ] `[APPROVE:id]` / `[REJECT:id:reason]` validation.
- [ ] Push-based shared snapshot cache: arbiter publishes
      `ROSTER_SNAPSHOT` every 30s on
      `subject_roster_snapshot(cid)` (broadcast, not RPC);
      cognitive_cores maintain a per-collective shared local
      cache.  Per-task latency drops to zero.
- [ ] Flip `compliance_officer` to `queue`.

## Phase 5 — `customer` profile — DEFERRED

Gates on AoA-P5b TUI auth.

- [ ] `customer` profile: per-`operator_id` recent-message tail
      + escalation queue depth + product catalog + active
      sibling tickets.
- [ ] `[ESCALATE:tier:reason]` validation.
- [ ] Flip `customer_support_agent`,
      `customer_success_manager`, `account_executive`,
      `sales_engineer` to `customer`.

## Phase 6 — marker validation enforce mode — DEFERRED

- [ ] `ComplianceConfig.marker_validation_enforce: bool = False`.
- [ ] When True, validation failures block dispatch instead of
      log + drop.
- [ ] Generalise validators across `[ROUTE]`, `[DELEGATE]`,
      `[USE_*]`, `[CRITIQUE]`, `[ESCALATE]`, `[APPROVE]`,
      `[REJECT]`.

## Phase 7 — SIP perception-quality reward — DEFERRED

- [ ] Per-task: extract "did the LLM reference any perceived
      item?" signal (e.g. did its reasoning mention the running
      `coding_agent` it should have routed to?).
- [ ] Emit `PERCEPTION_ALIGNMENT` event on a new bus subject.
- [ ] SIP-P2 harness consumes as a new composite-reward kind.

## Follow-on proposals to spawn after Phase 1 lands

- [ ] `20260601-role-perception-profile-domain`
- [ ] `20260601-role-perception-profile-reviewer-output`
- [ ] `20260601-role-perception-profile-queue`
- [ ] `20260601-role-perception-profile-customer`
- [ ] `20260601-role-perception-marker-validation-enforce`
- [ ] `20260601-role-perception-sip-reward-signal`
- [ ] `20260601-role-perception-shared-cache`
