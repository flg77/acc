# 20260923-lessons-that-travel — tasks

## Phase 1 (v0.22.0) — the envelope, the relay, the trace

### 1.1 The envelope
- [x] `acc/lessons.py`: `Lesson`, `LessonEvidence`, `parse_lesson`, `lesson_from_note`, `SCHEMA_REV`
- [x] `PeerLessonRing` — bounded, TTL, dedupe by id, consumed on `take`, `visible_to` (scope + ceiling)
- [x] `peer_lessons_parts` / `render_peer_lessons_block` (role label, dissent, never the agent id)

### 1.2 The producer
- [x] `Agent._publish_lessons` after `write_hot_cache` in `_run_reflection_once`
- [x] `acc.{cid}.knowledge.{tag}` with `tag` = role `domain_id` or `general`
- [x] Redis copy (`redis_lesson_key`, `redis_lessons_index_key`, 7 d)
- [x] tracelog `KIND_LESSON` `direction=published` in `lessons-<agent_id>`

### 1.3 The consumer
- [x] `Agent._subscribe_knowledge_share` in `run()`; JSON decode errors logged, never raised
- [x] `_accept_lesson`: invalid / own / not-addressed / no-receptor / no-core / accepted
- [x] `CognitiveCore.receive_lesson` + `pending_peer_lessons`
- [x] tracelog `direction=received`

### 1.4 The prompt
- [x] `context_budget.KIND_PEER_LESSONS`, fifth block, priority 4 (first evicted), display after notes
- [x] `_process_task_body` drains under `scope_key` + `ceiling_of`, capped by `memory_note_bandwidth`, gated by `memory_retrieval`
- [x] `_compose_user_content` / `_packed_user_content` take `peer_lessons`; empty ⇒ byte-identical
- [x] `ACC_PEER_LESSONS` kill switch, `acc/configschema.py`

### 1.5 The outcome join
- [x] `CognitiveResult.lessons_used`
- [x] `TASK_COMPLETE.lessons_used` (only when non-empty) + `redis_lesson_used_key` set

### 1.6 The tooling
- [x] `acc-cli lessons list|show|trace|send`, registered in `acc/cli/__init__.py`
- [x] `docs/CAPABILITIES.md` Memory table; `docs/SUBAGENT_COMMUNICATION.md` Pattern B carries the envelope
- [x] CHANGELOG `[Unreleased]`

### Verification
- [x] `tests/test_lessons.py` (23) + neighbouring suites (context budget, reflection loop, cognitive core, memory) — 349 passed
- [ ] full sweep (`tests/` minus `container/` + `integration/`)
- [ ] lighthouse smoke: `ACC_REFLECTION_INTERVAL_S=60`, two roles; `acc-cli lessons list` shows the analyst's note; `acc-cli lessons send --to <reviewer>` then a reviewer prompt renders `PEER_LESSONS`; `lessons trace <id>` names the task
- [ ] vault: PA-03 status row, `_INDEX — Prime Agent gap roadmap items`

## Phase 2 (same PR) — the ledger (PA-04)
- [x] `acc/refinements.py`: `record` / `load` / `find` / `trace` / `outcomes_for` / `changed_fields`; file + Redis mirror
- [x] writers: reflection (`_record_notes`), `RoleStore.apply_update` (old/new fields, approver, lesson_id), `rule_proposals.approve_proposal`, `memory_forget.forget_person`, `_dispatch_publish`
- [x] `memory_reflection.revoke_note`
- [x] `acc-cli refine list|show|trace|rollback [--apply]` (note/adopt revoke; role_patch → revert proposal; others refused)
- [x] `tests/test_refinements.py` (10)

## Phase 3 (same PR) — `role_patch` → `role_update` proposal
- [x] `_classify_lesson` (`role_patch` verdict, ring untouched), `_propose_from_lesson`, journal `proposed`
- [x] `_dispatch_role_update` forwards `lesson_id` / `rollback_of`
- [x] `acc-cli lessons send --kind role_patch --patch-role --set`
- [x] tests (3)
- [x] **Phase 7:** `Agent._countersign_role_update` (see below)

## Phase 4 (same PR) — durable adoption
- [x] `RoleDefinitionConfig.accept_peer_lessons` — default **True** since D-028 (operator, 2026-09-24; was False); `acc.config.ACCEPT_PEER_LESSONS_DEFAULT` is the one place it lives, and the agent's fallback reads it
- [x] tests: the default, the opt-out, and an adopted note filtered on read by the lesson's ceiling and scope (3, replacing the "off unless asked" test)
- [x] lighthouse smoke of D-028 (2026-09-24), 5/5 on a throwaway NATS + Redis beside the host (its live stack was down): kept by default, ceiling carried, a signed opt-out via `role_patch` with a control, `refine rollback`, the read filter after the 900 s probation — evidence §4d
- [x] the same five on the **live bus** (live NATS + Redis, beside the live collective, 2026-09-25), 5/5, the live collective undisturbed — evidence §4e
- [ ] the same five inside the live containers — after the release puts #480 in the images
- [x] `Agent._adopt_lesson` → `publish_note` into the reader role's shared tier, `adopt` ledger row; probation applies
- [x] test (1)

## Phase 5 (same PR) — the outcome
- [x] `Agent.lesson_outcome_delta` (objective signals only), `_record_lesson_outcomes` (observations, confidence, ledger)
- [x] `TASK_COMPLETE` computes the verdict before the outcome
- [x] tests (7)
- [x] a reviewer's verdict about another step reaching that step's `lessons_used`: `PlanExecutor._notify_critic_verdict` + `_Step.lessons_used` + `Agent._record_critic_verdict` (`source: critic`)

## Phase 10 (same PR) — an authenticated sender (PA-09 Phase 1)
- [x] `acc/nkeys.py`: `decode_seed` / `decode_public` / `public_key_of_seed` / `NKeyError` — the inverse of the encoder that has been there since proposal 013, no new dependency
- [x] `acc/wire.py`: canonical form (sorted compact JSON minus the proof), `sign_payload`, `verify_payload`, `load_public_keys`, `default_public_keys_path`, `SenderProof`
- [x] `security.nkey.public_keys_path` (+ `ACC_NKEY_PUBLIC_KEYS_PATH`); empty = beside the seed, where the generator puts it
- [x] `Agent._sign_ask` / `_sender_refusal` / `_sender_seed` / `_sender_public_keys` (each read once); `ROUTE_REQUEST` signed on publish and verified first in the refusal ladder
- [x] the identity→role binding where it exists: a claimed role that is itself an NKey identity must BE the signer
- [x] no key set readable ⇒ accept unverified and say so (the rollout order: sign, distribute, verify)
- [x] **proof schema rev 2** — the proof carries the signer's **public key**, and the verifier resolves *which* identity signed by finding that key in the key set, then verifies with the **key set's** copy. `identity` is a log label nothing authorises on. Found by the lighthouse smoke: rev 1 asked the signer to name itself and looked the name up, so a packaged role (no `ACC_NKEY_ROLE`, signing with `seed-coding_agent`, labelling itself `orchestrator`) was refused — in exactly the deployment Phase 9 exists for
- [x] `tests/test_wire.py` (40) + 10 in `tests/test_route_through_arbiter.py`, incl. the regression reproducing the smoke's `no public key for identity 'orchestrator'` verbatim
- [x] lighthouse smoke with the guards ACTIVE (2026-09-23), 6/6 after the fix — evidence §4c: a packaged role's ask (worker key, `orchestrator` label) accepted and routed; unsigned, tampered, lifted-proof, wrong-role and **relabelled-proof** asks all refused; nothing refused reached `task.assign`
- [ ] sign `AGENT_MESSAGE` too — its follow-up runs at the sender's attribution
- [ ] PA-09's other halves: typed envelopes for the legacy signals, `NATSBackend.request()`, a durable `task.*` stream, generated `docs/WIRE.md`

## Phase 9 (same PR) — a `can_route` role asks, the arbiter routes
- [x] `SIG_ROUTE_REQUEST` + `subject_route_request()`; matrix (worker publish + subscribe, arbiter publish + `acc.>` subscribe) and `comms_provisioning`
- [x] `build_routed_task()` — one builder for both sides (same `task_id`, chosen role, no pin, `routed_by` stamp)
- [x] `Agent._route_task()` — publishes where it may, asks otherwise; inert unless `security.nkey.enabled`
- [x] `Agent._route_request_refusal()` / `_handle_route_request()` / `_subscribe_route_requests()` — arbiter re-checks signed `can_route`, the roster, and the hop cap
- [x] `tests/test_route_through_arbiter.py` (22); the source-text wiring guard in `test_orchestrator_routing.py` updated to the new structure
- [x] lighthouse smoke with the guards ACTIVE (`ACC_NKEY_ENABLED=true` + throwaway seeds, 2026-09-23): the orchestrator asked and the arbiter routed (`routed_by` = the asking agent, the analyst answered the same `task_id`); all three refusals fired (no `can_route`, already-routed, roster mismatch) and nothing leaked past them — evidence §4b
- [x] the sender is self-declared → **closed by Phase 10** (below)

## Phase 8 (same PR) — who may dispatch an approved proposal
- [x] matrix: `acc.*.assistant.*` in the worker baseline (static + `comms_provisioning`); `acc.*.collective.reconcile` + `acc.*.assistant.*` on the arbiter — so the arbiter may dispatch **every** kind
- [x] `assistant_proposal._DISPATCH_SUBJECTS_OF` / `dispatch_subjects()` — every subject a kind's dispatch may publish (`infuse` = outcome **and** continuation `TASK_ASSIGN`)
- [x] `nats_permissions.may_publish(identity, subject)`; unnamed identity reads as a worker
- [x] `Agent._nkey_identity()` / `_may_dispatch_proposal()`; inert unless `security.nkey.enabled`
- [x] approval path: an identity that may not publish does not take the claim — the arbiter does
- [x] AUTO path: an auto-execute it may not publish is **queued**, not dropped
- [x] `tests/test_proposal_dispatch_authority.py` (34), incl. the `subject_covered` blind spot pinned
- [x] lighthouse smoke with the guards ACTIVE (2026-09-23): a worker left an approved `role_update` claim for the arbiter, which dispatched and countersigned it; an assistant in AUTO queued the two proposals it could not publish instead of dropping them, and approving one had the arbiter dispatch it — evidence §4b

## Phase 7 (same PR) — the arbiter countersigns
- [x] `Agent._countersign_role_update` (resolve role → apply fields → bump → validate → Ed25519 sign the canonical message → re-publish with `countersigned`, `countersigned_for`, `trigger` / `proposal_id` / `lesson_id` / `rollback_of`)
- [x] `_resolve_role_definition` (Redis role key of a roster member, else `RoleLoader` on `roles_root()`), `_bump_role_version`
- [x] `_handle_role_update`: unsigned → countersign (arbiter) or drop; signed + `role` → only that role applies
- [x] PA-04 harness fingerprint: `CognitiveCore.harness_fingerprint`, `CognitiveResult.harness_fingerprint`, episode `payload_json`, `TASK_COMPLETE.harness_fingerprint`, outcome observations
- [x] `tests/test_lessons_phase7.py` (11): a proposal is countersigned and a real `RoleStore` verifies + applies it; tampering rejected; refusals (not arbiter / no key / unresolvable / invalid); CLI infuse shape; the handler round trip (signed once, applied by the named role only, a worker neither signs nor applies); the critic join; the fingerprint

## Phase 6 (same PR) — the inbox (PA-02)
- [x] `SIG_AGENT_MESSAGE`, `subject_agent_inbox[_all]`, Redis keys; NKey matrix (workers subscribe, arbiter + tui publish); `comms_provisioning`
- [x] `acc/agent_messages.py`: `AgentMessage`, `parse_message`, `SteerRing`, `steering_parts`, `follow_up_task` (attribution copied)
- [x] `context_budget.KIND_STEER` (priority 1, before the task, evicted last); `CognitiveCore.receive_steer`, `steer_used`
- [x] `Agent._deliver_message` / `_subscribe_agent_inbox` / `_write_receipt`; `_tasks_in_flight`; `_local_task_handler`
- [x] tracelog `KIND_AGENT_MESSAGE`; `TASK_COMPLETE.steer_used`
- [x] `acc-cli msg send|tail|show`
- [x] `tests/test_agent_messages.py` (13, incl. the matrix)
- [x] lighthouse runtime smoke 2026-09-23 (staged `smoke-01` collective on the live bus, not a rebuild): (a) reflection → lesson → peer accept → `lessons list`; (b) `lessons_used` on the peer's next task, consumed once, `lessons trace`; (c) `msg send --follow-up` → `msg-…` task, `--steer` → delivered and rendered on the next task (`steer_used`); (d) `role_patch` → proposal → approve → unsigned then countersigned `ROLE_UPDATE` → applied by the analyst only; (e) `refine trace`. Evidence: vault `70-evidence/ACC-Tests/20260923-lessons-that-travel-lighthouse/` §4
- [x] **Phase 8** — see below
- [ ] the same five checks against the rebuilt live image (operator's release step)
- [ ] ~~lighthouse: `acc-cli msg send <reviewer> --steer` during a running plan step~~ (covered by the smoke) → `OPERATOR_STEERING` in its `prompt_in`; `--follow-up` on an idle agent → a `msg-…` task attributed to the operator
