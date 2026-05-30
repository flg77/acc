# Tasks — `20260530-assistant-agent-of-agents`

All tasks land per [[20260530-aoa-and-self-improve — integration & sequencing]].

## Phase 1 — default target + Knative dormant-watcher (LANDED v0.3.24)

- [x] Default `target_role = assistant` on the Prompt screen
      (`acc/tui/screens/prompt.py`).
- [x] `StressIndicators.dormant: bool` + `dormant_at_ts: float`.
      Carried on every HEARTBEAT.
- [x] `is_wake_trigger(payload, target_role)` activator decision tree
      in `acc/cognitive_core.py`.
- [x] Agent task loop: when role is Assistant + dormant + no wake
      trigger, drop the task; else flip flag and proceed.
- [x] `build_catchup_trace(dormant_at_ts, now_ts, …)` — prepended to
      the first reasoning trace after wake.
- [x] `/sleep` / `/wake` slash commands + `Ctrl+Z` chord
      (`acc/slash_commands.py`).
- [x] Redis persistence at `acc:{cid}:{agent_id}:dormant`;
      `_restore_dormancy_state` on boot.
- [x] `AgentSnapshot.dormant` + `dormant_at_ts` from heartbeat;
      `_route_heartbeat` wires both.
- [x] `acc/policy_layer.py` seam (log-only EWMA, opt-in via
      `ACC_POLICY_LAYER_ENABLED`).
- [x] Tests: 41 across `test_dormant_watcher.py`,
      `test_prompt_default_assistant.py`,
      `test_policy_layer_subscriptions.py`,
      `test_assistant_control_signals.py`.

## Phase 2a — proposal model + parser + mode gating + dispatch (LANDED v0.3.26)

- [x] `acc/assistant_proposal.py`: `AssistantProposal`,
      `parse_proposal_markers`, `decide_dispatch`,
      `dispatch_approved_proposal`.
- [x] `subject_assistant_proposal(cid)` in `acc/signals.py`.
- [x] Three marker shapes: `[PROPOSE_SPAWN:…]`,
      `[PROPOSE_ROLE_UPDATE:…]`, `[PROPOSE_ROUTE:…]`.
- [x] Mode gating: PLAN / AUTO / ASK_PERMISSIONS / ACCEPT_EDITS.
- [x] Per-kind risk defaults (SPAWN=MEDIUM, ROLE_UPDATE=HIGH,
      ROUTE=LOW).
- [x] Assistant role.yaml: `can_route: true`, propose actions.
- [x] Tests: 24 in `test_assistant_proposal.py`.

## Phase 2b — cognitive_core integration + agent dispatch (LANDED v0.3.27)

- [x] `CognitiveResult` gains `assistant_proposals_{queued,executed,
      plan}`.
- [x] Cognitive core classification step in `process_task` (gated on
      `role_label == "assistant"`).
- [x] PLAN proposals prepended to `result.reasoning`.
- [x] Agent `_handle_assistant_proposals` dispatches EXECUTE list +
      queues + Redis-caches QUEUE list under
      `acc:{cid}:assistant_proposal:{oversight_id}`.
- [x] `_subscribe_oversight_decisions` extension:
      `_maybe_dispatch_assistant_proposal` on APPROVE;
      `_discard_assistant_proposal_cache` on REJECT.
- [x] Tests: 16 in `test_assistant_proposal_phase2b.py`.

## Phase 3a — managed sub-collectives model + routing primitives (LANDED v0.3.29)

- [x] `SubCollectiveSpec` (role_templates, domain,
      idle_hibernate_minutes, model, description).
- [x] `CollectiveSpec.managed_sub_collectives: dict[str,
      SubCollectiveSpec]`.
- [x] `acc/sub_collective.py`: `SubCollectiveRegistry`,
      `route_for_domain`, `build_seed_context_block`,
      `encode_lifecycle_payload`, `decode_lifecycle_payload`.
- [x] Registry preserves `last_active_ts` across re-register
      (hibernation clock survives YAML edits).
- [x] `subject_sub_collective_lifecycle(hub_cid)`.
- [x] Tests: 22 in `test_sub_collective.py`.

## Phase 3b — host-side lifecycle + cognitive-core seed-context (LANDED v0.3.31)

- [x] `acc-deploy.sh resume <cid>` / `hibernate <cid>` / `lifecycle-
      watcher {start|stop|status}`.
- [x] `scripts/acc-lifecycle-watcher.sh` (content+mtime+size signature;
      idempotent same-key; per-iter error isolation).
- [x] `up -d` auto-starts the lifecycle-watcher.
- [x] `acc/sub_collective.py::write_lifecycle_request` (atomic .tmp +
      os.replace).
- [x] `cognitive_core.build_system_prompt` injects
      `build_seed_context_block(registry)` when `_sub_collectives` is
      set.
- [x] Tests: 10 in `test_aoa_p3b_host_integration.py`.

## Phase 4 — Assistant gatekeeper AgentCard extension (LANDED v0.3.33)

- [x] `_assistant_gatekeeper_extension` in `acc/a2a/card.py`.
- [x] `acc.gatekeeper` vendor sub-extension: `isGatekeeper`,
      `proposalKinds`, `dormancyAware`, `policyEnabled`,
      `canRouteSubCollectives`, `openSpec`.
- [x] No PII / no operator_id / no env-bound values in the block.
- [x] Tests: 9 in `test_aoa_p4_agent_card.py`.

## Phase 5 — operator_id resolution seam (LANDED v0.3.34)

- [x] `acc/operator_identity.py::resolve_operator_id(override=,
      source=)`.
- [x] Sources: override > `ACC_OPERATOR_ID` env > source rule
      (`session`, `user`, `default`) > fallback "default".
- [x] Phase-5b session source stubbed safely (falls back to
      "default" until TUI auth lands).
- [x] Prompt screen sleep/wake control uses the resolver.
- [x] Tests: 13 in `test_aoa_p5_operator_identity.py`.

## Phase 6 — Assistant learns route_confidence_threshold (LANDED v0.3.30)

- [x] `RoleDefinitionConfig.policy_enabled` / `policy_pinned` /
      `policy_update_every_n_tasks` / `policy_drift_cap`.
- [x] Agent boot threads role-level policy config into
      `RewardHarness`.
- [x] `_handle_task` calls `harness.observe_task(operating_mode,
      drift)` after every TASK_COMPLETE.
- [x] Assistant role.yaml v2.1.0: `policy_enabled: true`,
      `policy_pinned: [spawn_threshold, delegate_domain_match,
      memory_top_k, reasoning_depth_target]`.
- [x] Prompt screen mode hint: "💤 policy frozen" badge under AUTO.
- [x] Tests: 8 in `test_aoa_p6_role_policy_wiring.py`.

## Follow-up proposals spawned (per standing rule)

- `20260531-assistant-catchup-window` — bounded catch-up window.
- `20260531-assistant-dormancy-banner-ux` — persistent banner +
  reminder.
- `20260531-assistant-wake-triggers-tunable` — operator-tunable wake
  triggers.
